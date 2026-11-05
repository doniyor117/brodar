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
from .runtime_store import RuntimeStore
from .learning import LearningStore,chunks
from .scope import Scope
from .objects import ObjectStore
from .artifacts import Artifacts
from .recovery import ConsumerLease
from .telegram_context import ordinary_message,public_payload,describe_message
from .moderation import requests as moderation_requests
from .jobs import Reminders,input_requests,dispatch_reminder

log=logging.getLogger('brodar')


def undelivered_artifacts(artifacts,receipts):
    delivered={str(row['request'].get('artifact_id')) for row in receipts
               if row['status']=='sent' and row['method']=='send_document'}
    return [str(row['artifact_id']) for row in artifacts if str(row['artifact_id']) not in delivered]


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
    def __init__(self,cfg,store,bot,embeddings,semaphore,objects=None,delivery_guard=None):
        self.cfg,self.store,self.bot,self.embeddings=cfg,store,bot,embeddings
        self.semaphore=semaphore
        self.broker=ToolBroker(store,bot,embeddings=embeddings,artifacts=Artifacts(store,objects) if objects else None,
                               delivery_guard=delivery_guard,owner_ids=getattr(cfg,'owner_ids',()))
        self.mcp=ScopedMcpServer(self.broker)
        self.brain=None
        self.revision=None
        self.wake=asyncio.Event()
        self.direct=asyncio.Event()
        self.task=None
        self.inflight=[]
        self._steer_lock=asyncio.Lock()
        self._accept_steer=False
        self._last_ambient=0
        self._cancelled=False
        self._turn_id=None
        key=hashlib.sha256(store.scope.conversation.encode()).hexdigest()[:24]
        self.root=cfg.runtime_dir/'providers'/cfg.brain/key

    async def start(self):
        await self.mcp.start()
        tools=['mcp__hamroh__'+t.name for t in await self.mcp.mcp.list_tools()]
        prompt,self.revision=compose(model=self.cfg.model,provider=self.cfg.brain,tools=tools,timezone=self.cfg.timezone)
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.cfg.brain=='codex':
            # Share one dedicated CLI auth store so concurrent conversations do
            # not race independent copies of a rotating OAuth refresh token.
            # Scope-specific MCP endpoints are process argv, never shared config.
            self.brain=CodexBrain(home=self.cfg.runtime_dir/'codex',cwd=self.root/'work',model=self.cfg.model,
                effort=self.cfg.effort,instructions=prompt,env={'BRODAR_MCP_TOKEN':self.mcp.token},mcp_url=self.mcp.url)
        else:
            from .brains.claude import ClaudeBrain
            from hamroh.cc_worker.spec import CcSpawnSpec
            from hamroh.cc_worker.cc_schema import CONTROL_ACTION_SCHEMA
            seed=self.cfg.runtime_dir/'claude'/'.credentials.json'
            if seed.exists() and not (self.root/'.credentials.json').exists():
                private_write(self.root/'.credentials.json',seed.read_text())
            private_write(self.root/'system.md',prompt)
            private_write(self.root/'terminal.schema.json',json.dumps(CONTROL_ACTION_SCHEMA))
            private_write(self.root/'mcp.json',json.dumps({'mcpServers':{'hamroh':{
                'type':'http','url':self.mcp.url,'alwaysLoad':True,
                'headers':{'Authorization':'Bearer '+self.mcp.token}}}}))
            spec=CcSpawnSpec(binary='claude',model=self.cfg.model,effort=self.cfg.effort,
                system_prompt_path=self.root/'system.md',mcp_config_path=self.root/'mcp.json',
                json_schema_path=self.root/'terminal.schema.json',cc_logs_dir=None,
                hamroh_tool_names=tuple(t.name for t in await self.mcp.mcp.list_tools()))
            self.brain=ClaudeBrain(spec=spec,config=claude_worker_config(self.root),home=self.root,cwd=self.root/'work')
        session=await self.store.session_get(self.cfg.brain,self.revision)
        try:
            await self.brain.start(session)
            if self.brain.session_id:await self.store.session_put(self.cfg.brain,self.revision,self.brain.session_id)
        except BaseException:
            await self.brain.close()
            await self.mcp.close()
            raise
        self.task=asyncio.create_task(self.loop(),name='brodar-conversation')
        self.wake.set()

    async def signal(self,*,direct=False):
        self.wake.set()
        if direct:self.direct.set()
        async with self._steer_lock:
            if not self._accept_steer:return
            batch=await self.store.claim_inputs()
            if not batch:return
            if any((r['payload'].get('message') or r['payload'].get('edited_message') or {}).get('document') or
                   (r['payload'].get('message') or r['payload'].get('edited_message') or {}).get('photo') for r in batch):
                await self.store.finish_inputs([r['id'] for r in batch],retry=True)
                return  # materialize media in the next durable foreground turn
            messages=await self.store.history_get([str(r['message_id']) for r in batch])
            cap=self.broker.capability
            if cap is None:
                await self.store.finish_inputs([r['id'] for r in batch],retry=True)
                return
            accepted=await self.brain.steer(authorized(envelope(messages),cap))
            if accepted:
                self.inflight.extend(batch)
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
            self.direct.clear()
            async with self.semaphore:
                try:
                    await self.process()
                except asyncio.CancelledError:raise
                except Exception as exc:
                    log.error('Conversation processing failed: %s',type(exc).__name__)
                    await asyncio.sleep(3)
                    self.wake.set()
            self._last_ambient=time.monotonic()

    async def process(self):
        self._turn_id=None
        try:
            await self._process()
        except Exception:
            self.broker.end()
            if self.inflight:
                try:
                    receipts=await self.store.delivery_receipts(self._turn_id) if self._turn_id else []
                    retry=not self._cancelled and not any(r['status'] in {'sending','uncertain'} for r in receipts) and max(r['attempts'] for r in self.inflight)<3
                    await self.store.finish_inputs([r['id'] for r in self.inflight],error='processing_error',retry=retry,job_id=self._turn_id)
                    if retry:self.wake.set()
                except Exception:
                    pass  # retain the durable lease; the periodic recovery pass will revisit it
            raise
        finally:
            self.broker.end()
            self.inflight=[]

    async def materialize(self,batch):
        if self.broker.artifacts is None:return
        from .redaction import scrub
        from .store import digest
        for row in batch:
            payload=row['payload'];message=payload.get('message') or payload.get('edited_message') or {}
            document=message.get('document');photos=message.get('photo') or []
            media=document or (photos[-1] if photos else None)
            if not media or (media.get('file_size') or 0)>20_000_000:continue
            turn_id=str(uuid5(NAMESPACE_URL,f'inbound:{self.store.scope.bot_id}:{row["update_id"]}'))
            uid=str(uuid5(NAMESPACE_URL,f'{self.store.scope.audience}:{turn_id}:attachment'))
            record=await self.broker.artifacts.get_record(uid)
            if record:artifact=self.broker.artifacts.public(record)
            else:
                file=await self.bot.get_file(media['file_id'])
                body=bytes(await file.download_as_bytearray())
                filename=(document.get('file_name') or 'document') if document else f'photo-{message["message_id"]}.jpg'
                filename=Path(filename.replace('\\','/')).name[:180]
                mime=(document.get('mime_type') or 'application/octet-stream') if document else 'image/jpeg'
                artifact=await self.broker.artifacts.put(turn_id=turn_id,operation_id='attachment',filename=filename,
                    mime=mime,body=body,request_hash=hashlib.sha256(body).hexdigest(),
                    metadata={'source':'telegram','message_id':message['message_id']})
            text=scrub(message.get('text') or message.get('caption') or '')
            text+='\n[attachment: '+artifact['filename']+'; artifact_id: '+artifact['artifact_id']+']'
            def update():
                with self.store.db.transaction(self.store.scope.audience) as c:
                    c.execute('UPDATE brodar_v2.messages SET content=%s,source_hash=%s WHERE audience=%s AND id=%s',
                        (text,digest(text),self.store.scope.audience,str(row['message_id'])))
            await self.store.db.run(update)

    async def _process(self):
        batch=await self.store.claim_inputs()
        if not batch:return
        self.inflight=list(batch)
        self._cancelled=False
        self._turn_id=str(batch[0]['id'])
        payload_message=batch[0]['payload'].get('message') or batch[0]['payload'].get('edited_message') or {}
        requester=(payload_message.get('from') or {}).get('id') if not payload_message.get('sender_chat') else None
        work=await self.store.work_begin(self._turn_id,requester,[str(r['message_id']) for r in batch])
        if work['status'] in {'done','cancelled'}:
            await self.store.finish_inputs([r['id'] for r in batch],error='cancelled' if work['status']=='cancelled' else None)
            return
        self.broker.begin(TurnCapability(self._turn_id,requester_id=requester),moderation_requests=moderation_requests(batch,getattr(self.bot,'username',None)),
                          requests=input_requests(batch))
        await self.materialize(batch)
        if self._cancelled:
            await self.store.finish_inputs([r['id'] for r in self.inflight],error='cancelled',job_id=self._turn_id)
            return
        ids=[str(r['message_id']) for r in batch]
        messages=await self.store.history_get(ids)
        recent=await self.store.recent(35)
        context=[m for m in recent if str(m['id']) not in set(ids)]
        notes=await self.store.context_notes({m['sender_id'] for m in messages if m.get('sender_id')})
        query='\n'.join(m['content'] for m in messages)[-1800:]
        if self.embeddings and len(query)>=20 and await self.store.has_index(self.embeddings.model):
            try:
                vector=await self.embeddings.embed(query,query=True)
                recall=await self.store.search(query,vector=vector,limit=6)
                seen={str(m['id']) for m in context+messages}
                for item in recall:
                    if item['kind']=='message' and str(item['id']) not in seen:
                        context.append(item);seen.add(str(item['id']))
                    elif item['kind']=='memory' and not any(str(n['id'])==str(item['id']) for n in notes):
                        notes.append(item)
            except (EmbeddingError,ValueError):pass
        turn_id=self._turn_id
        prior=await self.store.delivery_receipts(turn_id)
        if any(r['status'] in {'sending','uncertain'} for r in prior):
            await self.store.finish_inputs([r['id'] for r in batch],error='previous_delivery_uncertain',job_id=turn_id)
            return
        payload=envelope(messages,context=context,notes=notes,receipts=prior)
        artifacts=await self.store.work_artifacts(turn_id)
        if work['checkpoint'] or artifacts:
            from xml.sax.saxutils import escape
            payload+='\n<task_progress>'+escape(json.dumps({'checkpoint':work['checkpoint'],'artifacts':artifacts},default=str))+'</task_progress>'
        outcome=None
        try:
            for continuation in range(4):
                if self._cancelled:break
                self._accept_steer=True
                outcome=await asyncio.wait_for(self.brain.turn(authorized(payload,self.broker.capability)),timeout=600)
                async with self._steer_lock:self._accept_steer=False
                if self.brain.session_id:
                    await self.store.session_put(self.cfg.brain,self.revision,self.brain.session_id)
                receipts=await self.store.delivery_receipts(turn_id)
                artifacts=await self.store.work_artifacts(turn_id)
                if outcome.status!='completed':break
                if outcome.action=='stop' and not any(r['status']=='sent' for r in receipts):
                    payload='No successful public delivery exists for this turn. Complete the requested work through the available tools, or explicitly skip if a reply is unnecessary.'
                    continue
                missing=undelivered_artifacts(artifacts,receipts)
                if outcome.action in {'stop','skip'} and missing:
                    payload='These created files have not been delivered: '+', '.join(missing)+'. Send each actual artifact through telegram_send_document before finishing.'
                    continue
                if outcome.action!='heartbeat':break
                payload='Continue the unfinished task. Finish through the actual tools. Do not repeat already delivered messages.'
            receipts=await self.store.delivery_receipts(turn_id)
            done=outcome is not None and outcome.status=='completed' and ((outcome.action=='skip' and not undelivered_artifacts(artifacts,receipts)) or
                (outcome.action=='stop' and any(r['status']=='sent' for r in receipts) and
                 not undelivered_artifacts(artifacts,receipts)))
            uncertain=any(r['status'] in {'sending','uncertain'} for r in receipts)
            interrupted=self._cancelled or (outcome is not None and outcome.status=='interrupted')
            retry=not done and not interrupted and not uncertain and max(r['attempts'] for r in self.inflight)<3
            await self.store.finish_inputs([r['id'] for r in self.inflight],
                error=None if done else ('cancelled' if interrupted else ((outcome.error or 'turn_incomplete') if outcome else 'turn_failed')),
                retry=retry,job_id=turn_id)
            if retry:self.wake.set()
            if outcome and outcome.status=='failed' and not interrupted:
                await self.brain.close()
                await asyncio.sleep(min(2**max(r['attempts'] for r in self.inflight),30))
                await self.brain.start(self.brain.session_id)
        except (asyncio.TimeoutError,asyncio.CancelledError) as exc:
            self.broker.end()
            await self.brain.interrupt()
            receipts=await self.store.delivery_receipts(turn_id)
            retry=not self._cancelled and not any(r['status'] in {'sending','uncertain'} for r in receipts) and max(r['attempts'] for r in self.inflight)<3
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
        self.broker.end()
        if self.task:
            self.task.cancel()
            await asyncio.gather(self.task,return_exceptions=True)
        if self.brain:await self.brain.close()
        await self.mcp.close()

    async def reflect(self):
        batch=await self.store.reflection_batch()
        if not batch:return
        # Bound each context presentation and only advance over presented rows.
        selected=[]
        size=0
        for row in batch:
            if selected and size+len(row['content'])>30000:break
            selected.append(row)
            size+=len(row['content'])
        turn_id=str(uuid5(NAMESPACE_URL,'reflection:'+self.store.scope.audience+':'+str(selected[-1]['id'])))
        self.broker.begin(TurnCapability(turn_id,mode='reflection'))
        try:
            payload=('This is a private maintenance turn, not a new group request. Learn from these archived experiences and feedback. '
                'Create, correct, merge or retire useful supplementary context notes; link source UUIDs. '
                'Do not turn jokes into certain facts. You may learn writing advice, relationships, interests, project state and task methods. '
                'Keep the main persona fixed. No Telegram delivery is allowed in this turn. When finished, return action skip with a short reason.\n'
                +envelope([],context=selected,notes=await self.store.memory_list(40)))
            outcome=await asyncio.wait_for(self.brain.turn(authorized(payload,self.broker.capability)),600)
            if outcome.status=='completed' and outcome.action in {'skip','stop'}:
                await self.store.reflection_finish(selected[-1])
            if self.brain.session_id:
                await self.store.session_put(self.cfg.brain,self.revision,self.brain.session_id)
        except asyncio.TimeoutError:
            self.broker.end()
            await self.brain.close()
            await self.brain.start(self.brain.session_id)
            raise
        finally:self.broker.end()


async def run(cfg):
    cfg.runtime_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
    import fcntl
    lock=(cfg.runtime_dir/'instance.lock').open('w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise RuntimeError('A Brodar process is already using this runtime directory') from None
    db=Postgres(cfg.database_url)
    embeddings=GeminiEmbeddings(cfg.google_key) if cfg.google_key else None
    bot=Bot(cfg.telegram_token)
    objects=ObjectStore.from_file(cfg.storage_config)
    conversations={}
    creating={}
    semaphore=asyncio.Semaphore(1)
    lease=None

    async def get_conversation(scope):
        key=scope.conversation
        if key not in conversations:
            async def create():
                conversation=Conversation(cfg,LearningStore(db,scope,lease_owner=lease.owner),bot,embeddings,semaphore,objects,
                                          delivery_guard=lease.require)
                await conversation.start()
                conversations[key]=conversation
                return conversation
            if key not in creating:creating[key]=asyncio.create_task(create())
            pending=creating[key]
            try:await asyncio.shield(pending)
            finally:
                if pending.done() and creating.get(key) is pending:creating.pop(key,None)
        return conversations[key]

    async def maintain(bot_id):
        while True:
            for chat_id in cfg.allowed_chats:
                scope=Scope(bot_id,chat_id,instance_id=cfg.instance_id)
                store=LearningStore(db,scope)
                if embeddings:
                    try:
                        for row in await store.index_candidates(embeddings.model):
                            vectors=[await embeddings.embed(part) for part in chunks(row['content'])]
                            await store.index_document(row['kind'],str(row['id']),row['content'],vectors,embeddings.model)
                    except EmbeddingError as exc:
                        log.warning('Embedding indexing deferred: %s',type(exc).__name__)
                        await asyncio.sleep(60)
                    except Exception as exc:log.warning('Index maintenance deferred: %s',type(exc).__name__)
                try:
                    batch=await store.reflection_batch()
                    if batch:
                        async with semaphore:
                            conversation=await get_conversation(scope)
                            await conversation.reflect()
                except Exception as exc:log.warning('Reflection deferred: %s',type(exc).__name__)
            await asyncio.sleep(60)

    maintenance=None
    heartbeat=None
    scheduler=None
    try:
        await bot.initialize()
        me=await bot.get_me()
        info=await bot.get_webhook_info()
        if info.url:raise RuntimeError('This bot has a webhook; use a separate test token or explicitly remove the webhook first')
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
        for chat_id in cfg.allowed_chats:
            scope=Scope(me.id,chat_id,instance_id=cfg.instance_id)
            def pending_topics():
                with db.transaction(scope.audience,readonly=True) as c:
                    c.execute("SELECT DISTINCT topic_id FROM brodar_v2.inbox WHERE audience=%s AND status IN ('pending','running')",(scope.audience,))
                    return [r[0] for r in c.fetchall()]
            for topic in await db.run(pending_topics):
                await get_conversation(dataclasses.replace(scope,topic_id=topic))
        log.warning('Brodar polling as @%s with %s (%s)',me.username,cfg.brain,cfg.model)
        maintenance=asyncio.create_task(maintain(me.id),name='brodar-learning')
        async def scheduled_reminders():
            while True:
                for chat_id in cfg.allowed_chats:
                    scope=Scope(me.id,chat_id,instance_id=cfg.instance_id)
                    store=LearningStore(db,scope,lease_owner=lease.owner)
                    reminders=Reminders(store)
                    try:
                        for job in await reminders.claim():
                            topic=dataclasses.replace(scope,topic_id=job['topic_id'])
                            current=LearningStore(db,topic,lease_owner=lease.owner)
                            status=await dispatch_reminder(job,current,bot,lease.require)
                            if status!='done':log.warning('Reminder delivery status: %s',status)
                    except Exception as exc:log.warning('Reminder scheduler deferred: %s',type(exc).__name__)
                await asyncio.sleep(15)
        scheduler=asyncio.create_task(scheduled_reminders(),name='brodar-reminders')
        while True:
            await lease.require()
            try:updates=await bot.get_updates(offset=offset,timeout=25,allowed_updates=['message','edited_message'])
            except Conflict:raise RuntimeError('Another Telegram poller is running for this bot token') from None
            except NetworkError:
                await asyncio.sleep(3)
                continue
            for update in updates:
                message=update.effective_message
                raw=update.to_dict()
                raw_message=raw.get('message') or raw.get('edited_message') or {}
                if message is not None and message.chat_id in cfg.allowed_chats and ordinary_message(raw_message):
                    sender=message.from_user if message.sender_chat is None else None
                    scope=Scope(me.id,message.chat_id,message.message_thread_id or 0,cfg.instance_id)
                    if message.edit_date is not None and scope.conversation in conversations:
                        mid=str(uuid5(NAMESPACE_URL,f'{scope.audience}:telegram:{message.message_id}'))
                        conversations[scope.conversation].broker.revoke_request_ids([mid])
                    store=LearningStore(db,scope,lease_owner=lease.owner)
                    text,service=describe_message(raw_message)
                    iid,new=await store.ingest(update_id=update.update_id,message_id=message.message_id,
                        sender_id=sender.id if sender else None,sender_name=sender.full_name if sender else 'anonymous sender',
                        text=text,timestamp=message.date,reply_to_id=message.reply_to_message.message_id if message.reply_to_message else None,
                        edited_at=message.edit_date,payload=public_payload(raw),role='service' if service else 'user')
                    if new and service:await store.finish_inputs([iid])
                    if new and not service:
                        conversation=await get_conversation(scope)
                        if text.split('@',1)[0].strip()=='/cancel' and sender:
                            await conversation.cancel(sender.id)
                            await store.finish_inputs([iid])
                        elif sender and sender.is_bot:
                            await store.finish_inputs([iid])  # context retained; no bot-to-bot reply loop
                        else:
                            direct=bool(message.chat.type=='private' or (me.username and '@'+me.username.lower() in text.lower()) or
                                (message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id==me.id))
                            await conversation.signal(direct=direct)
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
                await db.run(advance)
    finally:
        if scheduler:
            scheduler.cancel()
            await asyncio.gather(scheduler,return_exceptions=True)
        if heartbeat:
            heartbeat.cancel()
            await asyncio.gather(heartbeat,return_exceptions=True)
        if maintenance:
            maintenance.cancel()
            await asyncio.gather(maintenance,return_exceptions=True)
        await asyncio.gather(*(c.close() for c in conversations.values()),return_exceptions=True)
        await bot.shutdown()
        if embeddings:await embeddings.close()
        if objects:await objects.close()
        if lease:await lease.release()
        await db.close()
        lock.close()
