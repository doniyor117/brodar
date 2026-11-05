import asyncio
from types import SimpleNamespace
from uuid import uuid4

import pytest
from telegram.error import NetworkError

from brodar.broker import ToolBroker,TurnCapability


class Store:
    def __init__(self):
        self.scope=SimpleNamespace(chat_id=-100,topic_id=12)
        self.receipt=None
        self.entered=asyncio.Event()
        self.release=asyncio.Event()
        self.release.set()

    async def delivery_begin(self,turn_id,call_id,method,request):
        self.entered.set()
        await self.release.wait()
        if self.receipt is None:
            self.receipt={'id':'receipt','request':request,'method':method,'status':'pending'}
        return dict(self.receipt)

    async def delivery_finish(self,uid,status,response):
        self.receipt.update(status=status,response=response)

    async def memory_write(self,path,content,**kwargs):
        return {'path':path,'revision':kwargs['expected_revision']+1}


class Bot:
    def __init__(self,error=None):self.calls=[];self.error=error
    async def send_message(self,**kwargs):
        self.calls.append(kwargs)
        if self.error:raise self.error
        return SimpleNamespace(message_id=55)


@pytest.mark.asyncio
async def test_cancel_during_receipt_creation_prevents_send():
    store,bot=Store(),Bot()
    store.release.clear()
    broker=ToolBroker(store,bot)
    broker.begin(TurnCapability(str(uuid4())))
    task=asyncio.create_task(broker.send_message('stale answer',call_id='one'))
    await store.entered.wait()
    broker.end()
    store.release.set()
    with pytest.raises(PermissionError,match='cancelled'):await task
    assert bot.calls==[]
    assert store.receipt['status']=='failed'


@pytest.mark.asyncio
async def test_delivery_is_scoped_and_success_is_not_replayed():
    store,bot=Store(),Bot()
    broker=ToolBroker(store,bot);broker.begin(TurnCapability(str(uuid4())))
    assert (await broker.send_message('hello',call_id='stable'))['delivered']
    await broker.send_message('hello',call_id='stable')
    assert len(bot.calls)==1
    assert bot.calls[0]=={'chat_id':-100,'message_thread_id':12,'text':'hello'}
    with pytest.raises(ValueError,match='different delivery'):
        await broker.send_message('different',call_id='stable')


@pytest.mark.asyncio
async def test_uncertain_send_is_not_automatically_replayed():
    store,bot=Store(),Bot(NetworkError('test network timeout'))
    broker=ToolBroker(store,bot);broker.begin(TurnCapability(str(uuid4())))
    with pytest.raises(RuntimeError,match='uncertain'):
        await broker.send_message('hello',call_id='stable')
    assert store.receipt['status']=='uncertain'
    with pytest.raises(RuntimeError,match='uncertain'):
        await broker.send_message('hello',call_id='stable')
    assert len(bot.calls)==1


@pytest.mark.asyncio
async def test_reflection_can_learn_without_owner_but_cannot_publish():
    broker=ToolBroker(Store(),Bot())
    broker.begin(TurnCapability(str(uuid4()),mode='reflection'))
    note=await broker.memory_write('context/tone.md','be concise',0,'feedback')
    assert note['revision']==1
    with pytest.raises(PermissionError,match='Reflection'):
        await broker.send_message('should not post',call_id='denied')
    broker.end()
    with pytest.raises(PermissionError,match='No active turn'):
        await broker.memory_write('context/new.md','late write',0,'stale')


@pytest.mark.asyncio
async def test_expired_consumer_cannot_deliver_after_receipt_creation():
    async def expired():raise PermissionError('lease expired')
    store,bot=Store(),Bot();broker=ToolBroker(store,bot,delivery_guard=expired)
    broker.begin(TurnCapability(str(uuid4())))
    with pytest.raises(PermissionError,match='lease expired'):
        await broker.send_message('stale',call_id='stale')
    assert not bot.calls and store.receipt['status']=='failed'


@pytest.mark.asyncio
async def test_reaction_boolean_receipt_is_idempotent_and_topic_scoped():
    store=Store();calls=[]
    async def target(message_id,**kwargs):assert message_id==5
    async def react(**kwargs):calls.append(kwargs);return True
    broker=ToolBroker(store,SimpleNamespace(set_message_reaction=react))
    broker.message_target=target;broker.begin(TurnCapability(str(uuid4())))
    assert (await broker.react(5,'💯','reaction'))['delivered']
    await broker.react(5,'💯','reaction')
    assert calls==[{'chat_id':-100,'message_id':5,'reaction':'💯'}]
    assert store.receipt['status']=='sent'


@pytest.mark.asyncio
async def test_poll_preserves_current_topic():
    calls=[]
    async def poll(**kwargs):calls.append(kwargs);return SimpleNamespace(message_id=56)
    broker=ToolBroker(Store(),SimpleNamespace(send_poll=poll));broker.begin(TurnCapability(str(uuid4())))
    await broker.poll('Choose',['A','B'],'poll')
    assert calls[0]['chat_id']==-100 and calls[0]['message_thread_id']==12
    assert calls[0]['is_anonymous'] is True


@pytest.mark.asyncio
async def test_edit_rejects_another_members_message_before_telegram_call():
    from contextlib import contextmanager
    class Database:
        async def run(self,op):return op()
        @contextmanager
        def transaction(self,audience,**kwargs):
            assert audience=='telegram:brodar:-100'
            yield self
        def execute(self,sql,args):assert args==('telegram:brodar:-100',12,5)
        def fetchone(self):return (3,'user')
    store=Store();store.db=Database();store.scope.audience='telegram:brodar:-100';store.scope.bot_id=99
    broker=ToolBroker(store,SimpleNamespace());broker.begin(TurnCapability(str(uuid4())))
    with pytest.raises(PermissionError,match='own messages'):
        await broker.edit_message(5,'change someone else','edit')
    assert store.receipt is None
