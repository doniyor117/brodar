"""Primary-owner host actions. No extra model, no shared-provider native shell."""
from __future__ import annotations
import asyncio
import contextvars
import json
import os
import re
import signal
import shutil
from pathlib import Path
from uuid import NAMESPACE_URL,uuid5
from .commands import CREDENTIAL,SENSITIVE
from .redaction import scrub
from .store import records

CATASTROPHIC=re.compile(r'\b(?:mkfs(?:\.[\w]+)?|wipefs|flash_erase|reboot|shutdown|su|sudo)\b|(?:^|\s)dd\s+|rm\s+(?:-[^\s]*r[^\s]*f|-[^\s]*f[^\s]*r)\s+(?:/(?:\s|$)|/\*|~(?:\s|$)|\$HOME)|:\(\)\s*\{',re.I)


def owner_request(broker,request_id):
    cap=broker.require(visible=True);request=broker.fresh_request(request_id)
    operator=bool(getattr(broker,'operator_session',False) and cap.mode=='operator' and broker.store.scope.audience.startswith('terminal:') and request.actor is None)
    if not operator and (cap.requester_id not in broker.owner_ids or request.actor!=cap.requester_id):
        raise PermissionError('Only a fresh authenticated owner request can authorize host execution')
    def check():
        if broker.capability is not cap or broker._requests.get(request_id) is not request or (not operator and request.actor not in broker.owner_ids):
            raise PermissionError('Owner request was revoked, edited or completed')
    check();return cap,request,check


def classify_owner(argv,root):
    if not argv or len(argv)>100 or sum(len(x) for x in argv)>24000 or any('\x00' in x for x in argv):raise ValueError('Invalid command size')
    text=' '.join(argv)
    if CATASTROPHIC.search(text):raise PermissionError('Catastrophic command denied')
    if CREDENTIAL.search(text) or re.search(r'/(?:proc|dev|sys)(?:/|\b)|(?:\.config/brodar|\.local/share/brodar/(?:codex|claude)|brodar(?:-harness)?\.md)',text,re.I):
        raise PermissionError('Credential/runtime/main-prompt access is always denied')
    # Direct absolute paths outside the workspace need explicit action approval.
    risky=bool(SENSITIVE.search(text)) or Path(argv[0]).name in {'rm','chmod','chown','kill','pkill','mount','umount'}
    for value in argv[1:]:
        if value.startswith('/') and not Path(value).resolve().is_relative_to(root):risky=True
        if value=='..' or value.startswith('../'):risky=True
    return {'kind':'owner_command','argv':argv,'workspace':str(root)},risky


class OwnerJobs:
    def __init__(self,cfg,bot,lease):self.cfg,self.bot,self.lease=cfg,bot,lease;self.tasks={};self.semaphore=asyncio.Semaphore(1)
    async def submit(self,broker,request_id,argv,operation,approval_id=None,timeout=900):
        cap,request,check=owner_request(broker,request_id)
        if len(self.tasks)>=8:raise ValueError('Owner execution queue is full; wait or cancel a job')
        root=broker.commands.root/'owner';root=root.resolve()
        action,risky=classify_owner(argv,root)
        if risky:
            if not broker.approvals or not approval_id:raise PermissionError('This exact sensitive/risky command needs owner DM approval')
            await broker.approvals.consume_action(broker.store.scope,approval_id,action)
        check()
        if not operation or len(operation)>150 or not 1<=timeout<=1800:raise ValueError('Use a short operation ID and 1..1800 second timeout')
        uid=str(uuid5(NAMESPACE_URL,broker.store.scope.audience+':owner-exec:'+request.id+':'+operation))
        payload={'argv':argv,'root':str(root),'request_message_id':request.id,'requester_id':request.actor,'operation':operation,'timeout':timeout,'operator':bool(getattr(broker,'operator_session',False))}
        def create():
            check()
            with broker.store.db.transaction(broker.store.scope.audience) as c:
                c.execute('SELECT source_hash,changed_at FROM brodar_v2.messages WHERE audience=%s AND id=%s AND sender_id IS NOT DISTINCT FROM %s',(broker.store.scope.audience,request.id,request.actor))
                row=c.fetchone()
                if not row:raise PermissionError('Owner source request is unavailable')
                payload['source_hash']=row[0];payload['source_changed_at']=str(row[1])
                c.execute("INSERT INTO brodar_v2.jobs(id,audience,topic_id,kind,payload,due_at,dedup_key) VALUES (%s,%s,%s,'owner_exec',%s::jsonb,now(),%s) ON CONFLICT DO NOTHING RETURNING id",(uid,broker.store.scope.audience,broker.store.scope.topic_id,json.dumps(payload),uid))
                created=c.fetchone() is not None
                c.execute('SELECT payload,status FROM brodar_v2.jobs WHERE audience=%s AND id=%s',(broker.store.scope.audience,uid))
                prior,status=c.fetchone()
                if any(prior.get(k)!=payload[k] for k in ('argv','request_message_id','requester_id','root','timeout')):raise ValueError('Owner operation ID already has different content')
                return created,status
        created,status=await broker.store.db.run(create)
        if created:
            from .write_fence import WRITE_FENCE
            context=contextvars.copy_context();context.run(WRITE_FENCE.set,None)
            task=asyncio.create_task(self._run(broker.store,uid,payload),name='brodar-owner-job',context=context)
            self.tasks[uid]=task;task.add_done_callback(lambda _:self.tasks.pop(uid,None))
        return {'job_id':uid,'status':status,'asynchronous':True,'origin_chat_id':broker.store.scope.chat_id}

    async def _valid(self,store,payload):
        await self.lease().require()
        if not (payload.get('operator') and store.scope.audience.startswith('terminal:')) and payload['requester_id'] not in self.cfg.owner_ids:raise PermissionError('Owner authority revoked')
        def read():
            with store.db.transaction(store.scope.audience,readonly=True) as c:
                c.execute('SELECT source_hash,sender_id,changed_at FROM brodar_v2.messages WHERE audience=%s AND id=%s',(store.scope.audience,payload['request_message_id']))
                return c.fetchone()
        row=await store.db.run(read)
        if not row or row[0]!=payload['source_hash'] or row[1]!=payload['requester_id'] or str(row[2])!=payload['source_changed_at']:raise PermissionError('Owner source changed')

    async def _run(self,store,uid,payload):
        async with self.semaphore:
            await self._execute(store,uid,payload)

    async def _execute(self,store,uid,payload):
        proc=None;output=bytearray();status='failed';exit_code=None
        async def update(status,extra=None):
            def write():
                with store.db.transaction(store.scope.audience) as c:
                    c.execute('UPDATE brodar_v2.jobs SET status=%s,payload=payload || %s::jsonb,updated_at=now() WHERE audience=%s AND id=%s',(status,json.dumps(extra or {}),store.scope.audience,uid))
            await store.db.run(write)
        try:
            await self._valid(store,payload)
            root=Path(payload['root']);root.mkdir(mode=0o700,parents=True,exist_ok=True)
            env={key:os.environ[key] for key in ('PATH','PREFIX','LD_LIBRARY_PATH','TMPDIR','LANG') if key in os.environ}
            env.update(HOME=str(root),XDG_CONFIG_HOME=str(root/'.config'),GIT_TERMINAL_PROMPT='0')
            await update('running')
            binary=shutil.which(payload['argv'][0])
            if binary is None:raise ValueError('Requested executable is not installed')
            proc=await asyncio.create_subprocess_exec(binary,*payload['argv'][1:],cwd=root,env=env,start_new_session=True,stdin=asyncio.subprocess.DEVNULL,stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.STDOUT)
            async def collect():
                while data:=await proc.stdout.read(4096):
                    output.extend(data)
                    if len(output)>128000:raise ValueError('Owner job output limit reached')
            async def monitor():
                while proc.returncode is None:
                    await asyncio.sleep(3);await self._valid(store,payload)
                    def cancelled():
                        with store.db.transaction(store.scope.audience,readonly=True) as c:
                            c.execute('SELECT status FROM brodar_v2.jobs WHERE audience=%s AND id=%s',(store.scope.audience,uid));row=c.fetchone();return not row or row[0]=='cancelled'
                    if await store.db.run(cancelled):raise PermissionError('Job cancelled')
            readers=[asyncio.create_task(collect()),asyncio.create_task(monitor())]
            try:
                async with asyncio.timeout(payload['timeout']):
                    # Monitor exceptions must interrupt the process, not be silently discarded.
                    wait=asyncio.create_task(proc.wait())
                    done,_=await asyncio.wait([wait,*readers],return_when=asyncio.FIRST_COMPLETED)
                    for task in done:task.result()
                    if wait not in done:await asyncio.gather(wait,readers[0])
                    else:await readers[0]
                await self._valid(store,payload);exit_code=proc.returncode;status='done' if exit_code==0 else 'failed'
            finally:
                for task in readers:task.cancel()
                await asyncio.gather(*readers,return_exceptions=True)
        except asyncio.CancelledError:status='cancelled';raise
        except Exception as exc:output.extend(('\n'+type(exc).__name__+': '+str(exc)).encode())
        finally:
            if proc and proc.returncode is None:
                try:os.killpg(proc.pid,signal.SIGKILL)
                except ProcessLookupError:pass
                await proc.wait()
            from .skills import LIBRARY
            LIBRARY.updated=0
            skill_root=Path(payload['root'])/'skills'
            if skill_root not in LIBRARY.roots:LIBRARY.roots.append(skill_root)
            await asyncio.shield(update(status,{'output':scrub(output.decode(errors='replace'))[:128000],'exit_code':exit_code}))
            if status!='cancelled':
                try:
                    from .broker import ToolBroker,TurnCapability
                    reporter=ToolBroker(store,self.bot,delivery_guard=lambda:self.lease().require())
                    reporter.begin(TurnCapability(uid))
                    await reporter.send_message('owner task '+status+' (exit '+str(exit_code)+'). job '+uid,call_id='owner-job-completion')
                except Exception:pass  # durable result remains available via owner_job_status

    async def cancel_actor(self,store,actor):
        if actor not in self.cfg.owner_ids:return 0
        def cancel():
            with store.db.transaction(store.scope.audience) as c:
                c.execute("UPDATE brodar_v2.jobs SET status='cancelled',updated_at=now() WHERE audience=%s AND kind='owner_exec' AND status IN ('pending','running') AND (payload->>'requester_id')::bigint=%s RETURNING id",(store.scope.audience,actor))
                return [str(row[0]) for row in c.fetchall()]
        ids=await store.db.run(cancel)
        for uid in ids:
            if uid in self.tasks:self.tasks[uid].cancel()
        return len(ids)

    async def recover(self,db,scopes):
        # A process restart cannot safely replay an arbitrary external command.
        # Preserve unknown outcome and report it through actual job status.
        for scope in scopes:
            def reconcile():
                with db.transaction(scope.audience) as c:
                    c.execute("UPDATE brodar_v2.jobs SET status='failed',payload=payload || '{\"outcome_unknown\":true,\"error\":\"service_restarted_do_not_replay\"}'::jsonb,updated_at=now() WHERE audience=%s AND kind='owner_exec' AND status IN ('pending','running')",(scope.audience,))
            await db.run(reconcile)

    async def close(self):
        tasks=list(self.tasks.values())
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
