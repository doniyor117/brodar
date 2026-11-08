"""File lifecycle SQL + bytes checked only on a disposable checkpoint target."""
import os
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
import pytest
from brodar.postgres import Postgres
from brodar.scope import Scope
from brodar.learning import LearningStore
from brodar.artifacts import Artifacts
pytestmark=pytest.mark.skipif(not os.environ.get('BRODAR_TEST_DATABASE_URL'),reason='Disposable DB not configured')

class Objects:
    def __init__(self):self.files={};self.puts=0
    async def put(self,key,body,mime):self.files[key]=body;self.puts+=1
    async def get(self,key):return self.files[key]

@pytest.mark.asyncio
async def test_temporary_without_s3_explicit_retain_quota_recovery_and_expiry(tmp_path):
    db=Postgres(os.environ['BRODAR_TEST_DATABASE_URL']);scope=Scope(456,-abs(uuid4().int%10000000000),instance_id='file-test')
    store=LearningStore(db,scope);objects=Objects();files=Artifacts(store,root=tmp_path/'files',temp_budget=100,retained_budget=100)
    def register():
        with db.transaction(scope.audience) as c:
            c.execute('INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id) VALUES (%s,%s,%s)',(scope.audience,scope.instance_id,scope.chat_id))
    await db.run(register)
    turn=str(uuid4())
    try:
        # Use existing deployment totals + bounded headroom to avoid touching global counters.
        def totals():
            with db.transaction(readonly=True) as c:c.execute('SELECT kind,bytes FROM brodar_v2.storage_usage');return dict(c.fetchall())
        totals_=await db.run(totals);files.temp_budget=totals_['temporary']+100;files.retained_budget=totals_['retained']+100
        args=dict(turn_id=turn,operation_id='one',filename='hello.txt',mime='text/plain',body=b'hello',request_hash='hash')
        item=await files.put(**args);assert item['storage_kind']=='temporary' and objects.puts==0
        assert await files.put(**args)==item
        row,body=await files.read(item['artifact_id']);assert body==b'hello'
        with pytest.raises(RuntimeError,match='not configured'):await files.retain(item['artifact_id'],{'actor':7})
        files.objects=objects
        kept=await files.retain(item['artifact_id'],{'actor':7,'message_id':'explicit-save'})
        assert kept['storage_kind']=='retained' and objects.puts==1
        assert (await files.retain(item['artifact_id'],{'actor':7}))['storage_kind']=='retained' and objects.puts==1
        with pytest.raises(ValueError,match='budget'):
            await files.put(**dict(args,operation_id='big',body=b'x'*101,request_hash='other'))
        second=await files.put(**dict(args,operation_id='two'))
        def age():
            with db.transaction(scope.audience) as c:c.execute("UPDATE brodar_v2.artifacts SET expires_at=now()-interval '1 day' WHERE audience=%s AND id=%s",(scope.audience,second['artifact_id']))
        await db.run(age);await files.cleanup()
        with pytest.raises(ValueError,match='expired'):await files.read(second['artifact_id'])
        assert (await files.read(item['artifact_id']))[1]==b'hello'
    finally:
        def cleanup():
            with db.transaction(scope.audience) as c:
                c.execute("SELECT storage_kind,state,size_bytes FROM brodar_v2.artifacts WHERE audience=%s",(scope.audience,));rows=c.fetchall()
                for kind,state,size in rows:
                    if state!='expired':c.execute('UPDATE brodar_v2.storage_usage SET bytes=greatest(0,bytes-%s) WHERE kind=%s',(size,kind))
                c.execute('DELETE FROM brodar_v2.artifacts WHERE audience=%s',(scope.audience,));c.execute('DELETE FROM brodar_v2.audience_registry WHERE audience=%s',(scope.audience,))
        await db.run(cleanup);await db.close()

@pytest.mark.asyncio
async def test_authenticated_local_operator_job_detaches_turn_and_records_actual_result(tmp_path):
    import sys,asyncio
    from unittest.mock import AsyncMock
    from brodar.terminal import TerminalScope
    from brodar.broker import ToolBroker,TurnCapability
    from brodar.owner_tools import OwnerJobs
    from brodar.commands import CommandGateway
    from brodar.jobs import InputRequest
    from brodar.store import digest
    db=Postgres(os.environ['BRODAR_TEST_DATABASE_URL']);scope=TerminalScope(456,0,instance_id='exec-'+uuid4().hex[:8]);store=LearningStore(db,scope)
    request_id=str(uuid4());text='run a synthetic local operator calculation'
    def seed():
        with db.transaction(scope.audience) as c:
            c.execute('INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id) VALUES (%s,%s,0)',(scope.audience,scope.instance_id))
            c.execute("INSERT INTO brodar_v2.messages(id,audience,sender_name,role,content,source,source_id,source_hash) VALUES (%s,%s,'local operator','user',%s,'terminal',%s,%s)",(request_id,scope.audience,text,request_id,digest(text)))
    await db.run(seed)
    cfg=SimpleNamespace(owner_ids={7});bot=SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=77)))
    jobs=OwnerJobs(cfg,bot,lambda:SimpleNamespace(require=AsyncMock()))
    b=ToolBroker(store,bot,owner_ids={7});b.operator_session=True;b.commands=CommandGateway(tmp_path);b.begin(TurnCapability(str(uuid4()),mode='operator'))
    b.accept_requests([InputRequest(request_id,None,text)])
    try:
        result=await jobs.submit(b,request_id,[sys.executable,'-c','print(2+2)'],'calculation',timeout=10)
        b.end()  # Job is still authorized by the durable source, never a stale tool nonce.
        await asyncio.wait_for(asyncio.gather(*list(jobs.tasks.values())),30)
        def status():
            with db.transaction(scope.audience,readonly=True) as c:
                c.execute('SELECT status,payload FROM brodar_v2.jobs WHERE audience=%s AND id=%s',(scope.audience,result['job_id']));return c.fetchone()
        state,payload=await db.run(status)
        assert state=='done' and payload['exit_code']==0 and payload['output'].strip()=='4'
        bot.send_message.assert_awaited_once()
    finally:
        await jobs.close()
        def cleanup():
            with db.transaction(scope.audience) as c:
                for table in ('deliveries','jobs','messages','audience_registry'):c.execute('DELETE FROM brodar_v2.'+table+' WHERE audience=%s',(scope.audience,))
        await db.run(cleanup);await db.close()
