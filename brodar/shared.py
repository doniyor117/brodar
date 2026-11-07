"""One serialized group brain; each turn keeps its original host capability/store."""
import asyncio
import time
import logging
from dataclasses import dataclass
from .scope import Scope
from .prompt_files import render

@dataclass(frozen=True)
class SharedScope(Scope):
    @property
    def audience(self):return 'shared:'+self.instance_id
    @property
    def conversation(self):return self.audience

class BrokerProxy:
    def __init__(self,baseline):self.baseline=baseline;self.current=None
    def __getattr__(self,name):return getattr(self.current or self.baseline,name)

class GroupSession:
    def __init__(self,factory):
        self.factory=factory;self.resource=None;self.proxy=None;self.init_lock=asyncio.Lock();self.lock=asyncio.Lock();self.active=None
    async def bind(self,conversation):
        async with self.init_lock:
            if self.resource is None:
                candidate=await self.factory(conversation)
                self.proxy=candidate.broker
                try:await candidate.start(resource=True)
                except BaseException:
                    await candidate.close();self.proxy=None;raise
                self.resource=candidate
        return GroupBrain(self,conversation)
    async def close(self):
        if self.resource:await self.resource.close()

class GroupBrain:
    shared=True
    def __init__(self,session,conversation):self.session=session;self.conversation=conversation
    @property
    def session_id(self):return self.session.resource.brain.session_id
    async def turn(self,text):
        manager=self.session
        queued=time.monotonic()
        async with manager.lock:
            logging.getLogger('brodar.metrics').warning('Shared group queue_wait_ms=%d',int((time.monotonic()-queued)*1000))
            cap=self.conversation.broker.capability
            guard=getattr(self.conversation.broker,'loop_guard',None)
            if cap is not None and getattr(cap,'peer_bot_id',None) and guard and not await guard.available(cap.peer_bot_id):
                from .brains.base import Outcome
                return Outcome('completed','skip','host bot loop limit',session_id=self.session_id)
            manager.active=self.conversation;manager.proxy.current=self.conversation.broker
            scope=self.conversation.store.scope
            try:
                header=render('shared-group-turn.md',chat_id=scope.chat_id,topic_id=scope.topic_id)
                result=await manager.resource.brain.turn(header+'\n'+text)
                if self.session_id and self.session_id!=getattr(manager.resource,'_saved_session',None):
                    await manager.resource.store.session_put(self.conversation.cfg.brain,manager.resource.revision,self.session_id)
                    manager.resource._saved_session=self.session_id
                return result
            finally:manager.proxy.current=None;manager.active=None
    async def steer(self,text):
        if self.session.active is not self.conversation:return False
        return await self.session.resource.brain.steer(text)
    async def interrupt(self):
        if self.session.active is self.conversation:await self.session.resource.brain.interrupt()
    async def close(self):pass  # only GroupSession owns process lifetime
    async def start(self,session_id=None):
        async with self.session.lock:
            brain=self.session.resource.brain
            await brain.close();await brain.start(session_id or self.session_id)
