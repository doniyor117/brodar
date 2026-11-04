"""Termux-compatible Postgres transport: pure Python pg8000, off the event loop.

Every application transaction sets an audience locally. No model-controlled SQL
or database credentials cross the broker boundary. A failed write is never
automatically replayed: callers reconcile by their idempotency key first.
"""

from __future__ import annotations

import asyncio
import hashlib
import ssl
import threading
from contextlib import contextmanager
from importlib.resources import files
from urllib.parse import unquote, urlsplit

import pg8000.dbapi


class Postgres:
    def __init__(self, dsn: str):
        self._dsn = dsn
        self._conn = None
        self._lock = threading.RLock()

    def _connect(self):
        if self._conn is None:
            u = urlsplit(self._dsn)
            if u.scheme not in {'postgres', 'postgresql'} or not u.hostname:
                raise ValueError('Expected a PostgreSQL connection URI')
            self._conn = pg8000.dbapi.connect(
                user=unquote(u.username or ''), password=unquote(u.password or ''),
                host=u.hostname, port=u.port or 5432, database=unquote(u.path[1:]),
                ssl_context=ssl.create_default_context(), timeout=30,
            )
        return self._conn

    @contextmanager
    def transaction(self, audience: str | None = None, *, readonly: bool = False):
        with self._lock:
            conn = self._connect()
            cur = conn.cursor()
            try:
                cur.execute('BEGIN READ ONLY' if readonly else 'BEGIN')
                cur.execute("SET LOCAL statement_timeout = '30s'")
                if audience is not None:
                    cur.execute("SELECT set_config('brodar.audience', %s, true)", (audience,))
                yield cur
                conn.commit()
            except BaseException:
                try:
                    conn.rollback()
                except Exception:
                    self._conn = None
                    try:
                        conn.close()
                    except Exception:
                        pass
                raise
            finally:
                cur.close()

    async def run(self, operation, *args):
        return await asyncio.to_thread(operation, *args)

    async def close(self):
        def close():
            with self._lock:
                if self._conn is not None:
                    self._conn.close()
                    self._conn = None
        await self.run(close)

    async def migrate(self):
        def apply():
            with self.transaction() as cur:
                cur.execute("SELECT pg_advisory_xact_lock(774916033)")
                cur.execute('CREATE SCHEMA IF NOT EXISTS brodar_v2')
                cur.execute('CREATE TABLE IF NOT EXISTS brodar_v2.schema_migrations ('
                            'version integer PRIMARY KEY, checksum text NOT NULL,'
                            'applied_at timestamptz NOT NULL DEFAULT now())')
                for p in sorted(files('brodar').joinpath('migrations').iterdir(), key=lambda p:p.name):
                    if not p.name.endswith('.sql'):
                        continue
                    version = int(p.name.split('_')[0])
                    sql = p.read_text()
                    checksum = hashlib.sha256(sql.encode()).hexdigest()
                    cur.execute('SELECT checksum FROM brodar_v2.schema_migrations WHERE version=%s',(version,))
                    old = cur.fetchone()
                    if old:
                        if old[0] != checksum:
                            raise RuntimeError(f'Migration {version} checksum changed')
                        continue
                    cur.execute(sql)
                    cur.execute('INSERT INTO brodar_v2.schema_migrations(version,checksum) VALUES (%s,%s)',(version,checksum))
        await self.run(apply)
