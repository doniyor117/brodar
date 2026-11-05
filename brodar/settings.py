"""Operator configuration. Secrets are never included in repr or prompt text."""

from dataclasses import dataclass,field
import os
from pathlib import Path

from dotenv import dotenv_values


@dataclass(frozen=True)
class Settings:
    brain:str
    model:str
    effort:str
    database_url:str=field(repr=False)
    telegram_token:str=field(repr=False)
    google_key:str=field(repr=False)
    storage_config:Path|None=None
    allowed_chats:frozenset[int]=frozenset()
    owner_ids:frozenset[int]=frozenset({1000000001,1000000002})
    runtime_dir:Path=Path.home()/'.local/share/brodar'
    instance_id:str='brodar'
    debounce_seconds:float=1.5
    ambient_interval:float=30.0

    @classmethod
    def load(cls,*,env_file:Path|None=None,brain:str|None=None):
        values={}
        for p in [Path.home()/'.config/brodar/runtime.env',env_file or Path.home()/'secrets/.env']:
            if p.exists():values.update({k:v for k,v in dotenv_values(p).items() if v is not None})
        values.update(os.environ)
        provider=brain or values.get('BRODAR_BRAIN','codex')
        if provider not in {'codex','claude'}:raise ValueError('Choose codex or claude')
        model=values.get('BRODAR_'+provider.upper()+'_MODEL',
                         'gpt-6.1-sol' if provider=='codex' else 'claude-sonnet-4-6')
        ids=lambda v:frozenset(int(x.strip()) for x in v.split(',') if x.strip())
        cfg=cls(brain=provider,model=model,effort=values.get('BRODAR_'+provider.upper()+'_EFFORT','low' if provider=='codex' else 'high'),
                database_url=values.get('BRODAR_DATABASE_URL',values.get('DATABASE_URL','')),
                telegram_token=values.get('TELEGRAM_BOT_TOKEN',values.get('BRODAR_TEST_BOT_API_KEY','')),
                google_key=values.get('GOOGLE_AI_API_KEY',values.get('GEMINI_API_KEY','')),
                storage_config=Path(values.get('BRODAR_STORAGE_CONFIG',str(Path.home()/'.config/brodar/storage-production.json'))).expanduser(),
                allowed_chats=ids(values.get('BRODAR_ALLOWED_CHATS','')),
                owner_ids=ids(values.get('BRODAR_OWNER_IDS','1000000001,1000000002')),
                runtime_dir=Path(values.get('BRODAR_RUNTIME_DIR',str(Path.home()/'.local/share/brodar'))).expanduser().resolve(),
                instance_id=values.get('BRODAR_INSTANCE_ID','brodar'),
                debounce_seconds=float(values.get('BRODAR_DEBOUNCE_SECONDS','1.5')),
                ambient_interval=float(values.get('BRODAR_AMBIENT_INTERVAL','30')))
        if not cfg.instance_id or any(x not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for x in cfg.instance_id):
            raise ValueError('Invalid deployment instance ID')
        if cfg.debounce_seconds<0 or cfg.ambient_interval<1:raise ValueError('Invalid timing settings')
        return cfg

    def require_run(self):
        missing=[]
        if not self.database_url:missing.append('BRODAR_DATABASE_URL')
        if not self.telegram_token:missing.append('TELEGRAM_BOT_TOKEN (or BRODAR_TEST_BOT_API_KEY)')
        if not self.allowed_chats:missing.append('BRODAR_ALLOWED_CHATS')
        if missing:raise ValueError('Missing: '+', '.join(missing))
