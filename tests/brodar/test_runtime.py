from types import SimpleNamespace
import asyncio
from uuid import uuid4

import pytest

from brodar.brains.base import Outcome
from brodar.runtime import Conversation,undelivered_artifacts,origin_report_needed
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
    async def reply_context(self,*args):return []
    async def turn_context(self,ids,*args):
        return {'messages':await self.history_get(ids),'context':[],'notes':[],
            'receipts':[],'artifacts':[],'has_index':False}
    async def work_state(self,*args):return {'receipts':await self.delivery_receipts(None),'artifacts':await self.work_artifacts(None)}


class Brain:
    session_id='session'
    def __init__(self,outcome):self.outcome=outcome;self.calls=0;self.closed=False;self.started=[]
    async def turn(self,text):self.calls+=1;return self.outcome
    async def close(self):self.closed=True
    async def start(self,session_id=None):self.started.append(session_id)
    async def interrupt(self):pass


@pytest.mark.asyncio
async def test_queued_research_can_yield_without_delivery_repair_or_retry(tmp_path):
    store=Store();cfg=SimpleNamespace(runtime_dir=tmp_path,brain='codex')
    conversation=Conversation(cfg,store,SimpleNamespace(),None,None)
    class DelegatingBrain(Brain):
        async def turn(self,text):
            self.calls+=1
            conversation.broker.pending_research=True
            return self.outcome
    conversation.brain=DelegatingBrain(Outcome('completed','stop','research queued'))
    await conversation.process()
    assert conversation.brain.calls==1
    assert store.finished[-1]['error'] is None
    assert not store.finished[-1]['retry']


@pytest.mark.parametrize('method',['routed_message','routed_document'])
def test_cross_group_completion_needs_a_later_origin_report(method):
    scope=Scope(1,-100,10)
    local={'method':'send_message','status':'sent','topic_id':10,'request':{'text':'on it'}}
    remote={'method':method,'status':'sent','topic_id':10,'request':{'chat_id':-200,'topic_id':0}}
    assert origin_report_needed([remote],scope)
    assert origin_report_needed([local,remote],scope)
    assert not origin_report_needed([local,remote,local],scope)
    assert origin_report_needed([remote,dict(local,response={'final':False})],scope)
    assert origin_report_needed([remote,dict(local,method='set_reaction')],scope)


def test_other_topic_delivery_and_unknown_outcomes_still_need_origin_feedback():
    scope=Scope(1,-100,10)
    remote={'method':'routed_message','status':'uncertain','topic_id':10,'request':{'chat_id':-100,'topic_id':20}}
    assert origin_report_needed([remote],scope)
    destination_message={'method':'send_message','status':'sent','topic_id':20,'request':{}}
    assert origin_report_needed([remote,destination_message],scope)
    source_message=dict(destination_message,topic_id=10)
    assert not origin_report_needed([remote,destination_message,source_message],scope)


@pytest.mark.parametrize('action',['stop','skip'])
@pytest.mark.asyncio
async def test_destination_delivery_does_not_complete_turn_until_origin_report(tmp_path,action):
    store=Store();cfg=SimpleNamespace(runtime_dir=tmp_path,brain='codex')
    conversation=Conversation(cfg,store,SimpleNamespace(),None,None)
    receipts=[]
    async def delivery_receipts(*args):return receipts
    store.delivery_receipts=delivery_receipts
    class RoutingBrain(Brain):
        async def turn(self,text):
            self.calls+=1
            if self.calls==1:
                receipts.append({'method':'routed_message','status':'sent','request':{'chat_id':-200,'topic_id':0}})
            else:
                assert 'originating' in text
                receipts.append({'method':'send_message','status':'sent','request':{'text':'posted there'}})
            return Outcome('completed',action,'finished')
    conversation.brain=RoutingBrain(None)
    await conversation.process()
    assert conversation.brain.calls==2
    assert store.finished[-1]['error'] is None


@pytest.mark.asyncio
async def test_external_research_result_cannot_silently_finish_without_answer(tmp_path):
    store=Store();cfg=SimpleNamespace(runtime_dir=tmp_path,brain='codex')
    store.row['payload']={'internal_research':{'task_id':str(uuid4()),'requester_id':42}}
    conversation=Conversation(cfg,store,SimpleNamespace(),None,None)
    receipts=[]
    async def delivery_receipts(*args):return receipts
    store.delivery_receipts=delivery_receipts
    class ReportingBrain(Brain):
        async def turn(self,text):
            self.calls+=1
            if self.calls==2:
                receipts.append({'method':'send_message','status':'sent','request':{'text':'here is the result'}})
            return Outcome('completed','skip','done')
    conversation.brain=ReportingBrain(None)
    await conversation.process()
    assert conversation.brain.calls==2
    assert store.finished[-1]['error'] is None


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



@pytest.mark.asyncio
async def test_optional_embedding_timeout_does_not_block_turn(tmp_path):
    store=Store()
    original=store.turn_context
    async def context(*args):
        bundle=await original(*args)
        bundle['has_index']=True
        bundle['messages'][0]['content']='a sufficiently long conversation message'
        return bundle
    store.turn_context=context
    class Embeddings:
        model='test'
        async def embed(self,*args,**kwargs):await asyncio.Event().wait()
    cfg=SimpleNamespace(runtime_dir=tmp_path,brain='codex',recall_timeout_seconds=.01)
    conversation=Conversation(cfg,store,SimpleNamespace(),Embeddings(),None)
    conversation.brain=Brain(Outcome('completed','skip','nothing needed'))
    await asyncio.wait_for(conversation.process(),1)
    assert conversation.brain.calls==1
    assert store.finished[-1]['error'] is None


@pytest.mark.asyncio
async def test_exhausted_addressed_failure_gets_notice(tmp_path,monkeypatch):
    async def no_wait(*args):pass
    monkeypatch.setattr('brodar.runtime.asyncio.sleep',no_wait)
    store=Store();store.row['attempts']=3
    store.row['payload']['message']['text']='brodar help me'
    conversation=Conversation(SimpleNamespace(runtime_dir=tmp_path,brain='codex'),store,SimpleNamespace(),None,None)
    conversation.brain=Brain(Outcome('failed',error='provider_failed'))
    sent=[]
    async def send(text,**kwargs):sent.append((text,kwargs))
    conversation.broker.send_message=send
    await conversation.process()
    assert len(sent)==1 and sent[0][1]['call_id']=='terminal-failure'
    assert store.finished[-1]['retry'] is False
