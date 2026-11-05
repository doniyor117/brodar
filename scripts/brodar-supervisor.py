#!/usr/bin/env python3
"""Termux service wrapper: bounded logs, restart backoff and graceful shutdown."""

import fcntl
import argparse
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading
import time


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--backup-admin-file',type=Path)
    parser.add_argument('--backup-storage-file',type=Path)
    parser.add_argument('--backup-key-file',type=Path)
    parser.add_argument('--backup-interval',type=float,default=21600)
    args,runtime_args=parser.parse_known_args()
    if args.backup_interval<300:parser.error('Backup interval must be at least 300 seconds')
    if args.backup_admin_file and (not args.backup_storage_file or not args.backup_key_file):
        parser.error('Scheduled backups require all three private file paths')
    repo=Path(__file__).resolve().parents[1]
    state=Path(os.environ.get('BRODAR_RUNTIME_DIR',Path.home()/'.local/share/brodar'))
    state.mkdir(mode=0o700,parents=True,exist_ok=True)
    lock=(state/'supervisor.lock').open('w')
    try:fcntl.flock(lock,fcntl.LOCK_EX|fcntl.LOCK_NB)
    except BlockingIOError:raise SystemExit('Brodar supervisor is already running')
    os.umask(0o077)
    log=logging.getLogger('brodar.service');log.setLevel(logging.INFO)
    handler=RotatingFileHandler(state/'service.log',maxBytes=2_000_000,backupCount=3)
    handler.setFormatter(logging.Formatter('%(asctime)s %(message)s'));log.addHandler(handler)
    stop=threading.Event()
    child=None
    def terminate(signum,frame):
        stop.set()
        if child and child.poll() is None:child.send_signal(signal.SIGINT)
    signal.signal(signal.SIGTERM,terminate);signal.signal(signal.SIGINT,terminate)
    def backups():
        while not stop.wait(args.backup_interval):
            argv=[str(repo/'.venv/bin/python'),'-m','brodar','backup',
                  '--admin-url-file',str(args.backup_admin_file),
                  '--storage-config',str(args.backup_storage_file),
                  '--recovery-key-file',str(args.backup_key_file)]
            proc=subprocess.Popen(argv,cwd=repo,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True)
            try:
                output,_=proc.communicate(timeout=900)
                if proc.returncode:log.error('Scheduled backup failed code=%s; run the operator command to diagnose',proc.returncode)
                else:log.info('Scheduled backup complete id=%s',json.loads(output)['backup_id'])
            except subprocess.TimeoutExpired:
                proc.kill();proc.communicate();log.error('Scheduled backup timed out')
            except Exception as exc:log.error('Backup status unavailable: %s',type(exc).__name__)
    if args.backup_admin_file:threading.Thread(target=backups,daemon=True).start()
    subprocess.run(['termux-wake-lock'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
    try:
        delay=2
        while not stop.is_set():
            started=time.monotonic()
            child=subprocess.Popen([str(repo/'.venv/bin/python'),'-u','-m','brodar',*runtime_args],
                cwd=repo,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,text=True,start_new_session=True)
            log.info('Started Brodar pid=%s',child.pid)
            def drain(proc):
                for line in proc.stdout:log.info('%s',line.rstrip())
            reader=threading.Thread(target=drain,args=(child,),daemon=True);reader.start()
            while child.poll() is None:
                if stop.wait(1):
                    try:child.wait(timeout=30)
                    except subprocess.TimeoutExpired:
                        os.killpg(child.pid,signal.SIGTERM)
                        try:child.wait(timeout=10)
                        except subprocess.TimeoutExpired:os.killpg(child.pid,signal.SIGKILL);child.wait()
                    break
            reader.join(timeout=3)
            log.info('Brodar exited code=%s',child.returncode)
            if time.monotonic()-started>300:delay=2
            if stop.wait(delay):break
            delay=min(delay*2,60)
    finally:
        subprocess.run(['termux-wake-unlock'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,check=False)
        lock.close()


if __name__=='__main__':main()
