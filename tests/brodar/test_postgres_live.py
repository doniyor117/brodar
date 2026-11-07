"""Opt-in integration checks on a disposable Neon branch using the app role."""

import os
from datetime import datetime,timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from brodar.postgres import Postgres
from brodar.scope import Scope
from brodar.store import RevisionConflict,ScopedStore

pytestmark=pytest.mark.skipif(not os.environ.get('BRODAR_TEST_DATABASE_URL'),reason='Disposable Neon test URL not configured')


@pytest.mark.asyncio
async def test_research_jobs_recovery_private_events_and_interaction_limit(stores):
    from brodar.research import ResearchStore
    from brodar.recovery import ConsumerLease
    from brodar.runtime_store import RuntimeStore
    from brodar.coordination import LoopGuard
    a,b=stores
    lease=ConsumerLease(a.db,a.scope.bot_id);await lease.acquire()
    guard=LoopGuard(a.db,a.scope.instance_id);await guard.register()
    jobs=ResearchStore(a.db,a.scope.instance_id,lambda:lease)
    uid=str(uuid4())
    try:
        arguments=dict(task_id=uid,scope=a.scope,requester=42,request_id=str(uuid4()),brief='research',agent_id='a',message='brief',operation='one')
        assert (await jobs.enqueue(**arguments))['status']=='queued'
        assert str((await jobs.enqueue(**arguments))['id'])==uid
        assert await jobs.list(b.scope)==[]
        row=(await jobs.claim())[0]
        await jobs.submitting(row)
        # Restart between submit and receiving/persisting a remote thread ID.
        await lease.release()
        lease=ConsumerLease(a.db,a.scope.bot_id);await lease.acquire()
        assert await jobs.claim()==[]
        assert (await jobs.get(uid))['status']=='uncertain'
        # Result events have no human sender and do not alter the poll offset.
        event=dict(id=uid,generation=0,requester_id=42,status='uncertain')
        first=RuntimeStore(a.db,a.scope,lease_owner=lease.owner)
        second=RuntimeStore(a.db,b.scope,lease_owner=lease.owner)
        update,new=await first.enqueue_research_result(event,'outside research evidence')
        assert new and update<0
        assert await first.enqueue_research_result(event,'outside research evidence')==(update,False)
        other=dict(event,id=str(uuid4()))
        other_update,new=await second.enqueue_research_result(other,'other group evidence')
        assert new and other_update<0 and other_update!=update
        pending=await first.claim_inputs()
        assert len(pending)==1 and pending[0]['payload']['internal_research']['task_id']==uid
        assert 'message' not in pending[0]['payload']
        assert (await first.recent())[0]['source']=='research'
        assert (await second.recent())[0]['content']=='other group evidence'
        # Check actual SQL follow-up idempotency and the hard tenth-request cap.
        def ready():
            with a.db.transaction(jobs.audience) as c:
                c.execute("UPDATE brodar_v2.research_tasks SET status='complete',remote_thread_id='t',notified_generation=generation,interactions=9 WHERE id=%s",(uid,))
        await a.db.run(ready)
        follow=await jobs.follow_up(uid,'one more source','follow-1')
        assert follow['generation']==1
        assert (await jobs.follow_up(uid,'one more source','follow-1'))['generation']==1
        with pytest.raises(ValueError):await jobs.follow_up(uid,'changed content','follow-1')
        claimed=(await jobs.claim())[0]
        await jobs.submitting(claimed)
        await jobs.update(claimed,status='complete')
        complete=await jobs.get(uid);await jobs.notified(complete)
        with pytest.raises(PermissionError):await jobs.follow_up(uid,'eleventh request','follow-2')
        # An old operation replay is still idempotent after later transitions.
        assert (await jobs.follow_up(uid,'one more source','follow-1'))['interactions']==10
        # Waiting for a platform approval and the later report each notify once.
        second_id=str(uuid4())
        await jobs.enqueue(**dict(arguments,task_id=second_id,operation='approval-task'))
        second_row=(await jobs.claim())[0]
        await jobs.submitting(second_row)
        await jobs.update(second_row,status='waiting',remote_thread_id='approval-thread',last_result_id='awaiting_approval',
            result={'status':'blocked','report':'approval required','result_id':'awaiting_approval'})
        paused=next(r for r in await jobs.notifications() if str(r['id'])==second_id)
        await jobs.notified(paused)
        assert all(str(r['id'])!=second_id for r in await jobs.notifications())
        await jobs.update(second_row,status='complete',last_result_id='final-report',
            result={'status':'complete','report':'finished','result_id':'final-report'})
        completed=next(r for r in await jobs.notifications() if str(r['id'])==second_id)
        assert completed['result_version']>paused['result_version']
    finally:
        await lease.release()
        def clean():
            with a.db.transaction(jobs.audience) as c:
                c.execute('DELETE FROM brodar_v2.research_tasks WHERE audience=%s',(jobs.audience,))
                c.execute('DELETE FROM brodar_v2.audience_registry WHERE audience=%s',(jobs.audience,))
        await a.db.run(clean)


@pytest_asyncio.fixture
async def stores():
    db=Postgres(os.environ['BRODAR_TEST_DATABASE_URL'])
    namespace='test-'+uuid4().hex
    bot_id=int(uuid4().hex[:12],16)
    a=ScopedStore(db,Scope(bot_id,-11,instance_id=namespace))
    b=ScopedStore(db,Scope(bot_id,-22,instance_id=namespace))
    yield a,b
    def cleanup():
        for s in [a,b]:
            with db.transaction(s.scope.audience) as c:
                for table in ['approvals','maintenance_runs','reflection_state','memory_sources','memory_revisions','memory_documents','embeddings','deliveries','artifacts','tool_calls','inbox','messages','sessions','forgotten_sources','jobs']:
                    c.execute(f'DELETE FROM brodar_v2.{table} WHERE audience=%s',(s.scope.audience,))
                c.execute('DELETE FROM brodar_v2.poll_state WHERE bot_id=%s',(bot_id,))
                c.execute('DELETE FROM brodar_v2.audience_registry WHERE audience=%s',(s.scope.audience,))
    await db.run(cleanup)
    await db.close()


async def add(store,text,update=1):
    return await store.ingest(update_id=update,message_id=update,sender_id=42,
        sender_name='test',text=text,timestamp=datetime.now(timezone.utc),payload={'test':True})


@pytest.mark.asyncio
async def test_native_media_album_and_caption_edit_archive(stores):
    from types import SimpleNamespace
    from brodar.runtime_store import RuntimeStore
    from brodar.broker import ToolBroker,TurnCapability
    from brodar.telegram_media import send_media,send_album,chat_action
    a,b=stores
    store=RuntimeStore(a.db,a.scope)
    class Bot:
        username='BrodarBot'
        async def send_photo(self,**kw):return SimpleNamespace(message_id=1001)
        async def send_media_group(self,**kw):return (SimpleNamespace(message_id=1002),SimpleNamespace(message_id=1003))
        async def edit_message_caption(self,**kw):return SimpleNamespace(message_id=kw['message_id'])
    broker=ToolBroker(store,Bot())
    broker.begin(TurnCapability(str(uuid4()),requester_id=42))
    await send_media(broker,'photo','file:real_file_123','original caption','photo')
    await send_album(broker,[{'kind':'photo','media':'file:real_file_456','caption':'first'},
        {'kind':'video','media':'file:real_file_789','caption':'second'}],'album')
    await chat_action(broker,'edit_caption',{'message_id':1001,'caption':'corrected caption'},'edit')
    rows=await store.recent()
    assert len(rows)==3
    photo=next(r for r in rows if r['platform_message_id']==1001)
    assert photo['content']=='corrected caption\n[photo]'
    from brodar.store import digest
    assert photo['source_hash']==digest(photo['content'])
    assert {r['platform_message_id'] for r in rows}=={1001,1002,1003}
    assert all(r['role']=='assistant' and r['sender_id']==a.scope.bot_id for r in rows)
    assert await b.recent()==[]
    receipts=await store.delivery_receipts(broker.capability.turn_id)
    assert next(r for r in receipts if r['method']=='send_media_group')['response']['message_ids']==[1002,1003]


@pytest.mark.asyncio
async def test_duplicate_ingestion_and_edits_preserve_inbox(stores):
    a,b=stores
    # Bot update IDs are global; make this test's fake bot distinct too.
    first=await add(a,'hello')
    duplicate=await add(a,'hello')
    assert first[1] is True and duplicate==(first[0],False)
    rows=await a.recent()
    assert len(rows)==1 and rows[0]['content']=='hello'
    assert await b.recent()==[]


@pytest.mark.asyncio
async def test_reminder_restart_deduplication_permissions_and_cancel(stores):
    from brodar.jobs import Reminders,InputRequest
    from brodar.recovery import ConsumerLease
    from brodar.runtime_store import RuntimeStore
    from datetime import timedelta
    a,b=stores
    lease=ConsumerLease(a.db,a.scope.bot_id);await lease.acquire()
    store=RuntimeStore(a.db,a.scope,lease_owner=lease.owner);jobs=Reminders(store)
    request=InputRequest(str(uuid4()),42,'remind me')
    when=(datetime.now(timezone.utc)+timedelta(minutes=1)).isoformat()
    try:
        first=await jobs.create(request,when,'persisted reminder','one')
        assert (await jobs.create(request,when,'persisted reminder','one'))['job_id']==first['job_id']
        assert (await Reminders(RuntimeStore(a.db,a.scope,lease_owner=lease.owner)).list())[0]['text']=='persisted reminder'
        assert await Reminders(b).list()==[]
        with pytest.raises(PermissionError):await jobs.cancel(first['job_id'],43)
        def due():
            with a.db.transaction(a.scope.audience) as c:
                c.execute("UPDATE brodar_v2.jobs SET due_at=now()-interval '1 second' WHERE id=%s",(first['job_id'],))
        await a.db.run(due)
        claimed=await jobs.claim();assert len(claimed)==1
        await jobs.active(first['job_id'])
        result=await jobs.cancel(first['job_id'],42)
        assert result['delivery_may_be_in_flight']
        with pytest.raises(PermissionError):await jobs.active(first['job_id'])
        assert await jobs.claim()==[]
    finally:await lease.release()


@pytest.mark.asyncio
async def test_automatic_notes_revision_conflict_sources_and_rls(stores):
    a,b=stores
    await add(a,'please use shorter explanations')
    mid=str((await a.recent())[0]['id'])
    written=await a.memory_write('context/people/42.md','likes concise explanations',
        expected_revision=0,reason='direct feedback',source_ids=[mid],subject_ids=[42])
    assert written['revision']==1
    read=await a.memory_read('context/people/42.md')
    assert read['content']=='likes concise explanations'
    assert await b.memory_read('context/people/42.md') is None
    with pytest.raises(RevisionConflict):
        await a.memory_write('context/people/42.md','stale edit',expected_revision=0,reason='stale')
    with pytest.raises(ValueError,match='this audience'):
        await b.memory_write('context/forged.md','other group source',expected_revision=0,reason='forged',source_ids=[mid])
    def cross_scope_query():
        with a.db.transaction(b.scope.audience,readonly=True) as c:
            # Deliberately omit the audience predicate: RLS still filters it.
            c.execute('SELECT id FROM brodar_v2.memory_documents WHERE id=%s',(written['id'],))
            assert not c.fetchall()
    await a.db.run(cross_scope_query)
    await a.memory_delete('context/people/42.md',expected_revision=1,reason='superseded')
    assert await a.memory_read('context/people/42.md') is None


@pytest.mark.asyncio
async def test_vector_recall_and_stale_embedding_guard(stores):
    a,b=stores
    await add(a,'we agreed to build a presentation about astronomy')
    row=(await a.recent())[0]
    vector=[1.0]+[0.0]*767
    assert await a.put_embedding('message',str(row['id']),row['content'],vector,'test-model')
    assert not await a.put_embedding('message',str(row['id']),'outdated text',vector,'test-model')
    result=await a.search(' космос ',vector=vector,model='test-model')
    assert result[0]['content']==row['content']
    assert await b.search('astronomy',vector=vector,model='test-model')==[]


@pytest.mark.asyncio
async def test_consumer_takeover_reclaims_work_and_fences_previous_owner(stores):
    from brodar.recovery import ConsumerLease
    from brodar.runtime_store import RuntimeStore
    a,_=stores
    first=ConsumerLease(a.db,a.scope.bot_id)
    second=ConsumerLease(a.db,a.scope.bot_id)
    await first.acquire()
    try:
        with pytest.raises(RuntimeError,match='Another instance'):
            await second.acquire()
        old=RuntimeStore(a.db,a.scope,lease_owner=first.owner)
        new=RuntimeStore(a.db,a.scope,lease_owner=second.owner)
        await add(old,'recover this task')
        claimed=await old.claim_inputs()
        assert len(claimed)==1
        uid=str(claimed[0]['id'])
        await old.work_begin(uid,42,[str(claimed[0]['message_id'])])
        await old.work_checkpoint(uid,'deck created; next send the actual file')
        def expire():
            with a.db.transaction() as c:
                c.execute("UPDATE brodar_v2.poll_state SET lease_until=now()-interval '1 second' WHERE bot_id=%s",(a.scope.bot_id,))
        await a.db.run(expire)
        await second.acquire()
        with pytest.raises(PermissionError):await first.require()
        with pytest.raises(PermissionError):await old.claim_inputs()
        reclaimed=await new.claim_inputs()
        assert reclaimed[0]['id']==claimed[0]['id'] and reclaimed[0]['attempts']==2
        await old.finish_inputs([claimed[0]['id']],job_id=uid)
        recovered=await new.work_begin(uid,42,[str(claimed[0]['message_id'])])
        assert recovered['status']=='running' and recovered['checkpoint']['notes']=='deck created; next send the actual file'
        with pytest.raises(PermissionError):await old.work_checkpoint(uid,'stale write')
        def status():
            with a.db.transaction(a.scope.audience,readonly=True) as c:
                c.execute('SELECT status,lease_owner FROM brodar_v2.inbox WHERE id=%s',(str(claimed[0]['id']),))
                return c.fetchone()
        row=await a.db.run(status)
        assert row[0]=='running' and str(row[1])==second.owner
        await new.finish_inputs([claimed[0]['id']],job_id=uid)
        assert (await a.db.run(status))[0]=='done'
        assert (await new.work_list())[0]['status']=='done'
    finally:
        await first.release()
        await second.release()


@pytest.mark.asyncio
async def test_source_forgetting_removes_derivatives_and_prevents_reimport(stores):
    from brodar.forgetting import forget_messages
    from brodar.legacy import import_messages,verify_import
    from brodar.learning import LearningStore
    a,b=stores
    a=LearningStore(a.db,a.scope)
    await add(a,'source detail to forget')
    mid=str((await a.recent())[0]['id'])
    await add(a,'unrelated first source',update=2)
    other=str((await a.recent())[-1]['id'])
    work_id=str(uuid4())
    await a.work_begin(work_id,42,[other])
    await a.work_begin(work_id,42,[other,mid])
    work=(await a.work_list())[0]
    await a.work_checkpoint(str(work['id']),'checkpoint contains source detail to forget')
    await a.memory_write('context/detail.md','learned from that source',expected_revision=0,reason='fixture',source_ids=[mid])
    await a.session_put('codex','fixture','stale-session')
    result=await forget_messages(a.db,a.scope.audience,[mid])
    assert result['forgotten_messages']==1 and result['retired_source_linked_notes']==1
    assert await a.memory_read('context/detail.md') is None
    assert len(await a.recent())==1 and (await a.recent())[0]['content']=='unrelated first source'
    assert await a.work_list()==[]
    assert await a.session_get('codex','fixture') is None
    assert (await add(a,'source detail to forget',update=1))[1] is False
    archive={'source':'legacy-fixture','tables':{'messages':[{'id':1,'chat_id':a.scope.chat_id,
        'created_at':'2026-07-25 12:00:00','role':'user','content':'legacy detail'}]}}
    assert (await import_messages(a.db,archive,instance_id=a.scope.instance_id))['inserted']==1
    legacy=str(next(row for row in await a.recent() if row['source']=='legacy-fixture')['id'])
    await forget_messages(a.db,a.scope.audience,[legacy])
    assert (await import_messages(a.db,archive,instance_id=a.scope.instance_id))['inserted']==0
    assert await verify_import(a.db,archive,instance_id=a.scope.instance_id)==0
    assert await b.recent()==[]


@pytest.mark.asyncio
async def test_delivered_reply_parent_is_context_without_becoming_a_new_request(stores):
    from brodar.runtime_store import RuntimeStore
    from brodar.moderation import requests
    a,b=stores;a=RuntimeStore(a.db,a.scope)
    parent={'message_id':5,'chat':{'id':a.scope.chat_id,'type':'supergroup'},'date':1793820000,
            'from':{'id':1,'first_name':'quoted sender'},'text':'/ban 3'}
    payload={'message':{'message_id':10,'from':{'id':42},'text':'what does this mean?',
                        'reply_to_message':parent}}
    _,new=await a.ingest(update_id=1,message_id=10,sender_id=42,sender_name='test',text='what does this mean?',
        timestamp=datetime.now(timezone.utc),reply_to_id=5,payload=payload)
    assert new
    batch=await a.claim_inputs();assert len(batch)==1
    assert requests(batch)==[]  # the quoted owner's command has no fresh authority
    chain=await a.reply_context([str(batch[0]['message_id'])])
    assert len(chain)==1 and chain[0]['content']=='/ban 3' and chain[0]['sender_id']==1
    assert chain[0]['metadata']['delivered_as_reply_context']
    from brodar.learning import LearningStore
    bundle=await LearningStore(a.db,a.scope).turn_context([str(batch[0]['message_id'])],str(uuid4()))
    assert bundle['messages'][0]['content']=='what does this mean?'
    assert any(row['content']=='/ban 3' for row in bundle['context'])
    assert bundle['receipts']==[] and bundle['artifacts']==[] and not bundle['has_index']
    hidden=await LearningStore(b.db,b.scope).turn_context([str(batch[0]['message_id'])],str(uuid4()))
    assert hidden['messages']==[] and hidden['context']==[]
    assert await b.recent()==[]
    other={'message':{'from':{'id':42},'text':'cross-chat quote','reply_to_message':{
        **parent,'message_id':6,'chat':{'id':b.scope.chat_id,'type':'supergroup'},'text':'private other-room text'}}}
    await a.ingest(update_id=2,message_id=11,sender_id=42,sender_name='test',text='cross-chat quote',
        timestamp=datetime.now(timezone.utc),reply_to_id=6,payload=other)
    latest=next(row for row in await a.recent() if row['platform_message_id']==11)
    assert await a.reply_context([str(latest['id'])])==[]

@pytest.mark.asyncio
async def test_maintenance_chunk_restart_and_evidence_edit(stores):
    from brodar.learning import LearningStore
    a,b=stores
    store=LearningStore(a.db,a.scope)
    await add(a,'x'*65000)
    first=await store.maintenance_prepare()
    assert len(first['messages'][0]['content'])==30000 and first['offset']==30000
    await store.maintenance_finish(first)
    second=await store.maintenance_prepare()
    assert second['offset']==60000
    # Crash without checkpoint: a fresh worker replays the exact pending chunk.
    replay=await LearningStore(a.db,a.scope).maintenance_prepare()
    assert replay['messages']==second['messages']
    await store.maintenance_finish(replay)
    last=await store.maintenance_prepare()
    assert len(last['messages'][0]['content'])==5000 and last['offset']==0
    def edit():
        with a.db.transaction(a.scope.audience) as c:
            c.execute("UPDATE brodar_v2.messages SET changed_at=now() WHERE audience=%s",(a.scope.audience,))
    await a.db.run(edit)
    with pytest.raises(RevisionConflict):await store.maintenance_finish(last)
    other=LearningStore(b.db,b.scope)
    assert await other.maintenance_prepare() is None

@pytest.mark.asyncio
async def test_review_flag_requires_source_read_evidence(stores):
    a,_=stores
    await add(a,'prefers explanations in uzbek')
    mid=str((await a.recent())[0]['id'])
    await a.memory_write('context/person.md','uzbek',expected_revision=0,reason='feedback',source_ids=[mid])
    def flag():
        with a.db.transaction(a.scope.audience) as c:
            c.execute('UPDATE brodar_v2.memory_documents SET needs_review=true WHERE audience=%s',(a.scope.audience,))
    await a.db.run(flag)
    await a.memory_write('context/person.md','changed guess',expected_revision=1,reason='unchecked')
    assert (await a.memory_read('context/person.md'))['needs_review']
    await a.memory_write('context/person.md','uzbek',expected_revision=2,reason='checked original',reviewed_sources={mid:__import__('hashlib').sha256(b'prefers explanations in uzbek').hexdigest()})
    assert not (await a.memory_read('context/person.md'))['needs_review']

@pytest.mark.asyncio
async def test_batched_index_and_exact_owner_approval(stores):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from brodar.learning import LearningStore
    from brodar.approvals import Approvals
    a,b=stores
    await add(a,'batch indexing evidence')
    row=(await a.recent())[0]
    learning=LearningStore(a.db,a.scope)
    vector=[1.0]+[0.0]*767
    assert await learning.index_documents([dict(kind='message',id=str(row['id']),content=row['content'],vectors=[vector])],'test-batch')==[str(row['id'])]
    assert await learning.index_documents([dict(kind='message',id=str(row['id']),content='stale edit',vectors=[vector])],'test-batch')==[]
    cfg=SimpleNamespace(owner_ids={42},allowed_chats={-11,-22},instance_id=a.scope.instance_id)
    approvals=Approvals(a.db,SimpleNamespace(send_message=AsyncMock()),cfg)
    artifact_id=str(uuid4());content_hash='test-hash'
    request=await approvals.request(a.scope,str(uuid4()),artifact_id,content_hash,'synthetic test')
    uid=request['approval_id']
    assert await approvals.decide(42,42,'/approve '+uid,a.scope.bot_id,private=True)
    with pytest.raises(PermissionError):await approvals.consume(b.scope,uid,artifact_id,content_hash)
    await approvals.consume(a.scope,uid,artifact_id,content_hash)
    with pytest.raises(PermissionError):await approvals.consume(a.scope,uid,artifact_id,content_hash)
    def cleanup():
        with a.db.transaction(a.scope.audience) as c:c.execute('DELETE FROM brodar_v2.approvals WHERE audience=%s',(a.scope.audience,))
    await a.db.run(cleanup)

@pytest.mark.asyncio
async def test_group_admission_persists_across_policy_restart(stores):
    from types import SimpleNamespace
    from brodar.access import ChatAccess
    a,b=stores
    cfg=SimpleNamespace(allowed_chats={-11},owner_ids={42,73},instance_id=a.scope.instance_id)
    access=ChatAccess(cfg,a.db)
    try:
        await access.load()
        raw={'message':{'chat':{'id':-22,'type':'supergroup'},'from':{'id':42},'text':'/approve'}}
        await access.approve(raw,'BrodarBot')
        restored=ChatAccess(cfg,a.db);await restored.load()
        assert restored.accepts({'chat':{'id':-22,'type':'supergroup'},'from':{'id':999}})
        assert not restored.accepts({'chat':{'id':999,'type':'private'},'from':{'id':999}})
        with a.db.transaction(access.audience,readonly=True) as c:
            c.execute('SELECT approved_by FROM brodar_v2.group_access WHERE audience=%s AND chat_id=-22',(access.audience,))
            assert c.fetchone()[0]==42
    finally:
        def cleanup():
            with a.db.transaction(access.audience) as c:
                c.execute('DELETE FROM brodar_v2.group_access WHERE audience=%s',(access.audience,))
                c.execute('DELETE FROM brodar_v2.audience_registry WHERE audience=%s',(access.audience,))
        await a.db.run(cleanup)

@pytest.mark.asyncio
async def test_global_peer_budget_survives_restart_and_group_change(stores):
    from brodar.coordination import LoopGuard
    a,b=stores
    guard=LoopGuard(a.db,a.scope.instance_id)
    await guard.register()
    try:
        original=await guard.resolve({'id':123,'username':'hyperresearcher_bot'},'hyperresearcher_bot')
        renamed=await LoopGuard(a.db,a.scope.instance_id).resolve({'id':123,'username':'renamed_bot'},'hyperresearcher_bot')
        assert original==renamed
        assert await guard.incoming(99,-11,'first bot question')
        for i in range(10):await guard.reserve(99,-11,'operation-'+str(i))
        await guard.reserve(99,-22,'operation-0')  # idempotent replay doesn't buy another send
        restored=LoopGuard(a.db,a.scope.instance_id)
        await restored.register()
        assert not await restored.available(99)
        assert not await restored.incoming(99,-22,'try a different group')
        with pytest.raises(PermissionError):await restored.reserve(99,-22,'eleventh')
        await restored.human(-22)
        assert not await restored.available(99)
        await restored.human(-11)
        assert await restored.available(99)
        await restored.close(99)
        assert not await restored.available(99)
    finally:
        def clean():
            with a.db.transaction(guard.audience) as c:
                for table in ['bot_reservations','bot_exchanges','bot_aliases','research_tasks']:
                    c.execute(f'DELETE FROM brodar_v2.{table} WHERE audience=%s',(guard.audience,))
                c.execute('DELETE FROM brodar_v2.audience_registry WHERE audience=%s',(guard.audience,))
        await a.db.run(clean)
