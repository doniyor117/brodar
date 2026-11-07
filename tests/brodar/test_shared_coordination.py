import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from brodar.shared import GroupSession,BrokerProxy,GroupBrain
from brodar.brains.base import Outcome
from brodar.scope import Scope
from brodar.broker import ToolBroker,TurnCapability
from brodar.group_admin import command_parameters
from brodar.routing import GroupRouter,peer_key

@pytest.mark.asyncio
async def test_shared_turns_serialize_and_switch_exact_broker():
    entered=asyncio.Event();release=asyncio.Event();seen=[]
    first=SimpleNamespace(broker=SimpleNamespace(capability='one'),store=SimpleNamespace(scope=Scope(1,-11)),cfg=SimpleNamespace(brain='codex'))
    second=SimpleNamespace(broker=SimpleNamespace(capability='two'),store=SimpleNamespace(scope=Scope(1,-22)),cfg=first.cfg)
    proxy=BrokerProxy(first.broker)
    async def turn(text):
        seen.append((proxy.capability,text));entered.set()
        if len(seen)==1:await release.wait()
        return Outcome('completed','skip','done')
    resource=SimpleNamespace(broker=proxy,brain=SimpleNamespace(turn=turn,session_id='global'),revision='rev',
        store=SimpleNamespace(session_put=AsyncMock()),start=AsyncMock(),close=AsyncMock())
    manager=GroupSession(AsyncMock(return_value=resource))
    a=await manager.bind(first);b=await manager.bind(second)
    t1=asyncio.create_task(a.turn('first'))
    await entered.wait();t2=asyncio.create_task(b.turn('second'));await asyncio.sleep(0)
    assert len(seen)==1
    assert not await b.steer('cannot steer another room')
    release.set();await asyncio.gather(t1,t2)
    assert [x[0] for x in seen]==['one','two']
    assert '-11' in seen[0][1] and '-22' in seen[1][1]
    assert manager.active is None and proxy.current is None
    resource.start.assert_awaited_once()

@pytest.mark.asyncio
async def test_revoked_old_broker_does_not_inherit_next_group_capability():
    a=ToolBroker(SimpleNamespace(),None);b=ToolBroker(SimpleNamespace(),None)
    cap=TurnCapability(str(uuid4()));a.begin(cap)
    proxy=BrokerProxy(a);proxy.current=a
    bound=proxy.require
    a.end();b.begin(TurnCapability(str(uuid4())));proxy.current=b
    with pytest.raises(PermissionError):bound()
    assert proxy.require() is b.capability

def test_researcher_peer_key_follows_bot_across_groups():
    assert peer_key({'id':123,'username':'HyperResearcher_bot'},'hyperresearcher_bot')==peer_key({'id':123,'username':'hyperresearcher_bot'},'hyperresearcher_bot')
    assert peer_key({'id':456,'username':'other_bot'},'hyperresearcher_bot')==peer_key({'username':'other_bot'},'hyperresearcher_bot')

def test_admin_action_parameters_bound_to_exact_human_command():
    command_parameters('/pin 123','pin',{'message_id':123})
    command_parameters('/set_title hello','set_title',{'title':'hello'})
    with pytest.raises(PermissionError):command_parameters('/pin 123','pin',{'message_id':999})
    with pytest.raises(PermissionError):command_parameters('do not /pin 123','pin',{'message_id':123})
    with pytest.raises(PermissionError):command_parameters('/pin@OtherBot 123','pin',{'message_id':123},'BrodarBot')

def test_router_rejects_unapproved_and_private_destinations():
    router=GroupRouter(SimpleNamespace(instance_id='test'),SimpleNamespace(groups={-11}),None,None,None,lambda:SimpleNamespace(bot_id=1,owner='owner'))
    for chat in [-22,42]:
        with pytest.raises(PermissionError):router.target(chat)

@pytest.mark.asyncio
async def test_silence_token_never_sent_through_telegram():
    bot=SimpleNamespace(send_message=AsyncMock())
    broker=ToolBroker(SimpleNamespace(),bot);broker.begin(TurnCapability(str(uuid4())))
    with pytest.raises(ValueError):await broker.send_message('[SILENT]',call_id='x')
    bot.send_message.assert_not_awaited()

@pytest.mark.asyncio
async def test_ambient_signal_does_not_steer_each_message(tmp_path):
    from brodar.runtime import Conversation
    c=Conversation(SimpleNamespace(runtime_dir=tmp_path,brain='codex'),SimpleNamespace(scope=Scope(1,-11)),None,None,None)
    c._steer_pending=AsyncMock()
    for _ in range(20):await c.signal(direct=False)
    await asyncio.sleep(0)
    c._steer_pending.assert_not_awaited()
    assert c.wake.is_set() and c._ambient_first is not None
    await c.signal(direct=True)
    await c._signal_task
    c._steer_pending.assert_awaited_once()

@pytest.mark.asyncio
async def test_delivery_guard_stops_eleventh_bot_action_before_receipt_or_network():
    store=SimpleNamespace(scope=Scope(1,-11),delivery_begin=AsyncMock())
    bot=SimpleNamespace(send_message=AsyncMock())
    broker=ToolBroker(store,bot)
    broker.loop_guard=SimpleNamespace(reserve=AsyncMock(side_effect=PermissionError('limit')))
    broker.begin(TurnCapability(str(uuid4()),peer_bot_id=99))
    with pytest.raises(PermissionError):await broker.send_message('one too many',call_id='eleventh')
    store.delivery_begin.assert_not_awaited();bot.send_message.assert_not_awaited()

@pytest.mark.asyncio
async def test_shared_queue_rechecks_loop_budget_before_inference():
    cap=TurnCapability(str(uuid4()),peer_bot_id=99)
    conversation=SimpleNamespace(broker=SimpleNamespace(capability=cap,loop_guard=SimpleNamespace(available=AsyncMock(return_value=False))))
    brain=SimpleNamespace(session_id='global',turn=AsyncMock())
    manager=SimpleNamespace(lock=asyncio.Lock(),resource=SimpleNamespace(brain=brain))
    result=await GroupBrain(manager,conversation).turn('queued bot input')
    assert result.action=='skip'
    brain.turn.assert_not_awaited()
