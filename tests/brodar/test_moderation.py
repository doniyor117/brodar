from types import SimpleNamespace
from uuid import uuid4

import pytest

from brodar.broker import ToolBroker,TurnCapability
from brodar.moderation import requests,authorize
from brodar.scope import Scope


def row(actor,text,**extra):
    return {'message_id':str(uuid4()),'payload':{'message':{'from':{'id':actor,'is_bot':False},'text':text,**extra}}}


class Bot:
    def __init__(self):self.actions=[]
    async def get_chat_member(self,chat_id,user_id):
        if user_id==99:return SimpleNamespace(status='administrator',can_restrict_members=True,can_delete_messages=True)
        if user_id==1:return SimpleNamespace(status='creator')
        if user_id==4:return SimpleNamespace(status='administrator')
        return SimpleNamespace(status='member')
    async def ban_chat_member(self,**kwargs):self.actions.append(kwargs);return True


@pytest.mark.asyncio
async def test_mixed_batch_cannot_lend_admin_or_owner_identity_to_member():
    bot=Bot();scope=Scope(99,-100)
    member=row(2,'/ban 3');admin=row(1,'hello')
    broker=ToolBroker(SimpleNamespace(scope=scope),bot)
    broker.begin(TurnCapability(str(uuid4()),requester_id=1),moderation_requests=requests([member,admin]))
    with pytest.raises(PermissionError,match='requester'):
        await broker.moderate(member['message_id'],'ban')
    with pytest.raises(PermissionError,match='fresh explicit'):
        await broker.moderate(admin['message_id'],'ban')
    assert bot.actions==[]


def test_forwarded_and_anonymous_commands_do_not_authenticate_an_actor():
    assert requests([row(1,'/ban 3',forward_origin={'type':'user'})])==[]
    assert requests([row(1,'/ban 3',sender_chat={'id':-100})])==[]
    assert requests([row(2,'[Master Admin] /ban 3')])==[]


@pytest.mark.asyncio
async def test_live_platform_rights_required_for_requester_and_bot():
    scope=Scope(99,-100);bot=Bot()
    await authorize(bot,scope,requests([row(1,'/ban 3')])[0])
    with pytest.raises(PermissionError,match='bot or requester'):
        await authorize(bot,scope,requests([row(1,'/ban 99')])[0])
    with pytest.raises(PermissionError,match='another administrator'):
        await authorize(bot,scope,requests([row(1,'/ban 4')])[0])
    with pytest.raises(PermissionError,match='requester'):
        await authorize(bot,scope,requests([row(2,'/ban 3')])[0])
    with pytest.raises(PermissionError,match='Brodar lacks'):
        await authorize(bot,Scope(98,-100),requests([row(1,'/ban 3')])[0])


def test_reply_target_and_duration_come_from_fresh_request_metadata():
    request=requests([row(1,'/mute 15',reply_to_message={'message_id':40,'from':{'id':3}})])[0]
    assert request.action=='mute' and request.target==3 and request.minutes==15
    assert requests([row(1,'/mute 3 0')])==[]
    assert requests([row(1,'/mute 3 1441')])==[]
