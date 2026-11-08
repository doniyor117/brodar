"""Resumable headless setup shared by plain prompts, the TUI, and automation."""
from __future__ import annotations
import fcntl
import json
import os
import secrets
from pathlib import Path
from dotenv import dotenv_values,set_key
from .profile import read_profile,write_profile,atomic_private,DEFAULT_PROFILE

class SetupEngine:
    def __init__(self,path=None):
        self.path,self.profile=read_profile(path)
        if not self.profile:self.profile={'version':1,'settings':{}}
        self.directory=self.path.parent
        self.directory.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.journal_path=self.directory/(self.path.stem+'.setup.json')
        try:self.journal=json.loads(self.journal_path.read_text())
        except FileNotFoundError:self.journal={'version':1,'steps':{},'resources':{}}
        self.lock=None
    def __enter__(self):
        self.lock=(self.directory/(self.path.stem+'.setup.lock')).open('a')
        try:fcntl.flock(self.lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock.close();raise RuntimeError('Another setup is already using this profile') from None
        return self
    def __exit__(self,*args):
        if self.lock:self.lock.close();self.lock=None
    def save(self):
        write_profile(self.path,self.profile)
        atomic_private(self.journal_path,json.dumps(self.journal,indent=2)+'\n')
    def done(self,step,state='configured'):
        self.journal['steps'][step]=state;self.save()
    def set(self,key,value):
        if not key.startswith('BRODAR_'):raise ValueError('Not an instance setting')
        self.profile['settings'][key]=value;self.save()
    def secret(self,key,value):
        from .profile import SECRET_KEYS
        if key not in SECRET_KEYS:raise ValueError('Unknown secret field')
        path=Path(self.profile.get('secret_env_file',self.directory/(self.path.stem+'.env')))
        old=dotenv_values(path) if path.exists() else {}
        old[key]=value
        # dotenv serialisation without secret-bearing temp command lines.
        body=''.join(k+"='"+str(v).replace('\\','\\\\').replace("'","\\'")+"'\n" for k,v in old.items() if v is not None)
        atomic_private(path,body);self.profile['secret_env_file']=str(path);self.save()
    def resource(self,key,value):
        # Values here are resource identifiers only, never credentials or callback URLs.
        self.journal['resources'][key]=value;self.save()
    def defaults(self):
        values={'BRODAR_BRAIN':'codex','BRODAR_INSTANCE_ID':'brodar','BRODAR_ALIASES':'brodar,brodar simon,samy,simon,simon bro','BRODAR_TIMEZONE':'UTC','BRODAR_RESEARCH_TRANSPORT':'disabled','BRODAR_TEMP_BUDGET_BYTES':500000000,'BRODAR_RETAINED_BUDGET_BYTES':1000000000,'BRODAR_BACKUP_BUDGET_BYTES':2000000000,'BRODAR_BACKUP_INTERVAL_SECONDS':1209600,'BRODAR_TEMP_IDLE_SECONDS':604800,'BRODAR_CLEANUP_INTERVAL_SECONDS':604800}
        for key,value in values.items():self.profile['settings'].setdefault(key,value)
        self.save()
    async def telegram(self,token):
        from .redaction import register_secrets
        register_secrets(token)
        from telegram import Bot
        async with Bot(token) as bot:
            me=await bot.get_me();webhook=await bot.get_webhook_info()
            if webhook.url:raise ValueError('Bot has an active webhook. Remove it explicitly or choose another bot; setup did not change it')
        self.secret('TELEGRAM_BOT_TOKEN',token);self.resource('telegram_bot_id',me.id);self.resource('telegram_username',me.username);self.done('telegram','validated')
        return {'id':me.id,'username':me.username,'can_read_all_group_messages':getattr(me,'can_read_all_group_messages',None)}
    async def provision_database(self,uri,*,role='brodar_runtime'):
        from .redaction import register_secrets
        from urllib.parse import urlsplit,unquote
        register_secrets(uri,unquote(urlsplit(uri).password or ""))
        from .postgres import Postgres
        from .provision import provision_runtime,runtime_uri
        from urllib.parse import urlsplit,unquote
        secrets_file=Path(self.profile.get('secret_env_file',self.directory/(self.path.stem+'.env')))
        old=dotenv_values(secrets_file) if secrets_file.exists() else {}
        existing=old.get('BRODAR_DATABASE_URL')
        pending=self.directory/(self.path.stem+'.db-pending')
        if pending.exists():existing=pending.read_text().strip()
        if existing:
            p=urlsplit(existing)
            if unquote(p.username or '')!=role or p.hostname!=urlsplit(uri).hostname or p.path!=urlsplit(uri).path:raise ValueError('Existing runtime connection belongs to another target or role; use a separate profile')
            password=unquote(p.password or '')
        else:password=secrets.token_urlsafe(40)
        atomic_private(pending,runtime_uri(uri,role,password))
        db=Postgres(uri)
        try:
            await db.migrate();runtime=await provision_runtime(db,uri,role=role,password=password)
            probe=Postgres(runtime)
            try:
                def verify():
                    with probe.transaction(readonly=True) as c:c.execute('SELECT current_user');return c.fetchone()
                await probe.run(verify)
            except Exception:raise ValueError('Runtime role credential could not be verified; existing role may use a different password. Resume using the correct private runtime credential') from None
            finally:await probe.close()
            self.secret('BRODAR_DATABASE_URL',runtime);self.done('database','migrated-and-provisioned');pending.unlink(missing_ok=True)
        finally:await db.close()
        return {'database':'ready','runtime_role':role}
