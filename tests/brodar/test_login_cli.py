"""Provider selection must be explicit, isolated and never a persistent switch."""
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from brodar.__main__ import main


@pytest.mark.parametrize('saved,target,flag,expected',[
    (None,None,None,'codex'),
    ('claude',None,None,'claude'),
    ('codex','claude',None,'claude'),
    ('claude','codex',None,'codex'),
    ('codex',None,'claude','claude'),
    ('claude','claude','claude','claude'),
])
def test_login_uses_dedicated_provider_without_changing_profile(tmp_path,monkeypatch,capsys,saved,target,flag,expected):
    from brodar.settings import Settings
    profile=tmp_path/'profile.json'
    profile.write_text(json.dumps({'version':1,'settings':{'BRODAR_BRAIN':saved} if saved else {}}))
    before=profile.read_bytes()
    chosen=[]
    def load(**kwargs):
        selected=kwargs.get('brain') or saved or 'codex'
        chosen.append(selected)
        return SimpleNamespace(brain=selected,runtime_dir=tmp_path/'runtime')
    monkeypatch.setattr(Settings,'load',load)
    calls=[]
    monkeypatch.setattr('subprocess.call',lambda argv,**kwargs:calls.append((argv,kwargs)) or 0)
    args=['brodar','login']+([target] if target else [])+(['--brain',flag] if flag else [])+['--profile',str(profile)]
    monkeypatch.setattr('sys.argv',args)
    with pytest.raises(SystemExit) as stop:main()
    assert stop.value.code==0 and chosen==[expected]
    argv,kwargs=calls[0]
    assert argv==(['codex','login','--device-auth'] if expected=='codex' else ['claude','auth','login'])
    home=tmp_path/'runtime'/expected
    assert kwargs['cwd']==str(home) and kwargs['env']['HOME']==str(home/'user')
    if expected=='claude':assert kwargs['env']['CLAUDE_CONFIG_DIR']==str(home) and 'CODEX_HOME' not in kwargs['env']
    else:assert kwargs['env']['CODEX_HOME']==str(home)
    assert profile.read_bytes()==before
    assert ('Codex' if expected=='codex' else 'Claude Code') in capsys.readouterr().out


@pytest.mark.parametrize('args',[['login','typo'],['login','claude','--brain','codex']])
def test_invalid_login_target_is_rejected_before_auth(monkeypatch,args):
    monkeypatch.setattr('sys.argv',['brodar',*args])
    monkeypatch.setattr('subprocess.call',lambda *a,**kw:pytest.fail('Must not start authorization'))
    with pytest.raises(SystemExit) as stop:main()
    assert stop.value.code==2
