import asyncio
from types import SimpleNamespace

import pytest

from brodar.presence import TypingPresence
from brodar.scope import Scope
from brodar.telegram_context import addressed


@pytest.mark.parametrize('text',['brodar','hey samy','simon?','brodar simon yo','simon bro','SAMY wtf'])
def test_natural_names_are_addressed_without_telegram_mentions(text):
    assert addressed({'chat':{'type':'supergroup'},'text':text},99,'BrodarBot')


@pytest.mark.parametrize('text',['simonov','brodarov','random conversation'])
def test_unrelated_words_are_ambient_not_name_calls(text):
    assert not addressed({'chat':{'type':'supergroup'},'text':text},99,'BrodarBot')


def test_mentions_replies_and_dms_still_have_priority():
    assert addressed({'chat':{'type':'private'},'text':'yo'},99,'BrodarBot')
    assert addressed({'text':'@BrodarBot hey'},99,'BrodarBot')
    assert addressed({'reply_to_message':{'from':{'id':99}},'text':'explain'},99,'BrodarBot')


@pytest.mark.asyncio
async def test_typing_refreshes_in_correct_topic_and_stops_without_a_chat_message():
    calls=[];refreshed=asyncio.Event()
    async def action(**kwargs):
        calls.append(kwargs)
        if len(calls)>=2:refreshed.set()
    presence=TypingPresence(SimpleNamespace(send_chat_action=action),interval=.01)
    scope=Scope(99,-100,12)
    presence.start(scope);presence.start(scope)
    await asyncio.wait_for(refreshed.wait(),1)
    await presence.stop(scope)
    count=len(calls);await asyncio.sleep(.03)
    assert count==len(calls) and not presence.tasks
    assert all(call=={'chat_id':-100,'message_thread_id':12,'action':'typing'} for call in calls)
