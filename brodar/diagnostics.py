"""Actionable, redacted operator diagnostics. No secret-bearing exceptions."""
import asyncio
import hashlib
import importlib.resources
import os
import shutil
import stat
from pathlib import Path

async def diagnose(cfg,*,network=True):
    checks=[]
    def add(name,status,detail,fix=''):checks.append({'name':name,'status':status,'detail':detail,'fix':fix})
    from .redaction import register_secrets
    from urllib.parse import urlsplit,unquote
    register_secrets(cfg.telegram_token,cfg.database_url,cfg.google_key,unquote(urlsplit(cfg.database_url).password or ''))
    binary=shutil.which(cfg.brain)
    add('provider','ok' if binary else 'error','CLI installed' if binary else 'CLI missing','Install '+cfg.brain+' for this platform')
    if binary:
        try:
            proc=await asyncio.create_subprocess_exec(binary,'--version',stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.DEVNULL)
            output,_=await asyncio.wait_for(proc.communicate(),10)
            add('provider_version','ok',output.decode(errors='replace').strip()[:120])
        except Exception:add('provider_version','warning','Version check failed')
    add('model','unverified',cfg.model+' / '+cfg.effort,'Use onboard/login and select an explicit live test; doctor does not spend inference quota')
    add('owner','ok' if len(cfg.owner_ids)==1 else 'error','Exactly one owner configured' if len(cfg.owner_ids)==1 else 'Configure one primary owner','brodar configure owner')
    if cfg.brain=='codex' and binary:
        from .brains.codex import child_environment
        home=cfg.runtime_dir/'codex';(home/'user').mkdir(parents=True,exist_ok=True,mode=0o700)
        try:
            proc=await asyncio.create_subprocess_exec(binary,'login','status',env=child_environment(home),stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE)
            await asyncio.wait_for(proc.communicate(),10)
            add('provider_auth','ok' if proc.returncode==0 else 'error','Dedicated runtime login present' if proc.returncode==0 else 'Dedicated runtime login missing','brodar login --brain codex')
        except Exception:add('provider_auth','unverified','CLI auth status unsupported')
    else:add('provider_auth','unverified','Run the selected provider login flow','brodar login --brain '+cfg.brain)
    if cfg.profile_path:
        from .profile import read_profile
        _,profile=read_profile(cfg.profile_path)
        for field in ('secret_env_file',):
            if profile.get(field):
                path=Path(profile[field]);safe=path.exists() and not (path.stat().st_mode & 0o077)
                add('private_permissions','ok' if safe else 'error','Secret file permissions restricted' if safe else 'Secret file missing or accessible to other users','chmod 600 the private secret file')
    if cfg.telegram_token and network:
        from telegram import Bot
        try:
            async with Bot(cfg.telegram_token) as bot:
                me=await bot.get_me();webhook=await bot.get_webhook_info()
                add('telegram','ok','@'+str(me.username))
                add('webhook','error' if webhook.url else 'ok','Active webhook detected' if webhook.url else 'Polling compatible','Remove webhook explicitly if switching to polling')
                add('group_visibility','ok' if getattr(me,'can_read_all_group_messages',False) else 'warning','Full group visibility' if getattr(me,'can_read_all_group_messages',False) else 'Check BotFather privacy mode or bot administrator permissions')
        except Exception as exc:add('telegram','error',type(exc).__name__,'Check the private bot token and network')
    else:add('telegram','unverified' if cfg.telegram_token else 'error','Network check skipped' if cfg.telegram_token else 'Token missing','brodar configure telegram')
    if cfg.database_url and network:
        from .postgres import Postgres
        db=Postgres(cfg.database_url)
        try:
            def inspect():
                with db.transaction(readonly=True) as c:
                    c.execute('SELECT rolsuper,rolbypassrls,rolcreatedb,rolcreaterole FROM pg_roles WHERE rolname=current_user');privileges=c.fetchone()
                    c.execute('SELECT version,checksum FROM brodar_v2.schema_migrations');applied=dict(c.fetchall())
                    expected={int(p.name.split('_')[0]):hashlib.sha256(p.read_bytes()).hexdigest() for p in importlib.resources.files('brodar').joinpath('migrations').iterdir() if p.name.endswith('.sql')}
                    c.execute('SELECT kind,bytes FROM brodar_v2.storage_usage');usage=dict(c.fetchall())
                    return privileges,applied==expected,usage
            privileges,migrations,usage=await db.run(inspect)
            add('database_role','error' if any(privileges) else 'ok','Excessive runtime privileges' if any(privileges) else 'Restricted runtime role','Use setup-neon/provision, never run with admin credentials')
            add('migrations','ok' if migrations else 'error','Checksums match' if migrations else 'Missing/changed migrations','Apply checked migrations with operator connection')
            add('storage_usage','ok',usage)
        except Exception as exc:add('database','error',type(exc).__name__,'Check the private runtime URI, migrations and grants')
        finally:await db.close()
    else:add('database','unverified' if cfg.database_url else 'error','Network check skipped' if cfg.database_url else 'Runtime connection missing','brodar setup-neon')
    add('retained_storage','configured' if cfg.storage_config and cfg.storage_config.exists() else 'disabled','Local temporary files remain available; permanent retention requires configured storage')
    if network and cfg.storage_config and cfg.storage_config.exists():
        from .objects import ObjectStore
        objects=None
        try:
            objects=ObjectStore.from_file(cfg.storage_config)
            await objects.inventory(prefix='brodar-healthcheck-nonexistent/')
            add('storage_access','ok','Private object read/list access verified; writes not probed')
        except Exception as exc:add('storage_access','warning',type(exc).__name__,'Check scoped storage credentials/bucket; temporary files still work')
        finally:
            if objects:await objects.close()

    from .control import socket_path
    add('worker','configured' if socket_path(cfg).exists() else 'stopped','Local worker control socket present' if socket_path(cfg).exists() else 'No local worker control socket; foreground/background start is an explicit choice')
    add('research','disabled' if cfg.research_transport=='disabled' else 'unverified',cfg.research_transport)
    return {'ready':not any(c['status']=='error' for c in checks),'checks':checks,'model_tested':False}
