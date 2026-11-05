"""The Brodar entry point. Historical `hamroh` CLI remains available."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .settings import Settings


async def doctor(cfg):
    report={'brain':cfg.brain,'model':cfg.model,'database_configured':bool(cfg.database_url),
            'telegram_configured':bool(cfg.telegram_token),'google_configured':bool(cfg.google_key),
            'allowed_chat_count':len(cfg.allowed_chats)}
    if cfg.database_url:
        from .postgres import Postgres
        db=Postgres(cfg.database_url)
        try:
            def check():
                with db.transaction(readonly=True) as c:
                    c.execute('SELECT current_user,rolsuper,rolbypassrls FROM pg_roles WHERE rolname=current_user')
                    role,superuser,bypass=c.fetchone()
                    c.execute("SELECT to_regclass('brodar_v2.messages') IS NOT NULL")
                    return {'connected':True,'runtime_role':role,'superuser':superuser,
                            'bypasses_row_security':bypass,'schema_present':c.fetchone()[0]}
            report['database']=await db.run(check)
        except Exception as e:report['database']={'connected':False,'error':type(e).__name__}
        finally:await db.close()
    print(json.dumps(report,indent=2))


def main():
    parser=argparse.ArgumentParser(prog='brodar',description='Brodar Telegram companion: selectable Claude/Codex brains and automatic learning.')
    parser.add_argument('command',nargs='?',choices=['run','doctor','migrate','provision','import-history','export-history','login','backup','restore','forget-history','setup-storage'],default='run')
    parser.add_argument('--brain',choices=['codex','claude'])
    parser.add_argument('--env-file',type=Path)
    parser.add_argument('--admin-url-file',type=Path,help='Private file holding an operator database URL; never pass a URL on the command line')
    parser.add_argument('--runtime-env-file',type=Path,default=Path.home()/'.config/brodar/runtime.env')
    parser.add_argument('--role',default='brodar_runtime')
    parser.add_argument('--archive',type=Path)
    parser.add_argument('--output',type=Path)
    parser.add_argument('--source',default='brodar')
    parser.add_argument('--instance-id',default='brodar')
    parser.add_argument('--storage-config',type=Path)
    parser.add_argument('--source-storage-config',type=Path,help='Restore source, if different from destination storage')
    parser.add_argument('--recovery-key-file',type=Path,help='Private recovery-key file; never commit it')
    parser.add_argument('--backup-id')
    parser.add_argument('--audience')
    parser.add_argument('--message-id',action='append',default=[])
    parser.add_argument('--project-id')
    parser.add_argument('--branch-id')
    args=parser.parse_args()
    logging.basicConfig(level=logging.WARNING,format='%(levelname)s %(name)s: %(message)s')
    logging.getLogger('httpx').setLevel(logging.ERROR)
    cfg=Settings.load(env_file=args.env_file,brain=args.brain)
    if args.command=='setup-storage':
        import os
        from dotenv import dotenv_values
        from .storage_setup import setup_storage
        values=dotenv_values(args.env_file or Path.home()/'secrets/.env')
        if args.storage_config is None:parser.error('--storage-config is required')
        key=os.environ.get('NEON_API_KEY') or values.get('NEON_API_KEY')
        print(json.dumps(asyncio.run(setup_storage(key,args.project_id,args.branch_id,args.storage_config)),indent=2))
    elif args.command in {'migrate','provision','import-history','export-history','backup','restore','forget-history'}:
        from .operator import database_command
        print(json.dumps(asyncio.run(database_command(args)),indent=2))
    elif args.command=='login':
        from .brains.codex import child_environment
        import subprocess
        home=cfg.runtime_dir/cfg.brain
        home.mkdir(mode=0o700,parents=True,exist_ok=True)
        (home/'user').mkdir(mode=0o700,exist_ok=True)
        env=child_environment(home)
        if cfg.brain=='codex':argv=['codex','login','--device-auth']
        else:
            env.pop('CODEX_HOME',None)
            env['CLAUDE_CONFIG_DIR']=str(home)
            argv=['claude','auth','login']
        raise SystemExit(subprocess.call(argv,env=env,cwd=str(home)))
    elif args.command=='doctor':
        asyncio.run(doctor(cfg))
    else:
        cfg.require_run()
        from .runtime import run
        try:asyncio.run(run(cfg))
        except KeyboardInterrupt:pass


if __name__=='__main__':main()
