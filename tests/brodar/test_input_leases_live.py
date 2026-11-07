"""Opt-in input lifecycle regressions; use only a disposable test database."""
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from brodar.postgres import Postgres
from brodar.recovery import ConsumerLease
from brodar.runtime_store import RuntimeStore
from brodar.scope import Scope

pytestmark = pytest.mark.skipif(not os.environ.get('BRODAR_TEST_DATABASE_URL'),
                              reason='Disposable test database not configured')


@pytest.mark.asyncio
async def test_deferred_inputs_do_not_spend_attempts_and_active_inputs_stay_owned():
    db = Postgres(os.environ['BRODAR_TEST_DATABASE_URL'])
    scope = Scope(int(uuid4().hex[:12], 16), -11, instance_id='lease-test-'+uuid4().hex)
    lease = ConsumerLease(db, scope.bot_id)
    store = RuntimeStore(db, scope, lease_owner=lease.owner)
    try:
        await lease.acquire()
        for uid in [1, 2]:
            await store.ingest(update_id=uid, message_id=uid, sender_id=42,
                sender_name='test', text='request', timestamp=datetime.now(timezone.utc), payload={})
        active = (await store.claim_inputs(limit=1))[0]
        for _ in range(4):
            queued = await store.claim_inputs(count_attempt=False, exclude_ids=[active['id']])
            assert len(queued) == 1 and queued[0]['attempts'] == 0
            await store.finish_inputs([queued[0]['id']], retry=True)
        accepted = await store.claim_inputs(count_attempt=False, exclude_ids=[active['id']])
        accepted = await store.mark_inputs_attempted([accepted[0]['id']])
        assert accepted[0]['attempts'] == 1
        def expire():
            with db.transaction(scope.audience) as c:
                c.execute("UPDATE brodar_v2.inbox SET lease_until=now()-interval '1 second' WHERE audience=%s", (scope.audience,))
        await db.run(expire)
        ids = [active['id'], accepted[0]['id']]
        assert await store.claim_inputs(count_attempt=False, exclude_ids=ids) == []
        assert await store.renew_inputs(ids) == 2
        assert await store.claim_inputs() == []
        # One exhausted member must not consume a fresher member's retries.
        await store.mark_inputs_attempted([active['id']])
        await store.mark_inputs_attempted([active['id']])
        await store.finish_inputs(ids, retry=True, error='temporary_failure')
        remaining = await store.claim_inputs()
        assert [r['id'] for r in remaining] == [accepted[0]['id']]
        assert remaining[0]['attempts'] == 2
        await lease.release()
        with pytest.raises(PermissionError):
            await store.renew_inputs([accepted[0]['id']])
    finally:
        def cleanup():
            with db.transaction(scope.audience) as c:
                for table in ['inbox', 'messages']:
                    c.execute(f'DELETE FROM brodar_v2.{table} WHERE audience=%s', (scope.audience,))
                c.execute('DELETE FROM brodar_v2.poll_state WHERE bot_id=%s', (scope.bot_id,))
                c.execute('DELETE FROM brodar_v2.audience_registry WHERE audience=%s', (scope.audience,))
        await db.run(cleanup)
        await db.close()
