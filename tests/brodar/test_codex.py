import asyncio

import pytest

from brodar.brains.codex import CodexBrain
from brodar.brains.rpc import RpcError


class FakeRpc:
    def __init__(self,argv,**kwargs):
        self.notify_cb=kwargs['on_notification']
        self.requests=[]
        self.closed=False
        self.steer_error=False

    async def start(self): pass
    async def notify(self,*args): pass
    async def close(self): self.closed=True

    async def request(self,method,params):
        self.requests.append((method,params))
        if method=='initialize':return {}
        if method in {'thread/start','thread/resume'}:
            return {'thread':{'id':'thread'},'model':'model','instructionSources':[]}
        if method=='turn/start':
            # Completion can race ahead of the request response.
            self.notify_cb('item/completed',{'threadId':'thread','turnId':'turn',
                'item':{'id':'answer','type':'agentMessage','text':'{"action":"skip","reason":"chatter"}'}})
            self.notify_cb('turn/completed',{'threadId':'thread','turn':{'id':'turn','status':'completed'}})
            return {'turn':{'id':'turn','status':'inProgress'}}
        if method=='turn/steer' and self.steer_error:raise RpcError(-32000)
        return {}


@pytest.mark.asyncio
async def test_completion_race_and_environment_disabled_on_start_and_turn(tmp_path):
    b=CodexBrain(home=tmp_path/'home',cwd=tmp_path/'work',model='model',effort='low',
                 instructions='fixed',transport_factory=FakeRpc)
    await b.start()
    result=await b.turn('message')
    assert result.status=='completed' and result.action=='skip'
    start=next(p for m,p in b.rpc.requests if m=='thread/start')
    turn=next(p for m,p in b.rpc.requests if m=='turn/start')
    assert start['environments']==turn['environments']==[]
    assert start['approvalPolicy']=='never'
    assert start['allowProviderModelFallback'] is False
    assert turn['outputSchema']['properties']['action']['enum']==['stop','skip','heartbeat']
    await b.close()
    assert b.rpc.closed


@pytest.mark.asyncio
async def test_failed_steer_keeps_input_pending_and_binds_expected_turn(tmp_path):
    b=CodexBrain(home=tmp_path/'home',cwd=tmp_path/'work',model='model',effort='low',
                 instructions='fixed',transport_factory=FakeRpc)
    await b.start()
    assert await b.steer('next') is False
    b.active_turn='expected'
    b.rpc.steer_error=True
    assert await b.steer('next') is False
    method,params=b.rpc.requests[-1]
    assert method=='turn/steer' and params['expectedTurnId']=='expected'
    await b.close()


def test_failed_completion_is_not_success(tmp_path):
    b=CodexBrain(home=tmp_path/'home',cwd=tmp_path/'work',model='model',effort='low',
                 instructions='fixed',transport_factory=FakeRpc)
    outcome=b._outcome({'id':'turn','status':'failed','error':{'codexErrorInfo':'unauthorized'}})
    assert outcome.status=='failed' and outcome.error=='unauthorized'


@pytest.mark.asyncio
async def test_permissions_requests_are_declined(tmp_path):
    b=CodexBrain(home=tmp_path/'home',cwd=tmp_path/'work',model='model',effort='low',
                 instructions='fixed',transport_factory=FakeRpc)
    assert await b._server_request('item/commandExecution/requestApproval',{})=={'decision':'decline'}
    assert await b._server_request('item/fileChange/requestApproval',{})=={'decision':'decline'}


@pytest.mark.asyncio
async def test_missing_local_session_starts_new_thread_for_canonical_restore(tmp_path):
    class MissingSession(FakeRpc):
        async def request(self,method,params):
            if method=='thread/resume':
                self.requests.append((method,params))
                raise RpcError(-32000,'no rollout found for thread')
            return await super().request(method,params)
    b=CodexBrain(home=tmp_path/'home',cwd=tmp_path/'work',model='model',effort='low',
        instructions='fixed',transport_factory=MissingSession)
    b._transport_closed=True
    await b.start('missing-thread')
    assert b._transport_closed is False
    assert any(m=='thread/resume' for m,p in b.rpc.requests)
    start=next(p for m,p in b.rpc.requests if m=='thread/start')
    assert start['environments']==[] and start['allowProviderModelFallback'] is False
    assert b.session_id=='thread'
    await b.close()


@pytest.mark.asyncio
async def test_timeout_cancellation_interrupts_before_turn_id_is_cleared(tmp_path):
    class Pending(FakeRpc):
        async def request(self,method,params):
            if method=='turn/start':
                self.requests.append((method,params))
                return {'turn':{'id':'still-running','status':'inProgress'}}
            return await super().request(method,params)
    brain=CodexBrain(home=tmp_path/'home',cwd=tmp_path/'work',model='model',effort='low',
        instructions='fixed',transport_factory=Pending)
    await brain.start()
    task=asyncio.create_task(brain.turn('work'))
    for _ in range(20):
        if brain.active_turn:break
        await asyncio.sleep(0)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):await task
    assert ('turn/interrupt',{'threadId':'thread','turnId':'still-running'}) in brain.rpc.requests
    assert brain.active_turn is None
    await brain.close()
