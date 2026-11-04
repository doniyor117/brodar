"""Capabilities and delivery controlled by the host, outside learned text."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from uuid import UUID

from telegram.error import BadRequest,Forbidden,NetworkError,RetryAfter

from .store import RevisionConflict


@dataclass(frozen=True)
class TurnCapability:
    turn_id: str
    mode: str = 'conversation'
    requester_id: int | None = None
    request_message_id: int | None = None


class ToolBroker:
    def __init__(self,store,bot,*,embeddings=None):
        self.store,self.bot,self.embeddings=store,bot,embeddings
        self.capability=None
        self._reads={}
        self._delivery_lock=asyncio.Lock()

    def begin(self,capability:TurnCapability):
        if self.capability is not None:raise RuntimeError('Previous capability is still active')
        UUID(capability.turn_id)
        self.capability=capability
        self._reads.clear()

    def end(self):
        self.capability=None
        self._reads.clear()

    def require(self,*,visible=False):
        cap=self.capability
        if cap is None:raise PermissionError('No active turn')
        if visible and cap.mode!='conversation':
            raise PermissionError('Reflection can update context, not post in chat')
        return cap

    async def send_message(self,text:str,*,call_id:str,reply_to_message_id:int|None=None):
        cap=self.require(visible=True)
        if not text.strip() or len(text)>4096:
            raise ValueError('Send 1 to 4096 characters per message')
        if reply_to_message_id is not None:
            # Replies must reference this chat and topic. Model-selected IDs
            # cannot redirect a reply to another conversation or hidden thread.
            def validate_reply():
                with self.store.db.transaction(self.store.scope.audience,readonly=True) as c:
                    c.execute('SELECT 1 FROM brodar_v2.messages WHERE audience=%s AND topic_id=%s '
                              'AND platform_message_id=%s',
                              (self.store.scope.audience,self.store.scope.topic_id,reply_to_message_id))
                    if c.fetchone() is None:raise ValueError('Reply target is not in this conversation')
            await self.store.db.run(validate_reply)
        async with self._delivery_lock:
            if self.capability is not cap:raise PermissionError('Turn was cancelled')
            request={'text':text,'reply_to_message_id':reply_to_message_id}
            receipt=await self.store.delivery_begin(cap.turn_id,call_id,'send_message',request)
            if receipt['request']!=request or receipt['method']!='send_message':
                raise ValueError('Operation ID was already used for a different delivery')
            if receipt['status']=='sent':return receipt['response']
            if receipt['status']!='pending':
                raise RuntimeError('Previous delivery is failed, in progress or uncertain; do not blindly replay it')
            kwargs={'chat_id':self.store.scope.chat_id,'text':text}
            if self.store.scope.topic_id:kwargs['message_thread_id']=self.store.scope.topic_id
            if reply_to_message_id is not None:kwargs['reply_to_message_id']=reply_to_message_id
            try:
                sent=await self.bot.send_message(**kwargs)
            except (BadRequest,Forbidden,RetryAfter) as exc:
                await self.store.delivery_finish(receipt['id'],'failed',{'error':type(exc).__name__})
                raise RuntimeError(f'Telegram rejected delivery ({type(exc).__name__})') from None
            except NetworkError:
                await self.store.delivery_finish(receipt['id'],'uncertain',{'error':'network_outcome_unknown'})
                raise RuntimeError('Telegram delivery outcome is uncertain; do not retry automatically') from None
            except BaseException:
                # Cancellation or process termination can happen after Telegram
                # accepted a request. Persist uncertainty whenever possible.
                await asyncio.shield(self.store.delivery_finish(receipt['id'],'uncertain',{'error':'interrupted_delivery'}))
                raise
            result={'message_id':sent.message_id,'delivered':True}
            await self.store.delivery_finish(receipt['id'],'sent',result)
            return result

    async def search(self,query,limit=10):
        self.require()
        vector=None
        if self.embeddings:
            from .embeddings import EmbeddingError
            try:vector=await self.embeddings.embed(query,query=True)
            except EmbeddingError:pass  # lexical search remains useful during quota/network failures
        return await self.store.search(query,vector=vector,limit=limit)

    async def memory_read(self,path):
        self.require()
        note=await self.store.memory_read(path)
        self._reads[path]=note['revision'] if note else 0
        return note

    async def memory_write(self,path,content,expected_revision,reason,source_ids=None,subject_ids=None,confidence='observed'):
        self.require()
        if expected_revision and self._reads.get(path)!=expected_revision:
            raise RevisionConflict('Read this context document in the current turn before editing')
        result=await self.store.memory_write(path,content,expected_revision=expected_revision,reason=reason,
            source_ids=source_ids,subject_ids=subject_ids,confidence=confidence)
        self._reads[path]=result['revision']
        return result

    async def memory_delete(self,path,expected_revision,reason):
        self.require()
        if self._reads.get(path)!=expected_revision:
            raise RevisionConflict('Read this context document before deleting')
        return await self.store.memory_delete(path,expected_revision=expected_revision,reason=reason)
