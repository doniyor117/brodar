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
