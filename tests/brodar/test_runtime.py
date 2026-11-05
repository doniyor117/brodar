from types import SimpleNamespace
from uuid import uuid4

import pytest

from brodar.brains.base import Outcome
from brodar.runtime import Conversation
from brodar.scope import Scope


class Store:
    scope=Scope(1,-100)
    def __init__(self):
        self.row={'id':str(uuid4()),'message_id':str(uuid4()),'update_id':1,'attempts':1,
            'payload':{'message':{'from':{'id':42},'text':'help me'}}}
        self.finished=[]
    async def claim_inputs(self):return [self.row]
    async def history_get(self,ids):return [{'id':self.row['message_id'],'sender_id':42,'content':'help me'}]
    async def recent(self,*args):return []
    async def context_notes(self,*args):return []
    async def delivery_receipts(self,turn_id):return []
    async def finish_inputs(self,ids,**kwargs):self.finished.append(kwargs)
    async def session_put(self,*args):pass


class Brain:
    session_id='session'
    def __init__(self,outcome):self.outcome=outcome;self.calls=0;self.closed=False;self.started=[]
    async def turn(self,text):self.calls+=1;return self.outcome
    async def close(self):self.closed=True
    async def start(self,session_id=None):self.started.append(session_id)


@pytest.mark.asyncio
async def test_stop_without_delivery_does_not_complete_or_publish_internal_text(tmp_path):
    store=Store()
    cfg=SimpleNamespace(runtime_dir=tmp_path,brain='codex')
    conversation=Conversation(cfg,store,SimpleNamespace(),None,None)
    conversation.brain=Brain(Outcome('completed','stop','done',internal_text='this must never become a public reply'))
    await conversation.process()
    assert conversation.brain.calls==4  # bounded repair, no infinite tool loop
    assert store.finished[-1]['retry'] is True
    assert store.finished[-1]['error']=='turn_incomplete'
    assert conversation.broker.capability is None
    assert conversation._turn_id==store.row['id']


@pytest.mark.asyncio
async def test_provider_failure_restarts_brain_and_keeps_durable_input(tmp_path,monkeypatch):
    async def no_wait(*args):pass
    monkeypatch.setattr('brodar.runtime.asyncio.sleep',no_wait)
    store=Store();cfg=SimpleNamespace(runtime_dir=tmp_path,brain='codex')
    conversation=Conversation(cfg,store,SimpleNamespace(),None,None)
    conversation.brain=Brain(Outcome('failed',error='transport_closed'))
    await conversation.process()
    assert conversation.brain.closed is True
    assert conversation.brain.started==['session']
    assert store.finished[-1]['retry'] is True
