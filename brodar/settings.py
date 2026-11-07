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
    ambient_interval:float=60.0
    ambient_quiet_seconds:float=8
    ambient_max_wait:float=60
    timezone:str='UTC'
    max_concurrent_turns:int=2
    auto_compact_token_limit:int|None=None
    recall_timeout_seconds:float=2.0
    learning_enabled:bool=True
    consolidation_enabled:bool=False
    consolidation_interval:float=21600
    maintenance_timeout:float=120
    maintenance_idle_seconds:float=300
    automatic_recall:bool=False
    research_chat_id:int=-1000000000102
    research_bot_username:str='hyperresearcher_bot'

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
        cfg=cls(research_chat_id=int(values.get('BRODAR_RESEARCH_CHAT_ID','-1000000000102')),research_bot_username=values.get('BRODAR_RESEARCH_BOT_USERNAME','hyperresearcher_bot').lstrip('@'),maintenance_idle_seconds=float(values.get('BRODAR_MAINTENANCE_IDLE_SECONDS','300')),automatic_recall=values.get('BRODAR_AUTOMATIC_RECALL','false').lower()=='true',learning_enabled=values.get('BRODAR_LEARNING_ENABLED','true').lower()=='true',
                consolidation_enabled=values.get('BRODAR_CONSOLIDATION_ENABLED','false').lower()=='true',
                consolidation_interval=float(values.get('BRODAR_CONSOLIDATION_INTERVAL','21600')),
                maintenance_timeout=float(values.get('BRODAR_MAINTENANCE_TIMEOUT','120')),auto_compact_token_limit=int(values['BRODAR_CODEX_AUTO_COMPACT_TOKEN_LIMIT']) if values.get('BRODAR_CODEX_AUTO_COMPACT_TOKEN_LIMIT') else None,
                recall_timeout_seconds=float(values.get('BRODAR_RECALL_TIMEOUT_SECONDS','2')),brain=provider,model=model,effort=values.get('BRODAR_'+provider.upper()+'_EFFORT','low' if provider=='codex' else 'high'),
                database_url=values.get('BRODAR_DATABASE_URL',values.get('DATABASE_URL','')),
                telegram_token=values.get('TELEGRAM_BOT_TOKEN',values.get('BRODAR_TEST_BOT_API_KEY','')),
                google_key=values.get('GOOGLE_AI_API_KEY',values.get('GEMINI_API_KEY','')),
                storage_config=Path(values.get('BRODAR_STORAGE_CONFIG',str(Path.home()/'.config/brodar/storage-production.json'))).expanduser(),
                allowed_chats=ids(values.get('BRODAR_ALLOWED_CHATS','')),
                owner_ids=ids(values.get('BRODAR_OWNER_IDS','1000000001,1000000002')),
                runtime_dir=Path(values.get('BRODAR_RUNTIME_DIR',str(Path.home()/'.local/share/brodar'))).expanduser().resolve(),
                instance_id=values.get('BRODAR_INSTANCE_ID','brodar'),
                debounce_seconds=float(values.get('BRODAR_DEBOUNCE_SECONDS','1.5')),
                ambient_interval=float(values.get('BRODAR_AMBIENT_INTERVAL','60')),
                ambient_quiet_seconds=float(values.get('BRODAR_AMBIENT_QUIET_SECONDS','8')),
                ambient_max_wait=float(values.get('BRODAR_AMBIENT_MAX_WAIT','60')),
                timezone=values.get('BRODAR_TIMEZONE','UTC'),
                max_concurrent_turns=int(values.get('BRODAR_MAX_CONCURRENT_TURNS','2')))
        if cfg.auto_compact_token_limit is not None and cfg.auto_compact_token_limit<1000:raise ValueError('Compaction threshold must be at least 1000 tokens')
        if cfg.maintenance_idle_seconds<1:raise ValueError('Idle window must be positive')
        if cfg.consolidation_interval<60 or cfg.maintenance_timeout<1:raise ValueError('Invalid maintenance timing')
        if cfg.recall_timeout_seconds<=0:raise ValueError('Recall timeout must be positive')
        from zoneinfo import ZoneInfo
        ZoneInfo(cfg.timezone)
        if cfg.max_concurrent_turns<1:raise ValueError('BRODAR_MAX_CONCURRENT_TURNS must be positive')
        if not cfg.instance_id or any(x not in 'abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-' for x in cfg.instance_id):
            raise ValueError('Invalid deployment instance ID')
        if cfg.ambient_quiet_seconds<0 or cfg.ambient_max_wait<1:raise ValueError('Invalid ambient batch window')
        if cfg.debounce_seconds<0 or cfg.ambient_interval<1:raise ValueError('Invalid timing settings')
        return cfg

    def require_run(self):
        missing=[]
        if not self.database_url:missing.append('BRODAR_DATABASE_URL')
        if not self.telegram_token:missing.append('TELEGRAM_BOT_TOKEN (or BRODAR_TEST_BOT_API_KEY)')
        if not self.owner_ids:missing.append('BRODAR_OWNER_IDS')
        if missing:raise ValueError('Missing: '+', '.join(missing))
