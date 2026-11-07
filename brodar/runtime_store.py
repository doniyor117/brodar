"""Durable provider sessions, inbox leases and delivery receipts."""

import json
from datetime import datetime,timezone
from uuid import NAMESPACE_URL,uuid5

from .store import ScopedStore,records,digest
from .redaction import scrub,scrub_object
from uuid import uuid4


class RuntimeStore(ScopedStore):
    async def enqueue_research_result(self,task,content):
        """Idempotent internal input; never touches Telegram's polling cursor.

        The consumer row serializes allocation of negative internal update IDs.
        Ordinary Telegram update IDs are nonnegative. No sender is impersonated.
        """
        source_id=f'{task["id"]}:{task["generation"]}:{task.get("result_version",0)}'
        mid=str(uuid5(NAMESPACE_URL,self.scope.audience+':research:'+source_id))
        iid=str(uuid5(NAMESPACE_URL,self.scope.audience+':research-input:'+source_id))
        content=scrub(content)
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR UPDATE',
                          (self.scope.bot_id,self.lease_owner))
                if c.fetchone() is None:raise PermissionError('Research result consumer lease expired')
                c.execute('SELECT update_id FROM brodar_v2.inbox WHERE audience=%s AND id=%s',(self.scope.audience,iid))
                existing=c.fetchone()
                if existing:return existing[0],False
                c.execute('UPDATE brodar_v2.poll_state SET internal_update_id=internal_update_id-1 WHERE bot_id=%s RETURNING internal_update_id',(self.scope.bot_id,))
                update_id=c.fetchone()[0]
                c.execute('INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                          (self.scope.audience,self.scope.instance_id,self.scope.chat_id))
                c.execute("INSERT INTO brodar_v2.messages(id,audience,topic_id,sender_name,role,content,source,source_id,source_hash) "
                          "VALUES (%s,%s,%s,'Hyper Researcher','tool',%s,'research',%s,%s) ON CONFLICT DO NOTHING",
                          (mid,self.scope.audience,self.scope.topic_id,content,source_id,digest(content)))
                payload={'internal_research':{'task_id':str(task['id']),'generation':task['generation'],
                    'requester_id':task['requester_id'],'status':task['status'],'result_version':task.get('result_version',0)}}
                c.execute('INSERT INTO brodar_v2.inbox(id,audience,topic_id,bot_id,update_id,message_id,payload) VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb)',
                          (iid,self.scope.audience,self.scope.topic_id,self.scope.bot_id,update_id,mid,json.dumps(payload)))
                return update_id,True
        return await self.db.run(op)

    async def work_state(self,uid):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute("SELECT jsonb_build_object('receipts',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) "
                          "FROM (SELECT id,topic_id,tool_call_id,method,status,response,request FROM brodar_v2.deliveries "
                          "WHERE audience=%s AND turn_id=%s ORDER BY created_at) t),"
                          "'artifacts',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (SELECT id AS artifact_id,filename "
                          "FROM brodar_v2.artifacts WHERE audience=%s AND turn_id=%s) t))",
                          (self.scope.audience,str(uid),self.scope.audience,str(uid)))
                return c.fetchone()[0]
        return await self.db.run(op)

    async def reply_context(self,message_ids):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('WITH RECURSIVE chain AS ('
                    'SELECT m.*,0 AS depth,ARRAY[m.id] AS path FROM brodar_v2.messages m '
                    'WHERE m.audience=%s AND m.id=ANY(%s::uuid[]) UNION ALL '
                    'SELECT p.*,c.depth+1,c.path||p.id FROM chain c JOIN brodar_v2.messages p '
                    'ON p.audience=c.audience AND p.platform_message_id=c.reply_to_id '
                    'WHERE c.depth<8 AND NOT p.id=ANY(c.path)) '
                    'SELECT DISTINCT ON(id) * FROM chain WHERE depth>0 ORDER BY id,depth',
                    (self.scope.audience,message_ids))
                return records(c)
        return await self.db.run(op)

    async def attach_content(self,uid,text):
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute('UPDATE brodar_v2.messages SET content=%s,source_hash=%s,changed_at=now() '
                          'WHERE audience=%s AND id=%s AND source_hash<>%s RETURNING id',
                          (text,digest(text),self.scope.audience,str(uid),digest(text)))
                if c.fetchone() is None:return
                c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='message' AND source_id=%s",(self.scope.audience,str(uid)))
                c.execute('UPDATE brodar_v2.memory_documents SET needs_review=true WHERE audience=%s AND id IN '
                          '(SELECT document_id FROM brodar_v2.memory_sources WHERE audience=%s AND message_id=%s)',
                          (self.scope.audience,self.scope.audience,str(uid)))
                c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='memory' AND source_id IN "
                          '(SELECT document_id FROM brodar_v2.memory_sources WHERE audience=%s AND message_id=%s)',
                          (self.scope.audience,self.scope.audience,str(uid)))
        await self.db.run(op)

    async def work_begin(self,uid,requester_id,message_ids):
        payload={'requester_id':requester_id,'request_message_id':message_ids[0],
                 'message_ids':message_ids,'consumer_owner':self.lease_owner}
        def op():
            with self.db.transaction(self.scope.audience) as c:
                if self.lease_owner:
                    c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR UPDATE',
                              (self.scope.bot_id,self.lease_owner))
                    if c.fetchone() is None:raise PermissionError('Work consumer lease expired')
                c.execute("INSERT INTO brodar_v2.jobs(id,audience,topic_id,kind,payload,dedup_key,status,attempts) "
                          "VALUES (%s,%s,%s,'work',%s::jsonb,%s,'running',1) ON CONFLICT(id) DO UPDATE "
                          "SET status=CASE WHEN jobs.status IN ('done','cancelled') THEN jobs.status ELSE 'running' END,"
                          "attempts=jobs.attempts+1,payload=jsonb_set(jsonb_set(jobs.payload,'{consumer_owner}',%s::jsonb),"
                          "'{message_ids}',coalesce(jobs.payload->'message_ids','[]'::jsonb)||%s::jsonb),updated_at=now() RETURNING status,checkpoint",
                          (uid,self.scope.audience,self.scope.topic_id,json.dumps(payload),uid,json.dumps(self.lease_owner),json.dumps(message_ids)))
                return records(c)[0]
        return await self.db.run(op)

    async def work_checkpoint(self,uid,text):
        if not text.strip() or len(text)>12000:raise ValueError('Checkpoint must contain 1..12000 characters')
        def op():
            with self.db.transaction(self.scope.audience) as c:
                if self.lease_owner:
                    c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR UPDATE',
                              (self.scope.bot_id,self.lease_owner))
                    if c.fetchone() is None:raise PermissionError('Work consumer lease expired')
                c.execute("UPDATE brodar_v2.jobs SET checkpoint=%s::jsonb,updated_at=now() WHERE audience=%s AND id=%s "
                          "AND kind='work' AND status='running' AND payload->>'consumer_owner' IS NOT DISTINCT FROM %s RETURNING id",
                          (json.dumps({'notes':scrub(text)}),self.scope.audience,str(uid),self.lease_owner))
                if c.fetchone() is None:raise PermissionError('No current active work item can accept this checkpoint')
                return {'job_id':str(uid),'checkpoint_saved':True}
        return await self.db.run(op)

    async def work_add_inputs(self,uid,message_ids):
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute("UPDATE brodar_v2.jobs SET payload=jsonb_set(payload,'{message_ids}',"
                          "coalesce(payload->'message_ids','[]'::jsonb)||%s::jsonb) WHERE audience=%s AND id=%s "
                          "AND payload->>'consumer_owner' IS NOT DISTINCT FROM %s",
                          (json.dumps(message_ids),self.scope.audience,str(uid),self.lease_owner))
        await self.db.run(op)

    async def work_list(self,limit=30):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute("SELECT id,topic_id,status,checkpoint,attempts,created_at,updated_at,payload->>'requester_id' AS requester_id "
                          "FROM brodar_v2.jobs WHERE audience=%s AND kind='work' ORDER BY updated_at DESC LIMIT %s",
                          (self.scope.audience,max(1,min(limit,100))))
                return records(c)
        return await self.db.run(op)

    async def work_artifacts(self,uid):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT id AS artifact_id,filename FROM brodar_v2.artifacts WHERE audience=%s AND turn_id=%s',
                          (self.scope.audience,str(uid)))
                return records(c)
        return await self.db.run(op)

    async def history_browse(self,*,before_id=None,sender_id=None,topic_id=None,limit=40):
        """Walk the archive without relying on semantic search finding a quote."""
        from uuid import UUID
        if before_id:before_id=str(UUID(before_id))
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                anchor=None
                if before_id:
                    c.execute('SELECT received_at,id FROM brodar_v2.messages WHERE audience=%s AND id=%s',
                              (self.scope.audience,before_id))
                    anchor=c.fetchone()
                    if anchor is None:raise ValueError('History cursor is not in this audience')
                filters=['audience=%s'];args=[self.scope.audience]
                if anchor:filters.append('(received_at,id)<(%s,%s::uuid)');args.extend([anchor[0],str(anchor[1])])
                if sender_id is not None:filters.append('sender_id=%s');args.append(sender_id)
                if topic_id is not None:filters.append('topic_id=%s');args.append(topic_id)
                args.append(max(1,min(limit,100))+1)
                c.execute('SELECT id,topic_id,platform_message_id,sender_id,sender_name,role,content,sent_at,'
                          'legacy_timestamp,reply_to_id,source,received_at FROM brodar_v2.messages WHERE '+
                          ' AND '.join(filters)+' ORDER BY received_at DESC,id DESC LIMIT %s',tuple(args))
                rows=records(c);has_more=len(rows)>max(1,min(limit,100))
                rows=rows[:max(1,min(limit,100))]
                return {'messages':list(reversed(rows)),'has_more':has_more,
                        'next_before_id':str(rows[-1]['id']) if rows and has_more else None}
        return await self.db.run(op)

    async def audit_tool(self,*,turn_id,name,arguments,result,error,duration_ms):
        arguments=scrub_object(arguments)
        output=scrub_object(result)
        def bounded(value):
            encoded=json.dumps(value,default=str,ensure_ascii=False)
            return encoded if len(encoded)<=64000 else json.dumps({'truncated':True,'preview':encoded[:60000]})
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute('INSERT INTO brodar_v2.tool_calls(id,audience,turn_id,name,arguments,result,error,duration_ms) '
                    'VALUES (%s,%s,%s,%s,%s::jsonb,%s::jsonb,%s,%s)',
                    (str(uuid4()),self.scope.audience,turn_id,name,bounded(arguments),bounded(output),
                     scrub(error)[:4000] if error else None,duration_ms))
        await self.db.run(op)

    async def claim_inputs(self,limit=50,*,count_attempt=True,exclude_ids=()):
        def op():
            with self.db.transaction(self.scope.audience) as c:
                if self.lease_owner:
                    c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now()',
                              (self.scope.bot_id,self.lease_owner))
                    if c.fetchone() is None:raise PermissionError('Telegram consumer lease is no longer active')
                # Materialize the locked candidate IDs once. An IN subquery with
                # SKIP LOCKED can otherwise be rescanned as rows change status,
                # claiming more than LIMIT and charging excluded inputs again.
                c.execute("WITH claimed AS MATERIALIZED (SELECT id FROM brodar_v2.inbox "
                          "WHERE audience=%s AND topic_id=%s AND NOT (id=ANY(%s::uuid[])) "
                          "AND (status='pending' OR (status='running' AND (lease_until<now() OR "
                          "(%s::uuid IS NOT NULL AND lease_owner IS DISTINCT FROM %s::uuid)))) "
                          "ORDER BY update_id LIMIT %s FOR UPDATE SKIP LOCKED) "
                          "UPDATE brodar_v2.inbox i SET status='running',attempts=i.attempts+%s,"
                          "lease_owner=%s,lease_until=now()+interval '15 minutes' "
                          "FROM claimed WHERE i.id=claimed.id RETURNING i.*",
                          (self.scope.audience,self.scope.topic_id,[str(uid) for uid in exclude_ids],
                           self.lease_owner,self.lease_owner,max(1,min(limit,100)),int(count_attempt),self.lease_owner))
                return sorted(records(c),key=lambda r:r['update_id'])
        return await self.db.run(op)

    async def _update_active_inputs(self,ids,*,count_attempt=False):
        if not ids:return []
        def op():
            with self.db.transaction(self.scope.audience) as c:
                if self.lease_owner:
                    c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR UPDATE',
                              (self.scope.bot_id,self.lease_owner))
                    if c.fetchone() is None:raise PermissionError('Telegram consumer lease is no longer active')
                c.execute("UPDATE brodar_v2.inbox SET attempts=attempts+%s,lease_until=now()+interval '15 minutes' "
                          "WHERE audience=%s AND topic_id=%s AND id=ANY(%s::uuid[]) AND status='running' "
                          "AND lease_owner IS NOT DISTINCT FROM %s::uuid RETURNING *",
                          (int(count_attempt),self.scope.audience,self.scope.topic_id,[str(uid) for uid in ids],self.lease_owner))
                rows=records(c)
                if len(rows)!=len(set(map(str,ids))):
                    raise PermissionError('Input ownership changed during active work')
                return sorted(rows,key=lambda row:row['update_id'])
        return await self.db.run(op)

    async def mark_inputs_attempted(self,ids):
        """Charge a steering attempt only after the provider accepts its inputs."""
        return await self._update_active_inputs(ids,count_attempt=True)

    async def renew_inputs(self,ids):
        """Keep long-running inputs leased, without consuming execution retries."""
        return len(await self._update_active_inputs(ids))

    async def finish_inputs(self,ids,*,error=None,retry=False,job_id=None):
        if not ids:return
        def op():
            with self.db.transaction(self.scope.audience) as c:
                if self.lease_owner:
                    c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR UPDATE',
                              (self.scope.bot_id,self.lease_owner))
                    if c.fetchone() is None:return
                c.execute("UPDATE brodar_v2.inbox SET status=CASE WHEN %s AND attempts<3 THEN 'pending' "
                          "WHEN %s THEN 'failed' ELSE %s END,lease_until=NULL,error_code=%s "
                          'WHERE audience=%s AND id=ANY(%s::uuid[]) AND '
                          '(%s::uuid IS NULL OR lease_owner=%s::uuid OR (status=\'pending\' AND lease_owner IS NULL))',
                          (retry,retry,'failed' if error else 'done',error,self.scope.audience,[str(x) for x in ids],self.lease_owner,self.lease_owner))
                if job_id:
                    c.execute("SELECT 1 FROM brodar_v2.inbox WHERE audience=%s AND id=ANY(%s::uuid[]) AND status='pending' LIMIT 1",
                              (self.scope.audience,[str(x) for x in ids]))
                    pending=c.fetchone() is not None
                    status='pending' if retry and pending else ('failed' if retry else ('cancelled' if error in {'cancelled','turn_cancelled_or_timed_out'} else ('failed' if error else 'done')))
                    c.execute("UPDATE brodar_v2.jobs SET status=%s,updated_at=now() WHERE audience=%s AND id=%s AND kind='work' "
                              "AND payload->>'consumer_owner' IS NOT DISTINCT FROM %s",
                              (status,self.scope.audience,str(job_id),self.lease_owner))
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
                if status=='sent' and (response.get('message_id') or response.get('message_ids')):
                    c.execute('SELECT method,request FROM brodar_v2.deliveries WHERE audience=%s AND id=%s',(self.scope.audience,str(uid)))
                    media_delivery=c.fetchone()
                    native={'send_photo','send_video','send_animation','send_audio','send_voice','send_video_note','send_sticker','send_media_group','send_location','send_venue','send_contact','send_dice','copy_message','forward_message'}
                    if media_delivery and media_delivery[0] in native:
                        method,request=media_delivery
                        entries=request.get('items') if method=='send_media_group' else [request]
                        message_ids=response.get('message_ids') or [response['message_id']]
                        if len(entries)!=len(message_ids):raise ValueError('Telegram album receipt count mismatch')
                        for platform_id,item in zip(message_ids,entries):
                            kind=item.get('kind') or method.removeprefix('send_')
                            text=item.get('caption','')+'\n['+kind
                            if item.get('artifact_id'):text+='; artifact_id: '+str(item['artifact_id'])
                            if item.get('file_id'):text+='; file_id: '+item['file_id']
                            if item.get('url'):text+='; url: '+item['url']
                            if method in {'send_location','send_venue'}:text+='; latitude: '+str(item.get('latitude'))+'; longitude: '+str(item.get('longitude'))
                            if method=='send_venue':text+='; '+item.get('title','')+'; '+item.get('address','')
                            if method=='send_contact':text+='; '+item.get('first_name','')+' '+item.get('last_name','')
                            text+=']'
                            mid=str(uuid5(NAMESPACE_URL,f'{self.scope.audience}:telegram:{platform_id}'))
                            c.execute("INSERT INTO brodar_v2.messages(id,audience,topic_id,platform_message_id,sender_id,sender_name,role,content,sent_at,reply_to_id,source_id,source_hash,metadata) "
                                      "VALUES (%s,%s,%s,%s,%s,'brodar','assistant',%s,now(),%s,%s,%s,%s::jsonb) ON CONFLICT(audience,source,source_id) DO NOTHING",
                                      (mid,self.scope.audience,self.scope.topic_id,platform_id,self.scope.bot_id,text,request.get('reply_to_message_id'),str(platform_id),digest(text),
                                       json.dumps({'media_kind':kind,'artifact_id':item.get('artifact_id')})))
                if status=='sent' and response.get('message_id'):
                    c.execute('SELECT method,request FROM brodar_v2.deliveries WHERE audience=%s AND id=%s',
                              (self.scope.audience,str(uid)))
                    delivery=c.fetchone()
                    if delivery and delivery[0] in {'send_message','send_document','send_poll'}:
                        request=delivery[1]
                        if delivery[0]=='send_message':text=request['text']
                        elif delivery[0]=='send_poll':text='[poll] '+request['question']+'\n'+'\n'.join(request['options'])
                        else:text=request.get('caption','')+'\n[document: '+request['filename']+'; artifact_id: '+request['artifact_id']+']'
                        mid=str(uuid5(NAMESPACE_URL,f'{self.scope.audience}:telegram:{response["message_id"]}'))
                        c.execute('INSERT INTO brodar_v2.messages '
                                  '(id,audience,topic_id,platform_message_id,sender_id,sender_name,role,content,'
                                  'sent_at,reply_to_id,source_id,source_hash) '
                                  "VALUES (%s,%s,%s,%s,%s,'brodar','assistant',%s,%s,%s,%s,%s) "
                                  'ON CONFLICT(audience,source,source_id) DO NOTHING',
                                  (mid,self.scope.audience,self.scope.topic_id,response['message_id'],self.scope.bot_id,
                                   text,datetime.now(timezone.utc),request.get('reply_to_message_id'),
                                   str(response['message_id']),digest(text)))
                if status=='sent':
                    c.execute('SELECT method,request FROM brodar_v2.deliveries WHERE audience=%s AND id=%s',
                              (self.scope.audience,str(uid)))
                    delivery=c.fetchone()
                    if delivery and delivery[0]=='edit_message':
                        message_id=delivery[1]['message_id'];text=delivery[1]['text']
                        c.execute('UPDATE brodar_v2.messages SET content=%s,source_hash=%s,edited_at=now(),changed_at=now() '
                                  'WHERE audience=%s AND platform_message_id=%s AND sender_id=%s RETURNING id',
                                  (text,digest(text),self.scope.audience,message_id,self.scope.bot_id))
                        changed=c.fetchone()
                        if changed:
                            mid=str(changed[0])
                            c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='message' AND source_id=%s",
                                      (self.scope.audience,mid))
                            c.execute('UPDATE brodar_v2.memory_documents SET needs_review=true WHERE audience=%s AND id IN '
                                      '(SELECT document_id FROM brodar_v2.memory_sources WHERE audience=%s AND message_id=%s)',
                                      (self.scope.audience,self.scope.audience,mid))
                            c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='memory' AND source_id IN "
                                      '(SELECT document_id FROM brodar_v2.memory_sources WHERE audience=%s AND message_id=%s)',
                                      (self.scope.audience,self.scope.audience,mid))
                    if delivery and delivery[0]=='edit_message_caption':
                        message_id=delivery[1]['message_id'];caption=delivery[1].get('caption','')
                        c.execute('SELECT metadata FROM brodar_v2.messages WHERE audience=%s AND platform_message_id=%s AND sender_id=%s',
                                  (self.scope.audience,message_id,self.scope.bot_id))
                        original=c.fetchone();metadata=original[0] if original else {}
                        content=caption+'\n['+metadata.get('media_kind','media')
                        if metadata.get('artifact_id'):content+='; artifact_id: '+metadata['artifact_id']
                        content+=']'
                        c.execute('UPDATE brodar_v2.messages SET content=%s,edited_at=now(),changed_at=now(),source_hash=%s '
                                  'WHERE audience=%s AND platform_message_id=%s AND sender_id=%s RETURNING id',
                                  (content,digest(content),self.scope.audience,message_id,self.scope.bot_id))
                        changed=c.fetchone()
                        if changed:
                            mid=str(changed[0])
                            c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='message' AND source_id=%s",(self.scope.audience,mid))
                            c.execute('UPDATE brodar_v2.memory_documents SET needs_review=true WHERE audience=%s AND id IN '
                                      '(SELECT document_id FROM brodar_v2.memory_sources WHERE audience=%s AND message_id=%s)',
                                      (self.scope.audience,self.scope.audience,mid))
                            c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='memory' AND source_id IN "
                                      '(SELECT document_id FROM brodar_v2.memory_sources WHERE audience=%s AND message_id=%s)',
                                      (self.scope.audience,self.scope.audience,mid))
        await self.db.run(op)

    async def delivery_receipts(self,turn_id):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT id,topic_id,tool_call_id,method,status,response,request FROM brodar_v2.deliveries '
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
        from .learning import LearningStore
        return await LearningStore(self.db,self.scope,lease_owner=self.lease_owner).index_candidates(model,limit)
