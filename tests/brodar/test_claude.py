from dataclasses import dataclass
from types import SimpleNamespace

import pytest

from brodar.brains.claude import ClaudeBrain


@dataclass
class Spec:
    session_id:str|None=None


class Worker:
    created=[]
    def __init__(self,spec,config,**kwargs):
        self.spec=spec;self.env=kwargs['child_env'];self.is_running=False;self.session_id=spec.session_id
        self.created.append(self)
    async def start(self):self.is_running=True
    async def supervise(self):pass
    async def stop(self):self.is_running=False
    def _abort_turn_locally(self,reason):pass
    async def send(self,text):self.session_id='claude-thread'
    async def wait_for_result(self):return SimpleNamespace(aborted_reason=None,api_error=None,
        control=SimpleNamespace(action='skip',reason='chatter'))


@pytest.mark.asyncio
async def test_claude_uses_shared_dedicated_auth_and_restarts_after_cancel(tmp_path,monkeypatch):
    monkeypatch.setenv('GOOGLE_AI_API_KEY','must-not-reach-cli')
    Worker.created=[];home=tmp_path/'claude'
    brain=ClaudeBrain(spec=Spec(),config=SimpleNamespace(),home=home,cwd=tmp_path/'room-a',worker_factory=Worker)
    await brain.start()
    assert brain.worker.env['CLAUDE_CONFIG_DIR']==str(home)
    assert 'CODEX_HOME' not in brain.worker.env and 'GOOGLE_AI_API_KEY' not in brain.worker.env
    assert (home/'user').is_dir()
    assert (await brain.turn('hello')).action=='skip'
    first=brain.worker;await brain.interrupt()
    assert not first.is_running
    assert (await brain.turn('next')).action=='skip'
    assert brain.worker is not first and brain.worker.spec.session_id=='claude-thread'
    other=ClaudeBrain(spec=Spec(),config=SimpleNamespace(),home=home,cwd=tmp_path/'room-b',worker_factory=Worker)
    await other.start()
    assert other.worker.env['CLAUDE_CONFIG_DIR']==brain.worker.env['CLAUDE_CONFIG_DIR']
    await brain.close();await other.close()
