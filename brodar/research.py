"""Durable research jobs. Network polling is ordinary background host code."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from uuid import UUID, NAMESPACE_URL, uuid5

from .hyperagent import AuthenticationRequired, HyperagentError, RemoteRejected
from .prompt_files import render
from .redaction import scrub, scrub_object
from .store import records, digest
from .write_fence import WRITE_FENCE

log = logging.getLogger('brodar.research')
TERMINAL = {'complete', 'partial', 'blocked', 'failed', 'uncertain'}


def caused_by(exc,kind):
    return isinstance(exc,kind) or (isinstance(exc,BaseExceptionGroup) and any(caused_by(child,kind) for child in exc.exceptions))


def remote_thread_id(value):
    thread = value.get('thread') or value
    uid = thread.get('threadId') or thread.get('thread_id') or thread.get('id')
    if not isinstance(uid, str) or not uid or len(uid) > 250:
        raise HyperagentError('Hyperagent returned no usable thread ID')
    return uid


def parse_status(value, expected_thread, expected_agent):
    """Accept an explicit idle state and actual assistant result, not progress."""
    thread = value.get('thread') or value
    uid = thread.get('threadId') or thread.get('thread_id') or thread.get('id')
    if uid and uid != expected_thread:
        raise HyperagentError('Hyperagent returned the wrong research thread')
    agent = thread.get('agentId') or thread.get('agent_id') or thread.get('namedAgentId')
    if agent and agent != expected_agent:
        raise HyperagentError('Hyperagent returned the wrong research agent')
    state = str(thread.get('status') or value.get('status') or '').lower()
    if value.get('awaitingApproval') or thread.get('awaitingApproval'):
        return {'status':'blocked','report':'Research is waiting for approval in Hyperagent. Review the pending action there; nothing was automatically approved.','result_id':'awaiting_approval'}
    if state in {'failed', 'error', 'cancelled', 'canceled'}:
        return {'status': 'failed', 'report': 'Hyperagent research run failed. Check the thread in Hyperagent.', 'result_id': state}
    if state in {'blocked', 'waiting_for_approval', 'requires_approval'}:
        return {'status': 'blocked', 'report': 'Research is waiting for approval in Hyperagent. No action was automatically approved.', 'result_id': 'awaiting_approval' if state!='blocked' else state}
    running = thread.get('isRunning', thread.get('is_running', thread.get('running', value.get('isRunning', value.get('running')))))
    if running is True or state in {'running', 'queued', 'pending', 'in_progress'}:
        return None
    if running is not False and state not in {'completed', 'complete', 'done', 'idle'}:
        raise HyperagentError('Hyperagent did not report a recognized run state')
    messages = thread.get('messages', value.get('messages', []))
    assistants = [m for m in messages if isinstance(m, dict) and m.get('role') == 'assistant']
    if not assistants:
        return {'status': 'failed', 'report': 'Hyperagent finished without an assistant research report.', 'result_id': 'empty'}
    last = assistants[-1]
    content = last.get('content', last.get('text', ''))
    if isinstance(content, list):
        content = '\n'.join(p.get('text', '') for p in content if isinstance(p, dict) and p.get('type') == 'text')
    if not isinstance(content, str) or not content.strip():
        return {'status': 'failed', 'report': 'Hyperagent finished without a readable research report.', 'result_id': 'empty'}
    # Bound remote content before persisting it or presenting it to the model.
    truncated=len(content)>100_000
    content = scrub(content[:100_000])
    if truncated:content+='\n[Report exceeded the 100,000-character transport limit; inspect the Hyperagent thread for the remainder.]'
    result_id = str(last.get('id') or last.get('messageId') or digest(content))
    declared=content.lstrip(' \n\r\t#*_`>-').upper()
    marker=re.match(r'(?:STATUS\s*:\s*)?(COMPLETE|PARTIAL|BLOCKED)\b',declared)
    declared_status=marker.group(1).lower() if marker else 'complete'
    status='partial' if truncated and declared_status!='blocked' else declared_status
    return {'status': status, 'report': content, 'result_id': result_id}


class ResearchStore:
    def __init__(self, db, instance, lease):
        self.db, self.instance, self.lease = db, instance, lease
        self.audience = 'coord:' + instance

    def fence(self, c):
        lease = self.lease()
        c.execute('SELECT 1 FROM brodar_v2.poll_state WHERE bot_id=%s AND lease_owner=%s AND lease_until>now() FOR UPDATE',
                  (lease.bot_id, lease.owner))
        if c.fetchone() is None:
            raise PermissionError('Research consumer lease expired')

    async def enqueue(self, *, task_id, scope, requester, request_id, brief, agent_id, message, operation):
        def op():
            with self.db.transaction(self.audience) as c:
                self.fence(c)
                c.execute('SELECT * FROM brodar_v2.research_tasks WHERE audience=%s AND id=%s', (self.audience, task_id))
                old = records(c)
                if old:
                    row = old[0]
                    if (row['brief'], row['origin_chat_id'], row['origin_topic_id']) != (brief, scope.chat_id, scope.topic_id):
                        raise ValueError('Research operation ID already has different content')
                    return row
                c.execute("SELECT count(*) FILTER (WHERE status IN ('queued','submitting','running','waiting')),count(*) "
                          "FROM brodar_v2.research_tasks WHERE audience=%s AND transport='hyperagent_mcp' AND created_at>now()-interval '1 day'", (self.audience,))
                active, daily = c.fetchone()
                if active >= 4 or daily >= 30:
                    raise ValueError('Research queue is full or its daily task budget is reached')
                c.execute("INSERT INTO brodar_v2.research_tasks(audience,id,origin_chat_id,origin_topic_id,requester_id,request_id,brief,"
                          "status,transport,remote_agent_id,outbound_message,operation_id,deadline) "
                          "VALUES (%s,%s,%s,%s,%s,%s,%s,'queued','hyperagent_mcp',%s,%s,%s,now()+interval '1 hour') RETURNING *",
                          (self.audience, task_id, scope.chat_id, scope.topic_id, requester, request_id, brief, agent_id, message, operation))
                return records(c)[0]
        return await self.db.run(op)

    async def get(self, task_id):
        def op():
            with self.db.transaction(self.audience, readonly=True) as c:
                c.execute('SELECT * FROM brodar_v2.research_tasks WHERE audience=%s AND id=%s', (self.audience, str(UUID(task_id))))
                rows = records(c)
                if not rows: raise ValueError('Research task does not exist')
                return rows[0]
        return await self.db.run(op)

    async def list(self, scope):
        def op():
            with self.db.transaction(self.audience, readonly=True) as c:
                c.execute('SELECT id,status,brief,created_at,deadline,interactions,error_code,remote_thread_id FROM brodar_v2.research_tasks '
                          'WHERE audience=%s AND origin_chat_id=%s AND origin_topic_id=%s ORDER BY created_at DESC LIMIT 30',
                          (self.audience, scope.chat_id, scope.topic_id))
                return records(c)
        return await self.db.run(op)

    async def follow_up(self, task_id, message, operation):
        def op():
            with self.db.transaction(self.audience) as c:
                self.fence(c)
                c.execute('SELECT * FROM brodar_v2.research_tasks WHERE audience=%s AND id=%s FOR UPDATE', (self.audience, task_id))
                row = records(c)[0]
                previous=(row.get('operation_log') or {}).get(operation)
                if previous:
                    if previous['hash'] != digest(message): raise ValueError('Follow-up operation ID already has different content')
                    return row
                c.execute("UPDATE brodar_v2.research_tasks SET status='queued',outbound_message=%s,operation_id=%s,"
                          "operation_log=operation_log||%s::jsonb,generation=generation+1,result=NULL,attempts=0,next_poll=now(),lease_until=NULL,error_code=NULL "
                          "WHERE audience=%s AND id=%s AND status IN ('complete','partial','blocked') AND interactions<10 "
                          "AND deadline>now() AND notified_generation=generation RETURNING *", (message, operation,
                          json.dumps({operation:{'hash':digest(message),'generation':row['generation']+1}}),self.audience, task_id))
                rows = records(c)
                if not rows: raise PermissionError('Research task cannot continue: pending, expired, uncertain or interaction limit reached')
                return rows[0]
        return await self.db.run(op)

    async def claim(self, limit=2):
        def op():
            with self.db.transaction(self.audience) as c:
                c.execute("SELECT 1 FROM brodar_v2.research_tasks WHERE audience=%s AND transport='hyperagent_mcp' AND "
                          "((status IN ('queued','running','waiting') AND (next_poll<=now() OR deadline<=now())) OR "
                          "(status='submitting' AND (lease_until<now() OR consumer_owner IS DISTINCT FROM %s::uuid))) LIMIT 1",
                          (self.audience,self.lease().owner))
                if c.fetchone() is None:return []
                self.fence(c)
                # A process may have died after the remote server accepted a job.
                # Never repeat that submission automatically without a thread ID.
                c.execute("UPDATE brodar_v2.research_tasks SET status='uncertain',error_code='submission_outcome_unknown',lease_until=NULL,result=NULL,result_version=result_version+1 "
                          "WHERE audience=%s AND transport='hyperagent_mcp' AND status='submitting' "
                          "AND (lease_until<now() OR consumer_owner IS DISTINCT FROM %s::uuid)", (self.audience, self.lease().owner))
                c.execute("UPDATE brodar_v2.research_tasks SET status='failed',error_code='research_deadline_expired',lease_until=NULL,result=NULL,result_version=result_version+1 "
                          "WHERE audience=%s AND transport='hyperagent_mcp' AND status IN ('queued','running','waiting') AND deadline<=now()", (self.audience,))
                c.execute("WITH due AS MATERIALIZED (SELECT id FROM brodar_v2.research_tasks WHERE audience=%s AND transport='hyperagent_mcp' "
                          "AND status IN ('queued','running','waiting') AND next_poll<=now() AND deadline>now() "
                          "AND (lease_until IS NULL OR lease_until<now() OR consumer_owner IS DISTINCT FROM %s::uuid) "
                          "ORDER BY next_poll LIMIT %s FOR UPDATE SKIP LOCKED) "
                          "UPDATE brodar_v2.research_tasks t SET consumer_owner=%s,lease_until=now()+interval '2 minutes' "
                          "FROM due WHERE t.id=due.id RETURNING t.*", (self.audience, self.lease().owner, limit, self.lease().owner))
                return records(c)
        return await self.db.run(op)

    async def update(self, row, **changes):
        delay=changes.pop('delay',15)
        allowed = {'status','remote_thread_id','interactions','result','last_result_id','error_code','attempts'}
        if set(changes) - allowed: raise ValueError('Invalid research update')
        def op():
            with self.db.transaction(self.audience) as c:
                self.fence(c)
                fields = []; args = []
                for key, value in changes.items():
                    fields.append(key + ('=%s::jsonb' if key == 'result' else '=%s'))
                    args.append(json.dumps(value) if key == 'result' else value)
                if 'result' in changes:fields.append('result_version=result_version+1')
                fields += ["next_poll=now()+(%s*interval '1 second')", 'lease_until=NULL']
                args += [delay, self.audience, str(row['id']), self.lease().owner, row['generation']]
                c.execute('UPDATE brodar_v2.research_tasks SET ' + ','.join(fields) +
                          ' WHERE audience=%s AND id=%s AND consumer_owner=%s AND generation=%s RETURNING id', tuple(args))
                if c.fetchone() is None: raise PermissionError('Research job ownership changed')
        await self.db.run(op)

    async def submitting(self, row):
        def op():
            with self.db.transaction(self.audience) as c:
                self.fence(c)
                c.execute("UPDATE brodar_v2.research_tasks SET status='submitting',interactions=interactions+1 "
                          "WHERE audience=%s AND id=%s AND consumer_owner=%s AND generation=%s AND interactions<10 RETURNING id",
                          (self.audience, str(row['id']), self.lease().owner, row['generation']))
                if c.fetchone() is None: raise PermissionError('Research interaction limit or lease reached')
        await self.db.run(op)

    async def notifications(self):
        def op():
            with self.db.transaction(self.audience, readonly=True) as c:
                c.execute("SELECT * FROM brodar_v2.research_tasks WHERE audience=%s AND transport='hyperagent_mcp' "
                          "AND status IN ('complete','partial','blocked','failed','uncertain','waiting') AND notified_result_version<result_version ORDER BY created_at LIMIT 10", (self.audience,))
                return records(c)
        return await self.db.run(op)

    async def next_delay(self):
        def op():
            with self.db.transaction(self.audience,readonly=True) as c:
                c.execute("SELECT extract(epoch FROM coalesce(min(next_poll),now()+interval '60 seconds')-now()) "
                          "FROM brodar_v2.research_tasks WHERE audience=%s AND transport='hyperagent_mcp' "
                          "AND status IN ('queued','running','submitting','waiting')",(self.audience,))
                return max(1,min(60,float(c.fetchone()[0])))
        return await self.db.run(op)

    async def notified(self, row):
        def op():
            with self.db.transaction(self.audience) as c:
                self.fence(c)
                c.execute('UPDATE brodar_v2.research_tasks SET notified_generation=%s,notified_result_version=%s WHERE audience=%s AND id=%s AND generation=%s AND result_version=%s',
                          (row['generation'],row['result_version'],self.audience,str(row['id']),row['generation'],row['result_version']))
        await self.db.run(op)


class ResearchService:
    def __init__(self, cfg, store, transport, access, notify):
        self.cfg, self.store, self.transport, self.access, self.notify = cfg, store, transport, access, notify
        self.wake = asyncio.Event()

    def ready(self):
        return self.cfg.research_transport == 'hyperagent_mcp' and self.transport.ready

    def origin(self, source, row=None):
        cap = source.require(visible=True)
        scope = source.store.scope
        if scope.chat_id < 0:
            if scope.chat_id not in self.access.groups: raise PermissionError('Research origin group is not approved')
        elif scope.chat_id not in self.cfg.owner_ids or cap.requester_id != scope.chat_id:
            raise PermissionError('Research DM must belong to its authenticated owner')
        if row and (row['origin_chat_id'], row['origin_topic_id']) != (scope.chat_id, scope.topic_id):
            raise PermissionError('Research task belongs to a different conversation')
        return cap, scope

    async def delegate(self, source, brief, operation):
        cap, scope = self.origin(source)
        if cap.peer_bot_id or getattr(source, 'research_context', ()):
            raise PermissionError('An external research result cannot recursively create another research task')
        if not self.ready(): raise AuthenticationRequired('Hyperagent research is unavailable; connect it with brodar hyperagent-login')
        if not brief.strip() or len(brief) > 6000: raise ValueError('Research brief must contain 1..6000 characters')
        if not operation or len(operation) > 200: raise ValueError('Use a research operation ID of 1..200 characters')
        task_id = str(uuid5(NAMESPACE_URL, f'{scope.audience}:{cap.turn_id}:research:{operation}'))
        brief = scrub(brief)
        message = render('research-delegation.md', task_id=task_id, brief=brief)
        def guard():
            if source.capability is not cap: raise PermissionError('Research requesting turn ended')
        token = WRITE_FENCE.set(guard)
        try:
            row = await self.store.enqueue(task_id=task_id, scope=scope, requester=cap.requester_id,
                request_id=cap.turn_id, brief=brief, agent_id=self.transport.agent_id, message=message, operation=operation)
        finally: WRITE_FENCE.reset(token)
        source.pending_research = row['status'] in {'queued','submitting','running','waiting'}
        if source.before_final_delivery: await source.before_final_delivery(cap)
        self.wake.set()
        return {'task_id': task_id, 'status': row['status'], 'transport': 'hyperagent_mcp',
                'background': source.pending_research, 'return_chat_id': scope.chat_id, 'return_topic_id': scope.topic_id,
                'previous_report':((row.get('result') or {}).get('report') or '')[:12000]}

    async def follow_up(self, source, task_id, brief, operation):
        cap, scope = self.origin(source)
        task_id = str(UUID(task_id))
        row = await self.store.get(task_id)
        if source.capability is not cap:raise PermissionError('Research requesting turn ended')
        self.origin(source,row)
        context = getattr(source, 'research_context', ())
        if cap.peer_bot_id or (context and task_id not in context): raise PermissionError('Follow up only on the research result being handled')
        if not self.ready(): raise AuthenticationRequired('Hyperagent research is unavailable')
        if not brief.strip() or len(brief) > 6000 or not operation or len(operation) > 200: raise ValueError('Invalid research follow-up')
        def guard():
            if source.capability is not cap: raise PermissionError('Research requesting turn ended')
        token = WRITE_FENCE.set(guard)
        try:
            row = await self.store.follow_up(task_id, render('research-delegation.md', task_id=task_id, brief=scrub(brief)), operation)
        finally: WRITE_FENCE.reset(token)
        source.pending_research = row['status'] in {'queued','submitting','running','waiting'}
        if source.before_final_delivery: await source.before_final_delivery(cap)
        self.wake.set()
        return {'task_id': task_id, 'status': row['status'], 'background': True}

    async def tasks(self, source):
        _, scope = self.origin(source)
        return await self.store.list(scope)

    async def step(self, row):
        submitting = False
        try:
            await self.transport.prepare()
            if row['remote_agent_id'] != self.transport.agent_id:
                raise HyperagentError('Research job is pinned to a different Hyperagent agent')
            if row['status'] == 'queued':
                await self.store.submitting(row)
                submitting = True
                if row.get('remote_thread_id'):
                    await self.transport.follow_up(row['remote_thread_id'], row['outbound_message'])
                    uid = row['remote_thread_id']
                else:
                    uid = remote_thread_id(await self.transport.submit(row['outbound_message']))
                await self.store.update(row, status='running', remote_thread_id=uid, error_code=None,attempts=0)
            else:
                result = parse_status(await self.transport.status(row['remote_thread_id']), row['remote_thread_id'], row['remote_agent_id'])
                if result is None or result['result_id'] == row.get('last_result_id'):
                    await self.store.update(row, status='waiting' if result and result['result_id']=='awaiting_approval' else 'running',attempts=0)
                else:
                    await self.store.update(row, status='waiting' if result['result_id']=='awaiting_approval' else result['status'], result=result, last_result_id=result['result_id'], error_code=None,attempts=0)
        except asyncio.CancelledError:
            # Persisted 'submitting' will become uncertain on restart; do not retry.
            raise
        except Exception as exc:
            if caused_by(exc,AuthenticationRequired):status='blocked'
            elif submitting:
                status = 'failed' if caused_by(exc,RemoteRejected) else 'uncertain'
            elif caused_by(exc,HyperagentError):status='failed'
            elif row['attempts'] >= 4: status = 'failed'
            else: status = row['status']
            code='hyperagent_login_required' if caused_by(exc,AuthenticationRequired) else ('hyperagent_rejected_request' if caused_by(exc,RemoteRejected) else type(exc).__name__)
            changes=dict(status=status,error_code=code,attempts=row['attempts']+1,delay=min(15*2**row['attempts'],120))
            if status in TERMINAL:
                if status=='uncertain':report="Hyperagent may have accepted the request, but its submission could not be confirmed. It was not automatically repeated. Inspect the research thread in Hyperagent."
                elif code=='hyperagent_login_required':report='Hyperagent research needs its dedicated OAuth connection reauthorized with brodar hyperagent-login.'
                elif caused_by(exc,RemoteRejected):report='Hyperagent rejected the request. Check the researcher execution mode (MCP requires Auto rather than legacy Ask first) and its access policy.'
                else:report='The Hyperagent research connection failed or returned an unsupported result. The requested research was not confirmed complete.'
                changes['result']={'status':status,'report':report,'result_id':code}
            await self.store.update(row, **changes)
            log.warning('Research operation deferred task=%s status=%s error=%s', row['id'], status, type(exc).__name__)

    async def tick(self):
        if self.cfg.research_transport != 'hyperagent_mcp': return 60
        rows = await self.store.claim()
        await asyncio.gather(*(self.step(row) for row in rows))
        for row in await self.store.notifications():
            await self.notify(row)
            await self.store.notified(row)
        return await self.store.next_delay()

    async def run(self):
        while True:
            self.wake.clear()
            delay=30
            try: delay=await self.tick()
            except asyncio.CancelledError: raise
            except Exception as exc: log.warning('Research worker deferred: %s', type(exc).__name__)
            try: await asyncio.wait_for(self.wake.wait(), delay)
            except asyncio.TimeoutError: pass
