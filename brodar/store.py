"""Audience-bound archive, durable inbox and freely editable contextual notes."""

from __future__ import annotations

import hashlib
import json
from pathlib import PurePosixPath
from datetime import datetime,timezone
from uuid import UUID, NAMESPACE_URL, uuid4, uuid5

from .postgres import Postgres
from .scope import Scope
from .redaction import scrub,scrub_object


def digest(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


def context_path(path: str) -> str:
    p = PurePosixPath(path)
    if (not path.startswith('context/') or not path.endswith('.md')
            or p.is_absolute() or '..' in p.parts or '\\' in path
            or str(p) != path or len(path) > 240 or any(ord(x) < 32 for x in path)):
        raise ValueError('Use a Markdown path under context/, without traversal')
    return path


def records(cur):
    names = [c[0] for c in cur.description]
    return [dict(zip(names, row)) for row in cur.fetchall()]


class RevisionConflict(RuntimeError):
    pass


class ScopedStore:
    def __init__(self, db: Postgres, scope: Scope, *, lease_owner=None):
        self.db = db
        self.scope = scope
        self.lease_owner=lease_owner

    async def ingest(self, *, update_id: int, message_id: int, sender_id: int | None,
                     sender_name: str, text: str, timestamp, payload: dict,
                     reply_to_id: int | None = None, edited_at=None,role:str='user') -> tuple[str, bool]:
        """Archive + queue + polling cursor commit together before Telegram ack."""
        text=scrub(text)
        if role not in {'user','service'}:raise ValueError('Unsupported incoming message role')
        payload=scrub_object(payload)
        mid = str(uuid5(NAMESPACE_URL, f'{self.scope.audience}:telegram:{message_id}'))
        iid = str(uuid5(NAMESPACE_URL, f'telegram:{self.scope.bot_id}:{update_id}'))

        def op():
            with self.db.transaction(self.scope.audience) as c:
                if self.lease_owner:
                    c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR UPDATE',
                              (self.scope.bot_id,self.lease_owner))
                    if c.fetchone() is None:raise PermissionError('Consumer lease expired before ingest')
                c.execute('INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id) VALUES (%s,%s,%s) '
                          'ON CONFLICT DO NOTHING',(self.scope.audience,self.scope.instance_id,self.scope.chat_id))
                c.execute("SELECT 1 FROM brodar_v2.forgotten_sources WHERE audience=%s AND source='telegram' AND source_id=%s",
                          (self.scope.audience,str(message_id)))
                if c.fetchone():
                    c.execute('INSERT INTO brodar_v2.poll_state(bot_id,next_update_id) VALUES (%s,%s) '
                              'ON CONFLICT(bot_id) DO UPDATE SET next_update_id=greatest(poll_state.next_update_id,excluded.next_update_id)',
                              (self.scope.bot_id,update_id+1))
                    return iid,False
                # Telegram may supply an older quoted parent that never reached
                # the bot as an update. Archive it as context, never as a fresh
                # request or a source of tool authority.
                message=payload.get('message') or payload.get('edited_message') or {}
                parent=message.get('reply_to_message') or {}
                parent_id=parent.get('message_id')
                parent_chat=(parent.get('chat') or {}).get('id',self.scope.chat_id)
                if parent_id and parent_chat==self.scope.chat_id and not parent.get('private_context_omitted'):
                    from .telegram_context import describe_message,ordinary_message
                    if ordinary_message(parent):
                        parent_text,parent_service=describe_message(parent)
                        author=parent.get('from') or {}
                        parent_sender=None if parent.get('sender_chat') else author.get('id')
                        parent_role='service' if parent_service else ('assistant' if parent_sender==self.scope.bot_id else 'user')
                        def date(value):
                            try:
                                if isinstance(value,datetime):return value
                                if isinstance(value,(int,float)):return datetime.fromtimestamp(value,timezone.utc)
                                if isinstance(value,str):
                                    parsed=datetime.fromisoformat(value.replace('Z','+00:00'))
                                    return parsed if parsed.tzinfo else None
                            except (ValueError,OverflowError,OSError):pass
                            return None
                        # Telegram supplies the parent from this same chat/thread.
                        # Repair legacy reply-chain topic IDs when quoted again.
                        parent_topic=self.scope.topic_id
                        c.execute('INSERT INTO brodar_v2.messages '
                            '(id,audience,topic_id,platform_message_id,sender_id,sender_name,role,content,sent_at,edited_at,source_id,source_hash,metadata) '
                            'SELECT %s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb WHERE NOT EXISTS '
                            "(SELECT 1 FROM brodar_v2.forgotten_sources WHERE audience=%s AND source='telegram' AND source_id=%s) "
                            'ON CONFLICT(audience,source,source_id) DO UPDATE SET topic_id=excluded.topic_id',
                            (str(uuid5(NAMESPACE_URL,f'{self.scope.audience}:telegram:{parent_id}')),self.scope.audience,
                             parent_topic,parent_id,parent_sender,
                             author.get('first_name') or author.get('username') or 'anonymous sender',parent_role,parent_text,
                             date(parent.get('date')),date(parent.get('edit_date')),str(parent_id),digest(parent_text),
                             json.dumps({'delivered_as_reply_context':True,'with_update_id':update_id}),self.scope.audience,str(parent_id)))
                c.execute('INSERT INTO brodar_v2.messages '
                          '(id,audience,topic_id,platform_message_id,sender_id,sender_name,role,content,'
                          'sent_at,reply_to_id,edited_at,source_id,source_hash,metadata) '
                          'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) '
                          'ON CONFLICT(audience,source,source_id) DO UPDATE SET '
                          'content=excluded.content,edited_at=excluded.edited_at,source_hash=excluded.source_hash,metadata=excluded.metadata,changed_at=now() '
                          'WHERE excluded.edited_at IS NOT NULL AND '
                          '(messages.edited_at IS NULL OR excluded.edited_at > messages.edited_at) RETURNING id',
                          (mid,self.scope.audience,self.scope.topic_id,message_id,sender_id,sender_name,
                           role,text,timestamp,reply_to_id,edited_at,str(message_id),digest(text),json.dumps({'sender_is_bot':bool(((payload.get('message') or payload.get('edited_message') or {}).get('from') or {}).get('is_bot')),'sender_username':((payload.get('message') or payload.get('edited_message') or {}).get('from') or {}).get('username')})))
                changed=c.fetchone() is not None
                c.execute('INSERT INTO brodar_v2.inbox(id,audience,topic_id,bot_id,update_id,message_id,payload) '
                          'VALUES (%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT(bot_id,update_id) DO NOTHING RETURNING id',
                          (iid,self.scope.audience,self.scope.topic_id,self.scope.bot_id,update_id,mid,
                           json.dumps(payload,default=str)))
                new = c.fetchone() is not None
                if edited_at is not None and changed:
                    c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='message' AND source_id=%s "
                              'AND content_hash<>%s',(self.scope.audience,mid,digest(text)))
                    c.execute('UPDATE brodar_v2.memory_documents SET needs_review=true WHERE audience=%s AND id IN '
                              '(SELECT document_id FROM brodar_v2.memory_sources WHERE audience=%s AND message_id=%s)',
                              (self.scope.audience,self.scope.audience,mid))
                    c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='memory' AND source_id IN "
                              '(SELECT document_id FROM brodar_v2.memory_sources WHERE audience=%s AND message_id=%s)',
                              (self.scope.audience,self.scope.audience,mid))
                c.execute('INSERT INTO brodar_v2.poll_state(bot_id,next_update_id) VALUES (%s,%s) '
                          'ON CONFLICT(bot_id) DO UPDATE SET next_update_id='
                          'greatest(poll_state.next_update_id,excluded.next_update_id)',
                          (self.scope.bot_id,update_id+1))
                return iid,new
        return await self.db.run(op)

    async def recent(self, limit: int = 40, *, all_topics: bool = False):
        limit = max(1,min(limit,100))
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT * FROM brodar_v2.messages WHERE audience=%s '
                          'AND (%s OR topic_id=%s) ORDER BY received_at DESC,id DESC LIMIT %s',
                          (self.scope.audience,all_topics,self.scope.topic_id,limit))
                return list(reversed(records(c)))
        return await self.db.run(op)

    async def memory_list(self, limit: int = 100,offset:int=0):
        if offset<0:raise ValueError('Memory offset must be nonnegative')
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT id,path,revision,subject_ids,confidence,updated_at,needs_review,'
                          'CASE WHEN needs_review THEN NULL ELSE left(content,180) END AS preview '
                          'FROM brodar_v2.memory_documents WHERE audience=%s AND NOT deleted '
                          'ORDER BY updated_at DESC,id LIMIT %s OFFSET %s',(self.scope.audience,max(1,min(limit,200)),offset))
                return records(c)
        return await self.db.run(op)

    async def memory_read(self, path: str):
        path = context_path(path)
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute('SELECT * FROM brodar_v2.memory_documents WHERE audience=%s AND path=%s AND NOT deleted',
                          (self.scope.audience,path))
                rows = records(c)
                if not rows:return None
                c.execute('SELECT message_id FROM brodar_v2.memory_sources WHERE audience=%s AND document_id=%s',(self.scope.audience,str(rows[0]['id'])))
                rows[0]['source_ids']=[str(r[0]) for r in c.fetchall()]
                return rows[0]
        return await self.db.run(op)

    async def memory_write(self, path: str, content: str, *, expected_revision: int,
                           reason: str, source_ids: list[str] | None = None,
                           subject_ids: list[int] | None = None, confidence: str = 'observed', reviewed_sources: dict[str,str] | None = None):
        """Create at revision 0; updating requires reading the current revision.

        Ordinary automatic learning has no approval step. Revisions and source
        links enable correction/rollback, not a restricted vocabulary for notes.
        """
        path = context_path(path)
        content=scrub(content)
        reason=scrub(reason)
        if not content.strip() or len(content) > 100_000 or not reason.strip():
            raise ValueError('Provide nonempty content (up to 100000 characters) and a reason')
        source_ids = [str(UUID(x)) for x in source_ids or []]
        def op():
            with self.db.transaction(self.scope.audience) as c:
                # Serialize creation and updates of one virtual document.
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(self.scope.audience+':'+path,))
                c.execute('SELECT id,revision FROM brodar_v2.memory_documents WHERE audience=%s AND path=%s FOR UPDATE',
                          (self.scope.audience,path))
                old = c.fetchone()
                if (old[1] if old else 0) != expected_revision:
                    raise RevisionConflict('Context changed; read it again before editing')
                if source_ids:
                    c.execute('SELECT id FROM brodar_v2.messages WHERE audience=%s AND id=ANY(%s::uuid[])',
                              (self.scope.audience,source_ids))
                    if {str(r[0]) for r in c.fetchall()} != set(source_ids):
                        raise ValueError('Source messages must exist in this audience')
                docid = str(old[0]) if old else str(uuid4())
                c.execute('SELECT message_id FROM brodar_v2.memory_sources WHERE audience=%s AND document_id=%s',(self.scope.audience,docid))
                linked=sorted(set(source_ids)|{str(r[0]) for r in c.fetchall()})
                c.execute('SELECT id,content FROM brodar_v2.messages WHERE audience=%s AND id=ANY(%s::uuid[]) FOR SHARE',(self.scope.audience,linked))
                evidence={str(uid):digest(body) for uid,body in c.fetchall()}
                reviewed=bool(linked) and all((reviewed_sources or {}).get(uid)==evidence.get(uid) and uid in evidence for uid in linked)
                revision = expected_revision+1
                c.execute('INSERT INTO brodar_v2.memory_documents '
                          '(id,audience,path,content,revision,subject_ids,confidence) '
                          'VALUES (%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(audience,path) DO UPDATE SET '
                          'content=excluded.content,revision=excluded.revision,subject_ids=excluded.subject_ids,'
                          'confidence=excluded.confidence,deleted=false,needs_review=brodar_v2.memory_documents.needs_review AND NOT %s,updated_at=now()',
                          (docid,self.scope.audience,path,content,revision,subject_ids or [],confidence,reviewed))
                c.execute('INSERT INTO brodar_v2.memory_revisions '
                          '(id,audience,document_id,revision,content,deleted,reason,metadata) VALUES (%s,%s,%s,%s,%s,false,%s,%s::jsonb)',
                          (str(uuid4()),self.scope.audience,docid,revision,content,reason,
                           json.dumps({'subject_ids':subject_ids or [],'confidence':confidence,'source_ids':linked})))
                for src in source_ids:
                    c.execute('INSERT INTO brodar_v2.memory_sources(audience,document_id,message_id) '
                              'VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',(self.scope.audience,docid,src))
                c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='memory' AND source_id=%s",
                          (self.scope.audience,docid))
                return {'id':docid,'path':path,'revision':revision}
        return await self.db.run(op)

    async def memory_delete(self, path: str, *, expected_revision: int, reason: str):
        path = context_path(path)
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute('UPDATE brodar_v2.memory_documents SET deleted=true,revision=revision+1,updated_at=now() '
                          'WHERE audience=%s AND path=%s AND revision=%s AND NOT deleted RETURNING id,revision,content,subject_ids,confidence',
                          (self.scope.audience,path,expected_revision))
                old = c.fetchone()
                if not old:
                    raise RevisionConflict('Context missing or changed; read again')
                c.execute('INSERT INTO brodar_v2.memory_revisions '
                          '(id,audience,document_id,revision,content,deleted,reason,metadata) VALUES (%s,%s,%s,%s,%s,true,%s,%s::jsonb)',
                          (str(uuid4()),self.scope.audience,str(old[0]),old[1],old[2],reason,
                           json.dumps({'subject_ids':old[3],'confidence':old[4]})))
                c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='memory' AND source_id=%s",
                          (self.scope.audience,str(old[0])))
                return {'deleted':True,'revision':old[1]}
        return await self.db.run(op)

    async def search(self, query: str, *, vector: list[float] | None = None,
                     model: str = 'gemini-embedding-2', limit: int = 10, mode: str = 'hybrid'):
        """Hybrid RRF retrieval; archived originals and notes carry source IDs."""
        limit = max(1,min(limit,30))
        if not query.strip() or len(query)>4000:
            raise ValueError('Provide a nonempty query of up to 4000 characters')
        def op():
            with self.db.transaction(self.scope.audience,readonly=True) as c:
                c.execute("SELECT 'message' AS kind,id,content,sent_at,legacy_timestamp,"
                          "sender_id,NULL::text AS path,source,source_id FROM brodar_v2.messages "
                          "WHERE audience=%s AND (to_tsvector('simple',content) @@ plainto_tsquery('simple',%s) "
                          "OR content ILIKE %s) ORDER BY ts_rank_cd(to_tsvector('simple',content),"
                          "plainto_tsquery('simple',%s)) DESC,received_at DESC LIMIT 60",
                          (self.scope.audience,query,'%'+query+'%',query))
                lexical = records(c)
                c.execute("SELECT 'memory' AS kind,id,content,NULL::timestamptz AS sent_at,"
                          "NULL::text AS legacy_timestamp,NULL::bigint AS sender_id,path,NULL::text AS source,"
                          "NULL::text AS source_id FROM brodar_v2.memory_documents WHERE audience=%s AND NOT deleted AND NOT needs_review "
                          "AND (to_tsvector('simple',content) @@ plainto_tsquery('simple',%s) OR content ILIKE %s) "
                          "ORDER BY updated_at DESC LIMIT 40",(self.scope.audience,query,'%'+query+'%'))
                notes = records(c)
                if mode=='semantic':lexical=[];notes=[]
                semantic = []
                if vector is not None:
                    from .embeddings import DIMENSIONS
                    import math
                    if len(vector)!=DIMENSIONS or not all(math.isfinite(x) for x in vector):
                        raise ValueError('Invalid search vector')
                    c.execute('SELECT kind,source_id FROM brodar_v2.embeddings WHERE audience=%s AND model=%s '
                              'GROUP BY kind,source_id ORDER BY min(embedding <=> %s::vector) LIMIT 60',
                              (self.scope.audience,model,json.dumps(vector)))
                    hits = c.fetchall()
                    # Hydrate in two bounded queries instead of one remote round trip per hit.
                    hydrated = {}
                    for kind in ('message','memory'):
                        ids = [str(uid) for hit_kind,uid in hits if hit_kind==kind]
                        if not ids:continue
                        if kind=='message':
                            c.execute("SELECT 'message' AS kind,id,content,sent_at,legacy_timestamp,sender_id,"
                                      "NULL::text AS path,source,source_id FROM brodar_v2.messages WHERE audience=%s AND id=ANY(%s::uuid[])",
                                      (self.scope.audience,ids))
                        else:
                            c.execute("SELECT 'memory' AS kind,id,content,NULL::timestamptz AS sent_at,"
                                      "NULL::text AS legacy_timestamp,NULL::bigint AS sender_id,path,"
                                      "NULL::text AS source,NULL::text AS source_id FROM brodar_v2.memory_documents "
                                      "WHERE audience=%s AND id=ANY(%s::uuid[]) AND NOT deleted AND NOT needs_review",
                                      (self.scope.audience,ids))
                        for item in records(c):hydrated[(kind,str(item['id']))]=item
                    semantic = [hydrated[(kind,str(uid))] for kind,uid in hits if (kind,str(uid)) in hydrated]
                combined = {}
                for ranking in [lexical,notes,semantic]:
                    for rank,item in enumerate(ranking):
                        key = (item['kind'],str(item['id']))
                        entry = combined.setdefault(key,{**item,'score':0.0})
                        entry['score'] += 1/(60+rank+1)
                return sorted(combined.values(),key=lambda r:r['score'],reverse=True)[:limit]
        return await self.db.run(op)

    async def put_embedding(self, kind: str, source_id: str, text: str, vector: list[float], model: str):
        if kind not in {'message','memory'}:
            raise ValueError('Invalid embedding source')
        from .embeddings import DIMENSIONS
        import math
        if len(vector)!=DIMENSIONS or not all(math.isfinite(x) for x in vector):
            raise ValueError('Invalid embedding vector')
        table = 'messages' if kind=='message' else 'memory_documents'
        def op():
            with self.db.transaction(self.scope.audience) as c:
                c.execute(f'SELECT content FROM brodar_v2.{table} WHERE audience=%s AND id=%s '+
                          ('AND NOT deleted AND NOT needs_review ' if kind=='memory' else '')+'FOR UPDATE',
                          (self.scope.audience,str(UUID(source_id))))
                row = c.fetchone()
                if not row or row[0]!=text:
                    return False  # changed while embedding was in flight
                c.execute('INSERT INTO brodar_v2.embeddings '
                          '(audience,kind,source_id,model,dimensions,content_hash,embedding) '
                          'VALUES (%s,%s,%s,%s,768,%s,%s::vector) ON CONFLICT(audience,kind,source_id,model,chunk_index) '
                          'DO UPDATE SET content_hash=excluded.content_hash,embedding=excluded.embedding,updated_at=now()',
                          (self.scope.audience,kind,source_id,model,digest(text),json.dumps(vector)))
                return True
        return await self.db.run(op)
