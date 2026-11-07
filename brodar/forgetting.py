"""Explicit operator source deletion; not a conversational configuration tool."""

from uuid import UUID


async def forget_messages(db,audience,message_ids):
    ids=[str(UUID(value)) for value in message_ids]
    if not 1<=len(ids)<=100:raise ValueError('Select 1 to 100 archive message IDs')
    def op():
        with db.transaction(audience) as c:
            # Operators stop the consumer first. This lock also prevents a new
            # lease being acquired halfway through the deletion transaction.
            c.execute('LOCK TABLE brodar_v2.poll_state IN SHARE ROW EXCLUSIVE MODE')
            c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE lease_until>now() LIMIT 1')
            if c.fetchone():raise RuntimeError('Stop the active bot service before forgetting source history')
            c.execute('SELECT id,source,source_id FROM brodar_v2.messages WHERE audience=%s AND id=ANY(%s::uuid[]) FOR UPDATE',
                      (audience,ids))
            rows=c.fetchall()
            if {str(row[0]) for row in rows}!=set(ids):raise ValueError('Every selected message must exist in this audience')
            c.execute('SELECT DISTINCT document_id FROM brodar_v2.memory_sources WHERE audience=%s AND message_id=ANY(%s::uuid[])',
                      (audience,ids))
            docs=[str(row[0]) for row in c.fetchall()]
            for _,source,source_id in rows:
                c.execute('INSERT INTO brodar_v2.forgotten_sources(audience,source,source_id) VALUES (%s,%s,%s) ON CONFLICT DO NOTHING',
                          (audience,source,source_id))
            if docs:
                c.execute("UPDATE brodar_v2.memory_documents SET content='',deleted=true,revision=revision+1,updated_at=now() "
                          'WHERE audience=%s AND id=ANY(%s::uuid[])',(audience,docs))
                c.execute("UPDATE brodar_v2.memory_revisions SET content='',reason='source forgotten',deleted=true "
                          'WHERE audience=%s AND document_id=ANY(%s::uuid[])',(audience,docs))
                c.execute('DELETE FROM brodar_v2.memory_sources WHERE audience=%s AND document_id=ANY(%s::uuid[])',(audience,docs))
                c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='memory' AND source_id=ANY(%s::uuid[])",
                          (audience,docs))
            c.execute("DELETE FROM brodar_v2.embeddings WHERE audience=%s AND kind='message' AND source_id=ANY(%s::uuid[])",
                      (audience,ids))
            c.execute('DELETE FROM brodar_v2.inbox WHERE audience=%s AND message_id=ANY(%s::uuid[])',(audience,ids))
            c.execute("DELETE FROM brodar_v2.jobs WHERE audience=%s AND (payload->>'request_message_id'=ANY(%s::text[]) "
                      "OR coalesce(payload->'message_ids','[]'::jsonb) ?| %s::text[])",(audience,ids,ids))
            c.execute('DELETE FROM brodar_v2.messages WHERE audience=%s AND id=ANY(%s::uuid[])',(audience,ids))
            # Audit outputs may contain copies returned by history/memory tools.
            # Keep no model-readable copies there after explicit source deletion.
            c.execute('DELETE FROM brodar_v2.tool_calls WHERE audience=%s',(audience,))
            c.execute('DELETE FROM brodar_v2.sessions WHERE audience=%s',(audience,))
            if audience.startswith('telegram:'):
                shared='shared:'+audience.split(':',2)[1]
                c.execute("SELECT set_config('brodar.audience',%s,true)",(shared,))
                c.execute('DELETE FROM brodar_v2.sessions WHERE audience=%s',(shared,))
                c.execute("SELECT set_config('brodar.audience',%s,true)",(audience,))
            return {'forgotten_messages':len(rows),'retired_source_linked_notes':len(docs),'provider_sessions_invalidated':True,
                    'limits':'Separate quoted messages, artifacts, legacy public source archives, old backups and provider retention are not erased by this command.'}
    return await db.run(op)
