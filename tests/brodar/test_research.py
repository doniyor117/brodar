import asyncio
import copy
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import pytest
from mcp.types import CallToolResult,TextContent

from brodar.broker import ToolBroker,TurnCapability
from brodar.hyperagent import (HyperagentTransport,HyperagentError,AuthenticationRequired,
    RemoteRejected,decode_result,token_record,write_auth,read_auth)
from brodar.research import ResearchService,parse_status,remote_thread_id
from brodar.scope import Scope


class Tasks:
    def __init__(self):self.rows={};self.queued=[];self.changes=[]
    async def enqueue(self,**values):
        uid=values['task_id']
        row=self.rows.setdefault(uid,dict(id=uid,status='queued',remote_thread_id=None,
            remote_agent_id=values['agent_id'],outbound_message=values['message'],generation=0,
            attempts=0,last_result_id=None,origin_chat_id=values['scope'].chat_id,
            origin_topic_id=values['scope'].topic_id,requester_id=values['requester'],notified_generation=-1))
        row.setdefault('result_version',0);row.setdefault('notified_result_version',-1)
        if not self.queued:self.queued.append(copy.copy(row))
        return row
    async def get(self,uid):return self.rows[uid]
    async def claim(self):
        rows,self.queued=self.queued,[]
        return rows
    async def submitting(self,row):self.rows[row['id']]['status']='submitting'
    async def update(self,row,**values):
        self.changes.append(values)
        self.rows[row['id']].update({k:v for k,v in values.items() if k!='delay'})
        if 'result' in values:self.rows[row['id']]['result_version']+=1
    async def notifications(self):
        return [r for r in self.rows.values() if r['status'] in {'complete','partial','blocked','failed','uncertain','waiting'} and r['notified_result_version']<r['result_version']]
    async def notified(self,row):
        self.rows[row['id']]['notified_generation']=row['generation']
        self.rows[row['id']]['notified_result_version']=row['result_version']
    async def next_delay(self):return 60


class Remote:
    ready=True;agent_id='researcher'
    def __init__(self):self.calls=[];self.block=None;self.error=None
    async def prepare(self):
        if self.block:await self.block.wait()
    async def submit(self,message):
        self.calls.append(message)
        if self.error:raise self.error
        return {'threadId':'remote-thread'}
    async def status(self,uid):return {'threadId':uid,'agentId':self.agent_id,'isRunning':False,
        'messages':[{'id':'report-1','role':'assistant','content':'COMPLETE\nactual report https://example.org/source'}]}


def setup(chat=-11):
    store=Tasks();remote=Remote();results=[]
    async def notify(row):results.append(copy.copy(row))
    cfg=SimpleNamespace(research_transport='hyperagent_mcp',owner_ids={42,73})
    service=ResearchService(cfg,store,remote,SimpleNamespace(groups={-11,-22}),notify)
    source=ToolBroker(SimpleNamespace(scope=Scope(1,chat)),None,owner_ids=cfg.owner_ids)
    source.begin(TurnCapability(str(uuid4()),requester_id=42))
    return service,store,remote,source,results


@pytest.mark.asyncio
async def test_enqueue_never_waits_for_remote_and_background_yields_to_foreground():
    service,store,remote,source,results=setup()
    remote.block=asyncio.Event()
    queued=await asyncio.wait_for(service.delegate(source,'find sources','one'),.1)
    assert queued['status']=='queued' and queued['background']
    assert source.pending_research and remote.calls==[]
    task=asyncio.create_task(service.tick())
    await asyncio.sleep(0)
    # A blocked remote request does not block this event loop or a live reply.
    foreground=asyncio.Event()
    async def answer():foreground.set()
    await asyncio.wait_for(answer(),.1)
    assert foreground.is_set() and not task.done()
    remote.block.set();await task
    assert store.rows[queued['task_id']]['remote_thread_id']=='remote-thread'
    assert results==[]


@pytest.mark.asyncio
async def test_completed_report_notified_once_and_survives_service_recreation():
    service,store,remote,source,results=setup()
    queued=await service.delegate(source,'research this','one')
    await service.tick()
    row=store.rows[queued['task_id']]
    store.queued=[copy.copy(row)]
    restarted=ResearchService(service.cfg,store,remote,service.access,service.notify)
    await restarted.tick();await restarted.tick()
    assert len(remote.calls)==1 and len(results)==1
    assert results[0]['result']['report'].startswith('COMPLETE')


@pytest.mark.asyncio
async def test_approval_pause_is_reported_once_then_final_result_resumes_without_resubmission():
    service,store,remote,source,results=setup()
    queued=await service.delegate(source,'research','one');await service.tick()
    row=store.rows[queued['task_id']]
    original=remote.status
    async def approval(uid):return {'thread':{'id':uid,'namedAgentId':remote.agent_id},'isRunning':False,'awaitingApproval':True,'messages':[]}
    remote.status=approval
    for _ in range(2):
        store.queued=[copy.copy(row)];await service.tick()
    assert row['status']=='waiting' and len(results)==1
    first_version=results[0]['result_version']
    remote.status=original
    store.queued=[copy.copy(row)];await service.tick()
    assert row['status']=='complete' and len(results)==2
    assert results[-1]['result_version']>first_version
    assert len(remote.calls)==1


@pytest.mark.asyncio
async def test_cli_grant_reference_refresh_preserves_other_codex_credentials(tmp_path,monkeypatch):
    import httpx,time
    from brodar.hyperagent import ResearchAuth,ENDPOINT
    canonical=tmp_path/'credentials.json';config=tmp_path/'research.json'
    key='hyperagent|example'
    write_auth(canonical,{key:{'server_name':'hyperagent','server_url':ENDPOINT,'issuer':'https://hyperagent.com',
        'client_id':'client','access_token':'old','refresh_token':'refresh','expires_at':int((time.time()-60)*1000),
        'scopes':['threads:read','threads:write']},'another':{'access_token':'untouched'}})
    write_auth(config,{'codex_credentials_file':str(canonical),'codex_server_key':key,'agent_id':'researcher'})
    factory=httpx.AsyncClient
    def handle(request):
        assert str(request.url)=='https://hyperagent.com/api/oauth/token'
        return httpx.Response(200,json={'access_token':'new','refresh_token':'next-refresh','expires_in':900,'scope':'threads:read threads:write'})
    monkeypatch.setattr('brodar.hyperagent.httpx.AsyncClient',lambda **kw:factory(transport=httpx.MockTransport(handle),**kw))
    assert await ResearchAuth(config)._token()=='new'
    value=read_auth(canonical)
    assert value[key]['access_token']=='new' and value['another']['access_token']=='untouched'
    assert 'tokens' not in read_auth(config)


@pytest.mark.asyncio
async def test_submission_timeout_is_uncertain_and_never_resubmitted():
    service,store,remote,source,results=setup()
    remote.error=TimeoutError()
    queued=await service.delegate(source,'research','one')
    await service.tick();await service.tick()
    assert store.rows[queued['task_id']]['status']=='uncertain'
    assert len(remote.calls)==1 and len(results)==1


@pytest.mark.asyncio
async def test_auth_error_wrapped_by_mcp_task_group_is_a_blocked_login():
    service,store,remote,source,results=setup()
    remote.error=ExceptionGroup('transport',[AuthenticationRequired('do not echo a token')])
    queued=await service.delegate(source,'research','one')
    await service.tick()
    row=store.rows[queued['task_id']]
    assert row['status']=='blocked' and row['error_code']=='hyperagent_login_required'


@pytest.mark.asyncio
async def test_external_results_cannot_recursively_delegate_and_private_routes_are_bound():
    service,store,remote,source,results=setup()
    source.research_context=('another-task',)
    with pytest.raises(PermissionError):await service.delegate(source,'more research','one')
    source.research_context=()
    service.origin(source,{'origin_chat_id':-11,'origin_topic_id':0})
    with pytest.raises(PermissionError):service.origin(source,{'origin_chat_id':42,'origin_topic_id':0})
    service,store,remote,source,results=setup(42)
    assert (await service.delegate(source,'private research','one'))['return_chat_id']==42
    source.end();source.begin(TurnCapability(str(uuid4()),requester_id=73))
    with pytest.raises(PermissionError):await service.delegate(source,'wrong private recipient','two')


@pytest.mark.asyncio
async def test_unconfigured_research_never_publishes_or_queues():
    service,store,remote,source,results=setup()
    remote.ready=False
    with pytest.raises(AuthenticationRequired):await service.delegate(source,'research','one')
    assert store.rows=={} and not source.pending_research


@pytest.mark.parametrize('state',[{'isRunning':True},{'status':'running'}])
def test_running_messages_are_never_mistaken_for_results(state):
    assert parse_status(dict(state,messages=[{'role':'assistant','content':'working on it'}]),'t','a') is None


def test_result_validation_checks_identity_state_and_redacts_credentials():
    with pytest.raises(HyperagentError):parse_status({'threadId':'wrong','isRunning':False},'t','a')
    with pytest.raises(HyperagentError):parse_status({'threadId':'t','agentId':'wrong','isRunning':False},'t','a')
    with pytest.raises(HyperagentError):parse_status({'messages':[]},'t','a')
    result=parse_status({'threadId':'t','isRunning':False,'messages':[{'role':'assistant','content':'PARTIAL\nhat_'+'A'*30}]},'t','a')
    assert result['status']=='partial' and 'hat_' not in result['report']
    assert parse_status({'isRunning':False,'messages':[]},'t','a')['status']=='failed'


def test_real_hyperagent_envelope_handles_named_agent_approval_and_markdown_status():
    value={'thread':{'id':'t','namedAgentId':'a'},'isRunning':False,'awaitingApproval':False,
        'messages':[{'role':'assistant','id':'m','content':'**PARTIAL — TASK_ID: example**\nOnly part was accessible.'}]}
    assert parse_status(value,'t','a')['status']=='partial'
    value['awaitingApproval']=True
    assert parse_status(value,'t','a')['status']=='blocked'
    value['thread']['namedAgentId']='wrong'
    with pytest.raises(HyperagentError):parse_status(value,'t','a')


def test_mcp_result_and_schema_validation_do_not_guess_or_echo_errors(tmp_path):
    with pytest.raises(RemoteRejected) as error:
        decode_result(CallToolResult(isError=True,content=[TextContent(type='text',text='secret backend details')]))
    assert 'secret' not in str(error.value)
    transport=HyperagentTransport(tmp_path/'not-connected')
    transport.schemas={'create_thread':{'properties':{'agentId':{},'message':{}},'required':['agentId','message']}}
    assert transport.arguments('create_thread',agent='a',message='brief')=={'agentId':'a','message':'brief'}
    transport.schemas['create_thread']['required'].append('unknown')
    with pytest.raises(HyperagentError):transport.arguments('create_thread',agent='a',message='brief')


def test_auth_store_is_private_and_unexpected_scopes_are_rejected(tmp_path):
    path=tmp_path/'private'/'auth.json'
    write_auth(path,{'tokens':{'access_token':'private'}})
    assert path.stat().st_mode & 0o777==0o600
    assert read_auth(path)['tokens']['access_token']=='private'
    with pytest.raises(AuthenticationRequired):token_record({'access_token':'private','scope':'threads:read threads:write approvals:write'})


@pytest.mark.asyncio
async def test_nonexpiring_oauth_tokens_do_not_get_an_invented_expiration(tmp_path):
    from brodar.hyperagent import ResearchAuth
    path=tmp_path/'auth.json'
    tokens=token_record({'access_token':'private','scope':'threads:read threads:write'})
    assert tokens['expires_at'] is None
    write_auth(path,{'tokens':tokens})
    assert await ResearchAuth(path)._token()=='private'


@pytest.mark.asyncio
async def test_group_proxy_marks_only_the_source_conversation_as_waiting():
    from brodar.shared import BrokerProxy
    from brodar.mcp import build_tools
    service,store,remote,source,results=setup()
    source.research=service
    source.audit_tool=lambda **kw:asyncio.sleep(0)
    source.store.audit_tool=source.audit_tool
    proxy=BrokerProxy(source);proxy.current=source
    mcp=build_tools(proxy)
    await mcp.call_tool('delegate_research',{'brief':'research','operation_id':'one','turn_token':source.capability.token})
    assert source.pending_research
    assert 'pending_research' not in vars(proxy)
    source.end();source.begin(TurnCapability(str(uuid4()),requester_id=42))
    assert not source.pending_research and not proxy.pending_research
