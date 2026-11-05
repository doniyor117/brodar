"""Automatic note revisions, correction and reflection checkpoints."""

import json
from uuid import uuid4

from .store import RevisionConflict,context_path,records,digest
from .runtime_store import RuntimeStore


class LearningStore(RuntimeStore):
    async def context_notes(self,speaker_ids,limit=12):
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT id,path,content,revision,subject_ids,confidence,updated_at '
                    'FROM brodar_v2.memory_documents WHERE audience=%s AND NOT deleted AND NOT needs_review '
                    'ORDER BY CASE WHEN subject_ids && %s::bigint[] THEN 0 ELSE 1 END,'
                    "CASE WHEN path LIKE 'context/group/%%' OR path='context/overview.md' THEN 0 ELSE 1 END,"
                    'updated_at DESC LIMIT %s',(self.scope.audience,list(speaker_ids),limit))
                rows=records(c)
                selected=[]
                remaining=14000
                for row in rows:
                    if remaining<=0:break
                    full=row['content'];row['content']=full[:min(3000,remaining)]
                    row['truncated']=len(row['content'])<len(full)
                    remaining-=len(row['content']);selected.append(row)
                return selected
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
                c.execute('SELECT d.id,d.revision AS current_revision,d.deleted,r.revision,r.content,r.reason,r.created_at '
                    'FROM brodar_v2.memory_documents d JOIN brodar_v2.memory_revisions r ON r.document_id=d.id '
                    'WHERE d.audience=%s AND d.path=%s ORDER BY r.revision DESC LIMIT %s',
                    (self.scope.audience,path,max(1,min(limit,20))))
                return records(c)
        return await self.db.run(op)

    async def memory_restore(self,path,revision,*,expected_revision,reason):
        rows=await self.memory_history(path,20)
        old=next((r for r in rows if r['revision']==revision),None)
        if not old or not old['content']:raise ValueError('Requested revision unavailable or forgotten')
        return await self.memory_write(path,old['content'],expected_revision=expected_revision,reason=reason)

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
                rows=records(c)
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
        table='messages' if kind=='message' else 'memory_documents'
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute(f'SELECT content FROM brodar_v2.{table} WHERE audience=%s AND id=%s '+
                    ('AND NOT deleted AND NOT needs_review ' if kind=='memory' else '')+'FOR UPDATE',(self.scope.audience,source_id))
                row=c.fetchone()
                if not row or row[0]!=text:return False
                c.execute('DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind=%s AND source_id=%s AND model=%s',
                    (self.scope.audience,kind,source_id,model))
                for index,vector in enumerate(vectors):
                    c.execute('INSERT INTO brodar_v2.embeddings(audience,kind,source_id,model,dimensions,content_hash,embedding,chunk_index) '
                        'VALUES (%s,%s,%s,%s,768,%s,%s::vector,%s)',
                        (self.scope.audience,kind,source_id,model,digest(text),json.dumps(vector),index))
                return True
        return await self.db.run(op)


def chunks(text,size=1800,overlap=120):
    if size<=overlap or overlap<0:raise ValueError('Invalid chunk sizes')
    return [text[start:start+size] for start in range(0,len(text),size-overlap)]
