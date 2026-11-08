"""Atomic Git skill installation, also used by the owner job gateway."""
from __future__ import annotations
import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path,PurePosixPath


def skill_directory(repo,name,subdirectory):
    """Prefer the supplied path; discover a unique nested skill for repo-root URLs."""
    src=repo/subdirectory
    if subdirectory=='.' and not (src/'SKILL.md').is_file():
        candidates=[]
        for base,dirs,files in os.walk(repo,followlinks=False):
            dirs[:]=sorted(d for d in dirs if d not in {'.git','node_modules','.venv'} and not (Path(base)/d).is_symlink())
            if 'SKILL.md' in files and not (Path(base)/'SKILL.md').is_symlink():
                candidates.append(Path(base).relative_to(repo).as_posix())
                if len(candidates)>100:raise ValueError('Many skills found; specify the exact subdirectory containing SKILL.md')
        matches=[p for p in candidates if PurePosixPath(p).name in {name,name.removesuffix('-skill')}]
        if len(candidates)==1:subdirectory=candidates[0]
        elif len(matches)==1:subdirectory=matches[0]
        elif candidates:raise ValueError('Multiple skills found; choose subdirectory: '+', '.join(candidates[:20]))
        else:raise ValueError('No SKILL.md found in this repository')
        src=repo/subdirectory
    if not src.is_dir() or not src.resolve().is_relative_to(repo):raise ValueError('Skill directory unavailable')
    if any(p.is_symlink() for p in (src,*src.parents) if p.is_relative_to(repo)):
        raise ValueError('Skill symlinks are not accepted')
    if not (src/'SKILL.md').is_file():raise ValueError('Selected directory needs SKILL.md')
    return src,src.relative_to(repo).as_posix()


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
        src,subdirectory=skill_directory(repo,name,subdirectory)
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
    args=parser.parse_args()
    try:receipt=manage(Path.cwd()/'skills',args.action,args.name,args.source,args.ref,args.subdirectory)
    except (ValueError,RuntimeError,OSError,subprocess.SubprocessError) as exc:
        parser.exit(1,'Skill install failed: '+str(exc)+'\n')
    print(json.dumps(receipt))
if __name__=='__main__':main()
