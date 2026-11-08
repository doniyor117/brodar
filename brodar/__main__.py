"""The Brodar entry point. Historical `hamroh` CLI remains available."""

import argparse
import asyncio
import json
import logging
from pathlib import Path

from .settings import Settings



def main():
    parser=argparse.ArgumentParser(prog='brodar',description='Brodar Telegram companion: selectable Claude/Codex brains and automatic learning.', formatter_class=argparse.RawDescriptionHelpFormatter, epilog='''GET STARTED
  brodar onboard --plain          Resumable setup (SSH friendly)
  brodar service start            Run in the background
  brodar tui                      Open the terminal dashboard/chat

DAILY COMMANDS
  brodar service status           Check managed worker status
  brodar service restart          Drain and restart the worker
  brodar service stop             Stop the worker and its brain subprocess
  brodar service logs             Read recent sanitized logs
  brodar run                      Foreground mode; Ctrl-C stops it
  brodar doctor --json            Check connections and readiness
  brodar configure brain          Change brain/model configuration
  brodar login codex              Authorize Brodar's Codex profile
  brodar login claude             Authorize Brodar's Claude profile
  brodar login                    Use configured brain (default: codex)
  brodar hyperagent-login         Authorize optional Hyperagent research
  brodar configure research       Enable/select the connected researcher

HELP AND REMOVAL
  brodar --help                    Show all commands and flags
  brodar service --help            Show service actions below
  sh uninstall.sh                 Remove a source install; keep data/config
  brodar service install          Write an optional boot/service launcher
  brodar service uninstall        Remove this profile's matching launcher

Service actions: start, stop, restart, status, logs, install, uninstall.
Configure sections: brain, telegram, owner, neon, persona, budgets, research.
Profile: export --output DIRECTORY; import --archive PROFILE_JSON.
All commands accept --profile PATH for a separate instance.
Full installation, updates, platform launchers and removal: README.md.
''')
    parser.add_argument('command',nargs='?',choices=['run','doctor','migrate','provision','import-history','export-history','login','hyperagent-login','hyperagent-status','backup','restore','forget-history','setup-storage','onboard','configure','setup-neon','profile','service','tui'],default=None)
    parser.add_argument('target',nargs='?')
    parser.add_argument('--profile',type=Path)
    parser.add_argument('--plain',action='store_true')
    parser.add_argument('--advanced',action='store_true')
    parser.add_argument('--resume',action='store_true')
    parser.add_argument('--non-interactive',action='store_true')
    parser.add_argument('--json',action='store_true')
    parser.add_argument('--offline',action='store_true')
    parser.add_argument('--brain',choices=['codex','claude'],help='Override the brain for this command only; persist changes with configure brain')
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
    parser.add_argument('--agent-id',help='Hyperagent researcher ID to pin after discovery')
    args=parser.parse_args()
    logging.basicConfig(level=logging.WARNING,format='%(levelname)s %(name)s: %(message)s')
    logging.getLogger('httpx').setLevel(logging.ERROR)
    import sys
    if args.command is None:
        if not sys.stdin.isatty():parser.error('Specify run for a worker, onboard for setup, or doctor --json for automation')
        from .profile import read_profile
        _,profile=read_profile(args.profile)
        args.command='tui' if profile else 'onboard'
    if args.command in {'onboard','configure','setup-neon'}:
        from .onboarding import wizard
        section='neon' if args.command=='setup-neon' else args.target if args.command=='configure' else None
        try:
            choice=asyncio.run(wizard(args.profile,plain=args.plain,advanced=args.advanced,section=section,non_interactive=args.non_interactive,admin_file=args.admin_url_file))
            if choice:
                cfg=Settings.load(profile=Path(choice['profile']))
                if choice['action']=='foreground':
                    from .runtime import run
                    cfg.require_run();asyncio.run(run(cfg))
                else:
                    from .service import service
                    print(json.dumps(service(cfg,'start'),indent=2))
        except KeyboardInterrupt:print('Setup cancelled; saved configuration kept.')
        return
    if args.command=='login' and args.target:
        if args.target not in {'codex','claude'}:parser.error('Choose login codex or login claude')
        if args.brain and args.brain!=args.target:parser.error('Login target and --brain must agree')
        args.brain=args.target
    cfg=Settings.load(env_file=args.env_file,brain=args.brain,profile=args.profile)
    if args.command=='profile':
        from .profile import export_profile,read_profile,write_profile
        if args.target=='export':
            if not args.output:parser.error('profile export needs --output DIRECTORY')
            print(json.dumps(export_profile(cfg.profile_path,args.output),indent=2))
        elif args.target=='import':
            if not args.archive:parser.error('profile import needs --archive PROFILE_JSON')
            _,value=read_profile(args.archive)
            if cfg.profile_path.exists():parser.error('Destination profile exists; choose a new --profile path to import without overwriting')
            if value.get('prompt_dir'):
                import shutil
                destination=cfg.profile_path.parent/'prompts'
                if destination.exists():parser.error('Destination prompt directory exists; choose a clean profile directory')
                shutil.copytree(value['prompt_dir'],destination,ignore=shutil.ignore_patterns('*.key','*.env'))
                value['prompt_dir']=str(destination)
            value.pop('secret_env_file',None)
            write_profile(cfg.profile_path,value);print('Profile imported; configure local credentials separately.')
        else:parser.error('Choose profile export or import')
        return
    if args.command=='service':
        from .service import service
        print(json.dumps(service(cfg,args.target or 'status'),indent=2));return
    if args.command=='tui':
        from .tui import tui
        asyncio.run(tui(cfg));return
    if args.command in {'hyperagent-login','hyperagent-status'}:
        from .hyperagent import login,HyperagentTransport,read_auth,write_auth,HyperagentError
        async def connect_researcher():
            if args.command=='hyperagent-login':await login(cfg.hyperagent_auth_file)
            transport=HyperagentTransport(cfg.hyperagent_auth_file,args.agent_id or cfg.hyperagent_agent_id)
            value=await transport.call('list_agents')
            agents=value.get('agents',[])
            if isinstance(agents,dict):agents=agents.get('items',[])
            if not isinstance(agents,list):raise HyperagentError('Unexpected Hyperagent agent listing')
            candidates=[a for a in agents if (a.get('id') or a.get('agentId'))==(args.agent_id or cfg.hyperagent_agent_id)] if (args.agent_id or cfg.hyperagent_agent_id) else [a for a in agents if a.get('name','').strip().casefold()=='hyper researcher']
            if len(candidates)==1:
                selected=candidates[0]
                record=read_auth(cfg.hyperagent_auth_file)
                record['agent_id']=selected.get('id') or selected.get('agentId')
                write_auth(cfg.hyperagent_auth_file,record)
                print(json.dumps({'connected':True,'researcher_id':record['agent_id'],'name':selected.get('name'),
                    'execution_mode':selected.get('executionMode',selected.get('execution_mode')),'tools':sorted(transport.schemas)},indent=2))
            else:
                print(json.dumps({'connected':True,'selection_required':True,'agents':[
                    {'id':a.get('id') or a.get('agentId'),'name':a.get('name'),'execution_mode':a.get('executionMode',a.get('execution_mode'))} for a in agents]},indent=2))
        try:asyncio.run(connect_researcher())
        except Exception as exc:
            print('Hyperagent connection failed: '+type(exc).__name__)
            raise SystemExit(1) from None
    elif args.command=='setup-storage':
        import os
        from dotenv import dotenv_values
        from .storage_setup import setup_storage
        values=dotenv_values(args.env_file) if args.env_file else {}
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
        print('Signing in to '+('Codex' if cfg.brain=='codex' else 'Claude Code')+' for Brodar (dedicated '+str(home)+' profile).',flush=True)
        raise SystemExit(subprocess.call(argv,env=env,cwd=str(home)))
    elif args.command=='doctor':
        from .diagnostics import diagnose
        report=asyncio.run(diagnose(cfg,network=not args.offline))
        print(json.dumps(report,indent=2) if args.json else '\n'.join(c['name']+': '+c['status']+' — '+str(c['detail'])+(' | '+c['fix'] if c['fix'] else '') for c in report['checks']))
        raise SystemExit(0 if report['ready'] else 1)
    else:
        cfg.require_run()
        from .runtime import run
        try:asyncio.run(run(cfg))
        except KeyboardInterrupt:pass


def cli():
    try:main()
    except (ValueError,RuntimeError) as exc:
        from .redaction import scrub
        raise SystemExit(scrub(str(exc))) from None
    except Exception as exc:
        raise SystemExit('Brodar action failed ('+type(exc).__name__+'). Use doctor or the private service log to diagnose.') from None


if __name__=='__main__':cli()
