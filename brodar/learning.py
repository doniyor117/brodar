"""Automatic note revisions, correction and reflection checkpoints."""

import json
from uuid import uuid4

from .store import RevisionConflict,context_path,records,digest
from .runtime_store import RuntimeStore


class LearningStore(RuntimeStore):
    async def turn_context(self,message_ids,turn_id,model=None):
        """Fetch the foreground context in one remote transaction, not seven."""
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute("WITH RECURSIVE inputs AS (SELECT * FROM brodar_v2.messages WHERE audience=%s AND id=ANY(%s::uuid[])), "
                    "chain AS (SELECT m.*,0 AS depth,ARRAY[m.id] AS path FROM inputs m UNION ALL "
                    "SELECT p.*,c.depth+1,c.path||p.id FROM chain c JOIN brodar_v2.messages p "
                    "ON p.audience=c.audience AND p.platform_message_id=c.reply_to_id WHERE c.depth<8 AND NOT p.id=ANY(c.path)) "
                    "SELECT jsonb_build_object("
                    "'messages',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (SELECT * FROM inputs ORDER BY received_at,id) t),"
                    "'context',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (SELECT * FROM brodar_v2.messages WHERE audience=%s AND topic_id=%s "
                    "AND id NOT IN (SELECT id FROM inputs) ORDER BY received_at DESC,id DESC LIMIT 35) t),"
                    "'parents',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (SELECT DISTINCT ON(id) * FROM chain WHERE depth>0 ORDER BY id,depth) t),"
                    "'notes',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (SELECT id,path,left(content,3000) AS content,"
                    "length(content)>3000 AS truncated,revision,subject_ids,confidence,updated_at FROM brodar_v2.memory_documents "
                    "WHERE audience=%s AND NOT deleted AND NOT needs_review ORDER BY CASE WHEN path='context/overview.md' THEN 0 ELSE 1 END,CASE WHEN subject_ids && "
                    "ARRAY(SELECT sender_id FROM inputs WHERE sender_id IS NOT NULL) THEN 0 ELSE 1 END,"
                    "CASE WHEN path LIKE 'context/group/%%' OR path='context/overview.md' THEN 0 ELSE 1 END,updated_at DESC LIMIT 12) t),"
                    "'receipts',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (SELECT id,tool_call_id,method,status,response,request "
                    "FROM brodar_v2.deliveries WHERE audience=%s AND turn_id=%s ORDER BY created_at) t),"
                    "'artifacts',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM (SELECT id AS artifact_id,filename "
                    "FROM brodar_v2.artifacts WHERE audience=%s AND turn_id=%s) t),"
                    "'has_index',EXISTS(SELECT 1 FROM brodar_v2.embeddings WHERE audience=%s AND model=%s))",
                    (self.scope.audience,message_ids,self.scope.audience,self.scope.topic_id,self.scope.audience,
                     self.scope.audience,str(turn_id),self.scope.audience,str(turn_id),self.scope.audience,model))
                bundle=c.fetchone()[0]
                bundle['context'].reverse()
                seen={row['id'] for row in bundle['messages']+bundle['context']}
                for parent in bundle.pop('parents'):
                    if parent['id'] not in seen:bundle['context'].append(parent);seen.add(parent['id'])
                bundle['notes']=bounded_notes(bundle['notes'])
                return bundle
        return await self.db.run(op)

    async def context_notes(self,speaker_ids,limit=12):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT id,path,content,revision,subject_ids,confidence,updated_at '
                    'FROM brodar_v2.memory_documents WHERE audience=%s AND NOT deleted AND NOT needs_review '
                    "ORDER BY CASE WHEN path='context/overview.md' THEN 0 ELSE 1 END,CASE WHEN subject_ids && %s::bigint[] THEN 0 ELSE 1 END,"
                    "CASE WHEN path LIKE 'context/group/%%' OR path='context/overview.md' THEN 0 ELSE 1 END,"
                    'updated_at DESC LIMIT %s',(self.scope.audience,list(speaker_ids),limit))
                rows=records(c)
                return bounded_notes(rows)
        return await self.db.run(op)

    async def has_index(self,model):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT EXISTS(SELECT 1 FROM brodar_v2.embeddings WHERE audience=%s AND model=%s)',
                    (self.scope.audience,model))
                return c.fetchone()[0]
        return await self.db.run(op)

    async def memory_history(self,path,limit=10):
        path=context_path(path)
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT d.id,d.revision AS current_revision,d.deleted,d.subject_ids AS current_subject_ids,'
                    'd.confidence AS current_confidence,r.revision,r.content,r.reason,r.created_at,r.metadata '
                    'FROM brodar_v2.memory_documents d JOIN brodar_v2.memory_revisions r ON r.document_id=d.id '
                    'WHERE d.audience=%s AND d.path=%s ORDER BY r.revision DESC LIMIT %s',
                    (self.scope.audience,path,max(1,min(limit,20))))
                return records(c)
        return await self.db.run(op)

    async def memory_restore(self,path,revision,*,expected_revision,reason):
        rows=await self.memory_history(path,20)
        old=next((r for r in rows if r['revision']==revision),None)
        if not old or not old['content']:raise ValueError('Requested revision unavailable or forgotten')
        # Pre-migration revisions have unknown metadata; preserve current values rather
        # than invent historical certainty. Existing source links remain conservative.
        metadata=old['metadata'] or {'subject_ids':old['current_subject_ids'],'confidence':old['current_confidence']}
        return await self.memory_write(path,old['content'],expected_revision=expected_revision,reason=reason,
                                       subject_ids=metadata['subject_ids'],confidence=metadata['confidence'],source_ids=metadata.get('source_ids',[]))

    async def memory_forget(self,path,*,expected_revision):
        path=context_path(path)
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute("UPDATE brodar_v2.memory_documents SET content='',deleted=true,revision=revision+1,updated_at=now() "
                    'WHERE audience=%s AND path=%s AND revision=%s RETURNING id,revision',
                    (self.scope.audience,path,expected_revision))
                row=c.fetchone()
                if not row:raise RevisionConflict('Context missing or changed')
                uid=str(row[0])
                c.execute("UPDATE brodar_v2.memory_revisions SET content='',reason='forgotten',deleted=true "
                    'WHERE audience=%s AND document_id=%s',(self.scope.audience,uid))
                c.execute('DELETE FROM brodar_v2.memory_sources WHERE audience=%s AND document_id=%s',(self.scope.audience,uid))
                c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='memory' AND source_id=%s",(self.scope.audience,uid))
                return {'forgotten':True,'revision':row[1]}
        return await self.db.run(op)

    async def reflection_batch(self,*,minimum=20,limit=50):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute("SELECT * FROM brodar_v2.reflection_state WHERE audience=%s",(self.scope.audience,))
                state=records(c)
                if state:
                    s=state[0]
                    c.execute('SELECT * FROM brodar_v2.messages WHERE audience=%s AND (changed_at,id)>(%s,%s::uuid) '
                        'ORDER BY changed_at,id LIMIT %s',(self.scope.audience,s['last_received_at'],str(s['last_id']),limit))
                else:
                    c.execute('SELECT * FROM brodar_v2.messages WHERE audience=%s ORDER BY changed_at,id LIMIT %s',
                        (self.scope.audience,limit))
                rows=records(c)
                if not rows:return []
                # Batch by accumulated experience or an hour since the last
                # successful reflection; never force a model call per message.
                c.execute("SELECT coalesce((SELECT last_run_at FROM brodar_v2.reflection_state WHERE audience=%s),"
                    "(SELECT min(received_at) FROM brodar_v2.messages WHERE audience=%s))<now()-interval '1 hour'",
                    (self.scope.audience,self.scope.audience))
                due=c.fetchone()
                return rows if len(rows)>=minimum or (due and due[0]) else []
        return await self.db.run(op)

    async def reflection_finish(self,last_message):
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute('INSERT INTO brodar_v2.reflection_state(audience,last_received_at,last_id,last_run_at) '
                    'VALUES (%s,%s,%s,now()) ON CONFLICT(audience) DO UPDATE SET '
                    'last_received_at=excluded.last_received_at,last_id=excluded.last_id,last_run_at=now()',
                    (self.scope.audience,last_message['changed_at'],str(last_message['id'])))
        await self.db.run(op)

    async def index_candidates(self,model,limit=5):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute("SELECT m.id,m.content,'message' AS kind FROM brodar_v2.messages m "
                    "WHERE m.audience=%s AND m.content<>'' AND NOT EXISTS (SELECT 1 FROM brodar_v2.embeddings e "
                    "WHERE e.audience=m.audience AND e.source_id=m.id AND e.kind='message' AND e.model=%s "
                    "AND e.content_hash=m.source_hash) ORDER BY m.received_at LIMIT %s",(self.scope.audience,model,limit))
                rows=records(c)[:max(1,limit-2)]
                if len(rows)<limit:
                    c.execute("SELECT m.id,m.content,'memory' AS kind FROM brodar_v2.memory_documents m WHERE m.audience=%s "
                        "AND NOT m.deleted AND NOT m.needs_review AND m.content<>'' AND NOT EXISTS (SELECT 1 FROM brodar_v2.embeddings e "
                        "WHERE e.audience=m.audience AND e.source_id=m.id AND e.kind='memory' AND e.model=%s) "
                        "ORDER BY m.updated_at LIMIT %s",(self.scope.audience,model,limit-len(rows)))
                    rows.extend(records(c))
                return rows
        return await self.db.run(op)

    async def index_document(self,kind,source_id,text,vectors,model):
        if kind not in {'message','memory'}:raise ValueError('Invalid embedding source')
        import math
        if not vectors or any(len(v)!=768 or not all(math.isfinite(x) for x in v) for v in vectors):
            raise ValueError('Invalid embedding vectors')
        return str(source_id) in await self.index_documents([{'kind':kind,'id':source_id,'content':text,'vectors':vectors}],model)

    async def index_documents(self,rows,model):
        """Persist a bounded batch atomically, rechecking original versions under locks."""
        import math
        if len(rows)>32:raise ValueError('Index batch too large')
        for row in rows:
            if row['kind'] not in {'message','memory'}:raise ValueError('Invalid embedding source')
            if not row['vectors'] or any(len(v)!=768 or not all(math.isfinite(x) for x in v) for v in row['vectors']):raise ValueError('Invalid vectors')
        def op():
            with self.db.transaction(self.scope.audience) as c:
                valid=[]
                for kind,table in [('message','messages'),('memory','memory_documents')]:
                    candidates={str(row['id']):row for row in rows if row['kind']==kind}
                    if not candidates:continue
                    c.execute(f'SELECT id,content FROM brodar_v2.{table} WHERE audience=%s AND id=ANY(%s::uuid[]) '+
                        ('AND NOT deleted AND NOT needs_review ' if kind=='memory' else '')+'ORDER BY id FOR UPDATE',
                        (self.scope.audience,list(candidates)))
                    valid.extend(candidates[str(uid)] for uid,text in c.fetchall() if text==candidates[str(uid)]['content'])
                if not valid:return []
                sources=json.dumps([{'kind':r['kind'],'id':str(r['id'])} for r in valid])
                c.execute("DELETE FROM brodar_v2.embeddings e USING jsonb_to_recordset(%s::jsonb) AS s(kind text,id uuid) WHERE e.audience=%s AND e.model=%s AND e.kind=s.kind AND e.source_id=s.id",(sources,self.scope.audience,model))
                values=[{'kind':r['kind'],'id':str(r['id']),'hash':digest(r['content']),'embedding':json.dumps(v),'chunk':i}
                    for r in valid for i,v in enumerate(r['vectors'])]
                c.execute("INSERT INTO brodar_v2.embeddings(audience,kind,source_id,model,dimensions,content_hash,embedding,chunk_index) SELECT %s,s.kind,s.id,%s,768,s.hash,s.embedding::vector,s.chunk FROM jsonb_to_recordset(%s::jsonb) AS s(kind text,id uuid,hash text,embedding text,chunk int)",
                    (self.scope.audience,model,json.dumps(values)))
                return [str(r['id']) for r in valid]
        return await self.db.run(op)


    async def maintenance_prepare(self,consolidation=False,interval=21600):
        def op():
            with self.db.transaction(self.scope.audience) as c:
                if self.lease_owner:
                    c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR SHARE',(self.scope.bot_id,self.lease_owner))
                    if not c.fetchone():raise PermissionError('Maintenance consumer lease expired')
                c.execute("INSERT INTO brodar_v2.reflection_state(audience) VALUES (%s) ON CONFLICT DO NOTHING",(self.scope.audience,))
                c.execute("SELECT *,consolidation_at IS NULL OR consolidation_at<now()-(%s * interval '1 second') AS consolidate FROM brodar_v2.reflection_state WHERE audience=%s FOR UPDATE",(interval,self.scope.audience))
                state=records(c)[0]
                c.execute("SELECT * FROM brodar_v2.messages WHERE audience=%s AND ((changed_at,id)>(%s,%s::uuid) OR (id=%s::uuid AND %s>0)) ORDER BY changed_at,id LIMIT 50",
                    (self.scope.audience,state['last_received_at'],str(state['last_id']),str(state['last_id']),state['chunk_offset']))
                rows=records(c)
                kind='incremental';notes=[];selected=[];remaining=30000;offset=0
                if len(rows)<20 and not state['chunk_offset']:
                    c.execute("SELECT %s::timestamptz<now()-interval '1 hour'",(state['last_run_at'],))
                    if not c.fetchone()[0]:rows=[]
                if consolidation and state['consolidate']:rows=[]
                for row in rows:
                    begin=state['chunk_offset'] if str(row['id'])==str(state['last_id']) and row['changed_at']==state['last_received_at'] else 0
                    content=row['content'][begin:]
                    take=min(len(content),remaining)
                    selected.append(dict(row,content=content[:take],chunk_start=begin,chunk_end=begin+take,content_length=len(row['content'])))
                    offset=begin+take if take<len(content) else 0
                    remaining-=take
                    if remaining<=0:break
                if not selected and consolidation and state['consolidate']:
                    kind='consolidation'
                    c.execute("SELECT * FROM brodar_v2.memory_documents WHERE audience=%s AND NOT deleted ORDER BY last_maintained_at NULLS FIRST,needs_review DESC,updated_at,id LIMIT 8",(self.scope.audience,))
                    notes=records(c)
                    if not notes:
                        c.execute('UPDATE brodar_v2.reflection_state SET consolidation_at=now() WHERE audience=%s',(self.scope.audience,))
                        return None
                    # Bound full documents individually; tools can retrieve the rest.
                    notes=[dict(n,content=n['content'][:3000]) for n in notes]
                elif not selected:return None
                uid=str(uuid4())
                manifest=[{'id':str(r['id']),'changed_at':str(r['changed_at']),'chunk_start':r['chunk_start'],'chunk_end':r['chunk_end']} for r in selected]
                c.execute("DELETE FROM brodar_v2.maintenance_runs WHERE audience=%s AND started_at<now()-interval '30 days'",(self.scope.audience,))
                c.execute("UPDATE brodar_v2.maintenance_runs SET status='interrupted',finished_at=now() WHERE audience=%s AND status='running'",(self.scope.audience,))
                c.execute("INSERT INTO brodar_v2.maintenance_runs(id,audience,owner,kind,input_manifest) VALUES (%s,%s,%s,%s,%s::jsonb)",
                    (uid,self.scope.audience,str(self.lease_owner),kind,json.dumps(manifest)))
                return {'id':uid,'kind':kind,'messages':selected,'notes':notes,'offset':offset,'state':state}
        return await self.db.run(op)

    async def maintenance_end(self,run,status):
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute("UPDATE brodar_v2.maintenance_runs SET status=%s,finished_at=now() WHERE audience=%s AND id=%s AND owner=%s AND status='running'",(status,self.scope.audience,run['id'],str(self.lease_owner)))
        await self.db.run(op)

    async def maintenance_finish(self,run):
        def op():
            with self.db.transaction(self.scope.audience) as c:
                if self.lease_owner:
                    c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR SHARE',(self.scope.bot_id,self.lease_owner))
                    if not c.fetchone():raise PermissionError('Maintenance consumer lease expired')
                # Lock and verify the exact evidence versions before checkpointing.
                for row in run['messages']:
                    c.execute('SELECT changed_at FROM brodar_v2.messages WHERE audience=%s AND id=%s FOR SHARE',(self.scope.audience,str(row['id'])))
                    current=c.fetchone()
                    if not current or current[0]!=row['changed_at']:raise RevisionConflict('Maintenance evidence changed')
                if run['kind']=='incremental':
                    last=run['messages'][-1];state=run['state']
                    c.execute('UPDATE brodar_v2.reflection_state SET last_received_at=%s,last_id=%s,chunk_offset=%s,last_run_at=now() WHERE audience=%s AND last_received_at=%s AND last_id=%s AND chunk_offset=%s',
                        (last['changed_at'],str(last['id']),run['offset'],self.scope.audience,state['last_received_at'],str(state['last_id']),state['chunk_offset']))
                    if c.rowcount!=1:raise RevisionConflict('Maintenance cursor changed')
                else:
                    for note in run['notes']:
                        c.execute('UPDATE brodar_v2.memory_documents SET last_maintained_at=now() WHERE audience=%s AND id=%s',(self.scope.audience,str(note['id'])))
                    c.execute('UPDATE brodar_v2.reflection_state SET consolidation_at=now() WHERE audience=%s',(self.scope.audience,))
                c.execute("UPDATE brodar_v2.maintenance_runs SET status='completed',finished_at=now(),outcome='processed',changed_revisions=%s::jsonb WHERE audience=%s AND id=%s AND owner=%s AND status='running'",
                    (json.dumps(run.get('changes',[])),self.scope.audience,run['id'],str(self.lease_owner)))
                if c.rowcount!=1:raise RevisionConflict('Maintenance ownership changed')
        await self.db.run(op)


def chunks(text,size=1800,overlap=120):
    if size<=overlap or overlap<0:raise ValueError('Invalid chunk sizes')
    return [text[start:start+size] for start in range(0,len(text),size-overlap)]


def bounded_notes(rows,budget=14000):
    result=[]
    for row in sorted(rows,key=lambda r:0 if r.get('path')=='context/overview.md' else 1):
        if budget<=0:break
        row=dict(row);full=row.get('content') or ''
        size=min(budget,2000 if row.get('path')=='context/overview.md' else 3000)
        row['content']=full[:size]
        row['truncated']=bool(row.get('truncated')) or len(full)>size
        budget-=len(row['content']);result.append(row)
    return result
