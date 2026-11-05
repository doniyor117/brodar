from types import SimpleNamespace
from uuid import uuid4

import pytest

from brodar.broker import ToolBroker,TurnCapability
from brodar.jobs import InputRequest,input_requests


@pytest.mark.asyncio
async def test_reminder_does_not_borrow_last_speakers_identity(monkeypatch):
    owner=InputRequest(str(uuid4()),1,'hello')
    member=InputRequest(str(uuid4()),2,'remind me tomorrow')
    seen=[]
    async def create(self,request,when,text,operation_id):seen.append(request.actor);return {'saved':True}
    monkeypatch.setattr('brodar.jobs.Reminders.create',create)
    broker=ToolBroker(SimpleNamespace(),SimpleNamespace(),owner_ids={1})
    broker.begin(TurnCapability(str(uuid4()),requester_id=1),requests=[member,owner])
    with pytest.raises(PermissionError,match='explicitly ask'):
        await broker.reminder_create(owner.id,'2027-01-01T00:00:00Z','test','one')
    await broker.reminder_create(member.id,'2027-01-01T00:00:00Z','test','one')
    assert seen==[2]
    broker.end()
    with pytest.raises(PermissionError):await broker.reminder_create(member.id,'2027-01-01T00:00:00Z','test','one')


def test_forwarded_and_anonymous_inputs_cannot_create_fresh_task_requests():
    def row(**kwargs):return {'message_id':str(uuid4()),'payload':{'message':{
        'text':'remind me','from':{'id':1},**kwargs}}}
    assert input_requests([row(forward_origin={'type':'user'}),row(sender_chat={'id':-100})])==[]


@pytest.mark.asyncio
async def test_unrelated_owner_greeting_cannot_authorize_reminder_cancellation():
    owner=InputRequest(str(uuid4()),1,'hello')
    broker=ToolBroker(SimpleNamespace(),SimpleNamespace(),owner_ids={1})
    broker.begin(TurnCapability(str(uuid4()),requester_id=1),requests=[owner])
    with pytest.raises(PermissionError,match='ask to cancel'):
        await broker.reminder_cancel(owner.id,str(uuid4()))


@pytest.mark.asyncio
async def test_scheduler_recovery_reuses_confirmed_delivery_and_does_not_duplicate(monkeypatch):
    from brodar.jobs import dispatch_reminder
    from telegram.error import NetworkError
    class Store:
        scope=SimpleNamespace(chat_id=-100,topic_id=12)
        def __init__(self):self.receipt=None;self.finished=[]
        async def delivery_begin(self,turn,call,method,request):
            if self.receipt is None:self.receipt={'id':'receipt','status':'pending','request':request,'method':method}
            return dict(self.receipt)
        async def delivery_finish(self,uid,status,response):self.receipt.update(status=status,response=response)
        async def delivery_receipts(self,uid):return [self.receipt] if self.receipt else []
    async def active(self,uid):pass
    async def finish(self,uid,status):self.store.finished.append(status)
    monkeypatch.setattr('brodar.jobs.Reminders.active',active)
    monkeypatch.setattr('brodar.jobs.Reminders.finish',finish)
    async def lease():pass
    calls=[]
    async def send(**kwargs):calls.append(kwargs);return SimpleNamespace(message_id=55)
    store=Store();job={'id':str(uuid4()),'payload':{'text':'reminder','requester_id':42}}
    assert await dispatch_reminder(job,store,SimpleNamespace(send_message=send),lease)=='done'
    assert await dispatch_reminder(job,store,SimpleNamespace(send_message=send),lease)=='done'
    assert len(calls)==1 and calls[0]['message_thread_id']==12
    async def uncertain(**kwargs):calls.append(kwargs);raise NetworkError('response lost')
    store=Store()
    assert await dispatch_reminder(job,store,SimpleNamespace(send_message=uncertain),lease)=='uncertain'
    assert await dispatch_reminder(job,store,SimpleNamespace(send_message=uncertain),lease)=='uncertain'
    assert len(calls)==2


@pytest.mark.asyncio
@pytest.mark.parametrize('withdraw',['turn','request'])
async def test_queued_reminder_write_is_fenced_before_database_access(monkeypatch,withdraw):
    import asyncio
    from brodar.postgres import Postgres
    from brodar.scope import Scope
    entered=asyncio.Event();release=asyncio.Event();db=Postgres('not-configured')
    original=db.run
    async def queued(op):entered.set();await release.wait();return await original(op)
    def forbidden_connection():raise AssertionError('Revoked write reached database connection')
    monkeypatch.setattr(db,'run',queued);monkeypatch.setattr(db,'_connect',forbidden_connection)
    request=InputRequest(str(uuid4()),42,'remind me')
    store=SimpleNamespace(db=db,scope=Scope(99,-100),lease_owner=None)
    broker=ToolBroker(store,SimpleNamespace());broker.begin(TurnCapability(str(uuid4())),requests=[request])
    task=asyncio.create_task(broker.reminder_create(request.id,'2030-01-01T00:00:00Z','test','one'))
    await entered.wait()
    if withdraw=='turn':broker.end()
    else:broker.revoke_request_ids([request.id])
    release.set()
    with pytest.raises(PermissionError):await task
