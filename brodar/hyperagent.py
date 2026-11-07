"""Host-only Hyperagent MCP connection and dedicated OAuth login.

No remote account tools or OAuth material are exposed to the conversational
model. Runtime authentication never launches an interactive browser flow.
"""
from __future__ import annotations

import asyncio
import json
import os
import secrets
import time
from pathlib import Path

import httpx
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client

BASE = 'https://hyperagent.com'
ENDPOINT = BASE + '/api/mcp'
SCOPES = 'threads:read threads:write offline_access'


class HyperagentError(RuntimeError):
    """Safe to report without echoing remote bodies or credentials."""


class AuthenticationRequired(HyperagentError):
    pass


class RemoteRejected(HyperagentError):
    """The server explicitly returned a tool error, rather than an unknown send."""


def read_auth(path):
    try:
        value=json.loads(Path(path).read_text())
        return value if isinstance(value,dict) else {}
    except (OSError, ValueError):
        return {}


def write_auth(path, value):
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_name(path.name + '.' + secrets.token_hex(8))
    fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    try:
        with os.fdopen(fd, 'w') as stream:
            json.dump(value, stream)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
        path.chmod(0o600)
    finally:
        temporary.unlink(missing_ok=True)


def token_record(value):
    if not isinstance(value, dict) or not isinstance(value.get('access_token'), str):
        raise AuthenticationRequired('Hyperagent returned no usable OAuth token')
    if str(value.get('token_type', 'Bearer')).lower() != 'bearer':
        raise AuthenticationRequired('Unsupported Hyperagent OAuth token type')
    granted = set(value.get('scope', SCOPES).split())
    if not {'threads:read', 'threads:write'} <= granted:
        raise AuthenticationRequired('Hyperagent did not grant research thread access')
    # Never request or silently accept approval-writing authority.
    if granted - set(SCOPES.split()):
        raise AuthenticationRequired('Unexpected Hyperagent OAuth scopes; reconnect with research scopes only')
    expiry=value.get('expires_in')
    return dict(value, expires_at=None if expiry is None else time.time()+max(0,float(expiry)))


def credentials(path):
    """Read either a dedicated grant or the owner's selected Codex grant."""
    record=read_auth(path)
    source=record.get('codex_credentials_file')
    if source:
        item=read_auth(source).get(record.get('codex_server_key'),{})
        if item.get('server_url')!=ENDPOINT or item.get('issuer',BASE)!=BASE:
            raise AuthenticationRequired('Codex Hyperagent connection is missing or belongs to another server')
        expiry=item.get('expires_at')
        tokens={'access_token':item.get('access_token'),'refresh_token':item.get('refresh_token'),
                'scope':' '.join(item.get('scopes',[])), 'expires_at':expiry/1000 if expiry else None}
        return dict(record,client_id=item.get('client_id')),tokens
    return record,record.get('tokens') or {}


class ResearchAuth(httpx.Auth):
    def __init__(self, path):
        self.path = Path(path)
        self.lock = asyncio.Lock()

    async def _token(self, *, refresh=False):
        async with self.lock:
            record,tokens=credentials(self.path)
            if not tokens.get('access_token'):
                raise AuthenticationRequired('Connect Hyperagent with brodar hyperagent-login')
            expiry=tokens.get('expires_at')
            if refresh or (expiry is not None and expiry < time.time() + 60):
                if not tokens.get('refresh_token') or not record.get('client_id'):
                    raise AuthenticationRequired('Hyperagent login expired; reconnect with brodar hyperagent-login')
                async with httpx.AsyncClient(timeout=25, follow_redirects=False) as client:
                    response = await client.post(BASE + '/api/oauth/token', data={
                        'grant_type': 'refresh_token', 'refresh_token': tokens['refresh_token'],
                        'client_id': record['client_id'],
                        'resource': ENDPOINT,
                    })
                if response.status_code != 200:
                    if record.get('codex_credentials_file'):
                        _,latest=credentials(self.path)
                        if latest.get('refresh_token')!=tokens['refresh_token'] and latest.get('access_token'):
                            return latest['access_token']
                    raise AuthenticationRequired('Hyperagent OAuth refresh failed; reconnect')
                updated = token_record(response.json())
                updated.setdefault('refresh_token', tokens['refresh_token'])
                if record.get('codex_credentials_file'):
                    # One canonical credential record, not two competing refresh
                    # token copies. Preserve other Codex server credentials.
                    source=record['codex_credentials_file'];key=record['codex_server_key']
                    latest=read_auth(source);entry=latest.get(key,{})
                    if entry.get('refresh_token')!=tokens['refresh_token']:
                        _,current=credentials(self.path)
                        return current['access_token']
                    entry.update(access_token=updated['access_token'],refresh_token=updated.get('refresh_token'),
                        expires_at=int(updated['expires_at']*1000) if updated['expires_at'] is not None else None,
                        scopes=updated.get('scope',tokens['scope']).split())
                    latest[key]=entry;write_auth(source,latest)
                else:
                    record['tokens'] = updated
                    write_auth(self.path, record)
                tokens = updated
            return tokens['access_token']

    async def async_auth_flow(self, request):
        request.headers['Authorization'] = 'Bearer ' + await self._token()
        response = yield request
        if response.status_code == 401:
            request.headers['Authorization'] = 'Bearer ' + await self._token(refresh=True)
            yield request


def decode_result(result):
    if result.isError:
        raise RemoteRejected('Hyperagent rejected the research operation; inspect its thread or access settings')
    value = result.structuredContent
    if value is None:
        parts = [part.text for part in result.content if part.type == 'text']
        if len(parts) != 1:
            raise HyperagentError('Unexpected Hyperagent MCP result format')
        try:
            value = json.loads(parts[0])
        except (ValueError, TypeError):
            raise HyperagentError('Hyperagent MCP result is not structured JSON') from None
    if not isinstance(value, dict):
        raise HyperagentError('Hyperagent MCP result must be an object')
    return value


class HyperagentTransport:
    def __init__(self, auth_file, agent_id=''):
        self.auth_file = Path(auth_file)
        self.agent_id = agent_id or read_auth(auth_file).get('agent_id', '')
        self.auth = ResearchAuth(auth_file)
        self.schemas = {}

    @property
    def ready(self):
        record=read_auth(self.auth_file)
        self.agent_id=self.agent_id or record.get('agent_id','')
        try:_,tokens=credentials(self.auth_file)
        except AuthenticationRequired:return False
        return bool(self.agent_id and tokens.get('access_token'))

    async def call(self, name, arguments=None):
        # This is deliberately a small host allowlist, not a public tool proxy.
        if name not in {'list_agents', 'create_thread', 'get_thread', 'send_message'}:
            raise PermissionError('Unsupported Hyperagent research operation')
        async with streamablehttp_client(ENDPOINT, auth=self.auth, timeout=30, sse_read_timeout=40) as streams:
            async with ClientSession(streams[0], streams[1]) as session:
                await session.initialize()
                if not self.schemas:
                    response = await session.list_tools()
                    self.schemas = {tool.name: tool.inputSchema for tool in response.tools}
                if name not in self.schemas:
                    raise HyperagentError('Required Hyperagent MCP research tool is unavailable')
                return decode_result(await session.call_tool(name, arguments or {}))

    def arguments(self, tool, **values):
        """Match the advertised schema, failing before submission on mismatch."""
        schema = self.schemas.get(tool) or {}
        properties = schema.get('properties') or {}
        aliases = {'agent': ('agentId', 'agent_id'), 'thread': ('threadId', 'thread_id'),
                   'message': ('message', 'content', 'prompt')}
        result = {}
        for logical, value in values.items():
            names = [name for name in aliases[logical] if name in properties]
            if len(names) != 1:
                raise HyperagentError('Unsupported Hyperagent MCP input schema: ' + tool)
            result[names[0]] = value
        if set(schema.get('required', [])) - result.keys():
            raise HyperagentError('Hyperagent requires additional research arguments: ' + tool)
        return result

    async def prepare(self):
        if not self.ready:
            raise AuthenticationRequired('Hyperagent researcher is not connected')
        if not self.schemas:
            await self.call('list_agents')
        # Preflight all schemas before a possibly billable create_thread call.
        self.arguments('create_thread', agent=self.agent_id, message='preflight')
        self.arguments('get_thread', thread='preflight')
        self.arguments('send_message',thread='preflight',message='preflight')

    async def submit(self, message):
        return await self.call('create_thread', self.arguments('create_thread', agent=self.agent_id, message=message))

    async def status(self, thread_id):
        return await self.call('get_thread', self.arguments('get_thread', thread=thread_id))

    async def follow_up(self, thread_id, message):
        return await self.call('send_message', self.arguments('send_message', thread=thread_id, message=message))


async def login(auth_file):
    """Operator CLI login, including SSH's hidden callback-URL prompt."""
    profile=Path(os.environ.get('CODEX_HOME',str(Path.home()/'.codex')))
    argv=['codex','-c','mcp_oauth_credentials_store="file"',
          '-c','mcp_servers.hyperagent.url="'+ENDPOINT+'"',
          'mcp','login','hyperagent','--no-browser','--scopes',SCOPES.replace(' ',',')]
    process=await asyncio.create_subprocess_exec(*argv)
    if await process.wait():raise AuthenticationRequired('Codex Hyperagent OAuth login did not complete')
    source=profile/'.credentials.json'
    rows=read_auth(source)
    matches=[key for key,row in rows.items() if row.get('server_name')=='hyperagent' and row.get('server_url')==ENDPOINT]
    if len(matches)!=1:raise AuthenticationRequired('Expected one authenticated Hyperagent Codex connection')
    record=read_auth(auth_file)
    # Reference the canonical CLI record, rather than copying a rotating grant.
    write_auth(auth_file,{'codex_credentials_file':str(source),'codex_server_key':matches[0],
                         'agent_id':record.get('agent_id','')})
    return await HyperagentTransport(auth_file).call('list_agents')
