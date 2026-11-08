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


@pytest.mark.asyncio
async def test_recovered_and_ambient_inputs_gain_feedback_without_stealing_new_work():
    presence=TypingPresence(SimpleNamespace(send_chat_action=lambda **kw: asyncio.sleep(0)))
    scope=Scope(99,-100)
    recovered=presence.claim(scope,[10])
    assert recovered==1 and presence.for_inputs(scope,[10])==1
    await presence.stop(scope,generation=recovered)
    ambient=presence.claim(scope,[11])
    assert ambient==2
    await presence.stop(scope,generation=ambient)
    presence.start(scope,new_work=True,update_id=13)
    assert presence.claim(scope,[12])==0
    await presence.stop(scope,generation=0)
    assert scope.conversation in presence.tasks
    await presence.close()
