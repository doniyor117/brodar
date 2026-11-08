"""Direct operator MCP calls via an ephemeral Codex app-server, never inference."""
from __future__ import annotations
import json
import asyncio
import os
import shutil
import tempfile
from pathlib import Path
from .brains.rpc import RpcProcess
from .brains.codex import app_server_argv

class OperatorMcp:
    def __init__(self,server='neon',*,factory=RpcProcess,home=None):
        if not server.replace('_','').replace('-','').isalnum():raise ValueError('Invalid MCP server name')
        self.server,self.factory,self.home=server,factory,home;self.rpc=None;self.temp=None;self.thread=None;self.tools={}
    async def __aenter__(self):
        binary=shutil.which('codex')
        if binary is None:raise RuntimeError('Codex CLI is unavailable; use an existing Neon connection file instead')
        self.temp=tempfile.TemporaryDirectory(prefix='brodar-operator-')
        env={key:os.environ[key] for key in ('PATH','HOME','PREFIX','TMPDIR','LD_LIBRARY_PATH','SSL_CERT_FILE','SSL_CERT_DIR') if key in os.environ}
        if self.home:env['CODEX_HOME']=str(self.home)
        argv=app_server_argv(binary)+['-c','project_doc_max_bytes=0','-c','history.persistence="none"']
        # Disable every configured MCP except the explicitly selected operator connection.
        config=Path(self.home or os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'config.toml'
        if config.exists():
            import tomllib
            servers=tomllib.loads(config.read_text()).get('mcp_servers',{})
            if self.server not in servers:
                found=[name for name,settings in servers.items() if name.casefold()==self.server.casefold() or settings.get('url','').rstrip('/')=='https://mcp.neon.tech/mcp']
                if len(found)==1:self.server=found[0]
            for name in servers:
                if name!=self.server:argv+=['-c','mcp_servers.'+name+'.enabled=false']
        self.rpc=self.factory(argv,env=env,cwd=self.temp.name)
        try:
            await self.rpc.start()
            await self.rpc.request('initialize',{'clientInfo':{'name':'brodar-setup','version':'0.1.0'},'capabilities':{'experimentalApi':True}})
            await self.rpc.notify('initialized')
            result=await self.rpc.request('thread/start',{'ephemeral':True,'cwd':self.temp.name,'environments':[], 'approvalPolicy':'never','sandbox':'read-only','baseInstructions':'Operator tool transport. No inference turns are allowed.','config':{'web_search':'disabled'}})
            self.thread=result['thread']['id']
            import time
            deadline=time.monotonic()+45
            auth_status='unknown'
            while time.monotonic()<deadline:
                status=await self.rpc.request('mcpServerStatus/list',{'threadId':self.thread,'serverName':self.server,'detail':'toolsAndAuthOnly','limit':100},timeout=60)
                for entry in status.get('data',[]):
                    if entry.get('name')==self.server:
                        auth_status=entry.get('authStatus','unknown')
                        self.tools=entry.get('tools',{})
                        if isinstance(self.tools,list):self.tools={x['name']:x for x in self.tools}
                if self.tools:break
                if auth_status=='notLoggedIn' or auth_status=='not_logged_in':break
                await asyncio.sleep(1)
            if not self.tools:raise RuntimeError('Neon MCP unavailable (auth '+auth_status+'). Run codex mcp login '+self.server+' --no-browser; also check granted scopes')
            return self
        except BaseException:
            await self.__aexit__(None,None,None);raise
    async def call(self,tool,arguments=None):
        if tool not in self.tools:raise PermissionError('Authorized Neon MCP does not expose '+tool+'; check read-only/role-pinned scopes')
        result=await self.rpc.request('mcpServer/tool/call',{'threadId':self.thread,'server':self.server,'tool':tool,'arguments':arguments or {}},timeout=120)
        if result.get('isError'):raise RuntimeError('Neon MCP tool failed: '+tool+' (details withheld)')
        if result.get('structuredContent') is not None:return result['structuredContent']
        text='\n'.join(block.get('text','') for block in result.get('content',[]) if block.get('type')=='text')
        try:return json.loads(text)
        except ValueError:return {'text':text}
    async def __aexit__(self,*args):
        if self.rpc:await self.rpc.close();self.rpc=None
        if self.temp:self.temp.cleanup();self.temp=None
