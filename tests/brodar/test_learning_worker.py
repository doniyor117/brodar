import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from brodar.broker import ToolBroker,TurnCapability
from brodar.brains.base import Outcome
from brodar.mcp import build_tools
from brodar.reflection import Maintenance

@pytest.mark.asyncio
async def test_maintenance_inventory_has_no_delivery_or_job_tools():
    broker=ToolBroker(SimpleNamespace(),None)
    broker.maintenance=True
    tools=await build_tools(broker).list_tools()
    names={t.name for t in tools}
    assert {'history_get','history_search','memory_write'}<=names
    assert all(n.startswith(('history_','memory_')) for n in names)

@pytest.mark.asyncio
async def test_tool_search_times_out_and_falls_back_to_lexical():
    async def blocked(*args,**kwargs):await asyncio.Event().wait()
    store=SimpleNamespace(search=AsyncMock(return_value=['lexical']))
    broker=ToolBroker(store,None,embeddings=SimpleNamespace(embed=blocked))
    broker.recall_timeout=.01
    broker.begin(TurnCapability(str(uuid4())))
    assert await broker.search('old topic')==['lexical']
    assert store.search.await_args.kwargs['vector'] is None

@pytest.mark.asyncio
async def test_learning_preemption_revokes_before_cancel_and_never_checkpoints():
    entered=asyncio.Event()
    async def turn(*args):entered.set();await asyncio.Event().wait()
    store=SimpleNamespace(maintenance_prepare=AsyncMock(return_value={'id':str(uuid4()),'kind':'incremental','messages':[]}),
        memory_list=AsyncMock(return_value=[]),maintenance_finish=AsyncMock(),maintenance_end=AsyncMock())
    broker=ToolBroker(store,None)
    worker=SimpleNamespace(store=store,broker=broker,brain=SimpleNamespace(turn=turn),
        start=AsyncMock(),close=AsyncMock())
    cfg=SimpleNamespace(consolidation_enabled=False,consolidation_interval=60,maintenance_timeout=10)
    service=Maintenance(cfg,AsyncMock(return_value=worker),asyncio.Semaphore(1),lambda:False)
    task=asyncio.create_task(service.run(None))
    await entered.wait()
    service.preempt()
    assert broker.capability is None
    await task
    store.maintenance_finish.assert_not_awaited()
    worker.close.assert_awaited_once()
    assert service.task is None and service.worker is None

@pytest.mark.asyncio
async def test_successful_no_change_can_checkpoint_without_note_writes():
    store=SimpleNamespace(maintenance_prepare=AsyncMock(return_value={'id':str(uuid4()),'kind':'incremental','messages':[]}),
        memory_list=AsyncMock(return_value=[]),maintenance_finish=AsyncMock(),maintenance_end=AsyncMock())
    broker=ToolBroker(store,None)
    worker=SimpleNamespace(store=store,broker=broker,brain=SimpleNamespace(turn=AsyncMock(return_value=Outcome('completed','skip','nothing new'))),start=AsyncMock(),close=AsyncMock())
    cfg=SimpleNamespace(consolidation_enabled=False,consolidation_interval=60,maintenance_timeout=10)
    service=Maintenance(cfg,AsyncMock(return_value=worker),asyncio.Semaphore(1),lambda:False)
    await service.run(None)
    store.maintenance_finish.assert_awaited_once()
    assert broker.capability is None

@pytest.mark.asyncio
async def test_late_source_read_does_not_authorize_next_turn():
    entered=asyncio.Event();release=asyncio.Event()
    async def history(ids):entered.set();await release.wait();return [{'id':ids[0]}]
    broker=ToolBroker(SimpleNamespace(history_get=history),None)
    broker.begin(TurnCapability(str(uuid4())))
    task=asyncio.create_task(broker.history_get(['old']))
    await entered.wait();broker.end();broker.begin(TurnCapability(str(uuid4())))
    release.set()
    with pytest.raises(PermissionError):await task
    assert not broker._checked_sources

def test_context_budget_includes_recalled_notes_and_small_overview():
    from brodar.learning import bounded_notes
    notes=[{'path':'context/a.md','content':'a'*100000} for _ in range(20)]
    notes.append({'path':'context/overview.md','content':'index'*10000})
    result=bounded_notes(notes)
    assert result[0]['path']=='context/overview.md'
    assert len(result[0]['content'])==2000
    assert sum(len(r['content']) for r in result)<=14000
    assert all(r['truncated'] for r in result)
