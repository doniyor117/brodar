"""Explicit, receipted group routing and research task briefs; no hidden text parser."""
import hashlib
import re
from uuid import UUID,uuid4,uuid5,NAMESPACE_URL
from .scope import Scope
from .learning import LearningStore
from .broker import ToolBroker,TurnCapability
from .store import records
from .prompt_files import render
from .redaction import scrub

def peer_key(sender,researcher):
    username=(sender.get('username') or '').lower().lstrip('@')
    if username:return -int(hashlib.sha256(username.encode()).hexdigest()[:12],16)
    return sender.get('id')

class GroupRouter:
    def __init__(self,cfg,access,bot,db,objects,lease):
        self.cfg,self.access,self.bot,self.db,self.objects,self.lease=cfg,access,bot,db,objects,lease
        self.loop_guard=None
    def target(self,chat_id,topic=0):
        if chat_id not in self.access.groups:raise PermissionError('Destination must be an approved group')
        lease=self.lease()
        return LearningStore(self.db,Scope(lease.bot_id,chat_id,topic,self.cfg.instance_id),lease_owner=lease.owner)
    async def rooms(self):
        result=[]
        for chat_id in sorted(self.access.groups):
            try:
                chat=await self.bot.get_chat(chat_id)
                result.append({'chat_id':chat_id,'title':chat.title,'type':chat.type})
            except Exception:result.append({'chat_id':chat_id,'metadata_unavailable':True})
        return result
    async def send(self,source,chat_id,text,operation,topic=0,reply_to=None,peer=None):
        cap=source.require(visible=True)
        target=self.target(chat_id,topic)
        if source.store.scope.chat_id>0 and cap.requester_id not in self.cfg.owner_ids:raise PermissionError('Only owner DM may route to groups')
        def guard():
            if source.capability is not cap:raise PermissionError('Source turn revoked')
        targets=set()
        if peer:targets.add(peer)
        for username in re.findall(r'@([A-Za-z0-9_]*bot)\b',text,re.I):
            if username.lower()!=(getattr(self.bot,'username','') or '').lower():
                targets.add(peer_key({'username':username},self.cfg.research_bot_username))
        for target_peer in targets:
            if target_peer!=cap.peer_bot_id:
                await self.loop_guard.reserve(target_peer,chat_id,f'target-peer:{target_peer}:{cap.turn_id}:route:{operation}:{chat_id}')
        async def routed_send():
            from .write_fence import WRITE_FENCE
            token=WRITE_FENCE.set(guard)
            try:
                target_broker=ToolBroker(target,self.bot,delivery_guard=self.lease().require,owner_ids=self.cfg.owner_ids)
                target_broker.begin(TurnCapability(cap.turn_id,requester_id=cap.requester_id))
                async def delivery_guard():guard();await self.lease().require()
                target_broker.delivery_guard=delivery_guard
                await target_broker.send_message(text,call_id='target:'+operation,reply_to_message_id=reply_to)
                return True
            finally:WRITE_FENCE.reset(token)
        return await source.deliver(cap,'routed_message',{'chat_id':chat_id,'topic_id':topic,'text':text,'reply_to':reply_to},'route:'+operation,routed_send)
    async def delegate(self,source,brief,operation):
        cap=source.require(visible=True)
        if cap.peer_bot_id:raise PermissionError('A bot cannot recursively create research tasks; finish the current exchange')
        if len(brief)>3000 or not brief.strip():raise ValueError('Brief must contain 1..3000 characters; use artifact references for larger context')
        chat_id=self.cfg.research_chat_id
        self.target(chat_id)
        scope=source.store.scope
        task=str(uuid5(NAMESPACE_URL,f'{cap.turn_id}:research:{operation}'))
        peer=peer_key({'username':self.cfg.research_bot_username},self.cfg.research_bot_username)
        def save():
            with self.db.transaction(self.loop_guard.audience) as c:
                c.execute('INSERT INTO brodar_v2.research_tasks(audience,id,origin_chat_id,origin_topic_id,requester_id,request_id,brief) VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING RETURNING id',
                    (self.loop_guard.audience,task,scope.chat_id,scope.topic_id,cap.requester_id,cap.turn_id,scrub(brief)))
                created=c.fetchone() is not None
                c.execute('SELECT brief,origin_chat_id,origin_topic_id FROM brodar_v2.research_tasks WHERE audience=%s AND id=%s',(self.loop_guard.audience,task))
                old=c.fetchone()
                if list(old)!=[scrub(brief),scope.chat_id,scope.topic_id]:raise ValueError('Research operation already has different content')
                return created
        created=await self.db.run(save)
        # Only a fresh human delegation resets this peer, never another bot reply.
        # New task generation resets only at first creation; retries never replenish the cap.
        if created:await self.loop_guard.human(chat_id)
        text=render('research-delegation.md',username=self.cfg.research_bot_username,task_id=task,
            source_chat=scope.chat_id,source_topic=scope.topic_id,brief=scrub(brief))
        sent=await self.send(source,chat_id,text,operation,peer=peer)
        return {'task_id':task,'status':'delegated','delivery':sent,'return_chat_id':scope.chat_id,'return_topic_id':scope.topic_id}
    async def tasks(self):
        def read():
            with self.db.transaction(self.loop_guard.audience,readonly=True) as c:
                c.execute("SELECT id,origin_chat_id,origin_topic_id,status,created_at,deadline,brief FROM brodar_v2.research_tasks WHERE audience=%s AND created_at>now()-interval '1 day' ORDER BY created_at DESC LIMIT 30",(self.loop_guard.audience,))
                return records(c)
        return await self.db.run(read)
    async def search(self,chat_id,query,vector=None,limit=10):return await self.target(chat_id).search(query,vector=vector,limit=limit)
    async def return_result(self,source,task_id,report,operation):
        cap=source.require(visible=True)
        task_id=str(UUID(task_id))
        def lookup():
            with self.db.transaction(self.loop_guard.audience,readonly=True) as c:
                c.execute("SELECT origin_chat_id,origin_topic_id,requester_id FROM brodar_v2.research_tasks WHERE audience=%s AND id=%s AND deadline>now()",(self.loop_guard.audience,task_id))
                row=c.fetchone()
                if row is None:raise ValueError('Research task missing or expired')
                return row
        chat_id,topic,requester=await self.db.run(lookup)
        if len(report)>12000 or not report.strip():raise ValueError('Report must contain 1..12000 characters; use an artifact for longer reports')
        chunks=[report[i:i+3800] for i in range(0,len(report),3800)]
        for i,text in enumerate(chunks):
            if chat_id<0:await self.send(source,chat_id,text,f'{operation}:{i}',topic)
            else:
                if chat_id not in self.cfg.owner_ids or chat_id!=requester:raise PermissionError('Invalid private research return route')
                target=LearningStore(self.db,Scope(self.lease().bot_id,chat_id,instance_id=self.cfg.instance_id),lease_owner=self.lease().owner)
                async def send_private(text=text,index=i):
                    async def guard():
                        await self.lease().require()
                        if source.capability is not cap:raise PermissionError('Source turn revoked')
                    broker=ToolBroker(target,self.bot,delivery_guard=guard)
                    broker.begin(TurnCapability(cap.turn_id))
                    await broker.send_message(text,call_id=f'private-return:{operation}:{index}')
                    return True
                await source.deliver(cap,'research_return',{'task_id':task_id,'part':i,'text':text},f'{operation}:{i}',send_private)
        def finish():
            with self.db.transaction(self.loop_guard.audience) as c:c.execute("UPDATE brodar_v2.research_tasks SET status='done' WHERE audience=%s AND id=%s",(self.loop_guard.audience,task_id))
        await self.db.run(finish)
        if cap.peer_bot_id:await self.loop_guard.close(cap.peer_bot_id)
        return {'task_id':task_id,'status':'done','return_chat_id':chat_id,'parts':len(chunks)}
    async def send_document(self,source,chat_id,artifact_id,operation,caption='',topic=0):
        from .artifacts import Artifacts
        cap=source.require(visible=True);target=self.target(chat_id,topic)
        if source.artifacts is None or self.objects is None:raise RuntimeError('Durable object storage unavailable')
        row=await source.artifacts.get_record(artifact_id)
        if row is None:raise ValueError('Source artifact missing')
        if (row.get('metadata') or {}).get('sensitive'):raise PermissionError('A sensitive-read approval does not authorize cross-group file disclosure; create an explicitly redacted deliverable')
        async def transfer():
            original,body=await source.artifacts.read(artifact_id)
            if source.capability is not cap:raise PermissionError('Source turn revoked')
            artifacts=Artifacts(target,self.objects)
            item=await artifacts.put(turn_id=cap.turn_id,operation_id='transfer:'+operation,filename=original['filename'],mime=original['mime_type'],body=body,request_hash=original['content_hash'],metadata={'source_artifact_id':artifact_id})
            async def guard():
                await self.lease().require()
                if source.capability is not cap:raise PermissionError('Source turn revoked')
            broker=ToolBroker(target,self.bot,artifacts=artifacts,delivery_guard=guard)
            broker.begin(TurnCapability(cap.turn_id))
            await broker.send_document(item['artifact_id'],caption,'target-file:'+operation)
            return True
        return await source.deliver(cap,'routed_document',{'chat_id':chat_id,'topic_id':topic,'artifact_id':artifact_id,'caption':caption},'route-file:'+operation,transfer)
