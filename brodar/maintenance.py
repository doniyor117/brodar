"""Host-only persistent schedules; no inference and no work on reply's critical path."""
import asyncio
import json
import time
from pathlib import Path
from .profile import atomic_private

class PersistentSchedule:
    def __init__(self,path,interval,clock=time.time):
        self.path=Path(path);self.interval=interval;self.clock=clock
        self.retry_at=0
        try:self.state=json.loads(self.path.read_text())
        except (FileNotFoundError,ValueError):
            self.state={'last_success':None,'created_at':clock()};self._write()
    def _write(self):atomic_private(self.path,json.dumps(self.state))
    def wait_seconds(self):return max(0,max(self.retry_at,(self.state['last_success'] or self.state['created_at'])+self.interval)-self.clock())
    def due(self):return self.wait_seconds()<=0
    def success(self):self.state['last_success']=self.clock();self.retry_at=0;self._write()
    def failure(self):self.retry_at=self.clock()+min(3600,self.interval)

async def cleanup_files(cfg,db,bot_id,busy=lambda:False):
    from .artifacts import Artifacts
    from .learning import LearningStore
    from .scope import Scope
    schedule=PersistentSchedule(cfg.runtime_dir/'cleanup-schedule.json',cfg.cleanup_interval_seconds)
    while True:
        await asyncio.sleep(min(60,max(1,schedule.wait_seconds())))
        if not schedule.due() or busy():continue
        try:
            def audiences():
                with db.transaction(readonly=True) as c:
                    c.execute('SELECT chat_id FROM brodar_v2.audience_registry WHERE instance_id=%s AND chat_id<>0',(cfg.instance_id,))
                    return [r[0] for r in c.fetchall()]
            for chat_id in await db.run(audiences):
                store=LearningStore(db,Scope(bot_id,chat_id,0,cfg.instance_id))
                files=Artifacts(store,root=cfg.runtime_dir/'files')
                for _ in range(20):
                    result=await files.cleanup()
                    if result['cleaned']<100:break
                    await asyncio.sleep(.05)
            def scratch():
                now=time.time();count=0
                for base in (cfg.runtime_dir/'workspaces',cfg.runtime_dir/'providers'):
                    if not base.exists():continue
                    for file in base.rglob('*'):
                        rel=file.relative_to(base)
                        if 'skills' in rel.parts or '.git' in rel.parts:continue
                        if base.name=='providers' and 'work' not in rel.parts:continue
                        if file.is_symlink() or not file.is_file():continue
                        if now-max(file.stat().st_atime,file.stat().st_mtime)>cfg.temp_idle_seconds:
                            file.unlink();count+=1
                            if count>=1000:return
            await asyncio.to_thread(scratch)
            schedule.success()
        except Exception:
            import logging
            logging.getLogger('brodar.cleanup').warning('File cleanup deferred; no retained objects deleted');schedule.failure()
