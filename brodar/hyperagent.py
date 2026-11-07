"""Host-only Hyperagent MCP connection and dedicated OAuth login.

No remote account tools or OAuth material are exposed to the conversational
model. Runtime authentication never launches an interactive browser flow.
"""
from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
import time
from pathlib import Path
from urllib.parse import urlencode, urlsplit, parse_qs

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


class ResearchAuth(httpx.Auth):
    def __init__(self, path):
        self.path = Path(path)
        self.lock = asyncio.Lock()

    async def _token(self, *, refresh=False):
        async with self.lock:
            record = read_auth(self.path)
            tokens = record.get('tokens') or {}
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
                    raise AuthenticationRequired('Hyperagent OAuth refresh failed; reconnect')
                updated = token_record(response.json())
                updated.setdefault('refresh_token', tokens['refresh_token'])
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
        return bool(self.agent_id and (record.get('tokens') or {}).get('access_token'))

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

    async def submit(self, message):
        return await self.call('create_thread', self.arguments('create_thread', agent=self.agent_id, message=message))

    async def status(self, thread_id):
        return await self.call('get_thread', self.arguments('get_thread', thread=thread_id))

    async def follow_up(self, thread_id, message):
        return await self.call('send_message', self.arguments('send_message', thread=thread_id, message=message))


async def login(auth_file):
    """Operator-only loopback OAuth; print the authorization URL, never tokens."""
    callback = asyncio.get_running_loop().create_future()

    async def receive(reader, writer):
        try:
            line = await asyncio.wait_for(reader.readline(), 5)
            parts = line.decode('ascii', errors='replace').split()
            url = urlsplit(parts[1]) if len(parts) >= 2 else None
            query = parse_qs(url.query) if url else {}
            if url and url.path == '/callback' and secrets.compare_digest(query.get('state',[''])[0],state) and ('code' in query or 'error' in query):
                if not callback.done(): callback.set_result(query)
                body = b'Authorization received. You can return to Brodar.'
                status = b'200 OK'
            else:
                body = b'Not found'
                status = b'404 Not Found'
            writer.write(b'HTTP/1.1 ' + status + b'\r\nContent-Type: text/plain\r\nConnection: close\r\n\r\n' + body)
            await writer.drain()
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(receive, '127.0.0.1', 0)
    port = server.sockets[0].getsockname()[1]
    redirect_uri = f'http://127.0.0.1:{port}/callback'
    verifier = secrets.token_urlsafe(48)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode().rstrip('=')
    state = secrets.token_urlsafe(32)
    try:
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as client:
            response = await client.post(BASE + '/api/oauth/register', json={
                'client_name': 'Brodar Research', 'redirect_uris': [redirect_uri],
                'token_endpoint_auth_method': 'none', 'grant_types': ['authorization_code', 'refresh_token'],
                'response_types': ['code'], 'scope': SCOPES,
            })
            if response.status_code not in {200, 201}:
                raise AuthenticationRequired('Hyperagent OAuth client registration failed')
            client_id = response.json()['client_id']
            query = urlencode({'response_type': 'code', 'client_id': client_id,
                'redirect_uri': redirect_uri, 'scope': SCOPES, 'state': state,
                'code_challenge': challenge, 'code_challenge_method': 'S256', 'resource': ENDPOINT})
            print('OPEN_BROWSER ' + BASE + '/api/oauth/authorize?' + query, flush=True)
            result = await asyncio.wait_for(callback, 900)
            if not secrets.compare_digest(result.get('state', [''])[0], state) or 'code' not in result:
                raise AuthenticationRequired('Hyperagent authorization denied or state mismatch')
            if result.get('iss', [BASE])[0] != BASE:
                raise AuthenticationRequired('Unexpected OAuth authorization issuer')
            response = await client.post(BASE + '/api/oauth/token', data={
                'grant_type': 'authorization_code', 'client_id': client_id,
                'code': result['code'][0], 'redirect_uri': redirect_uri,
                'code_verifier': verifier, 'resource': ENDPOINT,
            })
            if response.status_code != 200:
                raise AuthenticationRequired('Hyperagent OAuth code exchange failed')
            write_auth(auth_file, {'client_id': client_id, 'tokens': token_record(response.json())})
        transport = HyperagentTransport(auth_file)
        agents = await transport.call('list_agents')
        print('Hyperagent OAuth connected.', flush=True)
        return agents
    finally:
        server.close()
        await server.wait_closed()
