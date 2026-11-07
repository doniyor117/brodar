"""Persistent Codex app-server adapter, protocol baseline 0.166.0.

Environment access is disabled at thread AND turn boundaries. The runtime
must provision a dedicated CODEX_HOME with only Brodar's scoped MCP server.
No shell fallback is used when an Android sandbox is unavailable.
"""

from __future__ import annotations

import asyncio
import copy
import os
from pathlib import Path

from hamroh.cc_worker.cc_schema import CONTROL_ACTION_SCHEMA

from .base import Outcome, terminal
from .rpc import RpcError, RpcProcess

CODEX_CONTROL_SCHEMA=copy.deepcopy(CONTROL_ACTION_SCHEMA)
# Responses strict schemas require every property in `required`. Claude's
# schema intentionally allows omitting reason for heartbeat; keep it unchanged.
CODEX_CONTROL_SCHEMA['required']=['action','reason']

# Process-level feature gates are effective before any thread is created.
# Thread config alone does not establish a native-tool boundary on this build.
DISABLED_FEATURES=('shell_tool','unified_exec','view_image','multi_agent','plugins','apps','hooks',
    'skill_mcp_dependency_install','browser_use','browser_use_external','browser_use_full_cdp_access',
    'computer_use','in_app_browser','image_generation')


def app_server_argv(binary):
    argv=[binary,'app-server','--listen','stdio://']
    for feature in DISABLED_FEATURES:argv.extend(['--disable',feature])
    argv.extend(['--enable','skip_host_skill_discovery'])
    return argv


def child_environment(home: Path, extra: dict[str,str] | None = None):
    # Keep linker/certificate/temporary directory settings needed by Termux,
    # never the service's database, Google key, GitHub or Telegram credentials.
    keys = ['PATH','PREFIX','LD_LIBRARY_PATH','TMPDIR','SSL_CERT_FILE','SSL_CERT_DIR',
            'ANDROID_ROOT','ANDROID_DATA','LANG','TZ','TERM']
    env = {k:os.environ[k] for k in keys if k in os.environ}
    env.update({'HOME':str(home/'user'),'CODEX_HOME':str(home),'RUST_LOG':'error'})
    env.update(extra or {})
    return env


class CodexBrain:
    def __init__(self, *, home: Path, cwd: Path, model: str, effort: str,
                 instructions: str, binary: str = 'codex', env: dict[str,str] | None = None,
                 mcp_url:str|None=None,auto_compact_token_limit:int|None=None,transport_factory=RpcProcess):
        self.home,self.cwd = home,cwd
        self.model,self.effort,self.instructions = model,effort,instructions
        self.session_id = None
        self.active_turn = None
        self._starting = False
        self._completed = {}
        self._texts = {}
        self._waiter = None
        self._transport_closed = False
        argv=app_server_argv(binary)
        if auto_compact_token_limit is not None:
            argv.extend(['-c',f'model_auto_compact_token_limit={auto_compact_token_limit}'])
        if mcp_url:
            import json
            argv.extend(['-c','mcp_servers.hamroh.url='+json.dumps(mcp_url),
                         '-c','mcp_servers.hamroh.bearer_token_env_var="BRODAR_MCP_TOKEN"',
                         '-c','mcp_servers.hamroh.default_tools_approval_mode="approve"'])
        argv.extend(['-c','project_doc_max_bytes=0'])
        self.rpc = transport_factory(
            argv,
            env=child_environment(home,env),cwd=str(cwd),
            on_notification=self._notification,on_request=self._server_request)

    async def start(self, session_id: str | None = None):
        self._transport_closed=False
        self._completed.clear()
        self._texts.clear()
        self.home.mkdir(parents=True,exist_ok=True,mode=0o700)
        (self.home/'user').mkdir(exist_ok=True,mode=0o700)
        self.cwd.mkdir(parents=True,exist_ok=True,mode=0o700)
        await self.rpc.start()
        await self.rpc.request('initialize',{'clientInfo':{'name':'brodar','version':'0.1.0'},
                                              'capabilities':{'experimentalApi':True}})
        await self.rpc.notify('initialized')
        params = {'model':self.model,'approvalPolicy':'never','sandbox':'read-only',
                  'cwd':str(self.cwd),'baseInstructions':self.instructions,
                  'developerInstructions':'Use only the configured Brodar tools and public web research. '
                    'No native environment access is provided. Terminal output is internal control, never a public reply.',
                  'runtimeWorkspaceRoots':[],'config':{'web_search':'live',
                    'features.shell_tool':False,'features.apply_patch_freeform':False,
                    'features.multi_agent':False,'features.js_repl':False}}
        if session_id:
            try:
                result = await self.rpc.request('thread/resume',{**params,'threadId':session_id,'excludeTurns':True})
            except RpcError as exc:
                missing=any(marker in exc.message.lower() for marker in ['thread not found','no rollout found',
                    'failed to load rollout','no conversation found','does not exist','not found'])
                if not missing:raise
                result=await self.rpc.request('thread/start',{**params,'environments':[],
                                                            'allowProviderModelFallback':False})
        else:
            result = await self.rpc.request('thread/start',{**params,'environments':[],
                                                          'allowProviderModelFallback':False})
        self.session_id = result['thread']['id']
        if result['model'] != self.model:
            raise RuntimeError('Codex selected a different model; configure one explicitly')
        # A clean profile should never load personal instruction files.
        if result.get('instructionSources'):
            raise RuntimeError('Unexpected Codex instruction sources in dedicated profile')

    def _notification(self, method, params):
        if method=='brodar/transportClosed':
            self._transport_closed = True
            if self._waiter and not self._waiter.done():
                self._waiter.set_result(Outcome('failed',error='transport_closed',session_id=self.session_id))
            return
        if params.get('threadId')!=self.session_id:
            return
        if method=='item/completed':
            item = params.get('item',{})
            if item.get('type')=='agentMessage':
                self._texts.setdefault(params.get('turnId'),{})[item['id']] = item.get('text','')
        if method=='turn/completed':
            turn = params['turn']
            tid = turn['id']
            if tid != self.active_turn and not self._starting:
                return
            self._completed[tid] = turn
            if tid == self.active_turn and self._waiter and not self._waiter.done():
                self._waiter.set_result(self._outcome(turn))

    def _outcome(self, turn):
        tid = turn['id']
        if turn['status']!='completed':
            status = 'interrupted' if turn['status']=='interrupted' else 'failed'
            error = turn.get('error') or {}
            return Outcome(status,error=str(error.get('codexErrorInfo','provider_failed')),
                           session_id=self.session_id)
        texts = self._texts.pop(tid,{})
        # Some server versions return the terminal item only in turn.items.
        for item in turn.get('items',[]):
            if item.get('type')=='agentMessage':
                texts[item['id']] = item.get('text','')
        return terminal(next(reversed(texts.values()),''),self.session_id)

    async def turn(self, text: str) -> Outcome:
        if self.active_turn or self._starting:
            raise RuntimeError('A turn is already running')
        if self._transport_closed:
            return Outcome('failed',error='transport_closed',session_id=self.session_id)
        self._waiter = asyncio.get_running_loop().create_future()
        self._starting = True
        try:
            result = await self.rpc.request('turn/start',{
                'threadId':self.session_id,'input':[{'type':'text','text':text}],
                'environments':[],'runtimeWorkspaceRoots':[],
                'effort':self.effort,'outputSchema':CODEX_CONTROL_SCHEMA})
            self.active_turn = result['turn']['id']
            self._starting = False
            if self._transport_closed:
                return Outcome('failed',error='transport_closed',session_id=self.session_id)
            if self.active_turn in self._completed:
                return self._outcome(self._completed[self.active_turn])
            return await self._waiter
        except RpcError as exc:
            return Outcome('failed',error=f'rpc:{exc.code}',session_id=self.session_id)
        except asyncio.CancelledError:
            # wait_for cancels this coroutine before the runtime can call
            # interrupt. Send the interrupt while the actual turn ID survives.
            if self.active_turn:
                try:
                    await asyncio.wait_for(asyncio.shield(self.rpc.request('turn/interrupt',{
                        'threadId':self.session_id,'turnId':self.active_turn})),10)
                except Exception:
                    await self.rpc.close()
            raise
        finally:
            self._completed.clear()
            self._texts.clear()
            self.active_turn = None
            self._starting = False
            self._waiter = None

    async def steer(self, text: str) -> bool:
        tid = self.active_turn
        if tid is None or (self._waiter and self._waiter.done()):
            return False
        try:
            await self.rpc.request('turn/steer',{'threadId':self.session_id,'expectedTurnId':tid,
                                               'input':[{'type':'text','text':text}]})
            return True
        except RpcError:
            return False  # caller retains durable input for the next turn

    async def interrupt(self):
        if self.active_turn:
            await self.rpc.request('turn/interrupt',{'threadId':self.session_id,'turnId':self.active_turn})

    async def _server_request(self, method, params):
        if method=='item/commandExecution/requestApproval':
            return {'decision':'decline'}
        if method=='item/fileChange/requestApproval':
            return {'decision':'decline'}
        if method=='item/permissions/requestApproval':
            return {'permissions':{},'scope':'turn'}
        raise RpcError('unsupported_server_request')

    async def close(self):
        await self.rpc.close()
