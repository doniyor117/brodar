"""Private local-operator conversation using the ordinary coordinator/provider.

No Telegram sender is forged; terminal records have sender_id NULL and their own
source/audience. Telegram sends in this conversation are captured for the UI.
"""
from __future__ import annotations
import asyncio
import dataclasses
import json
import time
from datetime import datetime,timezone
from types import SimpleNamespace
from uuid import uuid4,NAMESPACE_URL,uuid5
from .scope import Scope
from .runtime import Conversation
from .learning import LearningStore
from .store import digest

@dataclasses.dataclass(frozen=True)
class TerminalScope(Scope):
    @property
    def audience(self):return 'terminal:'+self.instance_id

class TerminalBot:
    username='local_operator_console'
    def __init__(self):self.output=[];self.counter=0;self.store=None
    async def send_message(self,chat_id,text,**kwargs):
        if chat_id!=0:raise PermissionError('Use explicit approved-group routing for Telegram delivery')
        self.counter+=1;self.output.append({'id':self.counter,'text':text,'time':time.time()})
        self.output=self.output[-100:]
        if self.store:
            uid=str(uuid4())
            def persist():
                with self.store.db.transaction(self.store.scope.audience) as c:
                    c.execute("INSERT INTO brodar_v2.messages(id,audience,topic_id,sender_name,role,content,source,source_id,source_hash) VALUES (%s,%s,0,'brodar','assistant',%s,'terminal',%s,%s)",(uid,self.store.scope.audience,text,uid,digest(text)))
            await self.store.db.run(persist)
        return True
    async def send_document(self,chat_id,document,caption='',**kwargs):
        return await self.send_message(chat_id,'[local file prepared] '+str(caption))

class TerminalSession:
    def __init__(self,cfg,db,bot_id,semaphore,objects=None,*,owner_jobs=None,router=None,approvals=None):
        self.cfg,self.db,self.bot_id,self.semaphore,self.objects=cfg,db,bot_id,semaphore,objects
        self.owner_jobs,self.router,self.approvals=owner_jobs,router,approvals
        self.conversation=None;self.bot=TerminalBot();self.lock=asyncio.Lock()
    async def send(self,text):
        if not text.strip() or len(text)>16000:raise ValueError('Use 1..16000 characters')
        async with self.lock:
            if self.conversation is None:
                scope=TerminalScope(self.bot_id,0,0,self.cfg.instance_id)
                store=LearningStore(self.db,scope)
                # A terminal conversation has its own scope and provider, never the group thread.
                self.bot.store=store
                self.conversation=Conversation(self.cfg,store,self.bot,None,self.semaphore,self.objects)
                self.conversation.broker.operator_session=True
                self.conversation.broker.owner_jobs=self.owner_jobs
                self.conversation.broker.router=self.router
                self.conversation.broker.approvals=self.approvals
                await self.conversation.start()
            store=self.conversation.store;uid=str(uuid4());iid=str(uuid4())
            def insert():
                with self.db.transaction(store.scope.audience) as c:
                    c.execute('INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id) VALUES (%s,%s,0) ON CONFLICT DO NOTHING',(store.scope.audience,self.cfg.instance_id))
                    c.execute("INSERT INTO brodar_v2.messages(id,audience,topic_id,sender_name,role,content,source,source_id,source_hash) VALUES (%s,%s,0,'local operator','user',%s,'terminal',%s,%s)",(uid,store.scope.audience,text,uid,digest(text)))
                    # Separate cursor namespace: terminal events never touch Telegram poll_state.
                    update_id=-int(uuid4().int%9_000_000_000_000_000)
                    c.execute('INSERT INTO brodar_v2.inbox(id,audience,topic_id,bot_id,update_id,message_id,payload) VALUES (%s,%s,0,%s,%s,%s,%s::jsonb)',(iid,store.scope.audience,self.bot_id,update_id,uid,json.dumps({'terminal':True})))
            await self.db.run(insert);await self.conversation.signal(direct=True)
            return {'queued':True,'request_id':uid}
    async def close(self):
        if self.conversation:await self.conversation.close()
