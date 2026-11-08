"""Scoped files and editable PowerPoint creation without a general shell."""

import hashlib
import io
import json
import math
import zipfile
from pathlib import Path,PurePosixPath
import asyncio
import os
import tempfile
from uuid import UUID,NAMESPACE_URL,uuid5

from pydantic import BaseModel,Field,model_validator

from .store import records,digest


class Slide(BaseModel):
    title:str=Field(max_length=180)
    bullets:list[str]=Field(default_factory=list,max_length=8)
    body:str=Field(default='',max_length=2500)
    notes:str=Field(default='',max_length=10000)
    sources:list[str]=Field(default_factory=list,max_length=8)

    @model_validator(mode='after')
    def readable_density(self):
        if len(self.body)+sum(len(x) for x in self.bullets)>1800:
            raise ValueError('Split dense content across more slides; keep each slide below 1800 characters')
        return self


def read_presentation(body):
    # Reject archive bombs and XML entity declarations before parsing uploads.
    with zipfile.ZipFile(io.BytesIO(body)) as archive:
        entries=archive.infolist()
        if len(entries)>10000 or sum(x.file_size for x in entries)>100_000_000:
            raise ValueError('Presentation archive is too large to inspect')
        for entry in entries:
            if entry.filename.endswith(('.xml','.rels')):
                if entry.file_size>10_000_000:raise ValueError('Presentation XML part is too large')
                xml=archive.read(entry).upper()
                if b'<!DOCTYPE' in xml or b'<!ENTITY' in xml:
                    raise ValueError('Presentation XML entity declarations are unsupported')
    from pptx import Presentation
    return Presentation(io.BytesIO(body))


def presentation(title,slides):
    from pptx import Presentation
    from pptx.util import Inches,Pt
    from pptx.dml.color import RGBColor
    prs=Presentation()
    prs.slide_width=Inches(13.333)
    prs.slide_height=Inches(7.5)
    prs.core_properties.title=title
    prs.core_properties.author=''
    prs.core_properties.last_modified_by=''
    for index,spec in enumerate(slides):
        slide=prs.slides.add_slide(prs.slide_layouts[6])
        background=slide.background.fill;background.solid();background.fore_color.rgb=RGBColor.from_string('101C2B')
        def box(x,y,w,h,text,size,color='F3F6FA',bold=False):
            shape=slide.shapes.add_textbox(Inches(x),Inches(y),Inches(w),Inches(h))
            frame=shape.text_frame;frame.word_wrap=True
            for number,line in enumerate(text.split('\n')):
                p=frame.paragraphs[0] if number==0 else frame.add_paragraph()
                p.text=line;p.font.name='Arial';p.font.size=Pt(size);p.font.bold=bold
                p.font.color.rgb=RGBColor.from_string(color)
            return shape
        box(.65,.45,12,1.1,spec.title,32,'5AE0B1',True)
        lines=([spec.body] if spec.body else [])+['• '+text for text in spec.bullets]
        size=None
        for candidate in range(23,11,-1):
            per_line=max(1,int(11.8*72/(candidate*.53)))
            line_count=sum(max(1,math.ceil(len(part)/per_line)) for line in lines for part in line.split('\n'))+max(0,len(lines)-1)
            if line_count*candidate*1.25<=4.6*72:
                size=candidate;break
        if size is None:raise ValueError('Slide text does not fit; split it across more slides')
        box(.75,1.75,11.8,4.6,'\n\n'.join(lines),size)
        box(.75,6.85,10,.3,title[:100],10,'9CAEC0')
        box(12,6.85,.5,.3,str(index+1),10,'9CAEC0')
        if spec.sources:
            box(.75,6.45,11.7,.35,'Sources: '+' · '.join(spec.sources),9,'9CAEC0')
        if spec.notes:slide.notes_slide.notes_text_frame.text=spec.notes
    output=io.BytesIO();prs.save(output)
    return output.getvalue()


class Artifacts:
    """One scoped catalogue; temporary local bytes unless explicitly retained."""
    def __init__(self,store,objects=None,*,root=None,temp_budget=500_000_000,retained_budget=1_000_000_000,idle_seconds=604800):
        self.store,self.objects=store,objects
        self.root=Path(root or Path.home()/'.local/share/brodar/files').resolve()
        self.temp_budget,self.retained_budget,self.idle_seconds=temp_budget,retained_budget,idle_seconds

    def local_path(self,key):
        path=self.root/key
        if not path.resolve().is_relative_to(self.root):raise PermissionError('File path outside workspace')
        for item in (path,*path.parents):
            if item==self.root:break
            if item.is_symlink():raise PermissionError('Symlink file path denied')
        if self.root.is_symlink():raise PermissionError('Symlink workspace denied')
        return path

    @staticmethod
    def reserve(c,kind,size,budget):
        c.execute('UPDATE brodar_v2.storage_usage SET bytes=bytes+%s WHERE kind=%s AND bytes+%s<=%s RETURNING bytes',(size,kind,size,budget))
        if c.fetchone() is None:raise ValueError(kind+' storage budget reached; ask the owner to manage usage')

    async def put(self,*,turn_id,operation_id,filename,mime,body,request_hash,metadata=None):
        if (PurePosixPath(filename).name!=filename or '\\' in filename or not filename.strip() or len(filename)>180
                or filename in {'.','..'} or any(ord(c)<32 for c in filename)):
            raise ValueError('Use a filename without directory components')
        if len(body)>50_000_000:raise ValueError('Artifact exceeds 50 MB')
        if not operation_id or len(operation_id)>200:raise ValueError('Use an operation ID of 1..200 characters')
        uid=str(uuid5(NAMESPACE_URL,f'{self.store.scope.audience}:{turn_id}:{operation_id}'))
        audience_hash=hashlib.sha256(self.store.scope.audience.encode()).hexdigest()
        content_hash=hashlib.sha256(body).hexdigest()
        key=f'{audience_hash}/{uid}/{content_hash}'
        def reserve():
            with self.store.db.transaction(self.store.scope.audience) as c:
                c.execute('SELECT pg_advisory_xact_lock(hashtextextended(%s,0))',(uid,))
                c.execute('SELECT * FROM brodar_v2.artifacts WHERE audience=%s AND id=%s',(self.store.scope.audience,uid))
                rows=records(c)
                if rows:
                    if rows[0]['request_hash']!=request_hash:raise ValueError('Operation ID already belongs to another artifact')
                    return rows[0]
                self.reserve(c,'temporary',len(body),self.temp_budget)
                c.execute("INSERT INTO brodar_v2.artifacts (id,audience,topic_id,turn_id,operation_id,request_hash,filename,mime_type,local_key,size_bytes,content_hash,metadata,storage_kind,state,expires_at) "
                    "VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb,'temporary','pending',now()+%s*interval '1 second') RETURNING *",
                    (uid,self.store.scope.audience,self.store.scope.topic_id,turn_id,operation_id,request_hash,
                     filename,mime,key,len(body),content_hash,json.dumps(metadata or {},default=str),self.idle_seconds))
                return records(c)[0]
        row=await self.store.db.run(reserve)
        if row['state']=='expired':raise ValueError('Temporary file expired; import it again using a new operation ID')
        if row['state']!='pending':return self.public(row)
        path=self.local_path(key)
        def write():
            path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
            fd,temp=tempfile.mkstemp(dir=path.parent)
            try:
                with os.fdopen(fd,'wb') as output:
                    os.fchmod(output.fileno(),0o600);output.write(body);output.flush();os.fsync(output.fileno())
                os.replace(temp,path)
            finally:
                if os.path.exists(temp):os.unlink(temp)
        await asyncio.to_thread(write)
        def finish():
            with self.store.db.transaction(self.store.scope.audience) as c:
                c.execute("UPDATE brodar_v2.artifacts SET state='active' WHERE audience=%s AND id=%s AND state='pending' RETURNING *",(self.store.scope.audience,uid))
                rows=records(c)
                return rows[0] if rows else None
        saved=await self.store.db.run(finish) or await self.get_record(uid)
        return self.public(saved)

    def public(self,row):
        return {'artifact_id':str(row['id']),'filename':row['filename'],'mime_type':row['mime_type'],
                'size_bytes':row['size_bytes'],'created_at':str(row['created_at']),
                'storage_kind':row.get('storage_kind','retained'),'state':row.get('state','active'),
                'expires_at':str(row.get('expires_at')) if row.get('expires_at') else None}

    async def get_record(self,uid):
        uid=str(UUID(uid))
        def read():
            with self.store.db.transaction(self.store.scope.audience,readonly=True) as c:
                c.execute('SELECT * FROM brodar_v2.artifacts WHERE audience=%s AND id=%s',(self.store.scope.audience,uid))
                rows=records(c);return rows[0] if rows else None
        return await self.store.db.run(read)

    async def read(self,uid,*,approval_id=None,approvals=None):
        row=await self.get_record(uid)
        if row is None:raise ValueError('Artifact not found in this audience')
        if row.get('state') in {'expired','pending'}:raise ValueError('Temporary file unavailable or expired; re-import from Telegram')
        if (row.get('metadata') or {}).get('sensitive'):
            if not approval_id or approvals is None:raise PermissionError('Sensitive file requires owner DM approval')
            await approvals.consume(self.store.scope,approval_id,uid,row['content_hash'])
        if row.get('storage_kind','retained')=='retained':
            if self.objects is None:raise ValueError('Retained storage is not connected')
            body=await self.objects.get(row['object_key'])
        else:
            try:body=await asyncio.to_thread(self.local_path(row['local_key']).read_bytes)
            except FileNotFoundError:raise ValueError('Local temporary file unavailable; re-import from Telegram') from None
            def touch():
                with self.store.db.transaction(self.store.scope.audience) as c:
                    c.execute("UPDATE brodar_v2.artifacts SET last_used_at=now(),expires_at=now()+%s*interval '1 second' WHERE audience=%s AND id=%s AND state<>'expired'",(self.idle_seconds,self.store.scope.audience,str(uid)))
            await self.store.db.run(touch)
        if hashlib.sha256(body).hexdigest()!=row['content_hash']:raise RuntimeError('Artifact checksum mismatch')
        return row,body

    async def retain(self,uid,request,*,check=lambda:None,approval_id=None,approvals=None):
        if self.objects is None:raise RuntimeError('Retained storage is not configured; temporary files still work')
        row,body=await self.read(uid,approval_id=approval_id,approvals=approvals)
        if row.get('storage_kind')=='retained':return self.public(row)
        key='artifacts/'+hashlib.sha256(self.store.scope.audience.encode()).hexdigest()+'/'+str(uid)+'/'+row['content_hash']+'/'+row['filename']
        def reserve():
            check()
            with self.store.db.transaction(self.store.scope.audience) as c:
                c.execute('SELECT state,storage_kind FROM brodar_v2.artifacts WHERE audience=%s AND id=%s FOR UPDATE',(self.store.scope.audience,str(uid)))
                state,kind=c.fetchone()
                if kind=='retained':return False
                if state=='active':
                    self.reserve(c,'retained',row['size_bytes'],self.retained_budget)
                    c.execute("UPDATE brodar_v2.artifacts SET state='promoting',retention_request=%s::jsonb WHERE audience=%s AND id=%s",(json.dumps(request),self.store.scope.audience,str(uid)))
                elif state!='promoting':raise ValueError('File cannot be retained in its current state')
                return True
        if await self.store.db.run(reserve):
            check();await self.objects.put(key,body,row['mime_type'])
            if hashlib.sha256(await self.objects.get(key)).hexdigest()!=row['content_hash']:raise ValueError('Retained upload validation failed')
            def finish():
                check()
                with self.store.db.transaction(self.store.scope.audience) as c:
                    c.execute("UPDATE brodar_v2.artifacts SET storage_kind='retained',state='active',object_key=%s,retained_at=now(),expires_at=NULL WHERE audience=%s AND id=%s AND storage_kind='temporary' RETURNING id",(key,self.store.scope.audience,str(uid)))
                    if c.fetchone():c.execute("UPDATE brodar_v2.storage_usage SET bytes=greatest(0,bytes-%s) WHERE kind='temporary'",(row['size_bytes'],))
            await self.store.db.run(finish)
            # Keep the local bytes as a cache until the normal sweep, not a second canonical copy.
        return self.public(await self.get_record(uid))

    async def protect(self,uid):
        uid=str(UUID(uid))
        def update():
            with self.store.db.transaction(self.store.scope.audience) as c:
                c.execute("UPDATE brodar_v2.artifacts SET metadata=metadata || '{\"sensitive\":true}'::jsonb WHERE audience=%s AND id=%s RETURNING id",(self.store.scope.audience,uid))
                if not c.fetchone():raise ValueError('Artifact not found')
                return {'protected':True,'artifact_id':uid}
        return await self.store.db.run(update)

    async def cleanup(self,limit=100):
        # Claim expiry and decrement quota atomically; active work pins by turn_id.
        def expire():
            with self.store.db.transaction(self.store.scope.audience) as c:
                c.execute("SELECT a.id,a.local_key,a.size_bytes FROM brodar_v2.artifacts a WHERE a.audience=%s AND a.storage_kind='temporary' AND a.state IN ('active','pending') AND a.expires_at<now() AND NOT EXISTS (SELECT 1 FROM brodar_v2.jobs j WHERE j.audience=a.audience AND j.id=a.turn_id AND j.status IN ('pending','running')) ORDER BY a.expires_at FOR UPDATE OF a SKIP LOCKED LIMIT %s",(self.store.scope.audience,limit))
                rows=records(c)
                for row in rows:
                    c.execute("UPDATE brodar_v2.artifacts SET state='expired' WHERE audience=%s AND id=%s",(self.store.scope.audience,row['id']))
                if rows:c.execute("UPDATE brodar_v2.storage_usage SET bytes=greatest(0,bytes-%s) WHERE kind='temporary'",(sum(r['size_bytes'] for r in rows),))
                # Return earlier expired files too: an interrupted unlink is retried next sweep.
                c.execute("SELECT local_key FROM brodar_v2.artifacts WHERE audience=%s AND local_key IS NOT NULL AND NOT coalesce((metadata->>'local_evicted')::boolean,false) AND (state='expired' OR (storage_kind='retained' AND last_used_at<now()-interval '7 days')) LIMIT %s",(self.store.scope.audience,limit))
                return records(c)
        rows=await self.store.db.run(expire)
        for row in rows:
            path=self.local_path(row['local_key'])
            await asyncio.to_thread(path.unlink,missing_ok=True)
        if rows:
            def marked():
                with self.store.db.transaction(self.store.scope.audience) as c:
                    c.execute("UPDATE brodar_v2.artifacts SET metadata=metadata || '{\"local_evicted\":true}'::jsonb WHERE audience=%s AND local_key=ANY(%s::text[]) AND (state='expired' OR storage_kind='retained')",(self.store.scope.audience,[row['local_key'] for row in rows]))
            await self.store.db.run(marked)
        return {'cleaned':len(rows)}

    async def list(self,query='',limit=30):
        def read():
            with self.store.db.transaction(self.store.scope.audience,readonly=True) as c:
                c.execute("SELECT * FROM brodar_v2.artifacts WHERE audience=%s AND state<>'expired' AND NOT coalesce((metadata->>'sensitive')::boolean,false) AND filename ILIKE %s ORDER BY created_at DESC LIMIT %s",(self.store.scope.audience,'%'+query+'%',max(1,min(limit,100))))
                return [self.public(row) for row in records(c)]
        return await self.store.db.run(read)
