import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from brodar.broker import ToolBroker,TurnCapability
from brodar.presence import TypingPresence
from brodar.runtime import Conversation
from brodar.scope import Scope

@pytest.mark.asyncio
async def test_final_stops_renewal_before_send_even_when_receipt_save_blocks():
    scope=Scope(1,-11)
    presence=TypingPresence(SimpleNamespace(send_chat_action=AsyncMock()))
    presence.start(scope,new_work=True,update_id=1)
    generation=presence.for_inputs(scope,[1])
    saving=asyncio.Event();release=asyncio.Event()
    async def begin(turn,call,method,request):return {'id':'r','method':method,'request':request,'status':'pending'}
    async def finish(*args):saving.set();await release.wait()
    async def send(**kwargs):
        assert scope.conversation not in presence.tasks
        return SimpleNamespace(message_id=123)
    broker=ToolBroker(SimpleNamespace(scope=scope,delivery_begin=begin,delivery_finish=finish),SimpleNamespace(send_message=send))
    broker.begin(TurnCapability(str(uuid4())))
    async def stop(cap):await presence.stop(scope,generation=generation)
    broker.before_final_delivery=stop
    task=asyncio.create_task(broker.send_message('finished',call_id='final'))
    await asyncio.wait_for(saving.wait(),1)
    assert not task.done() and scope.conversation not in presence.tasks
    release.set();await task

@pytest.mark.asyncio
async def test_progress_delivery_keeps_typing():
    async def begin(turn,call,method,request):return {'id':'r','method':method,'request':request,'status':'pending'}
    broker=ToolBroker(SimpleNamespace(scope=Scope(1,-11),delivery_begin=begin,delivery_finish=AsyncMock()),SimpleNamespace(send_message=AsyncMock(return_value=SimpleNamespace(message_id=1))))
    broker.begin(TurnCapability(str(uuid4())));broker.before_final_delivery=AsyncMock()
    await broker.send_message('still making the slides',call_id='progress',final=False)
    broker.before_final_delivery.assert_not_awaited()

@pytest.mark.asyncio
async def test_ambient_wake_does_not_keep_completed_typing_alive(tmp_path):
    scope=Scope(1,-11);presence=TypingPresence(SimpleNamespace(send_chat_action=AsyncMock()))
    presence.start(scope,new_work=True,update_id=10)
    c=Conversation(SimpleNamespace(runtime_dir=tmp_path,brain='codex'),SimpleNamespace(scope=scope),None,None,None,presence=presence)
    async def process():
        c._presence_generation=presence.for_inputs(scope,[10]);c.wake.set()
    c._process=process
    await c._process_guarded()
    assert not presence.tasks and c.wake.is_set()

@pytest.mark.asyncio
async def test_older_completion_preserves_new_unclaimed_request(tmp_path):
    scope=Scope(1,-11);presence=TypingPresence(SimpleNamespace(send_chat_action=AsyncMock()))
    presence.start(scope,new_work=True,update_id=10)
    c=Conversation(SimpleNamespace(runtime_dir=tmp_path,brain='codex'),SimpleNamespace(scope=scope),None,None,None,presence=presence)
    async def process():
        c._presence_generation=presence.for_inputs(scope,[10])
        presence.start(scope,new_work=True,update_id=11)
    c._process=process
    await c._process_guarded()
    assert scope.conversation in presence.tasks
    await presence.close()

@pytest.mark.asyncio
async def test_steering_adopts_new_typing_generation_before_bookkeeping(tmp_path):
    scope=Scope(1,-11);presence=TypingPresence(SimpleNamespace(send_chat_action=AsyncMock()))
    presence.start(scope,new_work=True,update_id=1)
    presence.start(scope,new_work=True,update_id=2)
    row={'id':str(uuid4()),'message_id':str(uuid4()),'update_id':2,'attempts':1,
        'payload':{'message':{'text':'brodar next question','from':{'id':42}}}}
    async def marked(ids):
        assert c._presence_generation==2
        return [row]
    store=SimpleNamespace(scope=scope,claim_inputs=AsyncMock(return_value=[row]),history_get=AsyncMock(return_value=[]),
        mark_inputs_attempted=marked,work_add_inputs=AsyncMock())
    c=Conversation(SimpleNamespace(runtime_dir=tmp_path,brain='codex'),store,None,None,None,presence=presence)
    c._presence_generation=1;c._accept_steer=True;c.brain=SimpleNamespace(steer=AsyncMock(return_value=True))
    cap=TurnCapability(str(uuid4()));c.broker.begin(cap)
    await c._steer_pending();await c._stop_typing(cap)
    assert not presence.tasks
