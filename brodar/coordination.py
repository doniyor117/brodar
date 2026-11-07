"""Persistent global peer budgets; no prompt can bypass the eleventh-send denial."""
from uuid import uuid4
from .store import digest

class LoopGuard:
    def __init__(self,db,instance):
        self.db=db;self.audience='coord:'+instance;self.instance=instance;self.rooms=set();self.aliases={}
    async def register(self):
        def op():
            with self.db.transaction(self.audience) as c:
                c.execute('INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id) VALUES (%s,%s,0) ON CONFLICT DO NOTHING',(self.audience,self.instance))
                c.execute("DELETE FROM brodar_v2.bot_reservations WHERE audience=%s AND created_at<now()-interval '30 days'",(self.audience,))
                c.execute('SELECT DISTINCT origin_chat_id FROM brodar_v2.bot_exchanges WHERE audience=%s',(self.audience,))
                return {row[0] for row in c.fetchall()}
        self.rooms=await self.db.run(op)
    async def resolve(self,sender,researcher):
        from .routing import peer_key
        actual=sender['id']
        if actual in self.aliases:return self.aliases[actual]
        desired=peer_key(sender,researcher)
        def op():
            with self.db.transaction(self.audience) as c:
                c.execute('SELECT peer_id FROM brodar_v2.bot_aliases WHERE audience=%s AND bot_id=%s',(self.audience,actual))
                row=c.fetchone()
                if row:return row[0]
                c.execute('INSERT INTO brodar_v2.bot_aliases(audience,bot_id,peer_id) VALUES (%s,%s,%s) ON CONFLICT(audience,bot_id) DO NOTHING',(self.audience,actual,desired))
                c.execute('SELECT peer_id FROM brodar_v2.bot_aliases WHERE audience=%s AND bot_id=%s',(self.audience,actual))
                return c.fetchone()[0]
        result=await self.db.run(op);self.aliases[actual]=result
        return result

    async def incoming(self,peer,chat_id,text):
        self.rooms.add(chat_id)
        hashed=digest(text.strip().lower())
        def op():
            with self.db.transaction(self.audience) as c:
                c.execute('INSERT INTO brodar_v2.bot_exchanges(audience,peer_id,exchange_id,origin_chat_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',(self.audience,peer,str(uuid4()),chat_id))
                c.execute("UPDATE brodar_v2.bot_exchanges SET last_digest=%s,last_incoming_at=now() WHERE audience=%s AND peer_id=%s AND NOT closed AND used<10 AND created_at>now()-interval '20 minutes' AND (last_digest IS DISTINCT FROM %s) AND (last_incoming_at IS NULL OR last_incoming_at<now()-interval '3 seconds') RETURNING peer_id",(hashed,self.audience,peer,hashed))
                return c.fetchone() is not None
        return await self.db.run(op)
    async def available(self,peer):
        def op():
            with self.db.transaction(self.audience,readonly=True) as c:
                c.execute("SELECT 1 FROM brodar_v2.bot_exchanges WHERE audience=%s AND peer_id=%s AND NOT closed AND used<10 AND created_at>now()-interval '20 minutes'",(self.audience,peer))
                return c.fetchone() is not None
        return await self.db.run(op)
    async def human(self,chat_id):
        # No extra remote transaction for ordinary human chat without a bot exchange.
        if chat_id not in self.rooms:return
        def op():
            with self.db.transaction(self.audience) as c:
                c.execute('UPDATE brodar_v2.bot_exchanges SET exchange_id=%s,used=0,closed=false,last_digest=NULL,last_incoming_at=NULL,created_at=now() WHERE audience=%s AND origin_chat_id=%s',(str(uuid4()),self.audience,chat_id))
        await self.db.run(op)
        self.rooms.discard(chat_id)
    async def reserve(self,peer,chat_id,operation):
        self.rooms.add(chat_id)
        def op():
            with self.db.transaction(self.audience) as c:
                c.execute('INSERT INTO brodar_v2.bot_exchanges(audience,peer_id,exchange_id,origin_chat_id) VALUES (%s,%s,%s,%s) ON CONFLICT DO NOTHING',(self.audience,peer,str(uuid4()),chat_id))
                c.execute('SELECT exchange_id FROM brodar_v2.bot_exchanges WHERE audience=%s AND peer_id=%s FOR UPDATE',(self.audience,peer))
                exchange=c.fetchone()[0]
                c.execute('SELECT peer_id FROM brodar_v2.bot_reservations WHERE audience=%s AND operation_key=%s',(self.audience,operation))
                old=c.fetchone()
                if old:
                    if old[0]!=peer:raise PermissionError('Loop reservation belongs to another peer')
                    return
                c.execute("UPDATE brodar_v2.bot_exchanges SET used=used+1 WHERE audience=%s AND peer_id=%s AND NOT closed AND used<10 AND created_at>now()-interval '20 minutes' RETURNING used",(self.audience,peer))
                if not c.fetchone():raise PermissionError('Global bot exchange limit reached; stop without another bot-directed message')
                c.execute('INSERT INTO brodar_v2.bot_reservations(audience,operation_key,peer_id,exchange_id) VALUES (%s,%s,%s,%s)',(self.audience,operation,peer,str(exchange)))
        await self.db.run(op)
    async def close(self,peer):
        def op():
            with self.db.transaction(self.audience) as c:c.execute('UPDATE brodar_v2.bot_exchanges SET closed=true WHERE audience=%s AND peer_id=%s',(self.audience,peer))
        await self.db.run(op)
