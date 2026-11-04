"""Line-delimited app-server JSON-RPC with independent stdout/stderr pumps."""

from __future__ import annotations

import asyncio
import json
import time
from collections import deque


class RpcError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(f'app-server RPC failed ({code})')


class RpcProcess:
    def __init__(self, argv: list[str], *, env: dict[str,str], cwd: str,
                 on_notification=None, on_request=None):
        self.argv,self.env,self.cwd = argv,env,cwd
        self.on_notification,self.on_request = on_notification,on_request
        self.process = None
        self.pending = {}
        self.tasks = set()
        self.counter = 0
        self.stderr_tail = deque(maxlen=20)
        self.last_activity = time.monotonic()
        self._write_lock = asyncio.Lock()

    async def start(self):
        self.process = await asyncio.create_subprocess_exec(
            *self.argv,env=self.env,cwd=self.cwd,stdin=asyncio.subprocess.PIPE,
            stdout=asyncio.subprocess.PIPE,stderr=asyncio.subprocess.PIPE,limit=8*1024*1024)
        self._track(self._read())
        self._track(self._stderr())

    def _track(self, coro):
        task = asyncio.create_task(coro)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def write(self, message):
        async with self._write_lock:
            if self.process is None or self.process.returncode is not None:
                raise RpcError('transport_closed')
            self.process.stdin.write((json.dumps(message,ensure_ascii=False)+'\n').encode())
            try:
                await self.process.stdin.drain()
            except (BrokenPipeError,ConnectionResetError):
                raise RpcError('transport_closed') from None

    async def request(self, method, params=None, *, timeout=45):
        self.counter += 1
        rid = self.counter
        future = asyncio.get_running_loop().create_future()
        self.pending[rid] = future
        try:
            await self.write({'id':rid,'method':method,'params':params or {}})
            return await asyncio.wait_for(future,timeout=timeout)
        finally:
            self.pending.pop(rid,None)

    async def notify(self, method, params=None):
        await self.write({'method':method,'params':params or {}})

    async def _read(self):
        try:
            while line := await self.process.stdout.readline():
                self.last_activity = time.monotonic()
                try:
                    msg = json.loads(line)
                except (json.JSONDecodeError,UnicodeError):
                    continue
                if 'method' in msg:
                    if 'id' in msg:
                        self._track(self._server_request(msg))
                    elif self.on_notification:
                        self.on_notification(msg['method'],msg.get('params',{}))
                elif (f := self.pending.get(msg.get('id'))) is not None and not f.done():
                    if 'error' in msg:
                        f.set_exception(RpcError(msg['error'].get('code','unknown')))
                    else:
                        f.set_result(msg.get('result'))
        except asyncio.CancelledError:
            raise
        except Exception:
            pass
        finally:
            for future in list(self.pending.values()):
                if not future.done():
                    future.set_exception(RpcError('transport_closed'))
            if self.on_notification:
                self.on_notification('brodar/transportClosed',{})

    async def _server_request(self, msg):
        try:
            if self.on_request:
                result = await self.on_request(msg['method'],msg.get('params',{}))
                response = {'id':msg['id'],'result':result}
            else:
                response = {'id':msg['id'],'error':{'code':-32601,'message':'Not supported by Brodar'}}
        except Exception:
            response = {'id':msg['id'],'error':{'code':-32603,'message':'Brodar request failed'}}
        try:
            await self.write(response)
        except (RpcError,BrokenPipeError):
            pass

    async def _stderr(self):
        while line := await self.process.stderr.readline():
            # Kept in process memory only, never included in user-facing errors.
            self.stderr_tail.append(line.decode(errors='replace')[:2000])

    async def close(self):
        if self.process is not None and self.process.returncode is None:
            self.process.terminate()
            try:
                await asyncio.wait_for(self.process.wait(),5)
            except asyncio.TimeoutError:
                self.process.kill()
                await self.process.wait()
        tasks = list(self.tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks,return_exceptions=True)
        self.process = None
