"""Packaged service management without shell scripts or a second Telegram poller."""
from __future__ import annotations
import asyncio
import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from .profile import atomic_private
from .redaction import scrub


def identity(pid):
    try:
        p=Path('/proc')/str(pid)
        args=(p/'cmdline').read_bytes().split(b'\0')
        if b'brodar.service_supervisor' not in args:return None
        start=(p/'stat').read_text().rsplit(')',1)[1].split()[19]
        return start
    except (OSError,IndexError):return None


def service(cfg,action):
    from .control import socket_path
    state=cfg.runtime_dir;state.mkdir(mode=0o700,parents=True,exist_ok=True);record=state/'managed-service.json'
    try:saved=json.loads(record.read_text())
    except (FileNotFoundError,ValueError):saved={}
    running=bool(saved.get('pid') and identity(saved['pid'])==saved.get('start_time'))
    if action=='status':return {'running':running,'managed':bool(saved),'status_file':str(state/'status.json'),'foreground_control_available':socket_path(cfg).exists()}
    if action=='logs':
        path=state/'service.log';return {'log':scrub(path.read_text(errors='replace')[-16000:]) if path.exists() else ''}
    if action=='install':
        # Install an inert platform launcher. Starting it is a separate explicit command.
        import shutil
        if shutil.which('termux-wake-lock'):
            boot=Path.home()/'.termux/boot';boot.mkdir(mode=0o700,parents=True,exist_ok=True)
            launcher=boot/'brodar'
            import shlex
            command=[sys.executable,'-m','brodar','service','start','--profile',str(cfg.profile_path)]
            atomic_private(launcher,'#!/data/data/com.termux/files/usr/bin/sh\nexec '+shlex.join(command)+'\n');launcher.chmod(0o700)
            return {'installed':str(launcher),'note':'Enable Termux:Boot and background/autostart permissions in Android settings'}
        if sys.platform.startswith('linux') and shutil.which('systemctl'):
            target=Path.home()/'.config/systemd/user/brodar.service'
            # systemd quoting is not shell quoting; reject line breaks and escape literals.
            def quote(value):
                if '\n' in value or '\r' in value:raise ValueError('Invalid service path')
                return '"'+value.replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
            argv=[sys.executable,'-m','brodar','run','--profile',str(cfg.profile_path)]
            atomic_private(target,'[Unit]\nDescription=Brodar Telegram companion\n[Service]\nExecStart='+ ' '.join(map(quote,argv))+'\nRestart=on-failure\nRestartSec=5\n[Install]\nWantedBy=default.target\n')
            return {'installed':str(target),'note':'Run systemctl --user daemon-reload, then choose either systemd or brodar service start; never both'}
        return {'installed':False,'note':'Use brodar run in your platform process manager; automatic service adapter unavailable'}
    if action in {'stop','restart'}:
        if running:
            os.kill(saved['pid'],signal.SIGTERM)
            deadline=time.monotonic()+45
            while identity(saved['pid'])==saved.get('start_time') and time.monotonic()<deadline:time.sleep(.2)
            if identity(saved['pid'])==saved.get('start_time'):raise RuntimeError('Service has not drained yet; no replacement started')
        elif socket_path(cfg).exists():raise RuntimeError('A foreground/external worker exists; stop it through its actual process manager')
        if action=='stop':return {'stopped':True}
    if action in {'start','restart'}:
        if running:return {'running':True,'already_started':True}
        if socket_path(cfg).exists():raise RuntimeError('Existing worker/control socket detected; diagnose before starting another poller')
        profile_parent=cfg.profile_path.parent
        admin=cfg.backup_admin_file or profile_parent/(cfg.profile_path.stem+'.admin-uri')
        backup_args=[]
        if admin.exists() and cfg.storage_config and cfg.storage_config.exists():
            backup_args=['--backup-admin-file',str(admin),'--backup-storage-file',str(cfg.storage_config),'--backup-key-file',str(cfg.backup_key_file or profile_parent/(cfg.profile_path.stem+'.recovery-key.json')),'--backup-interval',str(cfg.backup_interval_seconds)]
        with (state/'supervisor-console.log').open('a') as out:
            proc=subprocess.Popen([sys.executable,'-m','brodar.service_supervisor',*backup_args,'--profile',str(cfg.profile_path)],stdin=subprocess.DEVNULL,stdout=out,stderr=subprocess.STDOUT,start_new_session=True,env={**os.environ,'BRODAR_RUNTIME_DIR':str(state)})
        start=None
        for _ in range(20):
            start=identity(proc.pid)
            if start:break
            time.sleep(.05)
        if not start:raise RuntimeError('Supervisor did not start; see private supervisor-console.log')
        atomic_private(record,json.dumps({'pid':proc.pid,'start_time':start,'profile':str(cfg.profile_path)}))
        return {'started':True,'pid':proc.pid,'ready':False,'note':'Use doctor/status to verify actual readiness'}
    raise ValueError('Choose install/start/stop/restart/status/logs')
