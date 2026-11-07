import ast
from pathlib import Path
import pytest
from brodar import prompt_files
from brodar.prompts import compose
from brodar.mcp import build_tools
from brodar.broker import ToolBroker
from types import SimpleNamespace

def test_runtime_templates_render_without_duplicate_tool_markers():
    text,revision=compose(model='test-model',provider='codex',tools=['mcp__hamroh__one','mcp__hamroh__two'])
    assert '- mcp__hamroh__one\n- mcp__hamroh__two' in text
    assert '${' not in text
    assert len(revision)==64

def test_prompt_revision_tracks_provider_guidance(monkeypatch):
    _,before=compose(model='x',provider='codex',tools=[])
    old=prompt_files.load
    monkeypatch.setattr(prompt_files,'load',lambda name:old(name)+(' changed' if name=='codex-developer.md' else ''))
    _,after=compose(model='x',provider='codex',tools=[])
    assert before!=after

@pytest.mark.asyncio
async def test_every_active_mcp_tool_has_external_description():
    broker=ToolBroker(SimpleNamespace(),None,artifacts=SimpleNamespace())
    tools=await build_tools(broker).list_tools()
    assert len(tools)>30
    for tool in tools:
        assert prompt_files.section('tool-descriptions.md',tool.name) in tool.description
        assert prompt_files.load('tool-authorization.md') in tool.description

@pytest.mark.parametrize('name',['../secret.md','runtime.env','/runtime.md'])
def test_prompt_loader_rejects_arbitrary_files(name):
    with pytest.raises(ValueError):prompt_files.load(name)

def test_packaging_maps_every_runtime_prompt():
    import tomllib
    root=Path(__file__).resolve().parents[2]
    includes=tomllib.loads((root/'pyproject.toml').read_text())['tool']['hatch']['build']['targets']['wheel']['force-include']
    for name in ['runtime.md','memory-maintenance.md','tool-descriptions.md','embedding-query.md','codex-developer.md','claude-tools.md','repair-no-delivery.md']:
        assert 'prompts/'+name in includes
