"""Durable provider sessions, inbox leases and delivery receipts."""

import json
from datetime import datetime,timezone
from uuid import NAMESPACE_URL,uuid5

from .store import ScopedStore,records,digest


class RuntimeStore(ScopedStore):
    async def claim_inputs(self,limit=50):
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute("UPDATE brodar_v2.inbox SET status='running',attempts=attempts+1,"
                          "lease_until=now()+interval '15 minutes' WHERE id IN ("
                          "SELECT id FROM brodar_v2.inbox WHERE audience=%s AND topic_id=%s "
                          "AND (status='pending' OR (status='running' AND lease_until<now())) "
                          "ORDER BY update_id LIMIT %s FOR UPDATE SKIP LOCKED) RETURNING *",
                          (self.scope.audience,self.scope.topic_id,max(1,min(limit,100))))
                return sorted(records(c),key=lambda r:r['update_id'])
        return await self.db.run(op)

    async def finish_inputs(self,ids,*,error=None,retry=False):
        if not ids:return
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute('UPDATE brodar_v2.inbox SET status=%s,lease_until=NULL,error_code=%s '
                          'WHERE audience=%s AND id=ANY(%s::uuid[])',
                          ('pending' if retry else ('failed' if error else 'done'),error,self.scope.audience,[str(x) for x in ids]))
        await self.db.run(op)

    async def session_get(self,provider,prompt_revision):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT session_id FROM brodar_v2.sessions WHERE audience=%s AND topic_id=%s '
                          'AND provider=%s AND prompt_revision=%s',
                          (self.scope.audience,self.scope.topic_id,provider,prompt_revision))
                r=c.fetchone()
                return r[0] if r else None
        return await self.db.run(op)

    async def session_put(self,provider,prompt_revision,session_id):
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute('INSERT INTO brodar_v2.sessions(audience,topic_id,provider,prompt_revision,session_id) '
                          'VALUES (%s,%s,%s,%s,%s) ON CONFLICT(audience,topic_id,provider,prompt_revision) '
                          'DO UPDATE SET session_id=excluded.session_id,updated_at=now()',
                          (self.scope.audience,self.scope.topic_id,provider,prompt_revision,session_id))
        await self.db.run(op)

    async def delivery_begin(self,turn_id,call_id,method,request):
        uid=str(uuid5(NAMESPACE_URL,f'{turn_id}:{call_id}'))
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute('INSERT INTO brodar_v2.deliveries(id,audience,topic_id,turn_id,tool_call_id,method,request,status) '
                          "VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb,'pending') ON CONFLICT(turn_id,tool_call_id) DO NOTHING",
                          (uid,self.scope.audience,self.scope.topic_id,str(turn_id),str(call_id),method,json.dumps(request)))
                c.execute('SELECT * FROM brodar_v2.deliveries WHERE audience=%s AND id=%s FOR UPDATE',
                          (self.scope.audience,uid))
                r=records(c)[0]
                if r['status']=='pending':
                    c.execute("UPDATE brodar_v2.deliveries SET status='sending',updated_at=now() WHERE id=%s",(uid,))
                return r
        return await self.db.run(op)

    async def delivery_finish(self,uid,status,response):
        if status not in {'sent','failed','uncertain'}:raise ValueError('Invalid receipt status')
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute('UPDATE brodar_v2.deliveries SET status=%s,response=%s::jsonb,updated_at=now() '
                          'WHERE audience=%s AND id=%s',(status,json.dumps(response,default=str),self.scope.audience,str(uid)))
                if status=='sent' and response.get('message_id'):
                    c.execute('SELECT method,request FROM brodar_v2.deliveries WHERE audience=%s AND id=%s',
                              (self.scope.audience,str(uid)))
                    delivery=c.fetchone()
                    if delivery and delivery[0]=='send_message':
                        request=delivery[1]
                        mid=str(uuid5(NAMESPACE_URL,f'{self.scope.audience}:telegram:{response["message_id"]}'))
                        c.execute('INSERT INTO brodar_v2.messages '
                                  '(id,audience,topic_id,platform_message_id,sender_id,sender_name,role,content,'
                                  'sent_at,reply_to_id,source_id,source_hash) '
                                  "VALUES (%s,%s,%s,%s,%s,'brodar','assistant',%s,%s,%s,%s,%s) "
                                  'ON CONFLICT(audience,source,source_id) DO NOTHING',
                                  (mid,self.scope.audience,self.scope.topic_id,response['message_id'],self.scope.bot_id,
                                   request['text'],datetime.now(timezone.utc),request.get('reply_to_message_id'),
                                   str(response['message_id']),digest(request['text'])))
        await self.db.run(op)

    async def delivery_receipts(self,turn_id):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT id,tool_call_id,method,status,response FROM brodar_v2.deliveries '
                          'WHERE audience=%s AND turn_id=%s ORDER BY created_at',
                          (self.scope.audience,str(turn_id)))
                return records(c)
        return await self.db.run(op)

    async def history_get(self,ids):
        if not 1<=len(ids)<=50:raise ValueError('Request 1 to 50 message IDs')
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT id,topic_id,platform_message_id,sender_id,sender_name,role,content,sent_at,'
                          'legacy_timestamp,reply_to_id,source,source_id FROM brodar_v2.messages '
                          'WHERE audience=%s AND id=ANY(%s::uuid[]) ORDER BY received_at,id',
                          (self.scope.audience,ids))
                return records(c)
        return await self.db.run(op)

    async def messages_for_index(self,model,limit=20):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute("SELECT m.id,m.content,'message' AS kind FROM brodar_v2.messages m "
                          "LEFT JOIN brodar_v2.embeddings e ON e.audience=m.audience AND e.source_id=m.id "
                          "AND e.kind='message' AND e.model=%s WHERE m.audience=%s AND length(m.content) BETWEEN 1 AND 7000 "
                          "AND (e.source_id IS NULL OR e.content_hash<>m.source_hash) ORDER BY m.received_at LIMIT %s",
                          (model,self.scope.audience,max(1,min(limit,100))))
                rows=records(c)
                if len(rows)<limit:
                    c.execute("SELECT m.id,m.content,'memory' AS kind FROM brodar_v2.memory_documents m "
                              "LEFT JOIN brodar_v2.embeddings e ON e.audience=m.audience AND e.source_id=m.id "
                              "AND e.kind='memory' AND e.model=%s WHERE m.audience=%s AND NOT m.deleted "
                              "AND length(m.content) BETWEEN 1 AND 7000 AND e.source_id IS NULL "
                              "ORDER BY m.updated_at LIMIT %s",(model,self.scope.audience,limit-len(rows)))
                    rows.extend(records(c))
                return rows
        return await self.db.run(op)
