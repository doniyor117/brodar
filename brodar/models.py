"""Operator model discovery, without starting an inference turn."""
import shutil
from .brains.codex import app_server_argv,child_environment
from .brains.rpc import RpcProcess

async def codex_models(cfg,*,factory=RpcProcess):
    binary=shutil.which('codex')
    if not binary:raise ValueError('Codex CLI missing')
    home=cfg.runtime_dir/'codex';(home/'user').mkdir(parents=True,mode=0o700,exist_ok=True)
    rpc=factory(app_server_argv(binary),env=child_environment(home),cwd=str(home))
    try:
        await rpc.start();await rpc.request('initialize',{'clientInfo':{'name':'brodar-models','version':'0.1.0'},'capabilities':{'experimentalApi':True}});await rpc.notify('initialized')
        result=await rpc.request('model/list',{'includeHidden':False},timeout=20)
        return [{'id':row.get('model',row.get('id')),'name':row.get('displayName'),
                 'efforts':row.get('supportedReasoningEfforts',[]),'default_effort':row.get('defaultReasoningEffort')}
                for row in result.get('data',[])]
    finally:await rpc.close()
