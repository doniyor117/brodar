"""Versioned operator-owned instance profiles; credentials remain separate."""
from __future__ import annotations
import json
import os
import tempfile
from pathlib import Path

DEFAULT_PROFILE=Path.home()/'.config/brodar/profile.json'
ALLOWED_PREFIXES=('BRODAR_','TELEGRAM_','GOOGLE_','GEMINI_')
SECRET_KEYS={'BRODAR_DATABASE_URL','DATABASE_URL','TELEGRAM_BOT_TOKEN','BRODAR_TEST_BOT_API_KEY','GOOGLE_AI_API_KEY','GEMINI_API_KEY'}


def atomic_private(path,body):
    path=Path(path).expanduser();path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,tmp=tempfile.mkstemp(prefix='.'+path.name+'.',dir=path.parent)
    try:
        with os.fdopen(fd,'w') as output:
            os.fchmod(output.fileno(),0o600);output.write(body);output.flush();os.fsync(output.fileno())
        os.replace(tmp,path)
    finally:
        if os.path.exists(tmp):os.unlink(tmp)


def read_profile(path=None):
    path=Path(path or os.environ.get('BRODAR_PROFILE',DEFAULT_PROFILE)).expanduser().resolve()
    if not path.exists():return path,{}
    try:
        value=json.loads(path.read_text())
        if not isinstance(value,dict) or value.get('version')!=1:raise ValueError()
        if set(value)-{'version','settings','secret_env_file','prompt_dir','engine_revision','skill_sources'}:raise ValueError()
        settings=value.get('settings',{})
        if not isinstance(settings,dict) or any(not isinstance(k,str) or not k.startswith(ALLOWED_PREFIXES) or not isinstance(v,(str,int,float,bool,list)) for k,v in settings.items()):raise ValueError()
        if SECRET_KEYS.intersection(settings):raise ValueError()
        for key in ('secret_env_file','prompt_dir'):
            if value.get(key):
                p=Path(value[key]).expanduser();value[key]=str((path.parent/p).resolve() if not p.is_absolute() else p.resolve())
        return path,value
    except (ValueError,TypeError,KeyError):raise ValueError('Invalid profile: expected version 1 settings and external secret/prompt paths') from None


def write_profile(path,value):
    # Validate in a private sibling before replacing an existing valid profile.
    import tempfile
    path=Path(path).expanduser();path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
    fd,temp=tempfile.mkstemp(prefix='.validate-',dir=path.parent);os.close(fd)
    try:
        atomic_private(temp,json.dumps(value,indent=2,ensure_ascii=False)+'\n')
        read_profile(temp)
        os.replace(temp,path)
    finally:
        if os.path.exists(temp):os.unlink(temp)


def export_profile(source,destination):
    import shutil
    path,value=read_profile(source);dest=Path(destination).expanduser()
    if dest.exists() and any(dest.iterdir()):raise ValueError('Export destination must be empty')
    dest.mkdir(mode=0o700,parents=True,exist_ok=True)
    value.pop('secret_env_file',None)
    if value.get('prompt_dir'):
        prompts=Path(value['prompt_dir']);target=dest/'prompts';target.mkdir(mode=0o700)
        for item in prompts.glob('*.md'):
            if item.is_symlink():continue
            shutil.copy2(item,target/item.name)
        value['prompt_dir']='prompts'
    write_profile(dest/'profile.json',value)
    return {'exported':str(dest),'secrets_included':False}
