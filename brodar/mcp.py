"""Authenticated, conversation-bound MCP transport for either brain."""

from __future__ import annotations

import asyncio
import json
import secrets

import uvicorn
from mcp.server.fastmcp import Context,FastMCP


class BearerAuth:
    def __init__(self,app,token):self.app,self.token=app,token

    async def __call__(self,scope,receive,send):
        if scope['type']=='http':
            headers=dict(scope.get('headers',[]))
            if not secrets.compare_digest(headers.get(b'authorization',b''),('Bearer '+self.token).encode()):
                await send({'type':'http.response.start','status':401,'headers':[(b'content-type',b'text/plain')]})
                await send({'type':'http.response.body','body':b'Unauthorized'})
                return
        await self.app(scope,receive,send)


def result(value):return json.dumps(value,default=str,ensure_ascii=False)


def build_tools(broker):
    mcp=FastMCP('hamroh',stateless_http=True,json_response=True,log_level='WARNING')

    @mcp.tool()
    async def telegram_send_message(text:str,operation_id:str,reply_to_message_id:int|None=None)->str:
        """Send text to the current chat/topic. Use a stable unique operation_id; delivery receipts prevent replay."""
        return result(await broker.send_message(text,call_id=operation_id,reply_to_message_id=reply_to_message_id))

    @mcp.tool()
    async def history_search(query:str,limit:int=10)->str:
        """Search this audience's full available history and learned notes, across group topics. Returns source IDs."""
        return result(await broker.search(query,limit))

    @mcp.tool()
    async def history_get(message_ids:list[str])->str:
        """Read original messages by archive UUID, from this audience only."""
        broker.require()
        return result(await broker.store.history_get(message_ids))

    @mcp.tool()
    async def memory_list(limit:int=100)->str:
        """Discover automatically learned context documents. No owner approval is needed for learning."""
        broker.require()
        return result(await broker.store.memory_list(limit))

    @mcp.tool()
    async def memory_read(path:str)->str:
        """Read a context/*.md document and its current revision before editing it."""
        return result(await broker.memory_read(path))

    @mcp.tool()
    async def memory_write(path:str,content:str,expected_revision:int,reason:str,
                           source_ids:list[str]|None=None,subject_ids:list[int]|None=None,
                           confidence:str='observed')->str:
        """Create/update supplementary context freely. New path: revision 0. Cite source message UUIDs where available."""
        return result(await broker.memory_write(path,content,expected_revision,reason,source_ids,subject_ids,confidence))

    @mcp.tool()
    async def memory_delete(path:str,expected_revision:int,reason:str)->str:
        """Retire an obsolete context document after reading it. Revision history is retained for undo."""
        return result(await broker.memory_delete(path,expected_revision,reason))

    @mcp.tool()
    async def memory_history(path:str,limit:int=10)->str:
        """Inspect prior revisions and the current revision, including retired documents, for correction or undo."""
        broker.require()
        rows=await broker.store.memory_history(path,limit)
        if rows:broker._reads[path]=rows[0]['current_revision']
        return result(rows)

    @mcp.tool()
    async def memory_restore(path:str,revision:int,expected_revision:int,reason:str)->str:
        """Restore a prior contextual note after reading its history. Does not change the main prompt."""
        broker.require()
        if broker._reads.get(path)!=expected_revision:raise ValueError('Read memory history before restoring')
        return result(await broker.store.memory_restore(path,revision,expected_revision=expected_revision,reason=reason))

    @mcp.tool()
    async def memory_forget(path:str,expected_revision:int)->str:
        """Erase a contextual note and its revision contents and vectors. Old chat messages are separate; this cannot undo a forget."""
        broker.require()
        if broker._reads.get(path)!=expected_revision:raise ValueError('Read context or history before forgetting')
        return result(await broker.store.memory_forget(path,expected_revision=expected_revision))

    @mcp.tool()
    async def now()->str:
        """Current UTC time, for interpreting dates and stale context."""
        from datetime import datetime,timezone
        broker.require()
        return datetime.now(timezone.utc).isoformat()

    return mcp


class ScopedMcpServer:
    def __init__(self,broker):
        self.broker=broker
        self.token=secrets.token_urlsafe(40)
        self.mcp=build_tools(broker)
        self.server=None
        self.task=None
        self.port=None

    @property
    def url(self):
        if self.port is None:raise RuntimeError('MCP server is not started')
        return f'http://127.0.0.1:{self.port}/mcp'

    async def start(self):
        app=BearerAuth(self.mcp.streamable_http_app(),self.token)
        self.server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=0,
                                  access_log=False,log_level='warning',lifespan='on'))
        self.task=asyncio.create_task(self.server.serve())
        for _ in range(500):
            if self.task.done():
                await self.task
                raise RuntimeError('MCP server stopped during startup')
            if self.server.started and self.server.servers:
                self.port=self.server.servers[0].sockets[0].getsockname()[1]
                return
            await asyncio.sleep(.01)
        await self.close()
        raise RuntimeError('MCP startup timed out')

    async def close(self):
        if self.server:self.server.should_exit=True
        if self.task:
            try:await asyncio.wait_for(self.task,5)
            except asyncio.TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task,return_exceptions=True)
        self.port=None
