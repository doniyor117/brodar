from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from brodar.hyperagent import write_auth
from brodar.onboarding import configure_research
from brodar.profile import read_profile
from brodar.setup_engine import SetupEngine


@pytest.mark.asyncio
async def test_research_configuration_enables_authorized_agent_without_remote_call(tmp_path):
    auth=tmp_path/'research-auth.json'
    write_auth(auth,{'tokens':{'access_token':'fixture'},'agent_id':'researcher-1'})
    with SetupEngine(tmp_path/'profile.json') as engine:
        engine.defaults();engine.set('BRODAR_HYPERAGENT_AUTH_FILE',str(auth))
        q=SimpleNamespace(yes=AsyncMock(return_value=True),ask=AsyncMock(return_value='researcher-1'))
        await configure_research(engine,q)
    _,profile=read_profile(tmp_path/'profile.json')
    assert profile['settings']['BRODAR_RESEARCH_TRANSPORT']=='hyperagent_mcp'
    assert profile['settings']['BRODAR_HYPERAGENT_AGENT_ID']=='researcher-1'
    assert q.ask.await_args.args[1]=='researcher-1'


@pytest.mark.asyncio
async def test_research_configuration_requires_login_before_enabling(tmp_path):
    with SetupEngine(tmp_path/'profile.json') as engine:
        engine.defaults();engine.set('BRODAR_HYPERAGENT_AUTH_FILE',str(tmp_path/'missing'))
        q=SimpleNamespace(yes=AsyncMock(return_value=True),ask=AsyncMock(return_value='researcher-1'))
        with pytest.raises(ValueError,match='Authorize first'):await configure_research(engine,q)
        assert engine.profile['settings']['BRODAR_RESEARCH_TRANSPORT']=='disabled'
        assert 'BRODAR_HYPERAGENT_AGENT_ID' not in engine.profile['settings']


@pytest.mark.asyncio
async def test_research_can_be_disabled_without_authentication(tmp_path):
    with SetupEngine(tmp_path/'profile.json') as engine:
        engine.defaults();engine.set('BRODAR_RESEARCH_TRANSPORT','hyperagent_mcp')
        q=SimpleNamespace(yes=AsyncMock(return_value=False),ask=AsyncMock())
        await configure_research(engine,q)
        assert engine.profile['settings']['BRODAR_RESEARCH_TRANSPORT']=='disabled'
        q.ask.assert_not_awaited()


@pytest.mark.asyncio
async def test_research_configuration_rejects_display_names(tmp_path):
    with SetupEngine(tmp_path/'profile.json') as engine:
        engine.defaults()
        q=SimpleNamespace(yes=AsyncMock(return_value=True),ask=AsyncMock(return_value='Hyper Researcher'))
        with pytest.raises(ValueError,match='agent ID'):await configure_research(engine,q)
        assert engine.profile['settings']['BRODAR_RESEARCH_TRANSPORT']=='disabled'
