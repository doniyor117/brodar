"""Operator-only export/import. Raw history survives; legacy prompts aren't loaded.

This module is never registered as an agent tool. Imports use source-scoped
stable IDs and preserve unknown authors and naive timestamps explicitly.
"""

from __future__ import annotations

import gzip
import hashlib
import json
import os
from datetime import datetime,timezone,timedelta
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from .postgres import Postgres
from .scope import Scope
from .store import digest, records
from .redaction import scrub,scrub_object


def identifier(value: str) -> str:
    return '"'+value.replace('"','""')+'"'


async def export_public(db: Postgres, destination: Path, source: str) -> dict:
    def export():
        with db.transaction(readonly=True) as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            c.execute("SELECT table_name FROM information_schema.tables WHERE table_schema='public' "
                      "AND table_type='BASE TABLE' ORDER BY table_name")
            tables = [r[0] for r in c.fetchall()]
            archive = {'format':1,'source':source,'tables':{}}
            for name in tables:
                c.execute('SELECT * FROM public.'+identifier(name))
                archive['tables'][name] = records(c)
        content = json.dumps(archive,default=str,ensure_ascii=False,sort_keys=True).encode()
        destination.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd = os.open(destination,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'wb') as raw:
            with gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as gz:
                gz.write(content)
        manifest = {'source':source,'sha256':hashlib.sha256(content).hexdigest(),
                    'tables':{k:len(v) for k,v in archive['tables'].items()}}
        mp = destination.with_suffix(destination.suffix+'.manifest.json')
        fd = os.open(mp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:
            json.dump(manifest,f,indent=2)
        return manifest
    return await db.run(export)


def read_export(path: Path) -> dict:
    content = gzip.decompress(path.read_bytes())
    manifest = json.loads(path.with_suffix(path.suffix+'.manifest.json').read_text())
    if hashlib.sha256(content).hexdigest()!=manifest['sha256']:
        raise ValueError('Legacy export checksum mismatch')
    archive = json.loads(content)
    if {k:len(v) for k,v in archive['tables'].items()} != manifest['tables']:
        raise ValueError('Legacy export row counts mismatch')
    return archive


async def import_messages(db: Postgres, archive: dict, *, instance_id: str = 'brodar'):
    source = archive['source']
    rows = sorted(archive['tables'].get('messages',[]),key=lambda r:(r['created_at'],r['id']))
    def insert():
        inserted = 0
        groups={}
        imported_at=datetime.now(timezone.utc)
        for sequence,r in enumerate(rows):
            scope=Scope(0,int(r['chat_id']),instance_id=instance_id)
            mid = str(uuid5(NAMESPACE_URL,f'{scope.audience}:{source}:messages:{r["id"]}'))
            raw = json.dumps(r,default=str,sort_keys=True,ensure_ascii=False)
            text=scrub(r['content'])
            groups.setdefault(scope.audience,[]).append({'id':mid,'role':r['role'],'content':text,
                'legacy_timestamp':r['created_at'],'sender_id':r.get('user_id'),'source_id':str(r['id']),
                'source_hash':digest(text),'received_at':(imported_at+timedelta(microseconds=sequence)).isoformat(),
                'metadata':{'legacy_row':scrub_object(r),'legacy_row_sha256':digest(raw),
                            'timestamp_timezone':'unknown','author_verified':False}})
        for audience,batch in groups.items():
            with db.transaction(audience) as c:
                c.execute('INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id) VALUES (%s,%s,%s) '
                    'ON CONFLICT DO NOTHING',(audience,instance_id,int(audience.rsplit(":",1)[1])))
                c.execute('INSERT INTO brodar_v2.messages '
                          '(id,audience,role,content,legacy_timestamp,sender_id,source,source_id,source_hash,metadata,received_at,changed_at) '
                          'SELECT x.id,%s,x.role,x.content,x.legacy_timestamp,x.sender_id,%s,x.source_id,x.source_hash,x.metadata,x.received_at,x.received_at '
                          'FROM jsonb_to_recordset(%s::jsonb) AS x(id uuid,role text,content text,legacy_timestamp text,'
                          'sender_id bigint,source_id text,source_hash text,metadata jsonb,received_at timestamptz) '
                          'WHERE NOT EXISTS (SELECT 1 FROM brodar_v2.forgotten_sources f WHERE f.audience=%s AND f.source=%s AND f.source_id=x.source_id) '
                          'ON CONFLICT(audience,source,source_id) DO NOTHING RETURNING id',
                          (audience,source,json.dumps(batch,ensure_ascii=False),audience,source))
                inserted+=len(c.fetchall())
        return {'source':source,'input_rows':len(rows),'inserted':inserted}
    return await db.run(insert)


async def verify_import(db: Postgres, archive: dict, *, instance_id: str = 'brodar'):
    def verify():
        verified = 0
        groups={}
        for row in archive['tables'].get('messages',[]):groups.setdefault(int(row['chat_id']),[]).append(row)
        for chat_id,expected in groups.items():
            scope = Scope(0,chat_id,instance_id=instance_id)
            with db.transaction(scope.audience,readonly=True) as c:
                c.execute('SELECT content,legacy_timestamp,sender_id,metadata,source_id FROM brodar_v2.messages '
                          'WHERE audience=%s AND source=%s',(scope.audience,archive['source']))
                found={row[4]:row for row in c.fetchall()}
                c.execute('SELECT source_id FROM brodar_v2.forgotten_sources WHERE audience=%s AND source=%s',
                          (scope.audience,archive['source']))
                forgotten={row[0] for row in c.fetchall()}
                for r in expected:
                    if str(r['id']) in forgotten:continue
                    row=found.get(str(r['id']))
                    if not row or row[0]!=scrub(r['content']) or row[1]!=r['created_at'] or row[2]!=r.get('user_id'):
                        raise RuntimeError('Legacy import verification failed')
                    if row[3]['legacy_row']!=scrub_object(r):
                        raise RuntimeError('Legacy metadata verification failed')
                    verified += 1
        return verified
    return await db.run(verify)
