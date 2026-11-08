import asyncio
from types import SimpleNamespace
import pytest
from brodar.brains.codex import CodexBrain
from brodar.session_commands import SessionCommands,format_status,status_snapshot,telegram_command
from test_codex import FakeRpc


def brain(tmp_path,rpc=FakeRpc):
    return CodexBrain(home=tmp_path/'home',cwd=tmp_path/'work',model='model',effort='low',instructions='fixed',transport_factory=rpc)


def test_usage_is_last_request_not_cumulative_and_no_private_ids(tmp_path):
    b=brain(tmp_path);b.session_id='private-session'
    b._notification('thread/tokenUsage/updated',{'threadId':'other','tokenUsage':{'last':{'totalTokens':9}}})
    assert b.token_usage is None
    b._notification('thread/tokenUsage/updated',{'threadId':'private-session','tokenUsage':{'last':{'totalTokens':1200},'total':{'totalTokens':90000},'modelContextWindow':10000}})
    value=status_snapshot(b,target='groups',provider='codex',model='model',threshold=8000)
    assert value['context_tokens_estimate']==1200 and value['remaining_tokens_estimate']==8800
    assert value['used_percent_estimate']==12
    assert 'private-session' not in str(value)
    assert '1,200 / 10,000' in format_status(value)


@pytest.mark.asyncio
async def test_compact_ack_is_not_completion_and_other_turn_cannot_start(tmp_path):
    b=brain(tmp_path);await b.start()
    task=asyncio.create_task(b.compact())
    await asyncio.sleep(0)
    assert not task.done()
    assert b.rpc.requests[-1]==('thread/compact/start',{'threadId':'thread'})
    with pytest.raises(RuntimeError):await b.turn('must wait')
    b._notification('item/completed',{'threadId':'thread','item':{'type':'contextCompaction'}})
    assert not task.done()
    b._notification('turn/completed',{'threadId':'thread','turn':{'id':'compact','status':'completed'}})
    await task
    assert b.compactions==1 and b._compact_waiter is None


@pytest.mark.asyncio
async def test_compact_failure_recovers_session_without_model_turn(tmp_path):
    b=brain(tmp_path);await b.start()
    task=asyncio.create_task(b.compact());await asyncio.sleep(0)
    b._notification('turn/completed',{'threadId':'thread','turn':{'id':'compact','status':'failed'}})
    with pytest.raises(RuntimeError):await task
    assert any(m=='thread/resume' for m,p in b.rpc.requests)
    assert not any(m=='turn/start' for m,p in b.rpc.requests)


@pytest.mark.asyncio
async def test_queue_serializes_with_foreground_and_notifies_after_completion():
    lock=asyncio.Lock();await lock.acquire();called=[];messages=[]
    async def compact():called.append(True)
    b=SimpleNamespace(compact=compact)
    cfg=SimpleNamespace(brain='codex',model='model',auto_compact_token_limit=8000)
    commands=SessionCommands(lambda target:(SimpleNamespace(brain=b),lock),cfg)
    async def notify(text):messages.append(text)
    assert commands.queue('groups',notify)['queued']
    assert not commands.queue('groups')['queued']
    await asyncio.sleep(0);assert not called
    lock.release();await asyncio.gather(*commands.tasks.values())
    assert called and messages and 'completed' in messages[0]
    assert not commands.tasks and commands.status('groups')['compaction_status']=='completed'
    await commands.close()


def test_commands_match_this_bot_and_never_replay_forwarded_edited_bot_posts():
    base={'text':'/compact@MyBot','from':{'id':1}}
    assert telegram_command(base,'MyBot')=='compact'
    for extra in ({'edit_date':1},{'forward_origin':{'type':'user'}},{'sender_chat':{'id':-1}},{'from':{'id':2,'is_bot':True}},{'text':'/compact@other'},{'text':'/status more'}):
        assert telegram_command(dict(base,**extra),'MyBot') is None


def test_unknown_usage_and_unsupported_provider_are_honest():
    cfg=SimpleNamespace(brain='claude',model='m',auto_compact_token_limit=None)
    commands=SessionCommands(lambda target:(None,None),cfg)
    value=commands.status('dm')
    assert value['context_tokens_estimate'] is None
    assert not value['compaction_supported']
    assert 'unknown' in format_status(value)
    assert not commands.queue('dm')['queued']
