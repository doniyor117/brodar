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


@pytest.mark.asyncio
async def test_full_artifact_catalog_resolves_slide_types_before_wrapping():
    from brodar.artifacts import Slide
    async def audit_tool(**kwargs):pass
    broker=ToolBroker(SimpleNamespace(audit_tool=audit_tool),SimpleNamespace(),artifacts=SimpleNamespace())
    cap=TurnCapability(str(uuid4()));broker.begin(cap)
    seen=[]
    async def create(title,slides,filename,operation_id):
        assert isinstance(slides[0],Slide)
        seen.append(slides[0].title)
        return {'artifact_id':str(uuid4())}
    broker.create_presentation=create
    mcp=build_tools(broker)
    tools=await mcp.list_tools()
    deck=next(tool for tool in tools if tool.name=='create_presentation')
    assert 'Slide' in deck.inputSchema['$defs']
    assert all('turn_token' in tool.inputSchema['required'] for tool in tools)
    await mcp.call_tool('create_presentation',{'title':'Test','slides':[{'title':'Hello'}],
        'filename':'test.pptx','operation_id':'test','turn_token':cap.token})
    assert seen==['Hello']
