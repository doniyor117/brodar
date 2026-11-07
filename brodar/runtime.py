"""Telegram coordinator shared by the Claude and Codex brains."""

from __future__ import annotations

import asyncio
import dataclasses
import hashlib
import json
import logging
import os
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import NAMESPACE_URL,uuid5

from telegram import Bot
from telegram.error import NetworkError,Conflict

from .brains.codex import CodexBrain
from .broker import ToolBroker,TurnCapability
from .embeddings import GeminiEmbeddings,EmbeddingError
from .mcp import ScopedMcpServer
from .postgres import Postgres
from .prompts import compose,envelope,authorized
from .prompt_files import load,render
from .runtime_store import RuntimeStore
from .learning import LearningStore,chunks,bounded_notes
from .scope import Scope
from .objects import ObjectStore
from .artifacts import Artifacts
from .recovery import ConsumerLease
from .telegram_context import ordinary_message,public_payload,describe_message,addressed
from .presence import TypingPresence
from .moderation import requests as moderation_requests
from .jobs import Reminders,input_requests,dispatch_reminder

log=logging.getLogger('brodar')


def undelivered_artifacts(artifacts,receipts):
    delivered=set()
    for row in receipts:
        if row['status']!='sent' or row['method'] not in {'send_document','routed_document','routed_media','send_photo','send_video','send_animation','send_audio','send_voice','send_video_note','send_sticker','send_media_group'}:continue
        request=row.get('request') or {}
        if request.get('artifact_id'):delivered.add(str(request['artifact_id']))
        delivered.update(str(item['artifact_id']) for item in request.get('items',[]) if item.get('artifact_id'))
    return [str(row['artifact_id']) for row in artifacts if str(row['artifact_id']) not in delivered]


def origin_report_needed(receipts,scope):
    """A destination delivery is not a completion report to the requester.

    Require feedback after the latest remote action, not an earlier 'on it'.
    Topic identity matters because topics share a database audience.
    """
    remote=[]
    for index,row in enumerate(receipts):
        request=row.get('request') or {}
        routed=row.get('method') in {'routed_message','routed_document','routed_media'}
        elsewhere=(routed and (request.get('chat_id'),request.get('topic_id',0))!=(scope.chat_id,scope.topic_id)) or row.get('topic_id',scope.topic_id)!=scope.topic_id
        if elsewhere and row['status'] in {'sent','failed','sending','uncertain'}:remote.append(index)
    if not remote:return False
    for row in receipts[max(remote)+1:]:
        if (row['status']=='sent' and row.get('topic_id',scope.topic_id)==scope.topic_id
                and (row.get('method') in {'send_message','send_document','edit_message','edit_message_caption'} or
                     (row.get('method') in {'send_photo','send_audio','send_video','send_voice','send_animation','send_media_group'} and
                      bool((row.get('request') or {}).get('caption') or any(item.get('caption') for item in (row.get('request') or {}).get('items',[])))))
                and (row.get('response') or {}).get('final',True)):
            return False
    return True


def research_feedback_needed(broker,receipts):
    if not broker.research_context or broker.pending_research:return False
    return not any(row['status']=='sent' and (row.get('method','').startswith('send_') or row.get('method') in {'edit_message','edit_message_caption','copy_message','forward_message'})
                   and row.get('topic_id',broker.store.scope.topic_id)==broker.store.scope.topic_id
                   and (row.get('response') or {}).get('final',True) for row in receipts)


def private_write(path:Path,text:str):
    path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    temp=path.with_suffix(path.suffix+'.new')
    fd=os.open(temp,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
    with os.fdopen(fd,'w') as f:f.write(text)
    temp.replace(path)
    path.chmod(0o600)


def claude_worker_config(root):
    return SimpleNamespace(session_id_path=root/'session_id',liveness_timeout_seconds=600,
        liveness_poll_seconds=30,tool_error_max_count=10,tool_error_window_seconds=600,
        crash_backoff_base=2,crash_backoff_cap=64,crash_limit=10,crash_window_seconds=600)


class Conversation:
    def __init__(self,cfg,store,bot,embeddings,semaphore,objects=None,delivery_guard=None,presence=None):
        self.cfg,self.store,self.bot,self.embeddings=cfg,store,bot,embeddings
        self.semaphore=semaphore
        self.presence=presence
        self._saved_session=None
        self.broker=ToolBroker(store,bot,embeddings=embeddings,artifacts=Artifacts(store,objects) if objects else None,
                               delivery_guard=delivery_guard,owner_ids=getattr(cfg,'owner_ids',()))
        self.broker.before_final_delivery=self._stop_typing
        self.broker.recall_timeout=getattr(cfg,'recall_timeout_seconds',2.0)
        self.mcp=ScopedMcpServer(self.broker)
        self.shared_group=None
        self.brain=None
        self.revision=None
        self.wake=asyncio.Event()
        self.direct=asyncio.Event()
        self.task=None
        self.inflight=[]
        self._steer_lock=asyncio.Lock()
        self._turn_lock=asyncio.Lock()
        self._accept_steer=False
        self._signal_task=None
        self._presence_generation=None
        self._ambient_first=None
        self._ambient_last=None
        self._last_ambient=0
        self._cancelled=False
        self._turn_id=None
        key=hashlib.sha256(store.scope.conversation.encode()).hexdigest()[:24]
        self.root=cfg.runtime_dir/'providers'/cfg.brain/key
        from .commands import CommandGateway
        self.broker.commands=CommandGateway(cfg.runtime_dir/'workspaces'/key)

    async def _stop_typing(self,cap=None):
        if self.presence and (cap is None or self.broker.capability is cap):
            await self.presence.stop(self.store.scope,generation=self._presence_generation)

    async def start(self, *, maintenance=False,resource=False):
        if self.shared_group is not None and not maintenance and not resource:
            self.brain=await self.shared_group.bind(self)
            self.revision=self.shared_group.resource.revision
            self._saved_session=self.brain.session_id
            self.task=asyncio.create_task(self.loop(),name='brodar-group-room')
            self.wake.set()
            return
        if maintenance:
            from .mcp import build_tools
            self.root=self.root/'maintenance'
            self.broker.maintenance=True
            self.mcp.mcp=build_tools(self.broker)
        await self.mcp.start()
        tools=['mcp__hamroh__'+t.name for t in await self.mcp.mcp.list_tools()]
        prompt,self.revision=compose(model=self.cfg.model,provider=self.cfg.brain,tools=tools,timezone=self.cfg.timezone)
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.cfg.brain=='codex':
            # Share one dedicated CLI auth store so concurrent conversations do
            # not race independent copies of a rotating OAuth refresh token.
            # Scope-specific MCP endpoints are process argv, never shared config.
            self.brain=CodexBrain(home=self.cfg.runtime_dir/'codex',cwd=self.root/'work',model=self.cfg.model,
                effort=self.cfg.effort,instructions=prompt,auto_compact_token_limit=getattr(self.cfg,'auto_compact_token_limit',None),env={'BRODAR_MCP_TOKEN':self.mcp.token},mcp_url=self.mcp.url)
        else:
            from .brains.claude import ClaudeBrain
            from hamroh.cc_worker.spec import CcSpawnSpec
            from hamroh.cc_worker.cc_schema import CONTROL_ACTION_SCHEMA
            private_write(self.root/'system.md',prompt)
            private_write(self.root/'terminal.schema.json',json.dumps(CONTROL_ACTION_SCHEMA))
            private_write(self.root/'mcp.json',json.dumps({'mcpServers':{'hamroh':{
                'type':'http','url':self.mcp.url,'alwaysLoad':True,
                'headers':{'Authorization':'Bearer '+self.mcp.token}}}}))
            spec=CcSpawnSpec(binary='claude',model=self.cfg.model,effort=self.cfg.effort,
                system_prompt_path=self.root/'system.md',mcp_config_path=self.root/'mcp.json',
                json_schema_path=self.root/'terminal.schema.json',cc_logs_dir=None,
                hamroh_tool_names=tuple(t.name for t in await self.mcp.mcp.list_tools()))
            self.brain=ClaudeBrain(spec=spec,config=claude_worker_config(self.root),home=self.cfg.runtime_dir/'claude',cwd=self.root/'work')
        session=None if maintenance else await self.store.session_get(self.cfg.brain,self.revision)
        try:
            await self.brain.start(session)
            if self.brain.session_id and not maintenance:await self.store.session_put(self.cfg.brain,self.revision,self.brain.session_id)
            self._saved_session=self.brain.session_id
        except BaseException:
            await self.brain.close()
            await self.mcp.close()
            raise
        if maintenance or resource:return
        self.task=asyncio.create_task(self.loop(),name='brodar-conversation')
        self.wake.set()

    async def signal(self,*,direct=False):
        self.wake.set()
        if not direct:
            now=time.monotonic()
            if self._ambient_first is None:self._ambient_first=now
            self._ambient_last=now
        if direct:self.direct.set()
        if direct and (self._signal_task is None or self._signal_task.done()):
            self._signal_task=asyncio.create_task(self._steer_pending(),name='brodar-steer')
            self._signal_task.add_done_callback(self._signal_done)

    def _signal_done(self,task):
        if not task.cancelled() and task.exception():
            log.warning('Steering deferred: %s',type(task.exception()).__name__)

    async def _steer_pending(self):
        async with self._steer_lock:
            if not self._accept_steer:return
            batch=await self.store.claim_inputs(count_attempt=False,exclude_ids=[r['id'] for r in self.inflight])
            if not batch:return
            def has_media(row):
                message=row['payload'].get('message') or row['payload'].get('edited_message') or {}
                return any(message.get(kind) for kind in ('document','photo','video','animation','audio','voice','video_note','sticker'))
            if any(has_media(row) or row['payload'].get('internal_research') for row in batch):
                await self.store.finish_inputs([r['id'] for r in batch],retry=True)
                return  # materialize media in the next durable foreground turn
            messages=await self.store.history_get([str(r['message_id']) for r in batch])
            cap=self.broker.capability
            if cap is None:
                await self.store.finish_inputs([r['id'] for r in batch],retry=True)
                return
            accepted=await self.brain.steer(authorized(envelope(messages),cap))
            if accepted:
                if self.presence:self._presence_generation=max(self._presence_generation or 0,self.presence.for_inputs(self.store.scope,[r.get('update_id') for r in batch]))
                self.inflight.extend(batch)
                refreshed=await self.store.mark_inputs_attempted([r['id'] for r in batch])
                by_id={r['id']:r for r in refreshed}
                self.inflight=[by_id.get(r['id'],r) for r in self.inflight]
                self.broker.revoke_request_ids([str(row['message_id']) for row in batch])
                self.broker.accept_moderation_requests(moderation_requests(batch,getattr(self.bot,'username',None)))
                self.broker.accept_requests(input_requests(batch))
                await self.store.work_add_inputs(self._turn_id,[str(row['message_id']) for row in batch])
            else:await self.store.finish_inputs([r['id'] for r in batch],retry=True)

    async def cancel(self,requester_id):
        cap=self.broker.capability
        if cap is None:return False
        if requester_id not in self.cfg.owner_ids and requester_id!=cap.requester_id:return False
        self.broker.end()
        self._cancelled=True
        try:await self.brain.interrupt()
        except Exception as exc:log.warning('Interrupt transport failed: %s',type(exc).__name__)
        return True

    async def loop(self):
        while True:
            try:await asyncio.wait_for(self.wake.wait(),30)
            except asyncio.TimeoutError:pass  # expired durable leases must wake without new chat traffic
            self.wake.clear()
            delay=self.cfg.debounce_seconds
            if not self.direct.is_set():
                delay=max(delay,self.cfg.ambient_interval-(time.monotonic()-self._last_ambient))
            if delay>0:
                try:await asyncio.wait_for(self.direct.wait(),timeout=delay)
                except asyncio.TimeoutError:pass
            while self._ambient_first is not None and not self.direct.is_set():
                now=time.monotonic()
                due=min(self._ambient_first+getattr(self.cfg,'ambient_max_wait',60),self._ambient_last+getattr(self.cfg,'ambient_quiet_seconds',8))
                due=max(due,self._last_ambient+self.cfg.ambient_interval)
                if due<=now:break
                try:await asyncio.wait_for(self.direct.wait(),due-now)
                except asyncio.TimeoutError:pass
            self.direct.clear()
            queued=time.monotonic()
            async with self.semaphore:
                log.warning('Foreground semaphore_wait_ms=%d',int((time.monotonic()-queued)*1000))
                try:
                    await self.process()
                except asyncio.CancelledError:raise
                except Exception as exc:
                    log.error('Conversation processing failed: %s',type(exc).__name__)
                    await asyncio.sleep(3)
                    self.wake.set()
            self._last_ambient=time.monotonic()

    async def process(self):
        async with self._turn_lock:await self._process_guarded()

    async def _process_guarded(self):
        self._turn_id=None
        self._presence_generation=0
        renewal=asyncio.create_task(self._renew_inputs())
        try:
            await self._process()
        except Exception:
            self.broker.end()
            if self.inflight:
                try:
                    receipts=await self.store.delivery_receipts(self._turn_id) if self._turn_id else []
                    retry=not self._cancelled and not any(r['status'] in {'sending','uncertain'} for r in receipts) and any(r['attempts']<3 for r in self.inflight)
                    await self.store.finish_inputs([r['id'] for r in self.inflight],error='processing_error',retry=retry,job_id=self._turn_id)
                    if retry:self.wake.set()
                except Exception:
                    pass  # retain the durable lease; the periodic recovery pass will revisit it
            raise
        finally:
            renewal.cancel()
            await asyncio.gather(renewal,return_exceptions=True)
            self.broker.end()
            self.inflight=[]
            if self.presence:await self._stop_typing()

    async def _renew_inputs(self):
        while True:
            await asyncio.sleep(60)
            if self.inflight:
                try:
                    await self.store.renew_inputs([r['id'] for r in self.inflight])
                except Exception:
                    log.warning('Input lease renewal deferred')

    async def _notify_failure(self,receipts):
        waiting=[]
        for row in self.inflight:
            if row['payload'].get('internal_research'):
                waiting.append(row['payload']['internal_research'])
                continue
            msg=row['payload'].get('message') or row['payload'].get('edited_message') or {}
            if addressed(msg,self.store.scope.bot_id,getattr(self.bot,'username',None)):
                waiting.append(msg)
        if not waiting:return
        try:
            if any(r['status'] in {'sending','uncertain'} for r in receipts):
                text="i couldn't confirm the last delivery. i won't resend it automatically"
            elif origin_report_needed(receipts,self.store.scope) and any(r['status']=='sent' and r.get('method') in {'routed_message','routed_document','routed_media'} for r in receipts):
                text="the other-group delivery went through, but i couldn't finish the follow-up here"
            else:text="that failed on my side. send it again and i'll retry"
            await self.broker.send_message(text,call_id='terminal-failure')
        except Exception:
            log.warning('Terminal failure notice could not be delivered')

    async def materialize(self,batch):
        if self.broker.artifacts is None:return
        from .redaction import scrub
        for row in batch:
            payload=row['payload'];message=payload.get('message') or payload.get('edited_message') or {}
            if not addressed(message,self.store.scope.bot_id,getattr(self.bot,'username',None)):
                continue  # archive file references; fetch ambient media only on demand
            document=next((message.get(k) for k in ('document','video','animation','audio','voice','video_note','sticker') if message.get(k)),None);photos=message.get('photo') or []
            media=document or (photos[-1] if photos else None)
            if not media or (media.get('file_size') or 0)>20_000_000:continue
            turn_id=str(uuid5(NAMESPACE_URL,f'inbound:{self.store.scope.bot_id}:{row["update_id"]}'))
            uid=str(uuid5(NAMESPACE_URL,f'{self.store.scope.audience}:{turn_id}:attachment'))
            record=await self.broker.artifacts.get_record(uid)
            if record:artifact=self.broker.artifacts.public(record)
            else:
                file=await self.bot.get_file(media['file_id'])
                body=bytes(await file.download_as_bytearray())
                filename=(document.get('file_name') or ('media-'+media['file_unique_id'] if media.get('file_unique_id') else 'media')) if document else f'photo-{message["message_id"]}.jpg'
                filename=Path(filename.replace('\\','/')).name[:180]
                mime=(document.get('mime_type') or 'application/octet-stream') if document else 'image/jpeg'
                artifact=await self.broker.artifacts.put(turn_id=turn_id,operation_id='attachment',filename=filename,
                    mime=mime,body=body,request_hash=hashlib.sha256(body).hexdigest(),
                    metadata={'source':'telegram','message_id':message['message_id']})
            text=scrub(message.get('text') or message.get('caption') or '')
            text+='\n[attachment: '+artifact['filename']+'; artifact_id: '+artifact['artifact_id']+']'
            await self.store.attach_content(str(row['message_id']),text)

    async def _process(self):
        prep_started=time.monotonic()
        batch=await self.store.claim_inputs()
        if not batch:return
        self._ambient_first=None;self._ambient_last=None
        self.inflight=list(batch)
        if self.presence:
            self._presence_generation=self.presence.for_inputs(self.store.scope,[r.get('update_id') for r in batch])
            if self._presence_generation and self._presence_generation==self.presence.generation(self.store.scope):self.presence.start(self.store.scope)
        self._cancelled=False
        self._turn_id=str(batch[0]['id'])
        payload_message=batch[0]['payload'].get('message') or batch[0]['payload'].get('edited_message') or {}
        sender_meta=payload_message.get('from') or {}
        from .routing import peer_key
        peer=await self.broker.loop_guard.resolve(sender_meta,getattr(self.cfg,'research_bot_username','hyperresearcher_bot')) if sender_meta.get('is_bot') and self.broker.loop_guard else None
        if peer and self.broker.loop_guard and not await self.broker.loop_guard.available(peer):
            await self.store.finish_inputs([r['id'] for r in batch],error='bot_loop_limit');return
        requester=sender_meta.get('id') if not payload_message.get('sender_chat') and not peer else None
        internal=batch[0]['payload'].get('internal_research')
        if internal:requester=internal['requester_id']
        work=await self.store.work_begin(self._turn_id,requester,[str(r['message_id']) for r in batch])
        if work['status'] in {'done','cancelled'}:
            await self.store.finish_inputs([r['id'] for r in batch],error='cancelled' if work['status']=='cancelled' else None)
            return
        self.broker.begin(TurnCapability(self._turn_id,requester_id=requester,peer_bot_id=peer),moderation_requests=moderation_requests(batch,getattr(self.bot,'username',None)),
                          requests=input_requests(batch),research_context=[r['payload']['internal_research']['task_id'] for r in batch if r['payload'].get('internal_research')])
        await self.materialize(batch)
        if self._cancelled:
            await self.store.finish_inputs([r['id'] for r in self.inflight],error='cancelled',job_id=self._turn_id)
            return
        ids=[str(r['message_id']) for r in batch]
        bundle=await self.store.turn_context(ids,self._turn_id,self.embeddings.model if self.embeddings else None)
        messages,context,notes=bundle['messages'],bundle['context'],bundle['notes']
        query='\n'.join(m['content'] for m in messages)[-1800:]
        if getattr(self.cfg,'automatic_recall',False) and self.embeddings and len(query)>=20 and bundle['has_index']:
            try:
                vector=await asyncio.wait_for(self.embeddings.embed(query,query=True),
                    timeout=getattr(self.cfg,'recall_timeout_seconds',2.0))
                recall=await self.store.search(query,vector=vector,limit=6)
                seen={str(m['id']) for m in context+messages}
                for item in recall:
                    if item['kind']=='message' and str(item['id']) not in seen:
                        context.append(item);seen.add(str(item['id']))
                    elif item['kind']=='memory' and not any(str(n['id'])==str(item['id']) for n in notes):
                        notes.append(item)
            except (EmbeddingError,ValueError,asyncio.TimeoutError):pass
        turn_id=self._turn_id
        prior=bundle['receipts']
        if any(r['status'] in {'sending','uncertain'} for r in prior):
            await self._notify_failure(prior)
            await self.store.finish_inputs([r['id'] for r in batch],error='previous_delivery_uncertain',job_id=turn_id)
            return
        payload=envelope(messages,context=context,notes=bounded_notes(notes),receipts=prior)
        artifacts=bundle['artifacts']
        if work['checkpoint'] or artifacts:
            from xml.sax.saxutils import escape
            payload+='\n<task_progress>'+escape(json.dumps({'checkpoint':work['checkpoint'],'artifacts':artifacts},default=str))+'</task_progress>'
        outcome=None
        receipts=prior
        prep_seconds=time.monotonic()-prep_started
        log.warning('Foreground preparation_ms=%d turn=%s',int(prep_seconds*1000),self._turn_id)
        try:
            for continuation in range(4):
                if self._cancelled:break
                self._accept_steer=True
                provider_started=time.monotonic()
                outcome=await asyncio.wait_for(self.brain.turn(authorized(payload,self.broker.capability)),timeout=600)
                if outcome.action in {'stop','skip'}:await self._stop_typing()
                log.warning('Foreground provider turn seconds=%.2f continuation=%s status=%s',time.monotonic()-provider_started,continuation,outcome.status)
                async with self._steer_lock:self._accept_steer=False
                if self.brain.session_id and self.brain.session_id!=self._saved_session and not getattr(self.brain,'shared',False):
                    await self.store.session_put(self.cfg.brain,self.revision,self.brain.session_id)
                    self._saved_session=self.brain.session_id
                state=await self.store.work_state(turn_id)
                receipts,artifacts=state['receipts'],state['artifacts']
                if outcome.status!='completed':break
                if outcome.action in {'stop','skip'} and origin_report_needed(receipts,self.store.scope):
                    payload=load('repair-missing-origin.md')
                    continue
                if outcome.action in {'stop','skip'} and research_feedback_needed(self.broker,receipts):
                    payload=load('repair-no-delivery.md')
                    continue
                if outcome.action=='stop' and not any(r['status']=='sent' for r in receipts) and not self.broker.pending_research:
                    payload=load('repair-no-delivery.md')
                    continue
                missing=undelivered_artifacts(artifacts,receipts)
                if outcome.action in {'stop','skip'} and missing:
                    payload=render('repair-missing-files.md',artifact_ids=', '.join(missing))
                    continue
                if outcome.action!='heartbeat':break
                payload=load('continue-task.md')
            done=outcome is not None and outcome.status=='completed' and not origin_report_needed(receipts,self.store.scope) and not research_feedback_needed(self.broker,receipts) and ((outcome.action=='skip' and not undelivered_artifacts(artifacts,receipts)) or
                (outcome.action=='stop' and (any(r['status']=='sent' for r in receipts) or self.broker.pending_research) and
                 not undelivered_artifacts(artifacts,receipts)))
            uncertain=any(r['status'] in {'sending','uncertain'} for r in receipts)
            interrupted=self._cancelled or (outcome is not None and outcome.status=='interrupted')
            retry=not done and not interrupted and not uncertain and any(r['attempts']<3 for r in self.inflight)
            if not done and not retry and not interrupted:
                await self._notify_failure(receipts)
            await self.store.finish_inputs([r['id'] for r in self.inflight],
                error=None if done else ('cancelled' if interrupted else ((outcome.error or 'turn_incomplete') if outcome else 'turn_failed')),
                retry=retry,job_id=turn_id)
            if peer and done and outcome.action=='skip' and self.broker.loop_guard:await self.broker.loop_guard.close(peer)
            if retry:self.wake.set()
            if outcome and outcome.status=='failed' and not interrupted:
                await self.brain.close()
                await asyncio.sleep(min(2**max(r['attempts'] for r in self.inflight),30))
                await self.brain.start(self.brain.session_id)
        except (asyncio.TimeoutError,asyncio.CancelledError) as exc:
            self.broker.end()
            await self.brain.interrupt()
            receipts=await self.store.delivery_receipts(turn_id)
            retry=not self._cancelled and not any(r['status'] in {'sending','uncertain'} for r in receipts) and any(r['attempts']<3 for r in self.inflight)
            error='cancelled' if self._cancelled else ('service_restart' if isinstance(exc,asyncio.CancelledError) else 'turn_timed_out')
            await self.store.finish_inputs([r['id'] for r in self.inflight],error=error,retry=retry,job_id=turn_id)
            if isinstance(exc,asyncio.TimeoutError):
                await self.brain.close()
                await self.brain.start(self.brain.session_id)
                if retry:self.wake.set()
                return
            raise
        finally:
            self._accept_steer=False
            self.broker.end()

    async def close(self):
        if self._signal_task:
            self._signal_task.cancel()
            await asyncio.gather(self._signal_task,return_exceptions=True)
        self.broker.end()
        if self.presence:await self.presence.stop(self.store.scope)
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
        if self.brain:await self.brain.close()
        await self.mcp.close()



async def run(cfg):
    cfg.runtime_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    import fcntl
    lock=(cfg.runtime_dir/'instance.lock').open('w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('A Brodar process is already using this runtime directory') from None
    db=Postgres(cfg.database_url)
    coordination_db=Postgres(cfg.database_url)
    routing_db=Postgres(cfg.database_url)
    research_db=Postgres(cfg.database_url)
    research_events_db=Postgres(cfg.database_url)
    approval_db=Postgres(cfg.database_url)
    room_dbs=[coordination_db,routing_db,approval_db,research_db,research_events_db]
    learning_db=Postgres(cfg.database_url)
    jobs_db=Postgres(cfg.database_url)
    embeddings=GeminiEmbeddings(cfg.google_key) if cfg.google_key else None
    bot=Bot(cfg.telegram_token)
    presence=TypingPresence(bot)
    objects=ObjectStore.from_file(cfg.storage_config)
    from .approvals import Approvals
    approval_service=Approvals(approval_db,bot,cfg)
    from .access import ChatAccess
    access=ChatAccess(cfg,db)
    from .coordination import LoopGuard
    loop_guard=LoopGuard(coordination_db,cfg.instance_id)
    approval_service.allowed_chats=access.audiences
    conversations={}
    creating={}
    dispatch_tasks={}
    dispatch_flags={}
    semaphore=asyncio.Semaphore(cfg.max_concurrent_turns)
    lease=None

    from .shared import GroupSession,SharedScope,BrokerProxy
    from .routing import GroupRouter,peer_key
    router=GroupRouter(cfg,access,bot,routing_db,objects,lambda:lease)
    router.loop_guard=loop_guard
    from .research import ResearchService,ResearchStore
    from .hyperagent import HyperagentTransport
    transport=HyperagentTransport(cfg.hyperagent_auth_file,cfg.hyperagent_agent_id)
    async def research_notify(row):
        chat_id=row['origin_chat_id']
        if chat_id not in access.audiences():
            log.warning('Research result withheld: origin no longer admitted task=%s',row['id'])
            return
        if chat_id>0 and chat_id!=row['requester_id']:raise PermissionError('Invalid private research return route')
        scope=Scope(lease.bot_id,chat_id,row['origin_topic_id'],cfg.instance_id)
        result=row.get('result') or {'report':row.get('error_code') or 'research_failed'}
        report=result.get('report','')
        artifact=None
        if len(report)>12000 and objects:
            artifact=await Artifacts(LearningStore(research_events_db,scope,lease_owner=lease.owner),objects).put(
                turn_id=str(row['id']),operation_id=f'research-result:{row["generation"]}:{row["result_version"]}',filename=f'research-{row["id"]}.md',
                mime='text/markdown',body=report.encode(),request_hash=hashlib.sha256(report.encode()).hexdigest(),metadata={'source':'hyperagent','task_id':str(row['id'])})
        content=render('research-result.md',task_id=str(row['id']),status=row['status'],brief=row['brief'],
            report=report if not artifact else report[:12000],artifact=json.dumps(artifact,default=str) if artifact else 'none',
            thread_id=row.get('remote_thread_id') or 'none')
        event_store=LearningStore(research_events_db,scope,lease_owner=lease.owner)
        update_id,new=await event_store.enqueue_research_result(row,content)
        if new:
            learning.preempt()
            presence.start(scope,new_work=True,update_id=update_id)
        dispatch(scope,True)
    research=ResearchService(cfg,ResearchStore(research_db,cfg.instance_id,lambda:lease),transport,access,research_notify)
    async def shared_factory(first):
        session_db=Postgres(cfg.database_url);room_dbs.append(session_db)
        scope=SharedScope(first.store.scope.bot_id,0,instance_id=cfg.instance_id)
        def register():
            with session_db.transaction(scope.audience) as cursor:
                cursor.execute('INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id) VALUES (%s,%s,0) ON CONFLICT DO NOTHING',(scope.audience,cfg.instance_id))
        await session_db.run(register)
        resource=Conversation(cfg,LearningStore(session_db,scope,lease_owner=lease.owner),bot,embeddings,semaphore,objects)
        resource.broker=BrokerProxy(first.broker)
        resource.mcp=ScopedMcpServer(resource.broker)
        return resource
    shared_groups=GroupSession(shared_factory)

    async def get_conversation(scope):
        key=scope.conversation
        if key not in conversations:
            async def create():
                room_db=Postgres(cfg.database_url)
                room_dbs.append(room_db)
                conversation=Conversation(cfg,LearningStore(room_db,scope,lease_owner=lease.owner),bot,embeddings,semaphore,objects,
                                          delivery_guard=lease.require,presence=presence)
                audit_db=Postgres(cfg.database_url)
                room_dbs.append(audit_db)
                conversation.broker.audit_store=LearningStore(audit_db,scope,lease_owner=lease.owner)
                conversation.shared_group=shared_groups if scope.chat_id<0 else None
                conversation.broker.approvals=approval_service
                conversation.broker.router=router
                conversation.broker.loop_guard=loop_guard
                conversation.broker.research=research
                await conversation.start()
                conversations[key]=conversation
                return conversation
            if key not in creating:creating[key]=asyncio.create_task(create())
            pending=creating[key]
            try:await asyncio.shield(pending)
            finally:
                if pending.done() and creating.get(key) is pending:creating.pop(key,None)
        return conversations[key]

    def dispatch(scope,direct):
        key=scope.conversation
        dispatch_flags[key]=dispatch_flags.get(key,False) or direct
        if key in dispatch_tasks:return
        async def deliver_signal():
            try:
                while key in dispatch_flags:
                    priority=dispatch_flags.pop(key)
                    conversation=await get_conversation(scope)
                    await conversation.signal(direct=priority)
            except Exception as exc:log.error('Room dispatch deferred: %s',type(exc).__name__)
            finally:dispatch_tasks.pop(key,None)
        dispatch_tasks[key]=asyncio.create_task(deliver_signal(),name='brodar-dispatch')

    from .reflection import Maintenance
    async def make_worker(scope):
        return Conversation(cfg,LearningStore(learning_db,scope,lease_owner=lease.owner),bot,embeddings,semaphore,
                            delivery_guard=lease.require)
    learning=Maintenance(cfg,make_worker,asyncio.Semaphore(1),
        lambda:bool(creating) or bool(dispatch_tasks) or any(c.wake.is_set() or c.inflight or c._turn_lock.locked() for c in conversations.values()))

    async def maintain(bot_id):
        while True:
            for chat_id in sorted(access.audiences()):
                scope=Scope(bot_id,chat_id,instance_id=cfg.instance_id)
                store=LearningStore(learning_db,scope,lease_owner=lease.owner)
                if not learning.idle():break
                if embeddings and time.monotonic()>=learning.index_retry_at:
                    async def index_batch():
                        rows=await store.index_candidates(embeddings.model,limit=24)
                        parts=[chunks(row['content']) for row in rows]
                        flat=[part for group in parts for part in group]
                        if not flat or not learning.idle():return
                        vectors=await asyncio.wait_for(embeddings.embed_batch(flat),15)
                        offset=0;documents=[]
                        for row,group in zip(rows,parts):
                            documents.append(dict(row,vectors=vectors[offset:offset+len(group)]))
                            offset+=len(group)
                        if learning.idle():await store.index_documents(documents,embeddings.model)
                    learning.index_task=asyncio.create_task(index_batch())
                    try:await learning.index_task
                    except asyncio.CancelledError:
                        if asyncio.current_task().cancelling():raise
                    except Exception as exc:
                        log.warning('Index maintenance deferred: %s',type(exc).__name__)
                        learning.index_retry_at=time.monotonic()+60
                    finally:learning.index_task=None
                if cfg.learning_enabled:await learning.run(scope)
            await asyncio.sleep(60)

    maintenance=None
    heartbeat=None
    scheduler=None
    research_worker=None
    try:
        await bot.initialize()
        me=await bot.get_me()
        info=await bot.get_webhook_info()
        if info.url:raise RuntimeError('This bot has a webhook; use a separate test token or explicitly remove the webhook first')
        await access.load()
        await loop_guard.register()
        lease=ConsumerLease(db,me.id)
        await lease.acquire()
        parent_task=asyncio.current_task()
        async def renew_lease():
            while True:
                await asyncio.sleep(30)
                try:await lease.acquire()
                except Exception as exc:
                    log.error('Consumer lease renewal failed; stopping this instance: %s',type(exc).__name__)
                    parent_task.cancel()
                    return
        heartbeat=asyncio.create_task(renew_lease(),name='brodar-lease')
        def cursor():
            with db.transaction(readonly=True) as c:
                c.execute('SELECT next_update_id FROM brodar_v2.poll_state WHERE bot_id=%s',(me.id,))
                row=c.fetchone()
                return row[0] if row else None
        offset=await db.run(cursor)
        # Restore every pending topic for explicitly configured audiences.
        for chat_id in sorted(access.audiences()):
            scope=Scope(me.id,chat_id,instance_id=cfg.instance_id)
            def pending_topics():
                with db.transaction(scope.audience,readonly=True) as c:
                    c.execute("SELECT DISTINCT topic_id FROM brodar_v2.inbox WHERE audience=%s AND status IN ('pending','running')",(scope.audience,))
                    return [r[0] for r in c.fetchall()]
            for topic in sorted(set(await db.run(pending_topics))|({0} if chat_id in access.groups else set())):
                await get_conversation(dataclasses.replace(scope,topic_id=topic))
        log.warning('Brodar polling as @%s with %s (%s)',me.username,cfg.brain,cfg.model)
        maintenance=asyncio.create_task(maintain(me.id),name='brodar-learning')
        async def scheduled_reminders():
            while True:
                for chat_id in sorted(access.audiences()):
                    scope=Scope(me.id,chat_id,instance_id=cfg.instance_id)
                    store=LearningStore(jobs_db,scope,lease_owner=lease.owner)
                    reminders=Reminders(store)
                    try:
                        for job in await reminders.claim():
                            topic=dataclasses.replace(scope,topic_id=job['topic_id'])
                            current=LearningStore(jobs_db,topic,lease_owner=lease.owner)
                            status=await dispatch_reminder(job,current,bot,lease.require)
                            if status!='done':log.warning('Reminder delivery status: %s',status)
                    except Exception as exc:log.warning('Reminder scheduler deferred: %s',type(exc).__name__)
                await asyncio.sleep(15)
        scheduler=asyncio.create_task(scheduled_reminders(),name='brodar-reminders')
        research_worker=asyncio.create_task(research.run(),name='brodar-research')
        while True:
            await lease.require()
            try:updates=await bot.get_updates(offset=offset,timeout=25,allowed_updates=['message','edited_message'])
            except Conflict:raise RuntimeError('Another Telegram poller is running for this bot token') from None
            except NetworkError:
                await asyncio.sleep(3)
                continue
            for update in updates:
                persisted=False
                message=update.effective_message
                raw=update.to_dict()
                raw_message=raw.get('message') or raw.get('edited_message') or {}
                approval_handled=False
                if message is not None:
                    approval_handled=await approval_service.decide(message.chat_id,
                        message.from_user.id if message.from_user else None,message.text or '',me.id,
                        private=message.chat.type=='private',anonymous=message.sender_chat is not None or message.edit_date is not None or any(raw_message.get(k) for k in ('forward_origin','forward_from','forward_sender_name')))
                group_approval=access.approval_command(raw,me.username)
                if group_approval:
                    await access.approve(raw,me.username)
                    try:
                        await bot.send_message(chat_id=message.chat_id,text="group approved. i'm in",reply_to_message_id=message.message_id,
                            **({'message_thread_id':message.message_thread_id} if message.message_thread_id else {}))
                    except Exception as exc:log.warning('Group approval acknowledgment unavailable: %s',type(exc).__name__)
                if group_approval or approval_handled:
                    pass  # consumed by host; never authorizes changes through model text
                elif message is not None and access.accepts(raw_message):
                    sender=message.from_user if message.sender_chat is None else None
                    scope=Scope(me.id,message.chat_id,message.message_thread_id or 0,cfg.instance_id)
                    text,service=describe_message(raw_message)
                    direct=addressed(raw_message,me.id,me.username)
                    if not service and not (sender and sender.is_bot):learning.preempt()
                    if direct and not service and not (sender and sender.is_bot):presence.start(scope,new_work=True,update_id=update.update_id)
                    if message.edit_date is not None and scope.conversation in conversations:
                        mid=str(uuid5(NAMESPACE_URL,f'{scope.audience}:telegram:{message.message_id}'))
                        conversations[scope.conversation].broker.revoke_request_ids([mid])
                    store=LearningStore(db,scope,lease_owner=lease.owner)
                    iid,new=await store.ingest(update_id=update.update_id,message_id=message.message_id,
                        sender_id=sender.id if sender else None,sender_name=sender.full_name if sender else 'anonymous sender',
                        text=text,timestamp=message.date,reply_to_id=message.reply_to_message.message_id if message.reply_to_message else None,
                        edited_at=message.edit_date,payload=public_payload(raw),role='service' if service else 'user')
                    persisted=True
                    if new and service:await store.finish_inputs([iid])
                    if not new and direct:
                        existing=conversations.get(scope.conversation)
                        if existing is None or (not existing.inflight and not existing.wake.is_set()):await presence.stop(scope)
                    if new and not service:
                        if not (sender and sender.is_bot):
                            learning.preempt()
                        if text.split('@',1)[0].strip()=='/cancel' and sender:
                            conversation=await get_conversation(scope)
                            await conversation.cancel(sender.id)
                            await store.finish_inputs([iid])
                        elif sender and sender.is_bot:
                            if sender.id!=me.id and direct and text.strip().upper() not in {'[SILENT]','SILENT'} and await loop_guard.incoming(await loop_guard.resolve(raw_message.get('from') or {},cfg.research_bot_username),scope.chat_id,text):
                                dispatch(scope,False)
                            else:await store.finish_inputs([iid])

                        else:
                            if direct:await loop_guard.human(scope.chat_id)
                            dispatch(scope,direct)
                elif message is not None:
                    if not ordinary_message(raw_message):log.warning('Recipient-scoped Telegram input omitted from ordinary chat history')
                    else:log.warning('Unconfigured chat observed: id=%s type=%s',message.chat_id,message.chat.type)
                # Advance only after durable ingest (or deliberate access skip).
                offset=update.update_id+1
                def advance():
                    with db.transaction() as c:
                        c.execute('UPDATE brodar_v2.poll_state SET next_update_id=greatest(next_update_id,%s) '
                            'WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() RETURNING bot_id',(offset,me.id,lease.owner))
                        if c.fetchone() is None:raise PermissionError('Consumer lease expired before cursor commit')
                if not persisted:await db.run(advance)
    finally:
        if research_worker:
            research_worker.cancel()
            await asyncio.gather(research_worker,return_exceptions=True)
        if scheduler:
            scheduler.cancel()
            await asyncio.gather(scheduler,return_exceptions=True)
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat,return_exceptions=True)
        learning.preempt()
        if maintenance:
            maintenance.cancel()
            await asyncio.gather(maintenance,return_exceptions=True)
        for pending_creation in creating.values():pending_creation.cancel()
        await asyncio.gather(*list(creating.values()),return_exceptions=True)
        for pending_dispatch in dispatch_tasks.values():pending_dispatch.cancel()
        await asyncio.gather(*list(dispatch_tasks.values()),return_exceptions=True)
        await asyncio.gather(*(c.close() for c in conversations.values()),return_exceptions=True)
        await shared_groups.close()
        await presence.close()
        await bot.shutdown()
        if embeddings:await embeddings.close()
        if objects:await objects.close()
        if lease:await lease.release()
        await db.close()
        await asyncio.gather(*(room_db.close() for room_db in room_dbs),return_exceptions=True)
        await learning_db.close()
        await jobs_db.close()
        lock.close()
