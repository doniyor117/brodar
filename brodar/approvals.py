"""Durable, exact-action approval. Only trusted owner private-message metadata decides."""
import json
from uuid import uuid4,UUID
from .scope import Scope
from .store import records,digest
from .redaction import scrub

class Approvals:
    def __init__(self,db,bot,cfg):self.db,self.bot,self.cfg=db,bot,cfg

    async def request(self,scope,turn_id,artifact_id,content_hash,reason):
        action={'kind':'sensitive_file_read','artifact_id':str(UUID(artifact_id)),'content_hash':content_hash}
        return await self.request_action(scope,turn_id,action,reason)

    async def request_action(self,scope,turn_id,action,reason):
        uid=str(uuid4());fingerprint=digest(json.dumps(action,sort_keys=True))
        def save():
            with self.db.transaction(scope.audience) as c:
                c.execute("DELETE FROM brodar_v2.approvals WHERE audience=%s AND created_at<now()-interval '30 days'",(scope.audience,))
                c.execute('INSERT INTO brodar_v2.approvals(id,audience,topic_id,turn_id,action_hash,action) VALUES (%s,%s,%s,%s,%s,%s::jsonb)',
                    (uid,scope.audience,scope.topic_id,turn_id,fingerprint,json.dumps(action)))
        await self.db.run(save)
        text=(f'Brodar approval {uid}\nAction: {scrub(json.dumps(action))[:2000]}\nFor chat {scope.chat_id}, topic {scope.topic_id}. '
              f'Its contents may be used in replies there.\nReason: {scrub(reason)[:500]}\n'
              f'/approve {uid}\n/deny {uid}\nExpires in 15 minutes. Approves this exact action only.')
        from .write_fence import WRITE_FENCE
        notified=False
        for owner in sorted(self.cfg.owner_ids):
            try:
                guard=WRITE_FENCE.get()
                if guard:guard()
                await self.bot.send_message(chat_id=owner,text=text);notified=True
            except Exception:pass
        return {'approval_id':uid,'status':'pending','owner_dm_notified':notified}

    async def decide(self,chat_id,sender_id,text,bot_id,*,private=False,anonymous=False):
        if not private or anonymous or chat_id!=sender_id or sender_id not in self.cfg.owner_ids:return False
        parts=text.strip().split()
        if len(parts)!=2 or parts[0] not in {'/approve','/deny'}:return False
        try:uid=str(UUID(parts[1]))
        except ValueError:return False
        for target in self.cfg.allowed_chats:
            scope=Scope(bot_id,target,instance_id=self.cfg.instance_id)
            def update():
                with self.db.transaction(scope.audience) as c:
                    c.execute("UPDATE brodar_v2.approvals SET status=%s,decided_by=%s,decided_at=now() WHERE audience=%s AND id=%s AND status='pending' AND expires_at>now() RETURNING id",
                        ('approved' if parts[0]=='/approve' else 'denied',sender_id,scope.audience,uid))
                    return c.fetchone() is not None
            if await self.db.run(update):return True
        return False

    async def consume(self,scope,uid,artifact_id,content_hash):
        action={'kind':'sensitive_file_read','artifact_id':str(UUID(artifact_id)),'content_hash':content_hash}
        return await self.consume_action(scope,uid,action)

    async def consume_action(self,scope,uid,action):
        fingerprint=digest(json.dumps(action,sort_keys=True))
        def use():
            with self.db.transaction(scope.audience) as c:
                c.execute("UPDATE brodar_v2.approvals SET status='used' WHERE audience=%s AND topic_id=%s AND id=%s AND action_hash=%s AND status='approved' AND expires_at>now() RETURNING id",
                    (scope.audience,scope.topic_id,str(UUID(uid)),fingerprint))
                if not c.fetchone():raise PermissionError('Approval missing, expired, already used or for another action')
        await self.db.run(use)
