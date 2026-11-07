"""Operator-only encrypted snapshots, including private artifact objects.

The recovery key lives in an owner-only schema and an optional private key file.
Neither the runtime role nor the agent's tools can read it. Restore only accepts
an empty application schema; legacy public tables are never modified.
"""

import gzip
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from uuid import UUID, uuid4

from cryptography.fernet import Fernet

from .store import records

SCOPED = ('messages','memory_documents','memory_revisions','memory_sources',
          'embeddings','inbox','sessions','deliveries','jobs','reflection_state','maintenance_runs',
          'forgotten_sources','tool_calls','artifacts')
GLOBAL = ('audience_registry','poll_state')
FORMAT = 1


def encode(value):
    return json.dumps(value,default=str,ensure_ascii=False,sort_keys=True,separators=(',',':')).encode()


def sha(body):
    return hashlib.sha256(body).hexdigest()


async def recovery_key(db, key_file=None, *, create=False):
    if key_file and Path(key_file).exists():
        result=json.loads(Path(key_file).read_text())
        UUID(result['id']);Fernet(result['key'].encode())
        return result
    def op():
        with db.transaction() as c:
            c.execute("SELECT id,encryption_key FROM brodar_ops.backup_keys WHERE name='snapshots-v1'")
            row=c.fetchone()
            if row:return {'id':str(row[0]),'key':row[1]}
            if not create:raise ValueError('Provide the private recovery key file from the source deployment')
            result={'id':str(uuid4()),'key':Fernet.generate_key().decode()}
            c.execute("INSERT INTO brodar_ops.backup_keys(id,name,encryption_key) VALUES (%s,'snapshots-v1',%s)",
                      (result['id'],result['key']))
            return result
    result=await db.run(op)
    if key_file:
        p=Path(key_file);p.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd=os.open(p,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:json.dump(result,f)
    return result


async def snapshot(db):
    def op():
        with db.transaction(readonly=True) as c:
            c.execute('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ')
            c.execute('SELECT * FROM brodar_v2.audience_registry ORDER BY audience')
            registry=records(c)
            result={'format':FORMAT,'created_at':datetime.now(timezone.utc).isoformat(),
                    'global':{'audience_registry':registry},'audiences':{}}
            c.execute('SELECT * FROM brodar_v2.poll_state')
            result['global']['poll_state']=records(c)
            c.execute('SELECT version,checksum FROM brodar_v2.schema_migrations ORDER BY version')
            result['migrations']=records(c)
            for row in registry:
                audience=row['audience']
                c.execute("SELECT set_config('brodar.audience',%s,true)",(audience,))
                pairs=','.join("'"+table+"',(SELECT coalesce(jsonb_agg(t),'[]'::jsonb) FROM brodar_v2."+
                               table+' t WHERE audience=%s)' for table in SCOPED)
                c.execute('SELECT jsonb_build_object('+pairs+')',tuple(audience for _ in SCOPED))
                result['audiences'][audience]=c.fetchone()[0]
            return result
    return await db.run(op)


def counts(data):
    result={table:len(rows) for table,rows in data['global'].items()}
    for table in SCOPED:
        result[table]=sum(len(tables[table]) for tables in data['audiences'].values())
    return result


async def backup(db, objects, *, key_file=None):
    key=await recovery_key(db,key_file,create=True)
    cipher=Fernet(key['key'].encode())
    data=await snapshot(db)
    backup_id=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')+'-'+uuid4().hex[:12]
    prefix='snapshots/'+backup_id
    assets={}
    for tables in data['audiences'].values():
        for artifact in tables['artifacts']:
            digest=artifact['content_hash']
            if digest not in assets:
                body=await objects.get(artifact['object_key'])
                if sha(body)!=digest:raise ValueError('Artifact checksum mismatch during backup')
                remote=prefix+'/objects/'+digest+'.fernet'
                await objects.put(remote,cipher.encrypt(body),'application/octet-stream',bucket=objects.backups_bucket)
                assets[digest]=remote
    data['objects']=assets
    content=encode(data)
    encrypted=cipher.encrypt(gzip.compress(content,mtime=0))
    await objects.put(prefix+'/snapshot.fernet',encrypted,'application/octet-stream',bucket=objects.backups_bucket)
    manifest={'format':FORMAT,'backup_id':backup_id,'key_id':key['id'],
              'sha256':sha(content),'encrypted_sha256':sha(encrypted),'rows':counts(data),'objects':len(assets)}
    # Publish the manifest last: incomplete uploads are never listed as complete.
    await objects.put(prefix+'/manifest.json',encode(manifest),'application/json',bucket=objects.backups_bucket)
    return manifest


async def load_backup(db, objects, backup_id, *, key_file=None):
    if not backup_id or any(ch not in '0123456789TZabcdef-' for ch in backup_id):
        raise ValueError('Invalid backup ID')
    prefix='snapshots/'+backup_id
    manifest=json.loads(await objects.get(prefix+'/manifest.json',bucket=objects.backups_bucket))
    key=await recovery_key(db,key_file)
    if manifest['format']!=FORMAT or key['id']!=manifest['key_id']:
        raise ValueError('Snapshot format or recovery key does not match')
    encrypted=await objects.get(prefix+'/snapshot.fernet',bucket=objects.backups_bucket,max_bytes=500_000_000)
    if sha(encrypted)!=manifest['encrypted_sha256']:raise ValueError('Encrypted snapshot checksum mismatch')
    cipher=Fernet(key['key'].encode())
    content=gzip.decompress(cipher.decrypt(encrypted))
    if sha(content)!=manifest['sha256']:raise ValueError('Snapshot checksum mismatch')
    data=json.loads(content)
    if data['format']!=FORMAT or counts(data)!=manifest['rows']:raise ValueError('Snapshot row counts mismatch')
    return data,manifest,cipher


async def restore(db, objects, backup_id, *, key_file=None, source_objects=None):
    source=source_objects or objects
    data,manifest,cipher=await load_backup(db,source,backup_id,key_file=key_file)
    if set(data['global'])!=set(GLOBAL):raise ValueError('Unexpected global snapshot tables')
    for audience,tables in data['audiences'].items():
        if set(tables)!=set(SCOPED):raise ValueError('Unexpected scoped snapshot tables')
        if any(row.get('audience')!=audience for rows in tables.values() for row in rows):
            raise ValueError('Snapshot contains an audience mismatch')
    # Upload verified objects before touching SQL. An interrupted attempt leaves
    # harmless content-addressed objects and an empty database that can retry.
    for tables in data['audiences'].values():
        for artifact in tables['artifacts']:
            digest=artifact['content_hash']
            encrypted=await source.get(data['objects'][digest],bucket=source.backups_bucket,max_bytes=30_000_000)
            body=cipher.decrypt(encrypted)
            if sha(body)!=digest or len(body)!=artifact['size_bytes']:raise ValueError('Backup artifact checksum mismatch')
            await objects.put(artifact['object_key'],body,artifact['mime_type'])
    def op():
        with db.transaction() as c:
            c.execute('SELECT pg_advisory_xact_lock(774916033)')
            c.execute('SELECT version,checksum FROM brodar_v2.schema_migrations ORDER BY version')
            if records(c)!=data['migrations']:raise ValueError('Restore requires matching schema migrations')
            # Owner operation on a fresh database. TRUNCATE would bypass RLS and
            # risk the wrong deployment; we never issue it here.
            c.execute('SELECT count(*) FROM brodar_v2.audience_registry')
            if c.fetchone()[0]:raise ValueError('Restore destination must have an empty audience registry')
            c.execute('SELECT count(*) FROM brodar_v2.poll_state')
            if c.fetchone()[0]:raise ValueError('Restore destination must have no polling state')
            for audience in data['audiences']:
                c.execute("SELECT set_config('brodar.audience',%s,true)",(audience,))
                for table in SCOPED:
                    c.execute('SELECT 1 FROM brodar_v2.'+table+' LIMIT 1')
                    if c.fetchone():raise ValueError('Restore destination already contains application rows')
            def insert(table,rows):
                if not rows:return
                c.execute('INSERT INTO brodar_v2.'+table+' SELECT * FROM jsonb_populate_recordset(NULL::brodar_v2.'+
                          table+',%s::jsonb)',(json.dumps(rows),))
            insert('audience_registry',data['global']['audience_registry'])
            poll=[dict(row,lease_owner=None,lease_until=None) for row in data['global']['poll_state']]
            insert('poll_state',poll)
            for audience,tables in data['audiences'].items():
                c.execute("SELECT set_config('brodar.audience',%s,true)",(audience,))
                for table in SCOPED:
                    rows=tables[table]
                    if table=='inbox':
                        rows=[dict(row,lease_owner=None,lease_until=None,
                                   status='pending' if row['status']=='running' else row['status']) for row in rows]
                    elif table=='jobs':
                        rows=[dict(row,lease_until=None,status='pending' if row['status']=='running' else row['status']) for row in rows]
                    # Sending receipts remain uncertain/in progress and block
                    # replay. A restore must not claim those sends failed.
                    insert(table,rows)
    await db.run(op)
    return await verify_restore(db,objects,data,manifest)


async def verify_restore(db,objects,expected,manifest):
    actual=await snapshot(db)
    def normalized(data):
        data=json.loads(encode(data))
        for row in data['global']['poll_state']:
            row.update(lease_owner=None,lease_until=None)
        for tables in data['audiences'].values():
            for table in ['inbox','jobs']:
                for row in tables[table]:
                    row['lease_until']=None
                    if table=='inbox':row['lease_owner']=None
                    if row['status']=='running':row['status']='pending'
            for table in SCOPED:tables[table]=sorted(tables[table],key=encode)
        for table in GLOBAL:data['global'][table]=sorted(data['global'][table],key=encode)
        return {name:data[name] for name in ['global','audiences','migrations']}
    if encode(normalized(actual))!=encode(normalized(expected)):
        raise ValueError('Restored database content differs from the backup')
    for tables in actual['audiences'].values():
        for artifact in tables['artifacts']:
            body=await objects.get(artifact['object_key'])
            if sha(body)!=artifact['content_hash']:raise ValueError('Restored artifact checksum mismatch')
    return {'backup_id':manifest['backup_id'],'restored_rows':counts(actual),
            'objects_verified':manifest['objects'],'database_content_verified':True}
