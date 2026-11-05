"""Opt-in integration checks on a disposable Neon branch using the app role."""

import os
from datetime import datetime,timezone
from uuid import uuid4

import pytest
import pytest_asyncio

from brodar.postgres import Postgres
from brodar.scope import Scope
from brodar.store import RevisionConflict,ScopedStore

pytestmark=pytest.mark.skipif(not os.environ.get('BRODAR_TEST_DATABASE_URL'),reason='Disposable Neon test URL not configured')


@pytest_asyncio.fixture
async def stores():
    db=Postgres(os.environ['BRODAR_TEST_DATABASE_URL'])
    namespace='test-'+uuid4().hex
    bot_id=int(uuid4().hex[:12],16)
    a=ScopedStore(db,Scope(bot_id,-11,instance_id=namespace))
    b=ScopedStore(db,Scope(bot_id,-22,instance_id=namespace))
    yield a,b
    def cleanup():
        for s in [a,b]:
            with db.transaction(s.scope.audience) as c:
                for table in ['memory_sources','memory_revisions','memory_documents','embeddings','inbox','messages','sessions','forgotten_sources']:
                    c.execute(f'DELETE FROM brodar_v2.{table} WHERE audience=%s',(s.scope.audience,))
                c.execute('DELETE FROM brodar_v2.poll_state WHERE bot_id=%s',(bot_id,))
                c.execute('DELETE FROM brodar_v2.audience_registry WHERE audience=%s',(s.scope.audience,))
    await db.run(cleanup)
    await db.close()


async def add(store,text,update=1):
    return await store.ingest(update_id=update,message_id=update,sender_id=42,
        sender_name='test',text=text,timestamp=datetime.now(timezone.utc),payload={'test':True})


@pytest.mark.asyncio
async def test_duplicate_ingestion_and_edits_preserve_inbox(stores):
    a,b=stores
    # Bot update IDs are global; make this test's fake bot distinct too.
    first=await add(a,'hello')
    duplicate=await add(a,'hello')
    assert first[1] is True and duplicate==(first[0],False)
    rows=await a.recent()
    assert len(rows)==1 and rows[0]['content']=='hello'
    assert await b.recent()==[]


@pytest.mark.asyncio
async def test_automatic_notes_revision_conflict_sources_and_rls(stores):
    a,b=stores
    await add(a,'please use shorter explanations')
    mid=str((await a.recent())[0]['id'])
    written=await a.memory_write('context/people/42.md','likes concise explanations',
        expected_revision=0,reason='direct feedback',source_ids=[mid],subject_ids=[42])
    assert written['revision']==1
    read=await a.memory_read('context/people/42.md')
    assert read['content']=='likes concise explanations'
    assert await b.memory_read('context/people/42.md') is None
    with pytest.raises(RevisionConflict):
        await a.memory_write('context/people/42.md','stale edit',expected_revision=0,reason='stale')
    with pytest.raises(ValueError,match='this audience'):
        await b.memory_write('context/forged.md','other group source',expected_revision=0,reason='forged',source_ids=[mid])
    def cross_scope_query():
        with a.db.transaction(b.scope.audience,readonly=True) as c:
            # Deliberately omit the audience predicate: RLS still filters it.
            c.execute('SELECT id FROM brodar_v2.memory_documents WHERE id=%s',(written['id'],))
            assert not c.fetchall()
    await a.db.run(cross_scope_query)
    await a.memory_delete('context/people/42.md',expected_revision=1,reason='superseded')
    assert await a.memory_read('context/people/42.md') is None


@pytest.mark.asyncio
async def test_vector_recall_and_stale_embedding_guard(stores):
    a,b=stores
    await add(a,'we agreed to build a presentation about astronomy')
    row=(await a.recent())[0]
    vector=[1.0]+[0.0]*767
    assert await a.put_embedding('message',str(row['id']),row['content'],vector,'test-model')
    assert not await a.put_embedding('message',str(row['id']),'outdated text',vector,'test-model')
    result=await a.search(' космос ',vector=vector,model='test-model')
    assert result[0]['content']==row['content']
    assert await b.search('astronomy',vector=vector,model='test-model')==[]


@pytest.mark.asyncio
async def test_consumer_takeover_reclaims_work_and_fences_previous_owner(stores):
    from brodar.recovery import ConsumerLease
    from brodar.runtime_store import RuntimeStore
    a,_=stores
    first=ConsumerLease(a.db,a.scope.bot_id)
    second=ConsumerLease(a.db,a.scope.bot_id)
    await first.acquire()
    try:
        with pytest.raises(RuntimeError,match='Another instance'):
            await second.acquire()
        old=RuntimeStore(a.db,a.scope,lease_owner=first.owner)
        new=RuntimeStore(a.db,a.scope,lease_owner=second.owner)
        await add(old,'recover this task')
        claimed=await old.claim_inputs()
        assert len(claimed)==1
        def expire():
            with a.db.transaction() as c:
                c.execute("UPDATE brodar_v2.poll_state SET lease_until=now()-interval '1 second' WHERE bot_id=%s",(a.scope.bot_id,))
        await a.db.run(expire)
        await second.acquire()
        with pytest.raises(PermissionError):await first.require()
        with pytest.raises(PermissionError):await old.claim_inputs()
        reclaimed=await new.claim_inputs()
        assert reclaimed[0]['id']==claimed[0]['id'] and reclaimed[0]['attempts']==2
        await old.finish_inputs([claimed[0]['id']])
        def status():
            with a.db.transaction(a.scope.audience,readonly=True) as c:
                c.execute('SELECT status,lease_owner FROM brodar_v2.inbox WHERE id=%s',(str(claimed[0]['id']),))
                return c.fetchone()
        row=await a.db.run(status)
        assert row[0]=='running' and str(row[1])==second.owner
        await new.finish_inputs([claimed[0]['id']])
        assert (await a.db.run(status))[0]=='done'
    finally:
        await first.release()
        await second.release()


@pytest.mark.asyncio
async def test_source_forgetting_removes_derivatives_and_prevents_reimport(stores):
    from brodar.forgetting import forget_messages
    from brodar.legacy import import_messages,verify_import
    from brodar.learning import LearningStore
    a,b=stores
    a=LearningStore(a.db,a.scope)
    await add(a,'source detail to forget')
    mid=str((await a.recent())[0]['id'])
    await a.memory_write('context/detail.md','learned from that source',expected_revision=0,reason='fixture',source_ids=[mid])
    await a.session_put('codex','fixture','stale-session')
    result=await forget_messages(a.db,a.scope.audience,[mid])
    assert result['forgotten_messages']==1 and result['retired_source_linked_notes']==1
    assert await a.memory_read('context/detail.md') is None
    assert await a.recent()==[]
    assert await a.session_get('codex','fixture') is None
    assert (await add(a,'source detail to forget',update=1))[1] is False
    archive={'source':'legacy-fixture','tables':{'messages':[{'id':1,'chat_id':a.scope.chat_id,
        'created_at':'2026-07-25 12:00:00','role':'user','content':'legacy detail'}]}}
    assert (await import_messages(a.db,archive,instance_id=a.scope.instance_id))['inserted']==1
    legacy=str((await a.recent())[0]['id'])
    await forget_messages(a.db,a.scope.audience,[legacy])
    assert (await import_messages(a.db,archive,instance_id=a.scope.instance_id))['inserted']==0
    assert await verify_import(a.db,archive,instance_id=a.scope.instance_id)==0
    assert await b.recent()==[]
