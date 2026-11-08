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
    owner_ids:frozenset[int]=frozenset()
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
    research_chat_id:int=0
    research_bot_username:str=''
    research_transport:str='disabled'
    hyperagent_auth_file:Path=Path.home()/'.config/brodar/hyperagent-oauth.json'
    hyperagent_agent_id:str=''

    aliases:tuple[str,...]=("brodar","brodar simon","samy","simon","simon bro")
    followup_seconds:float=120
    temp_budget_bytes:int=500_000_000
    retained_budget_bytes:int=1_000_000_000
    backup_budget_bytes:int=2_000_000_000
    temp_idle_seconds:int=604800
    cleanup_interval_seconds:int=604800
    backup_interval_seconds:int=1209600
    backup_admin_file:Path|None=None
    backup_key_file:Path|None=None
    prompt_dir:Path|None=None
    profile_path:Path|None=None

    @classmethod
    def load(cls,*,env_file:Path|None=None,brain:str|None=None,profile:Path|None=None):
        from .profile import read_profile
        profile_path,instance=read_profile(profile)
        values={}
        legacy=not instance and profile_path==Path.home()/'.config/brodar/profile.json'
        secret_file=env_file or instance.get('secret_env_file') or (Path.home()/'.config/brodar/runtime.env' if legacy else profile_path.parent/(profile_path.stem+'.env'))
        secret_file=Path(secret_file).expanduser()
        if secret_file.exists():values.update({k:v for k,v in dotenv_values(secret_file).items() if v is not None})
        values.update({k:(','.join(map(str,v)) if isinstance(v,list) else str(v).lower() if isinstance(v,bool) else str(v)) for k,v in instance.get('settings',{}).items()})
        values.update(os.environ)
        provider=brain or values.get('BRODAR_BRAIN','codex')
        if provider not in {'codex','claude'}:raise ValueError('Choose codex or claude')
        model=values.get('BRODAR_'+provider.upper()+'_MODEL',
                         'gpt-6.1-sol' if provider=='codex' else 'claude-sonnet-4-6')
        ids=lambda v:frozenset(int(x.strip()) for x in v.split(',') if x.strip())
        cfg=cls(backup_admin_file=Path(values['BRODAR_BACKUP_ADMIN_FILE']).expanduser() if values.get('BRODAR_BACKUP_ADMIN_FILE') else None,
                backup_key_file=Path(values['BRODAR_BACKUP_KEY_FILE']).expanduser() if values.get('BRODAR_BACKUP_KEY_FILE') else None,
                profile_path=profile_path,prompt_dir=Path(instance['prompt_dir']) if instance.get('prompt_dir') else None,
                aliases=tuple(x.strip() for x in values.get('BRODAR_ALIASES','brodar,brodar simon,samy,simon,simon bro').split(',') if x.strip()),
                followup_seconds=float(values.get('BRODAR_FOLLOWUP_SECONDS','120')),
                temp_budget_bytes=int(values.get('BRODAR_TEMP_BUDGET_BYTES','500000000')),
                retained_budget_bytes=int(values.get('BRODAR_RETAINED_BUDGET_BYTES','1000000000')),
                backup_budget_bytes=int(values.get('BRODAR_BACKUP_BUDGET_BYTES','2000000000')),
                temp_idle_seconds=int(values.get('BRODAR_TEMP_IDLE_SECONDS','604800')),
                cleanup_interval_seconds=int(values.get('BRODAR_CLEANUP_INTERVAL_SECONDS','604800')),
                backup_interval_seconds=int(values.get('BRODAR_BACKUP_INTERVAL_SECONDS','1209600')),
                research_transport=values.get('BRODAR_RESEARCH_TRANSPORT','disabled'),
                hyperagent_auth_file=Path(values.get('BRODAR_HYPERAGENT_AUTH_FILE',str(Path.home()/'.config/brodar/hyperagent-oauth.json'))).expanduser(),
                hyperagent_agent_id=values.get('BRODAR_HYPERAGENT_AGENT_ID',''),
                research_chat_id=int(values.get('BRODAR_RESEARCH_CHAT_ID','0')),research_bot_username=values.get('BRODAR_RESEARCH_BOT_USERNAME','').lstrip('@'),maintenance_idle_seconds=float(values.get('BRODAR_MAINTENANCE_IDLE_SECONDS','300')),automatic_recall=values.get('BRODAR_AUTOMATIC_RECALL','false').lower()=='true',learning_enabled=values.get('BRODAR_LEARNING_ENABLED','true').lower()=='true',
                consolidation_enabled=values.get('BRODAR_CONSOLIDATION_ENABLED','false').lower()=='true',
                consolidation_interval=float(values.get('BRODAR_CONSOLIDATION_INTERVAL','21600')),
                maintenance_timeout=float(values.get('BRODAR_MAINTENANCE_TIMEOUT','120')),auto_compact_token_limit=int(values['BRODAR_CODEX_AUTO_COMPACT_TOKEN_LIMIT']) if values.get('BRODAR_CODEX_AUTO_COMPACT_TOKEN_LIMIT') else None,
                recall_timeout_seconds=float(values.get('BRODAR_RECALL_TIMEOUT_SECONDS','2')),brain=provider,model=model,effort=values.get('BRODAR_'+provider.upper()+'_EFFORT','low' if provider=='codex' else 'high'),
                database_url=values.get('BRODAR_DATABASE_URL',values.get('DATABASE_URL','')),
                telegram_token=values.get('TELEGRAM_BOT_TOKEN',values.get('BRODAR_TEST_BOT_API_KEY','')),
                google_key=values.get('GOOGLE_AI_API_KEY',values.get('GEMINI_API_KEY','')),
                storage_config=Path(values['BRODAR_STORAGE_CONFIG']).expanduser() if values.get('BRODAR_STORAGE_CONFIG') else None,
                allowed_chats=ids(values.get('BRODAR_ALLOWED_CHATS','')),
                owner_ids=ids(values.get('BRODAR_OWNER_IDS','')),
                runtime_dir=Path(values.get('BRODAR_RUNTIME_DIR',str(Path.home()/'.local/share/brodar' if profile_path==Path.home()/'.config/brodar/profile.json' else profile_path.parent/'runtime'))).expanduser().resolve(),
                instance_id=values.get('BRODAR_INSTANCE_ID','brodar'),
                debounce_seconds=float(values.get('BRODAR_DEBOUNCE_SECONDS','1.5')),
                ambient_interval=float(values.get('BRODAR_AMBIENT_INTERVAL','60')),
                ambient_quiet_seconds=float(values.get('BRODAR_AMBIENT_QUIET_SECONDS','8')),
                ambient_max_wait=float(values.get('BRODAR_AMBIENT_MAX_WAIT','60')),
                timezone=values.get('BRODAR_TIMEZONE','UTC'),
                max_concurrent_turns=int(values.get('BRODAR_MAX_CONCURRENT_TURNS','2')))
        if min(cfg.temp_budget_bytes,cfg.retained_budget_bytes,cfg.backup_budget_bytes)<50_000_000:raise ValueError('Storage budgets must be at least one 50 MB file')
        if cfg.followup_seconds<0 or min(cfg.temp_idle_seconds,cfg.cleanup_interval_seconds,cfg.backup_interval_seconds)<1:raise ValueError('Invalid lifecycle timing')
        if cfg.auto_compact_token_limit is not None and cfg.auto_compact_token_limit<1000:raise ValueError('Compaction threshold must be at least 1000 tokens')
        if any(uid<=0 or uid>=2**63 for uid in cfg.owner_ids):raise ValueError('Owner IDs must be positive Telegram numeric user IDs')
        if cfg.research_transport not in {'disabled','hyperagent_mcp'}:raise ValueError('Choose disabled or hyperagent_mcp research transport')
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
        if len(self.owner_ids)>1:raise ValueError('Configure exactly one primary owner account')
        if missing:raise ValueError('Missing: '+', '.join(missing))
