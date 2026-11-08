"""Code-enforced command gateway. No model or approval agent in the decision path."""
import asyncio
import os
import re
import shutil
from pathlib import Path
from .redaction import scrub

# Interpreters/launchers defeat file checks, so they are not dispatched by this gateway.
DENIED={'rm','rmdir','dd','mkfs','mount','umount','su','sudo','chmod','chown','kill','killall',
        'pkill','reboot','shutdown','sh','bash','zsh','fish','eval','exec','env','xargs','find',
        'python','python3','node','perl','ruby','php','lua','curl','wget','ssh','scp','nc','ncat'}
READ_ONLY={'pwd','ls','cat','head','tail','wc','sort','uniq','diff','stat','file'}
MUTATING={'mkdir','touch','cp','mv'}
CREDENTIAL=re.compile(r'(^|[/\\.=_-])(\.?(?:env|ssh|aws|codex|claude)|secrets?|credentials?|auth|tokens?|passwords?|login|runtime\.env)([/\\.=_-]|$)',re.I)
OPTIONS={'pwd':{'--'},'ls':{'--','-l','-a','-h'},'cat':{'--','-n','-b','-s'},
 'head':{'--','-n','-c'},'tail':{'--','-n','-c'},'wc':{'--','-l','-w','-c'},
 'sort':{'--','-n','-r','-u'},'uniq':{'--','-c','-d','-u'},'diff':{'--','-u','-q','-s'},
 'stat':{'--'},'file':{'--','-b','--mime-type'},'mkdir':{'--','-p'},'touch':{'--'},'cp':{'--','-i'},'mv':{'--','-i'}}
SENSITIVE=re.compile(r'(^|[/\\])(?:private|sensitive)([/\\]|$)',re.I)

class CommandGateway:
    def __init__(self,root):
        self.root=Path(root).resolve();self.lock=asyncio.Lock()

    def classify(self,argv,*,owner=False):
        if not argv or len(argv)>60 or sum(len(x) for x in argv)>12000:raise ValueError('Invalid command size')
        name=argv[0]
        if name!=Path(name).name or name in DENIED or name not in READ_ONLY|MUTATING:
            raise PermissionError('Command denied by host policy; shell/interpreter escape is unavailable')
        approval=name in {'cp','mv'} and len(argv)>2 and (self.root/argv[-1]).exists()
        for arg in argv[1:]:
            if any(ch in arg for ch in ['\x00','\n','\r','`','$',';','|','&','>','<']):raise PermissionError('Shell syntax is denied')
            if CREDENTIAL.search(arg):raise PermissionError('Credential access is always denied, including with approval')
            if arg.startswith('-'):
                if arg not in OPTIONS[name]:raise PermissionError('Option denied; indirect file lists, launchers and executable options are unavailable')
                continue
            value=arg
            path=(self.root/value).resolve()
            if path.is_relative_to(self.root/'owner') and not owner:raise PermissionError('Owner job workspace requires a fresh owner request')
            if not path.is_relative_to(self.root):raise PermissionError('Path outside this chat workspace is denied')
            # Even workspace-local symlinks are rejected to avoid aliases to sensitive names.
            for part in [self.root/value,*list((self.root/value).parents)]:
                if part==self.root:break
                if part.is_symlink():raise PermissionError('Symlink paths are denied')
            if SENSITIVE.search(value):approval=True
        return {'kind':'workspace_command','argv':argv,'workspace':str(self.root)},approval

    async def run(self,argv,*,approvals=None,scope=None,approval_id=None,check=lambda:None):
        async with self.lock:
            action,needed=self.classify(argv)
            if needed:
                if not approvals or not approval_id:raise PermissionError('Owner DM approval required for this exact command')
                await approvals.consume_action(scope,approval_id,action)
            check();self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
            binary=shutil.which(argv[0])
            if binary is None:raise RuntimeError('Command not installed')
            # No service credentials in env; no shell interpretation, bounded I/O and lifetime.
            env={key:os.environ[key] for key in ('PATH','PREFIX','LD_LIBRARY_PATH','TMPDIR','LANG') if key in os.environ}
            env['HOME']=str(self.root)
            proc=await asyncio.create_subprocess_exec(binary,*argv[1:],cwd=self.root,env=env,
                stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
            try:
                async with asyncio.timeout(20):
                    output=bytearray()
                    while True:
                        part=await proc.stdout.read(4096)
                        if not part:break
                        output.extend(part)
                        if len(output)>64000:raise ValueError('Command output exceeds 64 KB')
                    await proc.wait()
                check()
                return {'exit_code':proc.returncode,'output':scrub(output.decode(errors='replace'))}
            finally:
                if proc.returncode is None:
                    proc.kill();await proc.wait()

    async def checkout(self,artifacts,uid,*,approvals=None,approval_id=None,check=lambda:None):
        async with self.lock:
            row,body=await artifacts.read(uid,approval_id=approval_id,approvals=approvals)
            name=row['filename']
            if (row.get('metadata') or {}).get('sensitive'):name='sensitive/'+name
            self.classify(['cat',name])
            path=self.root/name
            if path.exists():raise ValueError('Workspace file already exists; choose another artifact filename')
            check();path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            # Exclusive creation also rejects a concurrently introduced target symlink.
            def save():
                check()
                with path.open('xb') as output:output.write(body)
                path.chmod(0o600)
            await asyncio.to_thread(save)
            return {'workspace_path':name,'size_bytes':len(body)}
