import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4
import pytest
from brodar.telegram_context import FollowupAffinity
from brodar.scope import Scope
from brodar.maintenance import PersistentSchedule
from brodar.profile import read_profile,write_profile,export_profile
from brodar.settings import Settings
from brodar.setup_engine import SetupEngine
from brodar.broker import ToolBroker,TurnCapability
from brodar.jobs import InputRequest,input_requests
from brodar.owner_tools import owner_request,classify_owner
from brodar.telegram_media import selected_sticker,send_selected_sticker


def test_followup_is_same_person_room_topic_and_expires():
    now=[0];f=FollowupAffinity(clock=lambda:now[0]);scope=Scope(1,-10,4)
    assert f.classify(scope,7,'brodar send one',direct=True)
    assert f.classify(scope,7,'try again')
    assert not f.classify(scope,8,'try again')
    assert not f.classify(Scope(1,-10,5),7,'try again')
    assert not f.classify(scope,7,'unrelated dinner conversation')
    now[0]=121
    assert not f.classify(scope,7,'try again')


def test_backup_due_survives_restart_and_failure_backoff(tmp_path):
    now=[100];p=tmp_path/'schedule';s=PersistentSchedule(p,1209600,lambda:now[0])
    now[0]+=1209601;assert s.due()
    s=PersistentSchedule(p,1209600,lambda:now[0]);assert s.due()
    s.failure();assert not s.due();now[0]+=3601;assert s.due()
    s.success();s=PersistentSchedule(p,1209600,lambda:now[0]);assert not s.due()


def test_profile_no_shared_secret_fallback_and_explicit_overrides(tmp_path,monkeypatch):
    monkeypatch.setenv('HOME',str(tmp_path))
    path=tmp_path/'instance/profile.json';secret=path.parent/'runtime.env';secret.parent.mkdir();secret.write_text('BRODAR_OWNER_IDS=99\nBRODAR_DATABASE_URL=postgres://user:secret@example.invalid/db\n')
    write_profile(path,{'version':1,'secret_env_file':'runtime.env','settings':{'BRODAR_OWNER_IDS':'7','BRODAR_CODEX_MODEL':'my-model'}})
    cfg=Settings.load(profile=path)
    assert cfg.owner_ids=={7} and cfg.model=='my-model' and cfg.database_url.endswith('/db')
    assert cfg.research_chat_id==0 and cfg.research_bot_username==''
    export_profile(path,tmp_path/'export')
    assert 'secret' not in (tmp_path/'export/profile.json').read_text()


def test_setup_journal_keeps_secret_values_out_and_rejects_concurrent_setup(tmp_path):
    path=tmp_path/'profile.json'
    with SetupEngine(path) as e:
        e.defaults();e.secret('TELEGRAM_BOT_TOKEN','not-a-real-secret-canary');e.done('token')
        assert 'not-a-real-secret-canary' not in e.journal_path.read_text()+path.read_text()
        with pytest.raises(RuntimeError):
            with SetupEngine(path):pass
    with SetupEngine(path) as e:assert e.journal['steps']['token']=='configured'


def test_owner_gateway_requires_actual_fresh_owner_and_revokes_edits(tmp_path):
    b=ToolBroker(SimpleNamespace(scope=Scope(1,-1)),None,owner_ids={7});b.begin(TurnCapability(str(uuid4()),requester_id=7))
    request=InputRequest(str(uuid4()),7,'install a skill');b.accept_requests([request]);cap,r,check=owner_request(b,request.id);check()
    b.revoke_request_ids([request.id])
    with pytest.raises(PermissionError):check()
    b.end();b.begin(TurnCapability(str(uuid4()),requester_id=8));b.accept_requests([request])
    with pytest.raises(PermissionError):owner_request(b,request.id)
    classify_owner(['python','-c','print(2+2)'],tmp_path)
    classify_owner(['pip','install','pillow'],tmp_path)
    for argv in [['cat','/home/user/.codex/auth.json'],['rm','-rf','/'],['sh','-c','cat /proc/self/environ']]:
        with pytest.raises(PermissionError):classify_owner(argv,tmp_path)


def test_edited_and_forwarded_owner_text_is_not_fresh_authority():
    for metadata in [{'edit_date':1},{'forward_sender_name':'owner'},{'sender_chat':{'id':7}}]:
        assert not input_requests([{'message_id':str(uuid4()),'payload':{'message':{'from':{'id':7},'text':'install',**metadata}}}])


@pytest.mark.asyncio
async def test_sticker_handle_resolves_exact_id_and_refuses_reordered_pack():
    import time
    s1=SimpleNamespace(file_id='file_id_first');s2=SimpleNamespace(file_id='file_id_second')
    b=ToolBroker(SimpleNamespace(scope=Scope(1,-1)),SimpleNamespace(get_sticker_set=AsyncMock(return_value=SimpleNamespace(stickers=[s1,s2]))))
    b.begin(TurnCapability(str(uuid4())))
    b._sticker_cache={'short':{'audience':b.store.scope.audience,'expires':time.monotonic()+60,'name':'test_pack','snapshot':('file_id_first','file_id_second'),'entry':{'file_id':s2.file_id,'index':2,'file_unique_id':'unique-two'}}}
    from unittest.mock import patch
    with patch('brodar.telegram_media.send_media',AsyncMock(return_value={'message_id':3})) as send:
        receipt=await send_selected_sticker(b,'short','op')
        assert receipt['index']==2 and send.await_args.args[2]=='file:file_id_second'
        b.bot.get_sticker_set.return_value=SimpleNamespace(stickers=[s2,s1])
        with pytest.raises(ValueError,match='changed'):await send_selected_sticker(b,'short','other')
        b._sticker_cache['short']['expires']=0
        with pytest.raises(ValueError,match='expired'):selected_sticker(b,'short')


@pytest.mark.asyncio
async def test_private_control_socket_attaches_without_poller(tmp_path):
    from brodar.control import ControlServer,request
    cfg=SimpleNamespace(runtime_dir=tmp_path)
    handler=AsyncMock(return_value={'active':1});server=ControlServer(cfg,handler)
    await server.start()
    try:
        assert await request(cfg,'status')=={'active':1}
        assert server.path.stat().st_mode & 0o777==0o600
        handler.assert_awaited_once_with('status',{})
    finally:await server.close()

@pytest.mark.asyncio
async def test_operator_mcp_calls_are_ephemeral_without_inference_or_saved_credentials(tmp_path):
    from brodar.operator_mcp import OperatorMcp
    calls=[]
    canary='postgres://tester:private-canary-secret@example.invalid/database'
    class Rpc:
        def __init__(self,argv,**kwargs):self.argv=argv
        async def start(self):pass
        async def close(self):pass
        async def notify(self,*args):pass
        async def request(self,method,params=None,**kwargs):
            calls.append((method,params))
            if method=='thread/start':
                assert params['ephemeral'] is True
                return {'thread':{'id':'ephemeral'}}
            if method=='mcpServerStatus/list':return {'data':[{'name':'neon','tools':{'get_connection_string':{}}}]}
            if method=='mcpServer/tool/call':return {'structuredContent':{'uri':canary}}
            return {}
    async with OperatorMcp(factory=Rpc,home=tmp_path) as mcp:
        value=await mcp.call('get_connection_string',{'project_id':'explicit-test'})
        assert value['uri']==canary
        with pytest.raises(PermissionError):await mcp.call('delete_project',{})
    assert not any(method=='turn/start' for method,_ in calls)
    assert not any(canary in p.read_text(errors='replace') for p in tmp_path.rglob('*') if p.is_file())


def test_invalid_profile_does_not_destroy_previous_configuration(tmp_path):
    p=tmp_path/'profile.json';write_profile(p,{'version':1,'settings':{'BRODAR_OWNER_IDS':'7'}})
    original=p.read_bytes()
    with pytest.raises(ValueError):write_profile(p,{'version':1,'settings':{'TELEGRAM_BOT_TOKEN':'private'}})
    assert p.read_bytes()==original

@pytest.mark.asyncio
async def test_tui_detaches_cleanly_on_narrow_phone_terminal(tmp_path,monkeypatch):
    from prompt_toolkit.input.defaults import create_pipe_input
    from prompt_toolkit.output import DummyOutput
    from prompt_toolkit.application.current import create_app_session
    from prompt_toolkit.data_structures import Size
    from brodar.tui import tui
    import sys
    monkeypatch.setattr(sys.stdin,'isatty',lambda:True)
    monkeypatch.setattr(sys.stdout,'isatty',lambda:True)
    monkeypatch.setattr('brodar.tui.request',AsyncMock(return_value={'running':True}))
    class PhoneOutput(DummyOutput):
        def get_size(self):return Size(rows=18,columns=38)
    with create_pipe_input() as pipe:
        with create_app_session(input=pipe,output=PhoneOutput()):
            task=asyncio.create_task(tui(SimpleNamespace(runtime_dir=tmp_path)))
            await asyncio.sleep(.1);pipe.send_text('\x11')
            await asyncio.wait_for(task,2)


def test_managed_restart_starts_replacement_instead_of_reusing_old_running_flag(tmp_path,monkeypatch):
    from brodar.service import service
    state=tmp_path/'runtime';state.mkdir()
    (state/'managed-service.json').write_text(json.dumps({'pid':11,'start_time':'old'}))
    alive={11:'old',22:'new'}
    monkeypatch.setattr('brodar.service.identity',lambda pid:alive.get(pid))
    def kill(pid,signum):alive.pop(pid,None)
    monkeypatch.setattr('brodar.service.os.kill',kill)
    popen=SimpleNamespace(pid=22)
    from unittest.mock import Mock
    launcher=Mock(return_value=popen);monkeypatch.setattr('brodar.service.subprocess.Popen',launcher)
    cfg=SimpleNamespace(runtime_dir=state,profile_path=tmp_path/'profile.json',backup_admin_file=None,backup_key_file=None,storage_config=None,backup_interval_seconds=1209600)
    result=service(cfg,'restart')
    assert result['started'] is True and result['pid']==22
    assert launcher.call_count==1 and service(cfg,'status')['running'] is True


def test_regular_gateway_cannot_read_owner_job_scratch(tmp_path):
    from brodar.commands import CommandGateway
    gateway=CommandGateway(tmp_path)
    with pytest.raises(PermissionError,match='Owner job workspace'):gateway.classify(['cat','owner/report.txt'])
    gateway.classify(['cat','owner/report.txt'],owner=True)
    gateway.classify(['cat','group-report.txt'])
