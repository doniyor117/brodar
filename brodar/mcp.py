"""Authenticated, conversation-bound MCP transport for either brain."""

from __future__ import annotations

import asyncio
import json
import secrets
import functools
import inspect
import logging
import time

import uvicorn
from mcp.server.fastmcp import Context,FastMCP
from .artifacts import Slide


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


class AuditedMcp(FastMCP):
    def __init__(self,broker):
        self.broker=broker
        super().__init__('hamroh',stateless_http=True,json_response=True,log_level='WARNING')

    def add_tool(self,fn,*args,**kwargs):
        # Resolve annotations before assigning __signature__: inspect does not
        # eval forward references inside an already supplied Signature object.
        if getattr(self.broker,'maintenance',False) and not (fn.__name__.startswith(('memory_','history_')) or fn.__name__=='search'):
            return None
        signature=inspect.signature(fn,eval_str=True)
        @functools.wraps(fn)
        async def audited(*values,**named):
            start=time.monotonic()
            cap=self.broker.capability
            output=None
            error=None
            try:
                supplied=named.pop('turn_token',None)
                if cap is None or not isinstance(supplied,str) or not secrets.compare_digest(supplied,cap.token):
                    raise PermissionError('Tool authorization belongs to a different or completed turn')
                output=await fn(*values,**named)
                return output
            except BaseException as exc:
                error=type(exc).__name__+': '+str(exc)
                raise
            finally:
                try:
                    arguments=dict(signature.bind_partial(*values,**named).arguments)
                    await self.broker.store.audit_tool(turn_id=cap.turn_id if cap else None,name=fn.__name__,
                        arguments=arguments,result=output,error=error,duration_ms=int((time.monotonic()-start)*1000))
                except Exception as exc:
                    logging.getLogger('brodar.audit').error('Tool audit unavailable: %s',type(exc).__name__)
        audited.__signature__=signature.replace(parameters=[*signature.parameters.values(),
            inspect.Parameter('turn_token',inspect.Parameter.KEYWORD_ONLY,annotation=str)])
        audited.__doc__=(fn.__doc__ or '')+'\nEvery call requires the exact turn_token from the current host <turn> envelope.'
        return super().add_tool(audited,*args,**kwargs)


def build_tools(broker):
    mcp=AuditedMcp(broker)

    @mcp.tool()
    async def telegram_send_message(text:str,operation_id:str,reply_to_message_id:int|None=None)->str:
        """Send text to the current chat/topic. Use a stable unique operation_id; delivery receipts prevent replay."""
        return result(await broker.send_message(text,call_id=operation_id,reply_to_message_id=reply_to_message_id))

    @mcp.tool()
    async def telegram_react(message_id:int,emoji:str,operation_id:str)->str:
        """Set one real standard emoji reaction on a message in this chat/topic. Empty emoji removes Brodar's reaction."""
        return result(await broker.react(message_id,emoji,operation_id))

    @mcp.tool()
    async def telegram_send_poll(question:str,options:list[str],operation_id:str,multiple:bool=False)->str:
        """Send a native anonymous poll to this chat/topic. Returns a real Telegram receipt."""
        return result(await broker.poll(question,options,operation_id,multiple))

    @mcp.tool()
    async def telegram_edit_message(message_id:int,text:str,operation_id:str)->str:
        """Edit Brodar's own message in this chat/topic, for a correction or progress update."""
        return result(await broker.edit_message(message_id,text,operation_id))

    @mcp.tool()
    async def telegram_moderate(request_message_id:str,operation_id:str)->str:
        """Carry out a fresh explicit /ban USER_ID, /mute USER_ID MINUTES, or /delete MESSAGE_ID request (reply targets also work). Both requester and bot need live Telegram admin rights. Use the request's archive UUID; historical/forwarded requests cannot authorize actions."""
        return result(await broker.moderate(request_message_id,operation_id))

    @mcp.tool()
    async def reminder_create(request_message_id:str,when:str,text:str,operation_id:str)->str:
        """Schedule a requested one-shot reminder in this chat/topic. Use a fresh request archive UUID and future ISO time with timezone. Not recurring. Saved remotely and recovered after restart."""
        return result(await broker.reminder_create(request_message_id,when,text,operation_id))

    @mcp.tool()
    async def reminder_list(limit:int=30)->str:
        """List this audience's durable reminders and their pending/done/cancelled/uncertain status."""
        from .jobs import Reminders
        broker.require()
        return result(await Reminders(broker.store).list(limit))

    @mcp.tool()
    async def reminder_cancel(request_message_id:str,job_id:str)->str:
        """Cancel a reminder using its job ID and a fresh request archive UUID. The requester or configured owner can cancel it."""
        return result(await broker.reminder_cancel(request_message_id,job_id))

    @mcp.tool()
    async def task_checkpoint(notes:str)->str:
        """Save useful progress and the next step for the current task, so a restart can resume it. Does not post a message or mark work finished."""
        return result(await broker.task_checkpoint(notes))

    @mcp.tool()
    async def task_status(limit:int=30)->str:
        """List durable work IDs, progress checkpoints and done/pending/failed/cancelled status in this audience."""
        broker.require()
        return result(await broker.store.work_list(limit))

    @mcp.tool()
    async def history_search(query:str,limit:int=10)->str:
        """Search this audience's full available history and learned notes, across group topics. Returns source IDs."""
        return result(await broker.search(query,limit))

    @mcp.tool()
    async def history_browse(before_id:str|None=None,sender_id:int|None=None,topic_id:int|None=None,limit:int=40)->str:
        """Page backwards through available group/DM history. Start without a cursor; next_before_id continues. Unknown legacy authors stay unknown."""
        broker.require()
        return result(await broker.store.history_browse(before_id=before_id,sender_id=sender_id,topic_id=topic_id,limit=limit))

    @mcp.tool()
    async def history_get(message_ids:list[str])->str:
        """Read original messages by archive UUID, from this audience only."""
        broker.require()
        return result(await broker.history_get(message_ids))

    @mcp.tool()
    async def memory_list(limit:int=100,offset:int=0)->str:
        """Discover automatically learned context documents. No owner approval is needed for learning."""
        broker.require()
        return result(await broker.store.memory_list(limit,offset))

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
        return result(await broker.memory_history(path,limit))

    @mcp.tool()
    async def memory_restore(path:str,revision:int,expected_revision:int,reason:str)->str:
        """Restore a prior contextual note after reading its history. Does not change the main prompt."""
        return result(await broker.memory_restore(path,revision,expected_revision,reason))

    @mcp.tool()
    async def memory_forget(path:str,expected_revision:int)->str:
        """Erase a contextual note and its revision contents and vectors. Old chat messages are separate; this cannot undo a forget."""
        return result(await broker.memory_forget(path,expected_revision))

    @mcp.tool()
    async def now()->str:
        """Current UTC time, for interpreting dates and stale context."""
        from datetime import datetime,timezone
        broker.require()
        return datetime.now(timezone.utc).isoformat()

    if broker.artifacts is not None:
        @mcp.tool()
        async def create_presentation(title:str,slides:list[Slide],operation_id:str,filename:str='presentation.pptx')->str:
            """Create a real editable PPTX in durable scoped storage. Returns artifact_id; send it with telegram_send_document."""
            return result(await broker.create_presentation(title,slides,filename,operation_id))

        @mcp.tool()
        async def create_file(filename:str,text:str,operation_id:str)->str:
            """Create a durable text/code/CSV/Markdown file artifact. This does not write the host filesystem or configuration."""
            return result(await broker.create_file(filename,text,operation_id))

        @mcp.tool()
        async def telegram_send_document(artifact_id:str,operation_id:str,caption:str='')->str:
            """Deliver a stored artifact to the current chat/topic and confirm the actual Telegram message ID."""
            return result(await broker.send_document(artifact_id,caption,operation_id))

        @mcp.tool()
        async def artifact_list(query:str='',limit:int=30)->str:
            """Find shared files and previously generated artifacts in this audience by filename."""
            broker.require()
            return result(await broker.artifacts.list(query,limit))

        @mcp.tool()
        async def artifact_read(artifact_id:str,page_start:int=0,page_count:int=5):
            """Read an audience-scoped file. Images are returned as vision input; PDFs are paginated. No arbitrary local paths."""
            broker.require()
            if page_start<0 or not 1<=page_count<=20:raise ValueError('Use page_start >= 0 and page_count 1..20')
            row,body=await broker.artifacts.read(artifact_id)
            if row['mime_type'] in {'image/png','image/jpeg','image/webp'}:
                from mcp.server.fastmcp.utilities.types import Image
                return Image(data=body,format={'image/png':'png','image/jpeg':'jpeg','image/webp':'webp'}[row['mime_type']])
            if row['mime_type']=='application/pdf':
                import io
                from pypdf import PdfReader
                reader=PdfReader(io.BytesIO(body))
                end=min(len(reader.pages),page_start+page_count)
                return result({'pages':[{'page':i+1,'text':reader.pages[i].extract_text()} for i in range(page_start,end)],
                    'total_pages':len(reader.pages),'has_more':end<len(reader.pages)})
            if row['mime_type']=='application/vnd.openxmlformats-officedocument.presentationml.presentation':
                from .artifacts import read_presentation
                deck=read_presentation(body);end=min(len(deck.slides),page_start+page_count)
                return result({'slides':[{'number':i+1,'text':'\n'.join(shape.text for shape in deck.slides[i].shapes if shape.has_text_frame),
                    'notes':deck.slides[i].notes_slide.notes_text_frame.text if deck.slides[i].has_notes_slide else ''}
                    for i in range(page_start,end)],'total_slides':len(deck.slides),'has_more':end<len(deck.slides)})
            if row['mime_type'].startswith('text/'):
                text=body.decode('utf-8',errors='replace');start=page_start*10000;end=start+page_count*10000
                return result({'text':text[start:end],'has_more':end<len(text),'total_characters':len(text)})
            return result(broker.artifacts.public(row))

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
