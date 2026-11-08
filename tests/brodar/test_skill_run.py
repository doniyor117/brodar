import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from brodar.broker import ToolBroker,TurnCapability
from brodar.commands import CommandGateway
from brodar.jobs import InputRequest
from brodar.mcp import build_tools
from brodar.skills import SkillLibrary
from brodar.scope import Scope


@pytest.mark.asyncio
async def test_owner_can_prepare_skill_then_run_bash_and_node_modules(tmp_path,monkeypatch):
    package=tmp_path/'packages/slides-skill';package.mkdir(parents=True)
    (package/'SKILL.md').write_text('# Slides')
    (package/'run.sh').write_text('#!/usr/bin/env bash\nset -euo pipefail\necho ok\n')
    (package/'run.mjs').write_text('console.log(4)')
    (package/'preview-export-auth.mjs').write_text('export const isLoopback = host => host === "localhost";')
    library=SkillLibrary([package.parent]);monkeypatch.setattr('brodar.skills.LIBRARY',library)
    scope=Scope(1,-100)
    broker=ToolBroker(SimpleNamespace(scope=scope,audit_tool=AsyncMock()),SimpleNamespace(),owner_ids={7})
    broker.commands=CommandGateway(tmp_path/'workspace')
    broker.owner_jobs=SimpleNamespace(submit=AsyncMock(return_value={'job_id':'fixture','status':'pending'}))
    cap=TurnCapability(str(uuid4()),requester_id=7);broker.begin(cap)
    request_id=str(uuid4());broker.accept_requests([InputRequest(request_id,7,'use the slides skill')])
    mcp=build_tools(broker)
    args=dict(request_message_id=request_id,skill_id='slides-skill',resource='',arguments=[],operation_id='prepare',turn_token=cap.token)
    blocks,_=await mcp.call_tool('owner_skill_run',args)
    prepared=json.loads(blocks[0].text)
    assert prepared['prepared'] and not prepared['executed']
    assert (broker.commands.root/prepared['skill_workspace_path']/'SKILL.md').is_file()
    assert (broker.commands.root/prepared['skill_workspace_path']/'preview-export-auth.mjs').is_file()
    broker.owner_jobs.submit.assert_not_awaited()
    for resource,prefix in [('run.sh','bash'),('run.mjs','node')]:
        blocks,_=await mcp.call_tool('owner_skill_run',dict(args,resource=resource,operation_id=resource))
        submitted=broker.owner_jobs.submit.await_args
        assert submitted.args[2][0]==prefix
        assert submitted.args[2][1]==f"skill-sources/{prepared['skill_id']}/{resource}"
        assert json.loads(blocks[0].text)['skill_workspace_path']==prepared['skill_workspace_path']
    broker.end();broker.begin(TurnCapability(str(uuid4()),requester_id=8))
    broker.accept_requests([InputRequest(request_id,8,'use the slides skill')])
    with pytest.raises(Exception,match='Only a fresh authenticated owner'):
        await mcp.call_tool('owner_skill_run',dict(args,turn_token=broker.capability.token))


def test_auth_source_code_is_not_confused_with_credential_files(tmp_path):
    from brodar.owner_tools import check_skill_source
    for name in ['auth.json','.env','token.txt']:
        path=tmp_path/name;path.write_text('private')
        with pytest.raises(PermissionError):check_skill_source(path,Path(name))
    path=tmp_path/'auth.mjs';path.write_text('export const ok = true;')
    check_skill_source(path,Path('scripts/auth.mjs'))
    with pytest.raises(PermissionError):check_skill_source(path,Path('.aws/auth.mjs'))
