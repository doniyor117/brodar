"""Telegram typing feedback for addressed work, without posting filler."""

import asyncio
import logging

from telegram.error import BadRequest,Forbidden,TelegramError


class TypingPresence:
    def __init__(self,bot,interval=4):
        self.bot,self.interval=bot,interval
        self.tasks={}

    def start(self,scope):
        task=self.tasks.get(scope.conversation)
        if task is None or task.done():
            self.tasks[scope.conversation]=asyncio.create_task(self._run(scope),name='brodar-typing')

    async def _run(self,scope):
        kwargs={'chat_id':scope.chat_id,'action':'typing'}
        if scope.topic_id:kwargs['message_thread_id']=scope.topic_id
        while True:
            try:await self.bot.send_chat_action(**kwargs)
            except (BadRequest,Forbidden):return
            except TelegramError as exc:logging.getLogger('brodar').debug('Typing feedback deferred: %s',type(exc).__name__)
            await asyncio.sleep(self.interval)

    async def stop(self,scope):
        task=self.tasks.pop(scope.conversation,None)
        if task:
            task.cancel()
            await asyncio.gather(task,return_exceptions=True)

    async def close(self):
        tasks=list(self.tasks.values());self.tasks.clear()
        for task in tasks:task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
