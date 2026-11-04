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
from .prompts import compose,envelope
from .runtime_store import RuntimeStore
from .learning import LearningStore,chunks
from .scope import Scope

log=logging.getLogger('brodar')


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
    def __init__(self,cfg,store,bot,embeddings,semaphore):
        self.cfg,self.store,self.bot,self.embeddings=cfg,store,bot,embeddings
        self.semaphore=semaphore
        self.broker=ToolBroker(store,bot,embeddings=embeddings)
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
        key=hashlib.sha256(store.scope.conversation.encode()).hexdigest()[:24]
        self.root=cfg.runtime_dir/'providers'/cfg.brain/key

    async def start(self):
        await self.mcp.start()
        tools=['mcp__hamroh__'+t.name for t in await self.mcp.mcp.list_tools()]
        prompt,self.revision=compose(model=self.cfg.model,provider=self.cfg.brain,tools=tools)
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        if self.cfg.brain=='codex':
            seed=self.cfg.runtime_dir/'codex'/'auth.json'
            if seed.exists() and not (self.root/'auth.json').exists():
                private_write(self.root/'auth.json',seed.read_text())
            # No owner config, plugins, AGENTS files, or personal MCP servers.
            private_write(self.root/'config.toml','project_doc_max_bytes = 0\ncli_auth_credentials_store = "file"\n'
                '[mcp_servers.hamroh]\nurl = '+json.dumps(self.mcp.url)+'\n'
                'bearer_token_env_var = "BRODAR_MCP_TOKEN"\n')
            self.brain=CodexBrain(home=self.root,cwd=self.root/'work',model=self.cfg.model,
                effort=self.cfg.effort,instructions=prompt,env={'BRODAR_MCP_TOKEN':self.mcp.token})
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
            messages=await self.store.history_get([str(r['message_id']) for r in batch])
            accepted=await self.brain.steer(envelope(messages))
            if accepted:self.inflight.extend(batch)
            else:await self.store.finish_inputs([r['id'] for r in batch],retry=True)

    async def cancel(self,requester_id):
        cap=self.broker.capability
        if cap is None:return False
        if requester_id not in self.cfg.owner_ids and requester_id!=cap.requester_id:return False
        self.broker.end()
        await self.brain.interrupt()
        return True

    async def loop(self):
        while True:
            await self.wake.wait()
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
        batch=await self.store.claim_inputs()
        if not batch:return
        self.inflight=list(batch)
        ids=[str(r['message_id']) for r in batch]
        messages=await self.store.history_get(ids)
        recent=await self.store.recent(35)
        context=[m for m in recent if str(m['id']) not in set(ids)]
        notes=await self.store.memory_list(30)
        turn_id=str(uuid5(NAMESPACE_URL,self.store.scope.conversation+':'+','.join(str(r['update_id']) for r in batch)))
        prior=await self.store.delivery_receipts(turn_id)
        if any(r['status'] in {'sending','uncertain'} for r in prior):
            await self.store.finish_inputs([r['id'] for r in batch],error='previous_delivery_uncertain')
            return
        requester=next((m.get('sender_id') for m in reversed(messages) if m.get('sender_id')),None)
        self.broker.begin(TurnCapability(turn_id,requester_id=requester))
        payload=envelope(messages,context=context,notes=notes,receipts=prior)
        outcome=None
        try:
            for continuation in range(4):
                self._accept_steer=True
                outcome=await asyncio.wait_for(self.brain.turn(payload),timeout=600)
                async with self._steer_lock:self._accept_steer=False
                if self.brain.session_id:
                    await self.store.session_put(self.cfg.brain,self.revision,self.brain.session_id)
                receipts=await self.store.delivery_receipts(turn_id)
                if outcome.status!='completed':break
                if outcome.action=='stop' and not any(r['status']=='sent' for r in receipts):
                    payload='No successful public delivery exists for this turn. Complete the requested work through the available tools, or explicitly skip if a reply is unnecessary.'
                    continue
                if outcome.action!='heartbeat':break
                payload='Continue the unfinished task. Finish through the actual tools. Do not repeat already delivered messages.'
            done=outcome is not None and outcome.status=='completed' and outcome.action in {'stop','skip'}
            receipts=await self.store.delivery_receipts(turn_id)
            side_effect=any(r['status'] in {'sent','sending','uncertain'} for r in receipts)
            retry=not done and not side_effect and max(r['attempts'] for r in self.inflight)<3
            await self.store.finish_inputs([r['id'] for r in self.inflight],
                error=None if done else ((outcome.error or 'turn_incomplete') if outcome else 'turn_failed'),retry=retry)
            if retry:self.wake.set()
        except (asyncio.TimeoutError,asyncio.CancelledError):
            self.broker.end()
            await self.brain.interrupt()
            await self.store.finish_inputs([r['id'] for r in self.inflight],error='turn_cancelled_or_timed_out')
            raise
        finally:
            self._accept_steer=False
            self.broker.end()
            self.inflight=[]

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
            outcome=await asyncio.wait_for(self.brain.turn(payload),600)
            if outcome.status=='completed' and outcome.action in {'skip','stop'}:
                await self.store.reflection_finish(selected[-1])
            if self.brain.session_id:
                await self.store.session_put(self.cfg.brain,self.revision,self.brain.session_id)
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
    conversations={}
    creating={}
    semaphore=asyncio.Semaphore(1)

    async def get_conversation(scope):
        key=scope.conversation
        if key not in conversations:
            async def create():
                conversation=Conversation(cfg,LearningStore(db,scope),bot,embeddings,semaphore)
                await conversation.start()
                conversations[key]=conversation
                return conversation
            if key not in creating:creating[key]=asyncio.create_task(create())
            try:await asyncio.shield(creating[key])
            finally:
                if creating[key].done():creating.pop(key,None)
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
    try:
        await bot.initialize()
        me=await bot.get_me()
        info=await bot.get_webhook_info()
        if info.url:raise RuntimeError('This bot has a webhook; use a separate test token or explicitly remove the webhook first')
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
        while True:
            try:updates=await bot.get_updates(offset=offset,timeout=25,allowed_updates=['message','edited_message'])
            except Conflict:raise RuntimeError('Another Telegram poller is running for this bot token') from None
            except NetworkError:
                await asyncio.sleep(3)
                continue
            for update in updates:
                message=update.effective_message
                if message is not None and message.chat_id in cfg.allowed_chats:
                    sender=message.from_user if message.sender_chat is None else None
                    scope=Scope(me.id,message.chat_id,message.message_thread_id or 0,cfg.instance_id)
                    store=LearningStore(db,scope)
                    text=message.text or message.caption or '[attachment received; no text]'
                    iid,new=await store.ingest(update_id=update.update_id,message_id=message.message_id,
                        sender_id=sender.id if sender else None,sender_name=sender.full_name if sender else 'anonymous sender',
                        text=text,timestamp=message.date,reply_to_id=message.reply_to_message.message_id if message.reply_to_message else None,
                        edited_at=message.edit_date,payload=update.to_dict())
                    if new:
                        conversation=await get_conversation(scope)
                        if text.split('@',1)[0].strip()=='/cancel' and sender:
                            await conversation.cancel(sender.id)
                            await store.finish_inputs([iid])
                        elif sender and sender.is_bot:
                            await store.finish_inputs([iid])  # context retained; no bot-to-bot reply loop
                        else:
                            direct=bool((me.username and '@'+me.username.lower() in text.lower()) or
                                (message.reply_to_message and message.reply_to_message.from_user and message.reply_to_message.from_user.id==me.id))
                            await conversation.signal(direct=direct)
                # Advance only after durable ingest (or deliberate access skip).
                offset=update.update_id+1
                def advance():
                    with db.transaction() as c:
                        c.execute('INSERT INTO brodar_v2.poll_state(bot_id,next_update_id) VALUES (%s,%s) '
                            'ON CONFLICT(bot_id) DO UPDATE SET next_update_id=greatest(poll_state.next_update_id,excluded.next_update_id)',(me.id,offset))
                await db.run(advance)
    finally:
        if maintenance:
            maintenance.cancel()
            await asyncio.gather(maintenance,return_exceptions=True)
        await asyncio.gather(*(c.close() for c in conversations.values()),return_exceptions=True)
        await bot.shutdown()
        if embeddings:await embeddings.close()
        await db.close()
        lock.close()
