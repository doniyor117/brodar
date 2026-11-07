"""Owned, preemptible background learning; no conversational session persistence."""
import asyncio
import logging
import time
from uuid import uuid4

from .broker import TurnCapability
from .prompts import authorized,envelope

log=logging.getLogger('brodar.learning')
GUIDANCE='''Private memory maintenance. Learn freely from conversations and feedback.
Create, correct, merge or retire supplementary notes; read full notes before editing.
Chunk metadata marks partial messages; do not infer missing text. Use history tools to check cited originals. Preserve dates, uncertainty and whose
preference applies in which situation. Jokes and opinions are not verified facts.
Keep useful writing advice, social context, interests, plans and task methods.
Main persona and permissions stay fixed. Maintain context/overview.md as a small
index (aim under 2000 characters) linking useful notes, not a copy of all knowledge.
For derived notes, carry forward original source UUIDs from all notes you use.
Correct conflicting evidence and retire stale duplicates. No change is also valid.
Return {"action":"skip","reason":"brief result"} when finished.'''

class Maintenance:
    def __init__(self,cfg,factory,semaphore,busy):
        self.cfg,self.factory,self.semaphore,self.busy=cfg,factory,semaphore,busy
        self.task=None
        self.worker=None
        self.index_retry_at=0

    def preempt(self):
        # Revoke synchronously before cancellation crosses an await.
        if self.worker:self.worker.broker.end()
        if self.task:self.task.cancel()

    async def run(self,scope):
        if self.busy() or self.semaphore.locked():return
        self.task=asyncio.create_task(self._run(scope),name='brodar-reflection-run')
        try:await self.task
        except asyncio.CancelledError:
            if asyncio.current_task().cancelling():raise
        except Exception as exc:log.warning('Reflection deferred: %s',type(exc).__name__)
        finally:self.task=None

    async def _run(self,scope):
        start=time.monotonic()
        async with self.semaphore:
            if self.busy():return
            self.worker=worker=await self.factory(scope)
            run=None
            try:
                run=await worker.store.maintenance_prepare(self.cfg.consolidation_enabled,self.cfg.consolidation_interval)
                if not run:return
                async with asyncio.timeout(self.cfg.maintenance_timeout):
                    await worker.start(maintenance=True)
                    if self.busy():return
                    cap=TurnCapability(run['id'],mode='reflection')
                    worker.broker.begin(cap)
                    notes=await worker.store.memory_list(40)
                    payload=GUIDANCE+'\nmode: '+run['kind']+'\n'+envelope([],context=run['messages'],notes=notes)
                    if run['kind']=='consolidation':payload+='\nReview these full candidate notes: '+str(run['notes'])
                    outcome=await worker.brain.turn(authorized(payload,cap))
                    if outcome.status=='completed' and outcome.action in {'skip','stop'}:
                        # Capture capability identity through thread/commit checks.
                        from .write_fence import WRITE_FENCE
                        def check():
                            if worker.broker.capability is not cap:raise PermissionError('Maintenance revoked')
                        token=WRITE_FENCE.set(check)
                        try:
                            run['changes']=list(worker.broker._memory_changes)
                            await worker.store.maintenance_finish(run)
                        finally:WRITE_FENCE.reset(token)
            finally:
                worker.broker.end()
                if run:
                    try:await asyncio.wait_for(worker.store.maintenance_end(run,'interrupted'),5)
                    except Exception:log.warning('Maintenance status update deferred')
                try:await asyncio.wait_for(worker.close(),10)
                except (Exception,asyncio.CancelledError):log.warning('Maintenance cleanup interrupted')
                self.worker=None
                log.warning('Maintenance duration_ms=%d kind=%s',int((time.monotonic()-start)*1000),run['kind'] if run else 'none')
