"""Progressive, provider-neutral access to installed skill packages, without auth files."""
import asyncio
import hashlib
import time
from pathlib import Path,PurePosixPath
from .redaction import scrub

class SkillLibrary:
    def __init__(self,roots=None):
        home=Path.home()
        self.roots=roots or [home/'.agents/skills',home/'.codex/skills',home/'.claude/skills',
            home/'.codex/plugins/cache',home/'.claude/plugins/cache']
        self.entries={};self.updated=0

    def register_owner_roots(self,runtime_dir):
        # Canonical installs are deployment capabilities; exclude all job scratch.
        for root in sorted(Path(runtime_dir).glob('workspaces/*/owner/skills')):
            if root.is_symlink():continue
            if root not in self.roots:self.roots.append(root);self.updated=0

    async def refresh(self):
        if time.monotonic()-self.updated<300:return
        def scan():
            entries={}
            for root in self.roots:
                if not root.is_dir():continue
                for path in root.rglob('SKILL.md'):
                    if path.is_symlink() or path.stat().st_size>500_000:continue
                    resolved=path.resolve()
                    if not resolved.is_relative_to(root.resolve()):continue
                    uid=hashlib.sha256(str(resolved).encode()).hexdigest()[:20]
                    head=path.read_text(errors='replace')[:4000]
                    description=next((line.partition(':')[2].strip() for line in head.splitlines() if line.startswith('description:')),'')
                    entries[uid]={'id':uid,'name':path.parent.name,'description':scrub(description)[:600],'root':resolved.parent}
            return entries
        self.entries=await asyncio.to_thread(scan);self.updated=time.monotonic()

    async def list(self,query='',offset=0,limit=30):
        await self.refresh()
        if offset<0:raise ValueError('Invalid offset')
        rows=[{k:v for k,v in e.items() if k!='root'} for e in self.entries.values()
            if query.lower() in (e['name']+' '+e['description']).lower()]
        rows.sort(key=lambda e:(e['name'],e['id']))
        return {'skills':rows[offset:offset+max(1,min(limit,100))],'total':len(rows),'offset':offset}

    async def read(self,uid,resource='SKILL.md',offset=0):
        entry=await self.resolve(uid)
        rel=PurePosixPath(resource)
        if rel.is_absolute() or '..' in rel.parts or '\\' in resource or offset<0:raise ValueError('Invalid skill resource')
        root=entry['root'];path=(root/resource).resolve()
        if not path.is_relative_to(root) or not path.is_file() or path.stat().st_size>500_000:
            raise ValueError('Skill resource unavailable')
        if path.suffix.lower() not in {'.md','.txt','.py','.js','.mjs','.cjs','.ts','.sh','.bash','.json','.yaml','.yml','.toml','.csv'}:
            raise ValueError('Use a text skill resource')
        text=await asyncio.to_thread(path.read_text,errors='replace')
        return {'skill_id':entry['id'],'text':scrub(text[offset:offset+24000]),'has_more':offset+24000<len(text),
            'resources':sorted(str(p.relative_to(root)) for p in root.rglob('*') if p.is_file() and not p.is_symlink())[:200]}

    async def resolve(self,uid):
        await self.refresh()
        if uid in self.entries:return self.entries[uid]
        matches=[entry for entry in self.entries.values() if entry['name']==uid]
        if len(matches)==1:return matches[0]
        if matches:raise ValueError('Ambiguous skill name; use an exact ID from skills_list')
        raise ValueError('Unknown skill ID or name; use skills_list with the skill name as query')

LIBRARY=SkillLibrary()
