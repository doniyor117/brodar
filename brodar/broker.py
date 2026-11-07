"""Capabilities and delivery controlled by the host, outside learned text."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass,field
import secrets
import time
import logging
from uuid import UUID

from telegram.error import BadRequest,Forbidden,NetworkError,RetryAfter

from .store import RevisionConflict
from .write_fence import guarded_write,WRITE_FENCE


class DeliveryRejected(RuntimeError):
    """Known rejection can propagate through routed delivery without ambiguity."""


@dataclass(frozen=True)
class TurnCapability:
    turn_id: str
    mode: str = 'conversation'
    requester_id: int | None = None
    request_message_id: int | None = None
    peer_bot_id:int|None=None
    token:str=field(default_factory=lambda:secrets.token_urlsafe(24),repr=False)


class ToolBroker:
    def __init__(self,store,bot,*,embeddings=None,artifacts=None,delivery_guard=None,owner_ids=()):
        self.store,self.bot,self.embeddings=store,bot,embeddings
        self.artifacts=artifacts
        self.approvals=None
        self.loop_guard=None
        self.before_final_delivery=None
        self.delivery_guard=delivery_guard
        self.owner_ids=owner_ids
        self.capability=None
        self.research=None
        self.research_context=()
        self.pending_research=False
        self._reads={}
        self._source_reads=set()
        self._checked_sources={}
        self._memory_changes=[]
        self._delivery_lock=asyncio.Lock()
        self._moderation_requests={}
        self._requests={}

    def begin(self,capability:TurnCapability,*,moderation_requests=(),requests=(),research_context=()):
        if self.capability is not None:raise RuntimeError('Previous capability is still active')
        UUID(capability.turn_id)
        self.capability=capability
        self.research_context=tuple(research_context)
        self.pending_research=False
        self._turn_started=time.monotonic()
        self._first_delivery=False
        self._reads.clear()
        self._source_reads.clear()
        self._checked_sources.clear()
        self._memory_changes.clear()
        self.accept_moderation_requests(moderation_requests)
        self.accept_requests(requests)

    def accept_requests(self,requests):
        for request in requests:self._requests[request.id]=request

    def revoke_request_ids(self,ids):
        for uid in ids:
            self._requests.pop(str(uid),None)
            self._moderation_requests.pop(str(uid),None)

    def accept_moderation_requests(self,requests):
        for request in requests:self._moderation_requests[request.message_id]=request

    def end(self):
        self.capability=None
        self.research_context=()
        self.pending_research=False
        self._reads.clear()
        self._source_reads.clear()
        self._checked_sources.clear()
        self._memory_changes.clear()
        self._moderation_requests.clear()
        self._requests.clear()

    def fresh_request(self,uid):
        self.require(visible=True)
        request=self._requests.get(uid)
        if request is None:raise PermissionError('Use an actual fresh request archive ID from this turn')
        previous=WRITE_FENCE.get()
        if previous:
            def current_request():
                previous()
                if self._requests.get(uid) is not request:raise PermissionError('The authorizing request was edited or withdrawn')
            WRITE_FENCE.set(current_request)  # enclosing guarded_write resets the original context token
        return request

    @guarded_write
    async def reminder_create(self,request_id,when,text,operation_id):
        import re
        from .jobs import Reminders
        request=self.fresh_request(request_id)
        if not re.search(r'remind|timer|alarm|eslat|taymer|напом|таймер|будильник',request.text,re.I):
            raise PermissionError('The fresh request must explicitly ask for a reminder/timer')
        return await Reminders(self.store).create(request,when,text,operation_id)

    @guarded_write
    async def reminder_cancel(self,request_id,job_id):
        import re
        from .jobs import Reminders
        request=self.fresh_request(request_id)
        cancel=re.search(r'cancel|stop|delete|remove|bekor|отмен|удал',request.text,re.I)
        reference=job_id in request.text or re.search(r'remind|timer|alarm|eslat|taymer|напом|таймер|будильник',request.text,re.I)
        if not cancel or not reference:raise PermissionError('The fresh request must ask to cancel this reminder')
        return await Reminders(self.store).cancel(job_id,request.actor,self.owner_ids)

    @guarded_write
    async def task_checkpoint(self,notes):
        cap=self.require(visible=True)
        return await self.store.work_checkpoint(cap.turn_id,notes)

    async def moderate(self,request_message_id,operation_id):
        from .moderation import authorize,perform
        cap=self.require(visible=True)
        request=self._moderation_requests.get(request_message_id)
        if request is None:raise PermissionError('No fresh explicit moderation request with this archive ID')
        if request.action=='delete':await self.message_target(request.target)
        await authorize(self.bot,self.store.scope,request)
        async def current_request():
            if self._moderation_requests.get(request_message_id) is not request:
                raise PermissionError('The moderation request was edited or withdrawn')
        await current_request()
        async def send():return await perform(self.bot,self.store.scope,request)
        return await self.deliver(cap,'moderate',{'request_message_id':request_message_id,
            'actor_id':request.actor_id,'action':request.action,'target':request.target,'minutes':request.minutes},operation_id,send,preflight=current_request)

    def require(self,*,visible=False):
        cap=self.capability
        if cap is None:raise PermissionError('No active turn')
        if visible and cap.mode!='conversation':
            raise PermissionError('Reflection can update context, not post in chat')
        return cap

    async def message_target(self,message_id,*,own=False):
        def op():
            with self.store.db.transaction(self.store.scope.audience,readonly=True) as c:
                c.execute('SELECT sender_id,role FROM brodar_v2.messages WHERE audience=%s AND topic_id=%s '
                          'AND platform_message_id=%s',(self.store.scope.audience,self.store.scope.topic_id,message_id))
                row=c.fetchone()
                if row is None:raise ValueError('Message target is not in this conversation')
                if own and (row[0]!=self.store.scope.bot_id or row[1]!='assistant'):
                    raise PermissionError('Only Brodar’s own messages can be edited')
        await self.store.db.run(op)

    async def react(self,message_id,emoji,operation_id):
        cap=self.require(visible=True)
        if len(emoji)>16:raise ValueError('Use one standard reaction emoji, or empty text to remove it')
        await self.message_target(message_id)
        async def send():return await self.bot.set_message_reaction(chat_id=self.store.scope.chat_id,
            message_id=message_id,reaction=emoji or [])
        return await self.deliver(cap,'set_reaction',{'message_id':message_id,'emoji':emoji},operation_id,send)

    async def poll(self,question,options,operation_id,multiple=False):
        cap=self.require(visible=True)
        if not 1<=len(question)<=300 or not 1<=len(options)<=12 or any(not 1<=len(text)<=100 for text in options):
            raise ValueError('Use a question of 1..300 characters and 1..12 options of 1..100 characters')
        kwargs={'chat_id':self.store.scope.chat_id,'question':question,'options':options,
                'is_anonymous':True,'allows_multiple_answers':multiple}
        if self.store.scope.topic_id:kwargs['message_thread_id']=self.store.scope.topic_id
        async def send():return await self.bot.send_poll(**kwargs)
        return await self.deliver(cap,'send_poll',{'question':question,'options':options,'multiple':multiple},operation_id,send)

    async def edit_message(self,message_id,text,operation_id):
        cap=self.require(visible=True)
        from .redaction import scrub
        text=scrub(text)
        if text.strip().upper()=='[SILENT]':raise ValueError('Use the internal skip outcome; never send a silence token to Telegram')
        if not text.strip() or len(text)>4096:raise ValueError('Use 1..4096 characters')
        await self.message_target(message_id,own=True)
        async def send():return await self.bot.edit_message_text(chat_id=self.store.scope.chat_id,message_id=message_id,text=text)
        return await self.deliver(cap,'edit_message',{'message_id':message_id,'text':text},operation_id,send)

    async def send_message(self,text:str,*,call_id:str,reply_to_message_id:int|None=None,final:bool=True):
        cap=self.require(visible=True)
        from .redaction import scrub
        text=scrub(text)
        if text.strip().upper()=='[SILENT]':raise ValueError('Use the internal skip outcome; never send a silence token to Telegram')
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
        if self.loop_guard:
            import re
            from .routing import peer_key
            for username in set(re.findall(r'@([A-Za-z0-9_]*bot)\b',text,re.I)):
                if username.lower()==(getattr(self.bot,'username','') or '').lower():continue
                recipient=peer_key({'username':username},username)
                if recipient!=cap.peer_bot_id:
                    await self.loop_guard.reserve(recipient,self.store.scope.chat_id,f'mention-peer:{recipient}:{cap.turn_id}:{call_id}:{self.store.scope.chat_id}')
        kwargs={'chat_id':self.store.scope.chat_id,'text':text}
        if self.store.scope.topic_id:kwargs['message_thread_id']=self.store.scope.topic_id
        if reply_to_message_id is not None:kwargs['reply_to_message_id']=reply_to_message_id
        async def send():return await self.bot.send_message(**kwargs)
        return await self.deliver(cap,'send_message',{'text':text,'reply_to_message_id':reply_to_message_id},call_id,send,final=final)

    async def deliver(self,cap,method,request,call_id,send,*,preflight=None,final=False):
        if not call_id or len(call_id)>200:raise ValueError('Use an operation ID of 1 to 200 characters')
        from .redaction import scrub_object
        if scrub_object(request)!=request:raise PermissionError('Credential-like content cannot be delivered or stored in a receipt')
        async with self._delivery_lock:
            if self.capability is not cap:raise PermissionError('Turn was cancelled')
            if self.loop_guard and cap.peer_bot_id:
                await self.loop_guard.reserve(cap.peer_bot_id,self.store.scope.chat_id,f'{cap.turn_id}:{call_id}:{method}:{self.store.scope.chat_id}')
            receipt=await self.store.delivery_begin(cap.turn_id,call_id,method,request)
            if self.capability is not cap:
                if receipt['status']=='pending':
                    await self.store.delivery_finish(receipt['id'],'failed',{'error':'cancelled_before_send'})
                raise PermissionError('Turn was cancelled before delivery')
            if receipt['request']!=request or receipt['method']!=method:
                raise ValueError('Operation ID was already used for a different delivery')
            if receipt['status']=='sent':
                if final and self.before_final_delivery:await self.before_final_delivery(cap)
                return receipt['response']
            if receipt['status']!='pending':
                raise RuntimeError('Previous delivery is failed, in progress or uncertain; do not blindly replay it')
            if self.delivery_guard or preflight:
                try:
                    if self.delivery_guard:await self.delivery_guard()
                    if preflight:await preflight()
                    if self.capability is not cap:raise PermissionError('Turn was cancelled before delivery')
                except BaseException:
                    await asyncio.shield(self.store.delivery_finish(receipt['id'],'failed',{'error':'delivery_guard_rejected_before_send'}))
                    raise
            try:
                if final and self.before_final_delivery:await self.before_final_delivery(cap)
                sent=await send()
            except (BadRequest,Forbidden,RetryAfter,DeliveryRejected) as exc:
                await self.store.delivery_finish(receipt['id'],'failed',{'error':type(exc).__name__})
                raise DeliveryRejected(f'Telegram rejected delivery ({type(exc).__name__})') from None
            except NetworkError:
                await self.store.delivery_finish(receipt['id'],'uncertain',{'error':'network_outcome_unknown'})
                raise RuntimeError('Telegram delivery outcome is uncertain; do not retry automatically') from None
            except BaseException:
                # Cancellation or process termination can happen after Telegram
                # accepted a request. Persist uncertainty whenever possible.
                await asyncio.shield(self.store.delivery_finish(receipt['id'],'uncertain',{'error':'interrupted_delivery'}))
                raise
            if sent is False:
                await self.store.delivery_finish(receipt['id'],'failed',{'error':'telegram_returned_false'})
                raise RuntimeError('Telegram did not confirm the action')
            result={'delivered':True}
            if (method.startswith('send_') or method in {'copy_message','forward_message'}) and not final:result['final']=False
            if isinstance(sent,(list,tuple)) and sent and all(getattr(item,'message_id',None) for item in sent):
                result['message_ids']=[item.message_id for item in sent]
            elif getattr(sent,'message_id',None):result['message_id']=sent.message_id
            elif sent is not True:
                await self.store.delivery_finish(receipt['id'],'uncertain',{'error':'unexpected_telegram_result'})
                raise RuntimeError('Unexpected Telegram response; delivery outcome is uncertain')
            photos=getattr(sent,'photo',None)
            media=photos[-1] if photos else next((getattr(sent,key,None) for key in ['document','video','animation','audio','voice','video_note','sticker'] if getattr(sent,key,None)),None)
            if media is not None and getattr(media,'file_id',None):result['media_file_id']=media.file_id
            await self.store.delivery_finish(receipt['id'],'sent',result)
            if not self._first_delivery:
                self._first_delivery=True
                logging.getLogger('brodar.metrics').warning('First delivery_ms=%d turn=%s',int((time.monotonic()-self._turn_started)*1000),self.capability.turn_id if self.capability else 'revoked')
            return result

    async def send_document(self,artifact_id,caption,call_id,*,final=True):
        cap=self.require(visible=True)
        if self.artifacts is None:raise RuntimeError('Durable artifact storage is not configured')
        if len(caption)>1024:raise ValueError('Document captions allow at most 1024 characters')
        row,body=await self.artifacts.read(artifact_id)
        from telegram import InputFile
        kwargs={'chat_id':self.store.scope.chat_id,'document':InputFile(body,filename=row['filename']),'caption':caption}
        if self.store.scope.topic_id:kwargs['message_thread_id']=self.store.scope.topic_id
        async def send():return await self.bot.send_document(**kwargs)
        return await self.deliver(cap,'send_document',{'artifact_id':artifact_id,'filename':row['filename'],'caption':caption},call_id,send,final=final)

    @guarded_write
    async def create_presentation(self,title,slides,filename,operation_id):
        cap=self.require(visible=True)
        if self.artifacts is None:raise RuntimeError('Durable artifact storage is not configured')
        if not 1<=len(slides)<=60:raise ValueError('Create 1 to 60 slides per presentation')
        from .artifacts import presentation
        from .store import digest
        import json
        request_hash=digest(json.dumps({'title':title,'filename':filename,'slides':[s.model_dump() for s in slides]},ensure_ascii=False,sort_keys=True))
        body=await asyncio.to_thread(presentation,title,slides)
        if self.capability is not cap:raise PermissionError('Turn was cancelled')
        return await self.artifacts.put(turn_id=cap.turn_id,operation_id=operation_id,filename=filename,
            mime='application/vnd.openxmlformats-officedocument.presentationml.presentation',body=body,
            request_hash=request_hash,metadata={'title':title,'slides':len(slides)})

    @guarded_write
    async def create_file(self,filename,text,operation_id):
        cap=self.require(visible=True)
        if self.artifacts is None:raise RuntimeError('Durable artifact storage is not configured')
        from .redaction import scrub
        text=scrub(text)
        if len(text)>1_000_000:raise ValueError('Text files allow up to 1000000 characters')
        from pathlib import PurePosixPath
        if PurePosixPath(filename).suffix.lower() in {'.pptx','.docx','.xlsx','.pdf','.zip','.png','.jpg','.jpeg','.webp','.mp4'}:
            raise ValueError('This tool creates UTF-8 text; use a real format-specific creator for binary files')
        from .store import digest
        return await self.artifacts.put(turn_id=cap.turn_id,operation_id=operation_id,filename=filename,
            mime='text/plain; charset=utf-8',body=text.encode(),request_hash=digest(filename+'\n'+text))

    async def search(self,query,limit=10,mode="hybrid"):
        self.require()
        if mode not in {"hybrid","lexical","semantic"}:raise ValueError("Use hybrid, lexical or semantic")
        vector=None
        if self.embeddings and mode!="lexical":
            from .embeddings import EmbeddingError
            try:vector=await asyncio.wait_for(self.embeddings.embed(query,query=True),getattr(self,'recall_timeout',2.0))
            except (EmbeddingError,ValueError,asyncio.TimeoutError):pass  # lexical search remains useful during quota/network/size failures
        return await self.store.search(query,vector=vector,limit=limit,mode=mode if vector is not None else "lexical")

    async def history_get(self,ids):
        cap=self.require()
        rows=await self.store.history_get(ids)
        if self.capability is not cap:raise PermissionError('Reading turn ended')
        from .store import digest
        self._checked_sources.update({str(r['id']):digest(r['content']) for r in rows})
        return rows

    async def memory_read(self,path):
        cap=self.require()
        note=await self.store.memory_read(path)
        if self.capability is not cap:raise PermissionError('Reading turn ended')
        self._reads[path]=note['revision'] if note else 0
        if note:self._source_reads.update(note.get('source_ids',[]))
        return note

    @guarded_write
    async def memory_write(self,path,content,expected_revision,reason,source_ids=None,subject_ids=None,confidence='observed'):
        self.require()
        if expected_revision and self._reads.get(path)!=expected_revision:
            raise RevisionConflict('Read this context document in the current turn before editing')
        result=await self.store.memory_write(path,content,expected_revision=expected_revision,reason=reason,
            reviewed_sources=dict(self._checked_sources),source_ids=sorted(set(source_ids or [])|self._source_reads),subject_ids=subject_ids,confidence=confidence)
        self._reads[path]=result['revision']
        self._memory_changes.append({'path':path,'revision':result['revision']})
        return result

    @guarded_write
    async def memory_delete(self,path,expected_revision,reason):
        self.require()
        if self._reads.get(path)!=expected_revision:
            raise RevisionConflict('Read this context document before deleting')
        return await self.store.memory_delete(path,expected_revision=expected_revision,reason=reason)

    async def memory_history(self,path,limit=10):
        self.require()
        rows=await self.store.memory_history(path,limit)
        if rows:self._reads[path]=rows[0]['current_revision']
        return rows

    def require_read(self,path,expected_revision):
        self.require()
        if self._reads.get(path)!=expected_revision:
            raise RevisionConflict('Read the current document or its history before changing it')

    @guarded_write
    async def memory_restore(self,path,revision,expected_revision,reason):
        self.require_read(path,expected_revision)
        result=await self.store.memory_restore(path,revision,expected_revision=expected_revision,reason=reason)
        self._reads[path]=result['revision']
        self._memory_changes.append({'path':path,'revision':result['revision']})
        return result

    @guarded_write
    async def memory_forget(self,path,expected_revision):
        self.require_read(path,expected_revision)
        result=await self.store.memory_forget(path,expected_revision=expected_revision)
        self._reads.pop(path,None)
        return result
