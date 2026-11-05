"""Scoped files and editable PowerPoint creation without a general shell."""

import hashlib
import io
import json
import math
import zipfile
from pathlib import PurePosixPath
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
    def __init__(self,store,objects):self.store,self.objects=store,objects

    async def put(self,*,turn_id,operation_id,filename,mime,body,request_hash,metadata=None):
        if (PurePosixPath(filename).name!=filename or '\\' in filename or not filename.strip() or len(filename)>180
                or filename in {'.','..'} or any(ord(c)<32 for c in filename)):
            raise ValueError('Use a filename without directory components')
        if len(body)>20_000_000:raise ValueError('Artifact exceeds 20 MB')
        uid=str(uuid5(NAMESPACE_URL,f'{self.store.scope.audience}:{turn_id}:{operation_id}'))
        old=await self.get_record(uid)
        if old:
            if old['request_hash']!=request_hash:raise ValueError('Operation ID already belongs to another artifact')
            return self.public(old)
        audience_hash=hashlib.sha256(self.store.scope.audience.encode()).hexdigest()
        content_hash=hashlib.sha256(body).hexdigest()
        key=f'artifacts/{audience_hash}/{uid}/{content_hash}/{filename}'
        await self.objects.put(key,body,mime)
        def save():
            with self.store.db.transaction(self.store.scope.audience) as c:
                c.execute('INSERT INTO brodar_v2.artifacts '
                    '(id,audience,topic_id,turn_id,operation_id,request_hash,filename,mime_type,object_key,size_bytes,content_hash,metadata) '
                    'VALUES (%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s::jsonb) ON CONFLICT DO NOTHING',
                    (uid,self.store.scope.audience,self.store.scope.topic_id,turn_id,operation_id,request_hash,
                     filename,mime,key,len(body),content_hash,json.dumps(metadata or {},default=str)))
        await self.store.db.run(save)
        saved=await self.get_record(uid)
        if saved is None or saved['request_hash']!=request_hash:
            raise ValueError('Operation ID belongs to a different artifact')
        return self.public(saved)

    def public(self,row):
        return {'artifact_id':str(row['id']),'filename':row['filename'],'mime_type':row['mime_type'],
                'size_bytes':row['size_bytes'],'created_at':str(row['created_at'])}

    async def get_record(self,uid):
        uid=str(UUID(uid))
        def read():
            with self.store.db.transaction(self.store.scope.audience,readonly=True) as c:
                c.execute('SELECT * FROM brodar_v2.artifacts WHERE audience=%s AND id=%s',(self.store.scope.audience,uid))
                rows=records(c)
                return rows[0] if rows else None
        return await self.store.db.run(read)

    async def read(self,uid):
        row=await self.get_record(uid)
        if row is None:raise ValueError('Artifact not found in this audience')
        body=await self.objects.get(row['object_key'])
        if hashlib.sha256(body).hexdigest()!=row['content_hash']:raise RuntimeError('Artifact checksum mismatch')
        return row,body

    async def list(self,query='',limit=30):
        def read():
            with self.store.db.transaction(self.store.scope.audience,readonly=True) as c:
                c.execute('SELECT * FROM brodar_v2.artifacts WHERE audience=%s AND filename ILIKE %s '
                    'ORDER BY created_at DESC LIMIT %s',(self.store.scope.audience,'%'+query+'%',max(1,min(limit,100))))
                return [self.public(row) for row in records(c)]
        return await self.store.db.run(read)
