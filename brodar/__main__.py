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
    parser.add_argument('command',nargs='?',choices=['run','doctor'],default='run')
    parser.add_argument('--brain',choices=['codex','claude'])
    parser.add_argument('--env-file',type=Path)
    args=parser.parse_args()
    logging.basicConfig(level=logging.WARNING,format='%(levelname)s %(name)s: %(message)s')
    logging.getLogger('httpx').setLevel(logging.ERROR)
    cfg=Settings.load(env_file=args.env_file,brain=args.brain)
    if args.command=='doctor':
        asyncio.run(doctor(cfg))
    else:
        cfg.require_run()
        from .runtime import run
        try:asyncio.run(run(cfg))
        except KeyboardInterrupt:pass


if __name__=='__main__':main()
