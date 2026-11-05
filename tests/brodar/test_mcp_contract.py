from types import SimpleNamespace
from uuid import uuid4

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from brodar.broker import ToolBroker,TurnCapability
from brodar.mcp import build_tools


@pytest.mark.asyncio
async def test_old_process_tool_token_cannot_inherit_a_new_turn_capability():
    audits=[]
    async def audit_tool(**kwargs):audits.append(kwargs)
    broker=ToolBroker(SimpleNamespace(audit_tool=audit_tool),SimpleNamespace())
    mcp=build_tools(broker);first=TurnCapability(str(uuid4()))
    broker.begin(first)
    tool=next(tool for tool in await mcp.list_tools() if tool.name=='now')
    assert 'turn_token' in tool.inputSchema['required']
    await mcp.call_tool('now',{'turn_token':first.token})
    broker.end()
    second=TurnCapability(first.turn_id)  # retry of the same durable job
    broker.begin(second)
    assert second.token!=first.token
    with pytest.raises(ToolError,match='different or completed turn'):
        await mcp.call_tool('now',{'turn_token':first.token})
    await mcp.call_tool('now',{'turn_token':second.token})
    assert all('turn_token' not in row['arguments'] for row in audits)
