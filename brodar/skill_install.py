"""Atomic Git skill installation, also used by the owner job gateway."""
from __future__ import annotations
import argparse
import json
import re
import shutil
import subprocess
import tempfile
from pathlib import Path,PurePosixPath


def manage(root,action,name,source=None,ref='HEAD',subdirectory='.'):
    root=Path(root).resolve()
    if not re.fullmatch(r'[A-Za-z0-9_-]{1,80}',name):raise ValueError('Use a simple skill name')
    root.mkdir(mode=0o700,parents=True,exist_ok=True);target=root/name
    if target.is_symlink():raise ValueError('Symlink skill destination denied')
    if action=='remove':
        if target.is_dir():shutil.rmtree(target)
        return {'removed':name}
    if action=='update':
        receipt=json.loads((target/'.brodar-source.json').read_text())
        source=receipt['source'];subdirectory=receipt['subdirectory']
        if ref=='HEAD':ref=receipt.get('ref','HEAD')
    elif target.exists():raise ValueError('Skill already installed; use update')
    if not source or not re.fullmatch(r'https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+(?:\.git)?',source):raise ValueError('Use a public GitHub HTTPS repository URL without credentials')
    rel=PurePosixPath(subdirectory)
    if rel.is_absolute() or '..' in rel.parts or '\\' in subdirectory:raise ValueError('Invalid skill subdirectory')
    if not re.fullmatch(r'[A-Za-z0-9_./-]{1,200}',ref) or ref.startswith('-'):raise ValueError('Invalid Git ref')
    def git(*args):
        return subprocess.check_output(['git','-c','core.hooksPath=/dev/null',*args],stderr=subprocess.DEVNULL,text=True,timeout=120).strip()
    with tempfile.TemporaryDirectory(prefix='.install-',dir=root) as stage:
        repo=Path(stage)/'source';git('clone','--depth','1','--no-checkout','--filter=blob:none','--',source,str(repo))
        if ref!='HEAD':git('-C',str(repo),'fetch','--depth','1','origin',ref)
        git('-C',str(repo),'checkout','--detach','FETCH_HEAD' if ref!='HEAD' else 'HEAD')
        commit=git('-C',str(repo),'rev-parse','HEAD')
        src=repo/subdirectory
        if not src.is_dir() or not src.resolve().is_relative_to(repo):raise ValueError('Skill directory unavailable')
        if not (src/'SKILL.md').is_file():raise ValueError('Selected directory needs SKILL.md')
        total=0
        for file in src.rglob('*'):
            if '.git' in file.relative_to(src).parts:continue
            if file.is_symlink():raise ValueError('Skill symlinks are not accepted')
            if file.is_file():
                total+=file.stat().st_size
                if file.stat().st_size>50_000_000 or total>100_000_000:raise ValueError('Skill package too large')
        ready=Path(stage)/'ready';shutil.copytree(src,ready,ignore=shutil.ignore_patterns('.git'))
        receipt={'source':source,'ref':ref,'commit':commit,'subdirectory':subdirectory,'name':name}
        (ready/'.brodar-source.json').write_text(json.dumps(receipt,indent=2))
        old=root/('.old-'+name)
        if old.exists():shutil.rmtree(old)
        if target.exists():target.rename(old)
        try:ready.rename(target)
        except BaseException:
            if old.exists() and not target.exists():old.rename(target)
            raise
        if old.exists():shutil.rmtree(old)
    return receipt


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['install','update','remove']);parser.add_argument('name');parser.add_argument('--source');parser.add_argument('--ref',default='HEAD');parser.add_argument('--subdirectory',default='.')
    args=parser.parse_args();print(json.dumps(manage(Path.cwd()/'skills',args.action,args.name,args.source,args.ref,args.subdirectory)))
if __name__=='__main__':main()
