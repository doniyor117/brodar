"""A database lease fences Telegram consumers across phones and restarts."""

from uuid import uuid4


class ConsumerLease:
    def __init__(self, db, bot_id, owner=None):
        self.db, self.bot_id = db, bot_id
        self.owner = str(owner or uuid4())

    async def acquire(self):
        def op():
            with self.db.transaction() as c:
                c.execute("INSERT INTO brodar_v2.poll_state(bot_id,next_update_id,lease_owner,lease_until) "
                          "VALUES (%s,0,%s,now()+interval '120 seconds') ON CONFLICT(bot_id) DO UPDATE "
                          "SET lease_owner=excluded.lease_owner,lease_until=excluded.lease_until "
                          "WHERE poll_state.lease_until IS NULL OR poll_state.lease_until<now() "
                          "OR poll_state.lease_owner=excluded.lease_owner RETURNING bot_id",
                          (self.bot_id,self.owner))
                if c.fetchone() is None:
                    raise RuntimeError('Another instance holds this bot’s database lease')
        await self.db.run(op)

    async def require(self):
        def op():
            with self.db.transaction(readonly=True) as c:
                c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now()',
                          (self.bot_id,self.owner))
                if c.fetchone() is None:raise PermissionError('Telegram consumer lease has expired')
        await self.db.run(op)

    async def release(self):
        def op():
            with self.db.transaction() as c:
                c.execute('UPDATE brodar_v2.poll_state SET lease_until=NULL,lease_owner=NULL '
                          'WHERE bot_id=%s AND lease_owner=%s',(self.bot_id,self.owner))
        await self.db.run(op)
