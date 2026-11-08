"""SSH-friendly wizard. UI only asks questions; SetupEngine owns configuration."""
from __future__ import annotations
import asyncio
import getpass
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path
from .setup_engine import SetupEngine
from .profile import atomic_private

class Back(Exception):pass
class Cancelled(Exception):pass

class Questions:
    def __init__(self,plain=False):self.plain=plain;self.session=None
    async def ask(self,label,default='',*,secret=False):
        suffix=(' ['+str(default)+']') if default and not secret else ''
        if self.plain:
            value=await asyncio.to_thread(getpass.getpass if secret else input,label+suffix+': ')
        else:
            from prompt_toolkit import PromptSession
            if self.session is None:self.session=PromptSession(history=None)
            value=await self.session.prompt_async(label+suffix+': ',is_password=secret)
        if value.strip().lower() in {'quit','/quit'}:raise Cancelled()
        if value.strip().lower() in {'back','/back'}:raise Back()
        return value.strip() or str(default)
    async def yes(self,label,default=False):return (await self.ask(label+' (yes/no)','yes' if default else 'no')).lower() in {'y','yes'}

async def provider_login(cfg):
    from .brains.codex import child_environment
    home=cfg.runtime_dir/cfg.brain;home.mkdir(mode=0o700,parents=True,exist_ok=True);(home/'user').mkdir(mode=0o700,exist_ok=True)
    env=child_environment(home)
    if cfg.brain=='codex':argv=['codex','login','--device-auth']
    else:
        argv=['claude','auth','login'];env.pop('CODEX_HOME',None);env['CLAUDE_CONFIG_DIR']=str(home)
    proc=await asyncio.create_subprocess_exec(*argv,env=env,cwd=home)
    return await proc.wait()==0

async def neon_wizard(engine,q,*,existing_file=None):
    if existing_file:
        return await engine.provision_database(Path(existing_file).expanduser().read_text().strip())
    backend=await q.ask('Neon setup: mcp or connection-file','mcp' if shutil.which('codex') else 'connection-file')
    if backend=='connection-file':
        path=Path(await q.ask('Private file containing your Neon ADMIN connection URI')).expanduser()
        if not await q.yes('Apply migrations and provision a restricted role on this selected database?'):return
        return await engine.provision_database(path.read_text().strip())
    if backend!='mcp':raise ValueError('Choose mcp or connection-file')
    from .operator_mcp import OperatorMcp
    from .neon_setup import NeonSetup
    # Operator MCP uses existing CLI authorization; no chatbot or inference involved.
    import tomllib
    config=Path(os.environ.get('CODEX_HOME',Path.home()/'.codex'))/'config.toml'
    servers=tomllib.loads(config.read_text()).get('mcp_servers',{}) if config.exists() else {}
    matching=[name for name,value in servers.items() if value.get('url','').rstrip('/')=='https://mcp.neon.tech/mcp']
    name=matching[0] if len(matching)==1 else 'neon'
    if not matching:
        if not await q.yes('Register Neon MCP in your operator Codex CLI?'):return
        result=await asyncio.create_subprocess_exec('codex','mcp','add',name,'--url','https://mcp.neon.tech/mcp');await result.wait()
        if result.returncode:raise RuntimeError('Could not register Neon MCP')
    print('If needed, authorize Neon in your browser. SSH users can paste the callback with their CLI OAuth flow.')
    if await q.yes('Run codex mcp login '+name+' --no-browser now?'):
        result=await asyncio.create_subprocess_exec('codex','mcp','login',name,'--no-browser');await result.wait()
        if result.returncode:raise RuntimeError('Neon authorization was not completed')
    async with OperatorMcp(name) as mcp:
        neon=NeonSetup(engine,mcp);projects=await neon.projects()
        for project in projects:print(project.get('id'),project.get('name'),project.get('region_id',''))
        project=await q.ask('Choose an explicit project ID, or new',engine.journal['resources'].get('project_id',''))
        if project=='new':
            name=await q.ask('New project name','brodar');region=await q.ask('Neon region ID (check available regions in your console)');org=await q.ask('Organization ID (blank for personal account)')
            print('Create project:',name,'region:',region,'organization:',org or 'personal account')
            if not await q.yes('Create this project with account defaults, without changing your plan?'):return
            project=await neon.create(name,region,org or None)
        elif not any(p.get('id')==project for p in projects):raise ValueError('Select a project from the authorized list')
        engine.resource('project_id',project)
        details=await neon.describe(project)
        # Display only resource identities, never full responses that may contain URIs.
        for branch in details.get('branches',[]):print('branch:',branch.get('id'),branch.get('name'))
        branch=await q.ask('Branch ID (required for storage; blank selects project default)',engine.journal['resources'].get('branch_id',''))
        database=await q.ask('Database name','neondb')
        print('Target:',project,'branch:',branch or 'default','database:',database,'runtime role: brodar_runtime')
        if not await q.yes('Migrate this database and provision the restricted runtime role?'):return
        result=await neon.provision(project,branch or None,database)
        if branch and await q.yes('Configure optional private object storage for explicit saves/backups?'):
            print(await neon.storage(branch))
        return result

async def wizard(path=None,*,plain=False,advanced=False,section=None,non_interactive=False,admin_file=None):
    from .settings import Settings
    if not sys.stdin.isatty() and not non_interactive:raise ValueError('No interactive terminal. Use --non-interactive with a prepared profile, or run over an interactive SSH terminal')
    with SetupEngine(path) as engine:
        engine.defaults()
        if non_interactive:
            if admin_file:await engine.provision_database(Path(admin_file).read_text().strip())
            cfg=Settings.load(profile=engine.path);cfg.require_run();print('Profile configuration is complete; live provider test not run.');return
        q=Questions(plain)
        print('Brodar setup — back goes to the previous step, quit saves progress and exits.')
        sections=[section] if section else ['brain','telegram','owner','neon','persona','budgets','finish']
        position=0
        try:
            while position<len(sections):
                step=sections[position];settings=engine.profile['settings']
                try:
                    if step=='brain':
                        brain=await q.ask('Brain (codex/claude)',settings.get('BRODAR_BRAIN','codex'))
                        if brain not in {'codex','claude'}:raise ValueError('Choose codex or claude')
                        engine.set('BRODAR_BRAIN',brain)
                        if brain=='codex' and await q.yes('Show models/efforts available to the dedicated Codex login?'):
                            from .models import codex_models
                            try:
                                for entry in await codex_models(Settings.load(profile=engine.path)):print(entry['id'],entry['name'],entry['efforts'])
                            except Exception:print('Model catalogue unavailable. Choose an explicit identifier and finish login; no inference was run.')
                        model=await q.ask('Model identifier' ,settings.get('BRODAR_'+brain.upper()+'_MODEL','gpt-6-luna' if brain=='codex' else 'claude-sonnet-4-6'))
                        engine.set('BRODAR_'+brain.upper()+'_MODEL',model)
                        engine.set('BRODAR_'+brain.upper()+'_EFFORT',await q.ask('Reasoning effort',settings.get('BRODAR_'+brain.upper()+'_EFFORT','low' if brain=='codex' else 'high')))
                        if brain=='codex' and advanced:
                            threshold=await q.ask('Brodar-only compaction token threshold (blank = model default)',settings.get('BRODAR_CODEX_AUTO_COMPACT_TOKEN_LIMIT',''))
                            if threshold:engine.set('BRODAR_CODEX_AUTO_COMPACT_TOKEN_LIMIT',int(threshold))
                        if await q.yes('Open dedicated runtime provider login now?'):
                            if not await provider_login(Settings.load(profile=engine.path)):raise RuntimeError('Provider login unfinished')
                        engine.done('brain','configured-unverified')
                    elif step=='telegram':
                        if engine.journal['steps'].get('telegram')=='validated' and not await q.yes('Replace existing Telegram token?'):pass
                        else:
                            token=await q.ask('BotFather bot token',secret=True)
                            info=await engine.telegram(token);print('Bot:',info['username'])
                            print('For full group context, disable BotFather privacy mode or grant appropriate bot administrator access. Moderation permissions are optional.')
                    elif step=='owner':
                        owner=await q.ask('Your ONE primary numeric Telegram user ID (trusted local operator configuration)',settings.get('BRODAR_OWNER_IDS',''))
                        if not owner.isdigit() or int(owner)<=0:raise ValueError('A positive numeric owner ID is required')
                        engine.set('BRODAR_OWNER_IDS',owner);engine.done('owner')
                        print('Only this account can DM/configure the bot. In a group, send /approve from that account.')
                    elif step=='neon':
                        cfg=Settings.load(profile=engine.path)
                        if cfg.database_url and not await q.yes('Reconfigure the existing Neon connection?'):pass
                        else:print(await neon_wizard(engine,q,existing_file=admin_file))
                    elif step=='storage':print(await neon_wizard(engine,q,existing_file=admin_file))
                    elif step=='persona':
                        engine.set('BRODAR_ALIASES',await q.ask('Name aliases (comma separated)',settings.get('BRODAR_ALIASES','brodar,samy,simon')))
                        from zoneinfo import ZoneInfo
                        timezone=await q.ask('Timezone',settings.get('BRODAR_TIMEZONE','UTC'));ZoneInfo(timezone);engine.set('BRODAR_TIMEZONE',timezone)
                        directory=Path(engine.profile.get('prompt_dir',engine.directory/'prompts'));directory.mkdir(mode=0o700,parents=True,exist_ok=True)
                        from .prompt_files import SOURCE,load
                        for name in ('brodar.md','brodar-harness.md'):
                            target=directory/name
                            if not target.exists():atomic_private(target,(SOURCE/name).read_text() if (SOURCE/name).exists() else load(name)+'\n')
                        engine.profile['prompt_dir']=str(directory);engine.done('persona');print('Private Markdown prompts:',directory)
                        if await q.yes('Edit the persona now in your external editor?'):
                            import shlex
                            editor=os.environ.get('VISUAL') or os.environ.get('EDITOR') or ('nano' if shutil.which('nano') else 'vi')
                            argv=shlex.split(editor)
                            if not argv or shutil.which(argv[0]) is None:raise ValueError('No editor installed; set EDITOR or edit the printed Markdown path later')
                            child=await asyncio.create_subprocess_exec(*argv,str(directory/'brodar.md'))
                            if await child.wait()!=0:raise RuntimeError('Editor failed; existing prompt remains on disk')
                    elif step=='budgets':
                        for key,label,default in [('BRODAR_TEMP_BUDGET_BYTES','Temporary local budget',500000000),('BRODAR_RETAINED_BUDGET_BYTES','Permanent file budget',1000000000),('BRODAR_BACKUP_BUDGET_BYTES','Encrypted backup budget',2000000000)]:
                            value=int(await q.ask(label+' (bytes)',settings.get(key,default)))
                            if value<50000000:raise ValueError('Budget must fit a 50 MB file')
                            engine.set(key,value)
                        print('Files: temporary by default, seven inactive days. Explicit saves only. Backups: every fourteen days; two managed snapshots plus pinned/legacy backups.')
                    elif step=='finish':
                        cfg=Settings.load(profile=engine.path)
                        from .diagnostics import diagnose
                        report=await diagnose(cfg)
                        for check in report['checks']:print(check['name']+': '+check['status']+' — '+str(check['detail']))
                        engine.done('readiness','ready' if report['ready'] else 'needs-attention')
                        print('Next: brodar run (foreground), or brodar service start. Onboarding did not send group messages or run paid inference.')
                        if report['ready']:
                            action=await q.ask('Start now: foreground, background, or later','later')
                            if action in {'foreground','background'}:return {'action':action,'profile':str(engine.path)}
                            if action!='later':raise ValueError('Choose foreground, background or later')
                    else:raise ValueError('Unknown configuration section')
                    position+=1
                except Back:position=max(0,position-1)
                except (ValueError,RuntimeError) as exc:
                    from .redaction import scrub
                    print(scrub(str(exc)))
                    if not await q.yes('Retry this step?',True):position+=1
        except (Cancelled,EOFError,KeyboardInterrupt):print('Setup progress saved. Resume with brodar onboard --resume.')
