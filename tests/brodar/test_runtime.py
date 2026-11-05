from types import SimpleNamespace
import asyncio
from uuid import uuid4

import pytest

from brodar.brains.base import Outcome
from brodar.runtime import Conversation,undelivered_artifacts
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
    async def work_begin(self,*args):return {'status':'running','checkpoint':{}}
    async def work_artifacts(self,*args):return []


class Brain:
    session_id='session'
    def __init__(self,outcome):self.outcome=outcome;self.calls=0;self.closed=False;self.started=[]
    async def turn(self,text):self.calls+=1;return self.outcome
    async def close(self):self.closed=True
    async def start(self,session_id=None):self.started.append(session_id)
    async def interrupt(self):pass


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
async def test_service_shutdown_preserves_checkpointed_inputs_for_restart(tmp_path):
    store=Store();cfg=SimpleNamespace(runtime_dir=tmp_path,brain='codex')
    conversation=Conversation(cfg,store,SimpleNamespace(),None,None)
    class InterruptedBrain(Brain):
        async def turn(self,text):raise asyncio.CancelledError()
    conversation.brain=InterruptedBrain(None)
    with pytest.raises(asyncio.CancelledError):await conversation.process()
    assert store.finished[-1]['retry'] is True
    assert store.finished[-1]['error']=='service_restart'
    assert store.finished[-1]['job_id']==store.row['id']


def test_each_created_artifact_needs_its_own_successful_delivery_receipt():
    artifacts=[{'artifact_id':'one'},{'artifact_id':'two'}]
    receipts=[{'status':'sent','method':'send_document','request':{'artifact_id':'older'}}]
    assert undelivered_artifacts(artifacts,receipts)==['one','two']
    receipts.append({'status':'sent','method':'send_document','request':{'artifact_id':'one'}})
    assert undelivered_artifacts(artifacts,receipts)==['two']
    receipts.append({'status':'sent','method':'send_document','request':{'artifact_id':'two'}})
    assert undelivered_artifacts(artifacts,receipts)==[]


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
