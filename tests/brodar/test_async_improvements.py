import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
import httpx
import pytest
from brodar.embeddings import GeminiEmbeddings,EmbeddingError
from brodar.presence import TypingPresence
from brodar.reflection import Maintenance
from brodar.scope import Scope
from brodar.skills import SkillLibrary
from brodar.approvals import Approvals

@pytest.mark.asyncio
async def test_old_typing_generation_cannot_stop_new_arrival():
    presence=TypingPresence(SimpleNamespace(send_chat_action=AsyncMock()))
    scope=Scope(1,-100)
    presence.start(scope,new_work=True);old=presence.generation(scope)
    presence.start(scope,new_work=True)
    await presence.stop(scope,generation=old)
    assert scope.conversation in presence.tasks
    await presence.stop(scope,generation=presence.generation(scope))
    assert not presence.tasks

@pytest.mark.asyncio
async def test_idle_window_and_index_preemption():
    cfg=SimpleNamespace(maintenance_idle_seconds=300)
    service=Maintenance(cfg,AsyncMock(),asyncio.Semaphore(1),lambda:False)
    assert not service.idle()
    service.last_activity-=301
    assert service.idle()
    task=asyncio.create_task(asyncio.sleep(100));service.index_task=task
    service.preempt()
    assert not service.idle()
    await asyncio.gather(task,return_exceptions=True)
    assert task.cancelled()

@pytest.mark.asyncio
async def test_batch_vectors_are_independent_and_count_validated():
    calls=[]
    def respond(req):
        import json
        payload=json.loads(req.content);calls.append(payload)
        return httpx.Response(200,json={'embeddings':[{'values':[1.0]+[0.0]*767} for _ in payload['requests']]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(respond)) as client:
        api=GeminiEmbeddings('secret',client=client)
        rows=await api.embed_batch(['one','two'])
    assert len(rows)==2 and len(calls)==1 and len(calls[0]['requests'])==2
    assert 'one' in calls[0]['requests'][0]['content']['parts'][0]['text']

@pytest.mark.asyncio
async def test_skill_resources_cannot_escape_package(tmp_path):
    root=tmp_path/'skills';skill=root/'test';skill.mkdir(parents=True)
    (skill/'SKILL.md').write_text('---\ndescription: testing\n---\nUseful instructions')
    outside=tmp_path/'secret.md';outside.write_text('secret')
    (skill/'escape.md').symlink_to(outside)
    library=SkillLibrary([root]);rows=await library.list('test');uid=rows['skills'][0]['id']
    assert 'Useful' in (await library.read(uid))['text']
    for path in ['../secret.md','escape.md','/etc/passwd']:
        with pytest.raises(ValueError):await library.read(uid,path)

@pytest.mark.asyncio
async def test_approval_needs_trusted_owner_dm_before_any_sql():
    db=SimpleNamespace(run=AsyncMock())
    service=Approvals(db,None,SimpleNamespace(owner_ids={42},allowed_chats={-100}))
    for chat,sender,private,anonymous in [(-100,42,False,False),(42,9,True,False),(42,42,True,True)]:
        assert not await service.decide(chat,sender,'/approve 123',1,private=private,anonymous=anonymous)
    db.run.assert_not_awaited()

@pytest.mark.asyncio
async def test_slow_audit_does_not_block_tool_result():
    from uuid import uuid4
    from brodar.broker import ToolBroker,TurnCapability
    from brodar.mcp import AuditedMcp
    release=asyncio.Event();entered=asyncio.Event()
    async def audit(**kwargs):entered.set();await release.wait()
    broker=ToolBroker(SimpleNamespace(audit_tool=audit),None)
    cap=TurnCapability(str(uuid4()));broker.begin(cap)
    mcp=AuditedMcp(broker)
    async def fast()->str:return 'finished'
    mcp.add_tool(fast);mcp.start_audit()
    try:
        await asyncio.wait_for(mcp.call_tool('fast',{'turn_token':cap.token}),1)
        await asyncio.wait_for(entered.wait(),1)
        assert not release.is_set()
    finally:
        release.set();await mcp.close_audit()

@pytest.mark.parametrize('argv',[
 ['rm','-rf','.'],['bash','-c','cat secret'],['python3.14','-c','print(1)'],
 ['cat','../../secrets/.env'],['cat','credentials.json'],['cat','/proc/self/environ'],
 ['sort','--compress-program=python'],['file','--files-from=input.txt'],['cat','a;pwd']])
def test_commands_deny_dangerous_credentials_and_escape(tmp_path,argv):
    from brodar.commands import CommandGateway
    with pytest.raises(PermissionError):CommandGateway(tmp_path).classify(argv)

@pytest.mark.asyncio
async def test_commands_are_argv_only_and_mutations_need_approval(tmp_path):
    from brodar.commands import CommandGateway
    root=tmp_path/'work';root.mkdir();(root/'hello.txt').write_text('hello')
    gateway=CommandGateway(root)
    assert (await gateway.run(['cat','hello.txt']))['output']=='hello'
    await gateway.run(['touch','new.txt'])
    assert (root/'new.txt').exists()
    with pytest.raises(PermissionError):await gateway.run(['cp','hello.txt','new.txt'])
    assert gateway.classify(['cat','sensitive/report.txt'])[1]
    (root/'alias').symlink_to(root/'hello.txt')
    with pytest.raises(PermissionError):gateway.classify(['cat','alias'])
