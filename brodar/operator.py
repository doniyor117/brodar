"""Local operator commands; never exposed to conversational tools."""

import json
import os
import secrets
from pathlib import Path
from urllib.parse import urlsplit,unquote

from dotenv import dotenv_values,set_key

from .legacy import export_public,read_export,import_messages,verify_import
from .postgres import Postgres
from .provision import provision_runtime


async def database_command(args):
    if args.admin_url_file is None:raise ValueError('--admin-url-file is required')
    uri=args.admin_url_file.expanduser().read_text().strip()
    db=Postgres(uri)
    try:
        if args.command=='forget-history':
            from .forgetting import forget_messages
            if not args.audience:raise ValueError('--audience is required')
            return await forget_messages(db,args.audience,args.message_id)
        if args.command in {'backup','restore'}:
            from .backups import backup,restore
            from .objects import ObjectStore
            objects=ObjectStore.from_file(args.storage_config)
            if objects is None:raise ValueError('--storage-config must identify a private S3 configuration')
            source=ObjectStore.from_file(args.source_storage_config)
            try:
                if args.command=='backup':
                    from .backups import prune_managed
                    from .settings import Settings
                    result=await backup(db,objects,key_file=args.recovery_key_file,budget_bytes=Settings.load(profile=getattr(args,'profile',None)).backup_budget_bytes)
                    result.update(await prune_managed(db,objects,key_file=args.recovery_key_file))
                    return result
                if not args.backup_id:raise ValueError('--backup-id is required')
                return await restore(db,objects,args.backup_id,key_file=args.recovery_key_file,source_objects=source)
            finally:
                await objects.close()
                if source:await source.close()
        if args.command=='migrate':
            await db.migrate()
            return {'migrations':'applied'}
        if args.command=='import-history':
            if args.archive is None:raise ValueError('--archive is required')
            archive=read_export(args.archive.expanduser())
            result=await import_messages(db,archive,instance_id=args.instance_id)
            result['verified']=await verify_import(db,archive,instance_id=args.instance_id)
            return result
        if args.command=='export-history':
            if args.output is None:raise ValueError('--output is required')
            return await export_public(db,args.output.expanduser(),args.source)
        if args.command=='provision':
            path=args.runtime_env_file.expanduser()
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            existing=dotenv_values(path).get('BRODAR_DATABASE_URL') if path.exists() else None
            password=None
            if existing:
                parsed=urlsplit(existing)
                if unquote(parsed.username or '')!=args.role:
                    raise ValueError('Existing runtime role differs; use a separate private environment file')
                password=unquote(parsed.password or '')
            from .profile import atomic_private
            from .provision import runtime_uri
            pending=path.with_name(path.name+'.provision-pending')
            if not password and pending.exists():
                candidate=pending.read_text().strip();parsed=urlsplit(candidate)
                if unquote(parsed.username or '')!=args.role:raise ValueError('Pending runtime role differs')
                password=unquote(parsed.password or '')
            password=password or secrets.token_urlsafe(40)
            if not existing:atomic_private(pending,runtime_uri(uri,args.role,password))
            runtime=await provision_runtime(db,uri,role=args.role,password=password)
            path.touch(mode=0o600,exist_ok=True)
            path.chmod(0o600)
            set_key(str(path),'BRODAR_DATABASE_URL',runtime)
            pending.unlink(missing_ok=True)
            return {'runtime_role':args.role,'runtime_env_file':str(path),'credentials_printed':False}
        raise ValueError('Unknown database command')
    finally:await db.close()
