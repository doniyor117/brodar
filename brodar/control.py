"""Private local operator transport. Socket permissions, not Telegram impersonation."""
from __future__ import annotations
import asyncio
import json
import os
import socket
import struct
import hashlib
import tempfile
from pathlib import Path
from .profile import atomic_private
from .redaction import scrub_object

def socket_path(cfg):
    path=cfg.runtime_dir/'control.sock'
    if len(os.fsencode(path))<100:return path
    directory=Path(tempfile.gettempdir())/('brodar-'+str(os.getuid()))
    directory.mkdir(mode=0o700,parents=True,exist_ok=True)
    if directory.is_symlink() or directory.stat().st_uid!=os.getuid():raise PermissionError('Unsafe control directory')
    directory.chmod(0o700)
    return directory/(hashlib.sha256(str(cfg.runtime_dir.resolve()).encode()).hexdigest()[:16]+'.sock')

class ControlServer:
    def __init__(self,cfg,handler):self.cfg,self.handler=cfg,handler;self.server=None;self.path=socket_path(cfg)
    async def start(self):
        self.cfg.runtime_dir.mkdir(mode=0o700,parents=True,exist_ok=True)
        self.path.unlink(missing_ok=True)
        self.server=await asyncio.start_unix_server(self._client,path=str(self.path),limit=128000)
        self.path.chmod(0o600)
    async def _client(self,reader,writer):
        try:
            sock=writer.get_extra_info('socket')
            if hasattr(socket,'SO_PEERCRED'):
                _,uid,_=struct.unpack('3i',sock.getsockopt(socket.SOL_SOCKET,socket.SO_PEERCRED,12))
                if uid!=os.getuid():raise PermissionError('Local operator identity mismatch')
            line=await asyncio.wait_for(reader.readline(),5)
            if len(line)>64000:raise ValueError('Control request too large')
            request=json.loads(line);result=await self.handler(request.get('method'),request.get('params') or {})
            response={'ok':True,'result':scrub_object(result)}
        except Exception as exc:response={'ok':False,'error':type(exc).__name__}
        writer.write(json.dumps(response,default=str).encode()+b'\n');await writer.drain();writer.close();await writer.wait_closed()
    async def close(self):
        if self.server:self.server.close();await self.server.wait_closed()
        self.path.unlink(missing_ok=True)

async def request(cfg,method,params=None,timeout=15):
    reader,writer=await asyncio.wait_for(asyncio.open_unix_connection(str(socket_path(cfg)),limit=128000),timeout)
    try:
        writer.write(json.dumps({'method':method,'params':params or {}}).encode()+b'\n');await writer.drain()
        response=json.loads(await asyncio.wait_for(reader.readline(),timeout))
        if not response.get('ok'):raise RuntimeError(response.get('error','Control action failed'))
        return response['result']
    finally:writer.close();await writer.wait_closed()
