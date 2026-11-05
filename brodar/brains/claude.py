"""Thin adapter around Hamroh's existing persistent Claude stream-json worker.

The worker's argv/parser are retained. Its undrained injection fallback is
treated as rejected input, so Brodar's durable inbox owns replay instead.
"""

from __future__ import annotations

import asyncio
import dataclasses
import os

from hamroh.cc_worker import CcWorker

from .base import Outcome
from .codex import child_environment


class IsolatedCcWorker(CcWorker):
    def __init__(self,*args,child_env=None,child_cwd=None,**kwargs):
        super().__init__(*args,**kwargs)
        self._child_env=child_env
        self._child_cwd=child_cwd

    async def start(self):
        # Keep upstream build_argv and readers. Override only the process
        # environment/cwd; never expose service credentials to native CLI tools.
        from hamroh.cc_worker.spec import build_argv,FORBIDDEN_FLAG
        argv=build_argv(self.spec)
        assert FORBIDDEN_FLAG not in argv
        self._capture.open(self._session_id)
        self._proc=await asyncio.create_subprocess_exec(
            *argv,stdin=asyncio.subprocess.PIPE,stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,env=self._child_env,cwd=self._child_cwd,limit=4*1024*1024)
        self._stdout_task=asyncio.create_task(self._read_stdout(),name='brodar-claude-stdout')
        self._stderr_task=asyncio.create_task(self._read_stderr(),name='brodar-claude-stderr')


class ClaudeBrain:
    def __init__(self,*,spec,config,home, cwd,env=None,worker_factory=IsolatedCcWorker):
        self.spec,self.config=spec,config
        self.home,self.cwd=home,cwd
        self.env=env or {}
        self.worker_factory=worker_factory
        self.worker=None
        self.session_id=None
        self._active=False

    async def start(self,session_id=None):
        self.home.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.cwd.mkdir(mode=0o700,parents=True,exist_ok=True)
        env=child_environment(self.home,self.env)
        # Claude uses its own isolated HOME/CLAUDE_CONFIG_DIR, not Codex config.
        env.pop('CODEX_HOME',None)
        env['CLAUDE_CONFIG_DIR']=str(self.home)
        self.worker=self.worker_factory(dataclasses.replace(self.spec,session_id=session_id),self.config,
                                        child_env=env,child_cwd=str(self.cwd))
        await self.worker.start()
        await self.worker.supervise()
        self.session_id=session_id

    async def turn(self,text):
        if self._active:
            raise RuntimeError('A turn is already running')
        if self.worker is None or not self.worker.is_running:
            if self.worker is not None:await self.worker.stop()
            await self.start(self.session_id)
        self._active=True
        try:
            await self.worker.send(text)
            r=await self.worker.wait_for_result()
            self.session_id=self.worker.session_id
            if r.aborted_reason or r.api_error:
                if r.aborted_reason=='cancelled':
                    return Outcome('interrupted',error='cancelled',session_id=self.session_id)
                return Outcome('failed',error=r.aborted_reason or 'provider_api_error',session_id=self.session_id)
            if r.control is None:
                return Outcome('failed',error='invalid_terminal_output',session_id=self.session_id,
                               internal_text='\n'.join(r.text_blocks))
            return Outcome('completed',r.control.action,r.control.reason or '',session_id=self.session_id)
        except (BrokenPipeError,ConnectionResetError):
            return Outcome('failed',error='transport_closed',session_id=self.session_id)
        finally:
            self._active=False

    async def steer(self,text):
        if not self._active or not self.worker.is_running:
            return False
        await self.worker.inject(text)
        # The upstream worker explicitly documents that its fallback queue is
        # not replayed. Drain it here; the durable inbox remains authoritative.
        if not self.worker._inject_queue.empty():
            while not self.worker._inject_queue.empty():
                self.worker._inject_queue.get_nowait()
            return False
        return True

    async def interrupt(self):
        if self.worker is not None:
            self.worker._abort_turn_locally('cancelled')
            await self.worker.stop()

    async def close(self):
        if self.worker is not None:
            await self.worker.stop()
