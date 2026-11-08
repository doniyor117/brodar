"""Generation-owned Telegram typing; old work cannot stop new feedback."""
import asyncio
import logging
import time
from datetime import timedelta
from telegram.error import BadRequest,Forbidden,TelegramError,RetryAfter

log=logging.getLogger('brodar.presence')
class TypingPresence:
    def __init__(self,bot,interval=3):
        self.bot,self.interval=bot,interval
        self.tasks={}
        self.generations={}
        self.inputs={}

    def generation(self,scope):return self.generations.get(scope.conversation,0)

    def for_inputs(self,scope,update_ids):
        known=self.inputs.get(scope.conversation,{})
        return max((known.get(uid,0) for uid in update_ids),default=0)

    def claim(self,scope,update_ids):
        """Recover feedback for ambient/restarted work without stealing a newer input."""
        generation=self.for_inputs(scope,update_ids)
        if generation:return generation
        task=self.tasks.get(scope.conversation)
        if task is not None and not task.done():return 0
        self.start(scope,new_work=True,update_id=update_ids[-1] if update_ids else None)
        return self.generation(scope)

    def start(self,scope,*,new_work=False,update_id=None):
        if new_work:
            self.generations[scope.conversation]=self.generation(scope)+1
            if update_id is not None:
                known=self.inputs.setdefault(scope.conversation,{})
                known[update_id]=self.generation(scope)
                while len(known)>256:known.pop(next(iter(known)))
        task=self.tasks.get(scope.conversation)
        if task is None or task.done():
            self.tasks[scope.conversation]=asyncio.create_task(self._run(scope),name='brodar-typing')

    async def _run(self,scope):
        kwargs={'chat_id':scope.chat_id,'action':'typing'}
        if scope.topic_id:kwargs['message_thread_id']=scope.topic_id
        failures=0
        while True:
            started=time.monotonic();delay=self.interval
            try:
                await asyncio.wait_for(self.bot.send_chat_action(**kwargs),3)
                failures=0
            except (BadRequest,Forbidden) as exc:
                log.warning('Typing unavailable: %s',type(exc).__name__);return
            except RetryAfter as exc:
                delay=exc.retry_after.total_seconds() if isinstance(exc.retry_after,timedelta) else float(exc.retry_after)
                delay+=.1+(time.monotonic()-started)  # RetryAfter is measured from rejection, not request start
            except (TelegramError,asyncio.TimeoutError) as exc:
                failures+=1
                if failures==1 or failures%10==0:log.warning('Typing deferred: %s',type(exc).__name__)
            await asyncio.sleep(max(.01,delay-(time.monotonic()-started)))

    async def stop(self,scope,*,generation=None):
        if generation is not None and generation!=self.generation(scope):return
        task=self.tasks.pop(scope.conversation,None)
        if task:
            task.cancel();await asyncio.gather(task,return_exceptions=True)

    async def close(self):
        tasks=list(self.tasks.values());self.tasks.clear();self.generations.clear();self.inputs.clear()
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
