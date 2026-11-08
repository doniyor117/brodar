"""Authenticated, conversation-bound MCP transport for either brain."""

from __future__ import annotations

import asyncio
import json
import secrets
import functools
import inspect
import logging
import time

import uvicorn
from mcp.server.fastmcp import Context,FastMCP
from .artifacts import Slide


class BearerAuth:
    def __init__(self,app,token):self.app,self.token=app,token

    async def __call__(self,scope,receive,send):
        if scope['type']=='http':
            headers=dict(scope.get('headers',[]))
            if not secrets.compare_digest(headers.get(b'authorization',b''),('Bearer '+self.token).encode()):
                await send({'type':'http.response.start','status':401,'headers':[(b'content-type',b'text/plain')]})
                await send({'type':'http.response.body','body':b'Unauthorized'})
                return
        await self.app(scope,receive,send)


def result(value):return json.dumps(value,default=str,ensure_ascii=False)


def audit_result(value):
    # Vision input belongs in the tool response, not a base64 copy in Neon audits.
    from mcp.types import ImageContent,TextContent
    from mcp.server.fastmcp.utilities.types import Image
    from .redaction import scrub_object
    if isinstance(value,(ImageContent,Image)):return {'type':'image','omitted':True}
    if isinstance(value,TextContent):return {'type':'text','text':scrub_object(value.text)}
    if isinstance(value,(list,tuple)):return [audit_result(item) for item in value]
    return scrub_object(value)


class AuditedMcp(FastMCP):
    def __init__(self,broker):
        self.broker=broker
        super().__init__('hamroh',stateless_http=True,json_response=True,log_level='WARNING')
        self.audit_queue=asyncio.Queue(maxsize=100)
        self.audit_worker=None

    def start_audit(self):
        async def consume():
            while True:
                target,record=await self.audit_queue.get()
                try:await target.audit_tool(**record)
                except Exception as exc:logging.getLogger('brodar.audit').error('Tool audit deferred/lost: %s',type(exc).__name__)
                finally:self.audit_queue.task_done()
        self.audit_worker=asyncio.create_task(consume(),name='brodar-audit')

    async def close_audit(self):
        if self.audit_worker:
            try:await asyncio.wait_for(self.audit_queue.join(),5)
            except asyncio.TimeoutError:logging.getLogger('brodar.audit').warning('Unflushed audit records on shutdown: %d',self.audit_queue.qsize())
            self.audit_worker.cancel();await asyncio.gather(self.audit_worker,return_exceptions=True)
            self.audit_worker=None

    def add_tool(self,fn,*args,**kwargs):
        # Resolve annotations before assigning __signature__: inspect does not
        # eval forward references inside an already supplied Signature object.
        if getattr(self.broker,'maintenance',False) and not (fn.__name__.startswith(('memory_','history_')) or fn.__name__=='search'):
            return None
        from .prompt_files import section,load
        if fn.__module__==__name__:fn.__doc__=section('tool-descriptions.md',fn.__name__)
        signature=inspect.signature(fn,eval_str=True)
        @functools.wraps(fn)
        async def audited(*values,**named):
            start=time.monotonic()
            cap=self.broker.capability
            output=None
            error=None
            fence_token=None
            from .write_fence import WRITE_FENCE
            try:
                supplied=named.pop('turn_token',None)
                if cap is None or not isinstance(supplied,str) or not secrets.compare_digest(supplied,cap.token):
                    raise PermissionError('Tool authorization belongs to a different or completed turn')
                def check():
                    if self.broker.capability is not cap:raise PermissionError('Tool turn revoked')
                fence_token=WRITE_FENCE.set(check)
                activity=getattr(self.broker,"on_activity",None)
                if activity:await activity(cap)
                output=await fn(*values,**named)
                check()
                from .redaction import scrub
                return scrub(output) if isinstance(output,str) else output
            except BaseException as exc:
                error=type(exc).__name__+': '+str(exc)
                if isinstance(exc,Exception):
                    from .redaction import scrub
                    safe=scrub(str(exc))
                    if safe!=str(exc):raise RuntimeError(safe) from None
                raise
            finally:
                if fence_token is not None:WRITE_FENCE.reset(fence_token)
                logging.getLogger("brodar.metrics").warning("Tool duration_ms=%d name=%s",int((time.monotonic()-start)*1000),fn.__name__)
                try:
                    arguments=dict(signature.bind_partial(*values,**named).arguments)
                    from .redaction import scrub_object
                    record=dict(turn_id=cap.turn_id if cap else None,name=fn.__name__,
                        arguments=scrub_object(arguments),result=audit_result(output),error=error,duration_ms=int((time.monotonic()-start)*1000))
                    if self.audit_worker:
                        await self.audit_queue.put((getattr(self.broker,'audit_store',self.broker.store),record))  # bounded backpressure, no unowned task per call
                    else:await self.broker.store.audit_tool(**record)
                except Exception as exc:
                    logging.getLogger('brodar.audit').error('Tool audit unavailable: %s',type(exc).__name__)
        audited.__signature__=signature.replace(parameters=[*signature.parameters.values(),
            inspect.Parameter('turn_token',inspect.Parameter.KEYWORD_ONLY,annotation=str)])
        audited.__doc__=(fn.__doc__ or '')+'\n'+load('tool-authorization.md')
        return super().add_tool(audited,*args,**kwargs)


def build_tools(broker):
    mcp=AuditedMcp(broker)

    @mcp.tool()
    async def telegram_send_message(text:str,operation_id:str,reply_to_message_id:int|None=None,final:bool=True)->str:
        return result(await broker.send_message(text,call_id=operation_id,reply_to_message_id=reply_to_message_id,final=final))

    @mcp.tool()
    async def telegram_react(message_id:int,emoji:str,operation_id:str)->str:
        return result(await broker.react(message_id,emoji,operation_id))

    @mcp.tool()
    async def telegram_send_poll(question:str,options:list[str],operation_id:str,multiple:bool=False)->str:
        return result(await broker.poll(question,options,operation_id,multiple))

    @mcp.tool()
    async def telegram_edit_message(message_id:int,text:str,operation_id:str)->str:
        return result(await broker.edit_message(message_id,text,operation_id))

    @mcp.tool()
    async def telegram_moderate(request_message_id:str,operation_id:str)->str:
        return result(await broker.moderate(request_message_id,operation_id))

    @mcp.tool()
    async def reminder_create(request_message_id:str,when:str,text:str,operation_id:str)->str:
        return result(await broker.reminder_create(request_message_id,when,text,operation_id))

    @mcp.tool()
    async def reminder_list(limit:int=30)->str:
        from .jobs import Reminders
        broker.require()
        return result(await Reminders(broker.store).list(limit))

    @mcp.tool()
    async def reminder_cancel(request_message_id:str,job_id:str)->str:
        return result(await broker.reminder_cancel(request_message_id,job_id))

    @mcp.tool()
    async def task_checkpoint(notes:str)->str:
        return result(await broker.task_checkpoint(notes))

    @mcp.tool()
    async def task_status(limit:int=30)->str:
        broker.require()
        return result(await broker.store.work_list(limit))

    @mcp.tool()
    async def history_search(query:str,limit:int=10,mode:str='hybrid')->str:
        return result(await broker.search(query,limit,mode))

    @mcp.tool()
    async def history_browse(before_id:str|None=None,sender_id:int|None=None,topic_id:int|None=None,limit:int=40)->str:
        broker.require()
        return result(await broker.store.history_browse(before_id=before_id,sender_id=sender_id,topic_id=topic_id,limit=limit))

    @mcp.tool()
    async def history_get(message_ids:list[str])->str:
        broker.require()
        return result(await broker.history_get(message_ids))

    @mcp.tool()
    async def memory_list(limit:int=100,offset:int=0)->str:
        broker.require()
        return result(await broker.store.memory_list(limit,offset))

    @mcp.tool()
    async def memory_read(path:str)->str:
        return result(await broker.memory_read(path))

    @mcp.tool()
    async def memory_write(path:str,content:str,expected_revision:int,reason:str,
                           source_ids:list[str]|None=None,subject_ids:list[int]|None=None,
                           confidence:str='observed')->str:
        return result(await broker.memory_write(path,content,expected_revision,reason,source_ids,subject_ids,confidence))

    @mcp.tool()
    async def memory_delete(path:str,expected_revision:int,reason:str)->str:
        return result(await broker.memory_delete(path,expected_revision,reason))

    @mcp.tool()
    async def memory_history(path:str,limit:int=10)->str:
        return result(await broker.memory_history(path,limit))

    @mcp.tool()
    async def memory_restore(path:str,revision:int,expected_revision:int,reason:str)->str:
        return result(await broker.memory_restore(path,revision,expected_revision,reason))

    @mcp.tool()
    async def memory_forget(path:str,expected_revision:int)->str:
        return result(await broker.memory_forget(path,expected_revision))

    @mcp.tool()
    async def workspace_command(argv:list[str],approval_id:str|None=None)->str:
        cap=broker.require(visible=True)
        if not hasattr(broker,'commands'):raise RuntimeError('Workspace command gateway unavailable')
        def check():
            if broker.capability is not cap:raise PermissionError('Turn revoked')
        return result(await broker.commands.run(argv,approvals=broker.approvals,scope=broker.store.scope,approval_id=approval_id,check=check))

    @mcp.tool()
    async def request_command_approval(argv:list[str],reason:str)->str:
        cap=broker.require(visible=True)
        if not hasattr(broker,'commands') or broker.approvals is None:raise RuntimeError('Command approval unavailable')
        action,needed=broker.commands.classify(argv)
        if not needed:return result({'approval_required':False})
        return result(await broker.approvals.request_action(broker.store.scope,cap.turn_id,action,reason))

    @mcp.tool()
    async def owner_execute(request_message_id:str,argv:list[str],operation_id:str,approval_id:str|None=None,timeout_seconds:int=900)->str:
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        return result(await source.owner_jobs.submit(source,request_message_id,argv,operation_id,approval_id,timeout_seconds))

    @mcp.tool()
    async def owner_command_approval(request_message_id:str,argv:list[str],reason:str)->str:
        from .owner_tools import owner_request,classify_owner
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        cap,request,check=owner_request(source,request_message_id)
        action,needed=classify_owner(argv,(source.commands.root/'owner').resolve())
        if not needed:return result({'approval_required':False})
        check();return result(await source.approvals.request_action(source.store.scope,cap.turn_id,action,reason))

    @mcp.tool()
    async def owner_skill(request_message_id:str,action:str,name:str,operation_id:str,source_url:str='',ref:str='HEAD',subdirectory:str='.')->str:
        import sys
        if action not in {'install','update','remove'}:raise ValueError('Choose install, update or remove')
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        argv=[sys.executable,'-m','brodar.skill_install',action,name,'--ref',ref,'--subdirectory',subdirectory]
        if source_url:argv.extend(['--source',source_url])
        return result(await source.owner_jobs.submit(source,request_message_id,argv,operation_id,timeout=600))

    @mcp.tool()
    async def owner_skill_run(request_message_id:str,skill_id:str,resource:str,arguments:list[str],operation_id:str)->str:
        import shutil
        from pathlib import Path,PurePosixPath
        from .skills import LIBRARY
        from .owner_tools import owner_request,skill_script_prefix,check_skill_source
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        cap,request,check=owner_request(source,request_message_id)
        entry=await LIBRARY.resolve(skill_id);skill_id=entry['id']
        package=entry['root'];rel=PurePosixPath(resource)
        if rel.is_absolute() or '..' in rel.parts or '\\' in resource:raise ValueError('Use a relative skill resource')
        original=(package/resource).resolve()
        if resource and (not original.is_relative_to(package) or not original.is_file() or original.is_symlink()):raise ValueError('Skill script unavailable')
        prefix=skill_script_prefix(original) if resource else None
        if not resource and arguments:raise ValueError('Preparing a skill does not accept script arguments')
        destination=source.commands.root/'owner'/'skill-sources'/skill_id
        def copy():
            check();total=0
            for file in package.rglob('*'):
                if '.git' in file.relative_to(package).parts:continue
                if file.is_symlink():raise PermissionError('Skill symlinks denied')
                if file.is_file():
                    check_skill_source(file,file.relative_to(package))
                    total+=file.stat().st_size
                    if file.stat().st_size>50_000_000 or total>100_000_000:raise ValueError('Skill package too large')
            destination.mkdir(mode=0o700,parents=True,exist_ok=True)
            shutil.copytree(package,destination,dirs_exist_ok=True,ignore=shutil.ignore_patterns('.git'))
            check()
        await asyncio.to_thread(copy)
        paths={'skill_id':skill_id,'skill_workspace_path':str(Path('owner/skill-sources')/skill_id),'job_working_directory':'owner'}
        if not resource:return result(dict(paths,prepared=True,executed=False))
        script=str(Path('skill-sources')/skill_id/resource)
        check();job=await source.owner_jobs.submit(source,request_message_id,[*prefix,script,*arguments],operation_id,timeout=900)
        return result(dict(job,**paths))

    @mcp.tool()
    async def owner_job_status(request_message_id:str,job_id:str,cancel:bool=False)->str:
        from .owner_tools import owner_request
        from .store import records
        from uuid import UUID
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        _,request,check=owner_request(source,request_message_id);uid=str(UUID(job_id))
        def read():
            check()
            with source.store.db.transaction(source.store.scope.audience) as c:
                if cancel:c.execute("UPDATE brodar_v2.jobs SET status='cancelled' WHERE audience=%s AND id=%s AND kind='owner_exec' AND status IN ('pending','running')",(source.store.scope.audience,uid))
                c.execute("SELECT id,status,payload FROM brodar_v2.jobs WHERE audience=%s AND id=%s AND kind='owner_exec'",(source.store.scope.audience,uid))
                rows=records(c)
                if not rows:raise ValueError('No owner execution job in this audience')
                return rows[0]
        return result(await source.store.db.run(read))

    @mcp.tool()
    async def group_routes()->str:
        broker.require()
        return result(await broker.router.rooms())

    @mcp.tool()
    async def telegram_send_media(kind:str,media:str,operation_id:str,caption:str='',options:dict|None=None,
                                  chat_id:int|None=None,topic_id:int=0,reply_to_message_id:int|None=None,final:bool=True)->str:
        from .telegram_media import send_media
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        if chat_id is not None and (chat_id,topic_id)!=(source.store.scope.chat_id,source.store.scope.topic_id):
            return result(await source.router.send_media(source,chat_id,kind,media,operation_id,caption,options,topic_id,reply_to_message_id))
        return result(await send_media(source,kind,media,caption,operation_id,options,reply_to_message_id,final))

    @mcp.tool()
    async def telegram_send_album(items:list[dict],operation_id:str,reply_to_message_id:int|None=None,final:bool=True)->str:
        from .telegram_media import send_album
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        return result(await send_album(source,items,operation_id,reply_to_message_id,final))

    @mcp.tool()
    async def telegram_chat_action(action:str,parameters:dict,operation_id:str)->str:
        from .telegram_media import chat_action
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        return result(await chat_action(source,action,parameters,operation_id))

    @mcp.tool()
    async def telegram_media_lookup(kind:str,identifier:str,offset:int=0):
        from .telegram_media import lookup
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        if kind in {'sticker_preview','sticker_candidate'}:
            from .telegram_media import sticker_preview,sticker_candidate
            from mcp.types import TextContent
            from mcp.server.fastmcp.utilities.types import Image
            metadata,png=await (sticker_candidate(source,identifier) if kind=="sticker_candidate" else sticker_preview(source,identifier,offset))
            return [TextContent(type='text',text=result(metadata)),Image(data=png,format='png').to_image_content()]
        return result(await lookup(source,kind,identifier,offset))

    @mcp.tool()
    async def telegram_send_sticker(selection_ref:str,operation_id:str,reply_to_message_id:int|None=None,final:bool=True)->str:
        from .telegram_media import send_selected_sticker
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        return result(await send_selected_sticker(source,selection_ref,operation_id,reply_to_message_id,final))

    @mcp.tool()
    async def group_send(chat_id:int,text:str,operation_id:str,topic_id:int=0,reply_to_message_id:int|None=None)->str:
        return result(await broker.router.send(broker,chat_id,text,operation_id,topic_id,reply_to_message_id))

    @mcp.tool()
    async def group_send_document(chat_id:int,artifact_id:str,operation_id:str,caption:str='',topic_id:int=0)->str:
        return result(await broker.router.send_document(broker,chat_id,artifact_id,operation_id,caption,topic_id))

    @mcp.tool()
    async def group_history_search(chat_id:int,query:str,limit:int=10)->str:
        broker.require()
        vector=None
        if broker.embeddings:
            from .embeddings import EmbeddingError
            try:vector=await asyncio.wait_for(broker.embeddings.embed(query,query=True),2)
            except (EmbeddingError,ValueError,asyncio.TimeoutError):pass
        return result(await broker.router.search(chat_id,query,vector,limit))

    @mcp.tool()
    async def delegate_research(brief:str,operation_id:str)->str:
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        return result(await source.research.delegate(source,brief,operation_id))

    @mcp.tool()
    async def research_follow_up(task_id:str,brief:str,operation_id:str)->str:
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        return result(await source.research.follow_up(source,task_id,brief,operation_id))

    @mcp.tool()
    async def research_return(task_id:str,report:str,operation_id:str)->str:
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        cap=source.require(visible=True)
        row=await source.research.store.get(task_id)
        if source.capability is not cap:raise PermissionError('Research return turn ended')
        source.research.origin(source,row)
        return result(await source.router.return_result(source,task_id,report,operation_id))

    @mcp.tool()
    async def research_tasks()->str:
        source=getattr(broker,'current',None) or getattr(broker,'baseline',broker)
        return result(await source.research.tasks(source))

    @mcp.tool()
    async def telegram_group_info(kind:str='chat',user_id:int|None=None,offset:int=0,limit:int=50)->str:
        from .group_admin import info
        return result(await info(broker,kind,user_id,offset,limit))

    @mcp.tool()
    async def telegram_admin_action(request_message_id:str,action:str,parameters:dict,operation_id:str)->str:
        from .group_admin import perform
        return result(await perform(broker,request_message_id,action,parameters,operation_id))

    @mcp.tool()
    async def skills_list(query:str='',offset:int=0,limit:int=30)->str:
        from .skills import LIBRARY
        broker.require()
        return result(await LIBRARY.list(query,offset,limit))

    @mcp.tool()
    async def skill_read(skill_id:str,resource:str='SKILL.md',offset:int=0)->str:
        from .skills import LIBRARY
        broker.require()
        return result(await LIBRARY.read(skill_id,resource,offset))

    @mcp.tool()
    async def now()->str:
        from datetime import datetime,timezone
        broker.require()
        return datetime.now(timezone.utc).isoformat()

    if broker.artifacts is not None:
        @mcp.tool()
        async def create_presentation(title:str,slides:list[Slide],operation_id:str,filename:str='presentation.pptx')->str:
            return result(await broker.create_presentation(title,slides,filename,operation_id))

        @mcp.tool()
        async def create_file(filename:str,text:str,operation_id:str)->str:
            return result(await broker.create_file(filename,text,operation_id))

        @mcp.tool()
        async def telegram_send_document(artifact_id:str,operation_id:str,caption:str='',final:bool=True)->str:
            return result(await broker.send_document(artifact_id,caption,operation_id,final=final))

        @mcp.tool()
        async def workspace_save(filename:str,body_base64:str,operation_id:str,mime_type:str='application/octet-stream',sensitive:bool=False)->str:
            import base64,hashlib
            from .redaction import scrub
            from .write_fence import WRITE_FENCE
            cap=broker.require(visible=True)
            if len(body_base64)>1_400_000:raise ValueError('Tool payload too large; attach large files through Telegram')
            body=base64.b64decode(body_base64,validate=True)
            if mime_type.startswith('text/'):
                text=body.decode('utf-8')
                if scrub(text)!=text:raise ValueError('Credential-like content cannot be stored through this tool')
            def check():
                if broker.capability is not cap:raise PermissionError('Turn revoked')
            token=WRITE_FENCE.set(check)
            try:
                return result(await broker.artifacts.put(turn_id=cap.turn_id,operation_id=operation_id,
                    filename=filename,mime=mime_type,body=body,request_hash=hashlib.sha256(body+filename.encode()+mime_type.encode()+str(sensitive).encode()).hexdigest(),metadata={'sensitive':sensitive}))
            finally:WRITE_FENCE.reset(token)

        @mcp.tool()
        async def artifact_retain(artifact_id:str,request_message_id:str,approval_id:str|None=None)->str:
            import re
            cap=broker.require(visible=True)
            request=broker.fresh_request(request_message_id)
            if not re.search(r'\b(?:save|keep|retain|archive)\b|saqla|сохран|остав',request.text,re.I):
                raise PermissionError('Retention needs an explicit fresh keep/save request')
            def check():
                if broker.capability is not cap or broker._requests.get(request_message_id) is not request:raise PermissionError('Retention request ended or changed')
            return result(await broker.artifacts.retain(artifact_id,{'message_id':request.id,'actor':request.actor},check=check,approval_id=approval_id,approvals=broker.approvals))

        @mcp.tool()
        async def workspace_publish(path:str,operation_id:str,filename:str='',mime_type:str='',sensitive:bool=False,approval_id:str|None=None,request_message_id:str|None=None)->str:
            import mimetypes,hashlib,os
            from pathlib import Path
            from .redaction import scrub
            cap=broker.require(visible=True)
            owner=False
            owner_check=lambda:None
            if request_message_id:
                from .owner_tools import owner_request
                _,_,owner_check=owner_request(broker,request_message_id);owner=True
            action,needed=broker.commands.classify(['cat',path],owner=owner)
            if needed:
                if not broker.approvals or not approval_id:raise PermissionError('Sensitive workspace file requires exact owner DM approval')
                await broker.approvals.consume_action(broker.store.scope,approval_id,action)
            source=broker.commands.root/path
            def check():
                owner_check()
                if broker.capability is not cap:raise PermissionError('Workspace publish turn revoked')
            def read():
                check();fd=os.open(source,os.O_RDONLY|os.O_NOFOLLOW)
                with os.fdopen(fd,'rb') as file:
                    if os.fstat(file.fileno()).st_size>50_000_000:raise ValueError('File exceeds 50 MB')
                    body=file.read(50_000_001)
                    if len(body)>50_000_000:raise ValueError('File exceeds 50 MB')
                    return body
            body=await asyncio.to_thread(read);check()
            filename=filename or Path(path).name;mime_type=mime_type or mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            if mime_type.startswith('text/'):
                text=body.decode('utf-8')
                if scrub(text)!=text:raise PermissionError('Credential-like workspace contents cannot be published')
            return result(await broker.artifacts.put(turn_id=cap.turn_id,operation_id=operation_id,filename=filename,mime=mime_type,body=body,request_hash=hashlib.sha256(body+filename.encode()+mime_type.encode()).hexdigest(),metadata={'sensitive':sensitive,'source':'workspace'}))

        @mcp.tool()
        async def workspace_checkout(artifact_id:str,approval_id:str|None=None)->str:
            cap=broker.require(visible=True)
            if not hasattr(broker,'commands'):raise RuntimeError('Workspace unavailable')
            def check():
                if broker.capability is not cap:raise PermissionError('Turn revoked')
            return result(await broker.commands.checkout(broker.artifacts,artifact_id,approvals=broker.approvals,approval_id=approval_id,check=check))

        @mcp.tool()
        async def workspace_protect(artifact_id:str)->str:
            broker.require(visible=True)
            return result(await broker.artifacts.protect(artifact_id))

        @mcp.tool()
        async def request_file_approval(artifact_id:str,reason:str)->str:
            cap=broker.require(visible=True)
            if broker.approvals is None:raise RuntimeError('Owner DM approval service unavailable')
            row=await broker.artifacts.get_record(artifact_id)
            if not row:raise ValueError('Artifact not found in this audience')
            return result(await broker.approvals.request(broker.store.scope,cap.turn_id,artifact_id,row['content_hash'],reason))

        @mcp.tool()
        async def artifact_list(query:str='',limit:int=30)->str:
            broker.require()
            return result(await broker.artifacts.list(query,limit))

        @mcp.tool()
        async def artifact_read(artifact_id:str,page_start:int=0,page_count:int=5,approval_id:str|None=None):
            broker.require()
            if page_start<0 or not 1<=page_count<=20:raise ValueError('Use page_start >= 0 and page_count 1..20')
            row,body=await broker.artifacts.read(artifact_id,approval_id=approval_id,approvals=broker.approvals)
            if row['mime_type'] in {'image/png','image/jpeg','image/webp'}:
                from mcp.server.fastmcp.utilities.types import Image
                return Image(data=body,format={'image/png':'png','image/jpeg':'jpeg','image/webp':'webp'}[row['mime_type']])
            if row['mime_type']=='application/pdf':
                import io
                from pypdf import PdfReader
                reader=PdfReader(io.BytesIO(body))
                end=min(len(reader.pages),page_start+page_count)
                return result({'pages':[{'page':i+1,'text':reader.pages[i].extract_text()} for i in range(page_start,end)],
                    'total_pages':len(reader.pages),'has_more':end<len(reader.pages)})
            if row['mime_type']=='application/vnd.openxmlformats-officedocument.presentationml.presentation':
                from .artifacts import read_presentation
                deck=read_presentation(body);end=min(len(deck.slides),page_start+page_count)
                return result({'slides':[{'number':i+1,'text':'\n'.join(shape.text for shape in deck.slides[i].shapes if shape.has_text_frame),
                    'notes':deck.slides[i].notes_slide.notes_text_frame.text if deck.slides[i].has_notes_slide else ''}
                    for i in range(page_start,end)],'total_slides':len(deck.slides),'has_more':end<len(deck.slides)})
            if row['mime_type'].startswith('text/'):
                text=body.decode('utf-8',errors='replace');start=page_start*10000;end=start+page_count*10000
                return result({'text':text[start:end],'has_more':end<len(text),'total_characters':len(text)})
            return result(broker.artifacts.public(row))

    return mcp


class ScopedMcpServer:
    def __init__(self,broker):
        self.broker=broker
        self.token=secrets.token_urlsafe(40)
        self.mcp=build_tools(broker)
        self.server=None
        self.task=None
        self.port=None

    @property
    def url(self):
        if self.port is None:raise RuntimeError('MCP server is not started')
        return f'http://127.0.0.1:{self.port}/mcp'

    async def start(self):
        self.mcp.start_audit()
        app=BearerAuth(self.mcp.streamable_http_app(),self.token)
        self.server=uvicorn.Server(uvicorn.Config(app,host='127.0.0.1',port=0,
                                  access_log=False,log_level='warning',lifespan='on'))
        self.task=asyncio.create_task(self.server.serve())
        for _ in range(500):
            if self.task.done():
                await self.task
                raise RuntimeError('MCP server stopped during startup')
            if self.server.started and self.server.servers:
                self.port=self.server.servers[0].sockets[0].getsockname()[1]
                return
            await asyncio.sleep(.01)
        await self.close()
        raise RuntimeError('MCP startup timed out')

    async def close(self):
        if self.server:self.server.should_exit=True
        if self.task:
            try:await asyncio.wait_for(self.task,5)
            except asyncio.TimeoutError:
                self.task.cancel()
                await asyncio.gather(self.task,return_exceptions=True)
        self.port=None
        await self.mcp.close_audit()
