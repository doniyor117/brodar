"""Durable one-shot reminders; no model inference is needed at delivery time."""

import json
from dataclasses import dataclass
from datetime import datetime,timezone
from uuid import NAMESPACE_URL,UUID,uuid5

from .store import records


@dataclass(frozen=True)
class InputRequest:
    id:str
    actor:int
    text:str


def input_requests(batch):
    result=[]
    for row in {str(row['message_id']):row for row in batch}.values():
        message=row['payload'].get('message') or row['payload'].get('edited_message') or {}
        sender=message.get('from') or {}
        if (not sender.get('id') or sender.get('is_bot') or message.get('sender_chat') or
                message.get('forward_origin') or message.get('forward_from')):continue
        result.append(InputRequest(str(row['message_id']),sender['id'],message.get('text') or message.get('caption') or ''))
    return result


class Reminders:
    def __init__(self,store):self.store=store

    def _fence(self,cursor):
        owner=getattr(self.store,'lease_owner',None)
        if owner:
            cursor.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR UPDATE',
                           (self.store.scope.bot_id,owner))
            if cursor.fetchone() is None:raise PermissionError('Reminder mutation consumer lease expired')

    async def create(self,request,when,text,operation_id):
        due=datetime.fromisoformat(when.replace('Z','+00:00'))
        if due.tzinfo is None:raise ValueError('Use an ISO timestamp with an explicit timezone')
        if due<=datetime.now(timezone.utc):raise ValueError('Reminder time must be in the future')
        if not text.strip() or len(text)>4000:raise ValueError('Reminder text must contain 1..4000 characters')
        if not operation_id or len(operation_id)>200:raise ValueError('Use an operation ID of 1..200 characters')
        scope=self.store.scope
        uid=str(uuid5(NAMESPACE_URL,scope.audience+':reminder:'+request.id+':'+operation_id))
        payload={'text':text,'requester_id':request.actor,'request_message_id':request.id}
        def op():
            with self.store.db.transaction(scope.audience) as c:
                self._fence(c)
                c.execute("INSERT INTO brodar_v2.jobs(id,audience,topic_id,kind,payload,due_at,dedup_key) "
                          "VALUES (%s,%s,%s,'reminder',%s::jsonb,%s,%s) ON CONFLICT DO NOTHING",
                          (uid,scope.audience,scope.topic_id,json.dumps(payload),due,uid))
                c.execute('SELECT id,payload,due_at,status FROM brodar_v2.jobs WHERE audience=%s AND id=%s',
                          (scope.audience,uid))
                row=records(c)[0]
                original={k:v for k,v in row['payload'].items() if k!='consumer_owner'}
                if original!=payload or row['due_at']!=due:raise ValueError('Reminder operation ID already has different content')
                return {'job_id':str(row['id']),'due_at':row['due_at'],'status':row['status']}
        return await self.store.db.run(op)

    async def list(self,limit=30):
        def op():
            with self.store.db.transaction(self.store.scope.audience,readonly=True) as c:
                c.execute("SELECT id,topic_id,status,due_at,payload->>'text' AS text,payload->>'requester_id' AS requester_id "
                          "FROM brodar_v2.jobs WHERE audience=%s AND kind='reminder' ORDER BY due_at DESC LIMIT %s",
                          (self.store.scope.audience,max(1,min(limit,100))))
                return records(c)
        return await self.store.db.run(op)

    async def cancel(self,uid,actor,owners=()):
        uid=str(UUID(uid))
        def op():
            with self.store.db.transaction(self.store.scope.audience) as c:
                self._fence(c)
                c.execute("SELECT payload,status FROM brodar_v2.jobs WHERE audience=%s AND id=%s AND kind='reminder' FOR UPDATE",
                          (self.store.scope.audience,uid))
                row=c.fetchone()
                if row is None:raise ValueError('No reminder in this audience has that job ID')
                if actor!=row[0]['requester_id'] and actor not in owners:
                    raise PermissionError('Only its requester or the bot owner can cancel this reminder')
                if row[1] not in {'pending','running'}:return {'job_id':uid,'status':row[1],'cancelled':False}
                c.execute("UPDATE brodar_v2.jobs SET status='cancelled',lease_until=NULL,updated_at=now() WHERE id=%s",(uid,))
                return {'job_id':uid,'status':'cancelled','cancelled':row[1]=='pending',
                        'delivery_may_be_in_flight':row[1]=='running'}
        return await self.store.db.run(op)

    async def claim(self):
        scope=self.store.scope;owner=self.store.lease_owner
        if not owner:raise PermissionError('Reminder scheduler requires an active consumer lease')
        def op():
            with self.store.db.transaction(scope.audience) as c:
                c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now()',
                          (scope.bot_id,owner))
                if c.fetchone() is None:raise PermissionError('Reminder consumer lease expired')
                c.execute("UPDATE brodar_v2.jobs SET status='running',attempts=attempts+1,lease_until=now()+interval '2 minutes',"
                          "payload=jsonb_set(payload,'{consumer_owner}',to_jsonb(%s::text)),updated_at=now() WHERE id IN ("
                          "SELECT id FROM brodar_v2.jobs WHERE audience=%s AND kind='reminder' AND due_at<=now() "
                          "AND (status='pending' OR (status='running' AND (lease_until<now() OR payload->>'consumer_owner' IS DISTINCT FROM %s))) "
                          "ORDER BY due_at LIMIT 10 FOR UPDATE SKIP LOCKED) RETURNING *",(owner,scope.audience,owner))
                return records(c)
        return await self.store.db.run(op)

    async def active(self,uid):
        def op():
            with self.store.db.transaction(self.store.scope.audience,readonly=True) as c:
                c.execute("SELECT 1 FROM brodar_v2.jobs WHERE audience=%s AND id=%s AND status='running' "
                          "AND payload->>'consumer_owner'=%s",(self.store.scope.audience,str(uid),self.store.lease_owner))
                if c.fetchone() is None:raise PermissionError('Reminder is cancelled or claimed by another consumer')
        return await self.store.db.run(op)

    async def finish(self,uid,status):
        if status not in {'done','failed','uncertain'}:raise ValueError('Invalid reminder status')
        def op():
            with self.store.db.transaction(self.store.scope.audience) as c:
                c.execute("UPDATE brodar_v2.jobs SET status=%s,lease_until=NULL,updated_at=now() "
                          "WHERE audience=%s AND id=%s AND status='running' AND payload->>'consumer_owner'=%s",
                          (status,self.store.scope.audience,str(uid),self.store.lease_owner))
        await self.store.db.run(op)


async def dispatch_reminder(job,store,bot,lease_guard):
    from .broker import ToolBroker,TurnCapability
    reminders=Reminders(store)
    async def guard():
        await lease_guard()
        await reminders.active(job['id'])
    broker=ToolBroker(store,bot,delivery_guard=guard)
    broker.begin(TurnCapability(str(job['id']),requester_id=job['payload']['requester_id']))
    try:
        await broker.send_message(job['payload']['text'],call_id='reminder-delivery')
        status='done'
    except Exception:
        receipts=await store.delivery_receipts(job['id'])
        status='uncertain' if any(row['status'] in {'sending','uncertain'} for row in receipts) else 'failed'
    finally:broker.end()
    await reminders.finish(job['id'],status)
    return status
