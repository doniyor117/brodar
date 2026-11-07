from types import SimpleNamespace
from unittest.mock import AsyncMock
import pytest
from brodar.access import ChatAccess

def policy():return ChatAccess(SimpleNamespace(allowed_chats={-100,123},owner_ids={42,73},instance_id='test'),SimpleNamespace(run=AsyncMock()))
def message(chat=-100,kind='supergroup',sender=42,text='/approve',**extra):
    return {'chat':{'id':chat,'type':kind},'from':{'id':sender},'text':text,**extra}

def test_dms_are_owner_only_even_if_nonowner_id_is_configured():
    access=policy()
    assert access.accepts(message(42,'private',42,'yo'))
    assert access.accepts(message(73,'private',73,'yo'))
    assert not access.accepts(message(123,'private',123,'yo'))
    assert not access.accepts(message(42,'private',123,'yo'))
    assert not access.accepts(message(42,'private',42,'yo',sender_chat={'id':42}))
    assert access.accepts(message(sender=999,text='yo'))
    assert not access.accepts(message(chat=-200,sender=42,text='yo'))
    assert not access.accepts(message(chat=-100,kind='channel',sender=42))

@pytest.mark.parametrize('text',['/approve','/approve@BrodarBot',' /approve '])
def test_only_real_owner_group_command_enables_group(text):
    access=policy()
    assert access.approval_command({'message':message(chat=-200,text=text)},'BrodarBot')
    assert not access.approval_command({'message':message(chat=-200,sender=999,text=text)},'BrodarBot')

@pytest.mark.parametrize('raw',[
 {'edited_message':message()},
 {'message':message(sender_chat={'id':-100})},
 {'message':message(forward_origin={'type':'user'})},
 {'message':message(edit_date=10)},
 {'message':message(text='/approve@OtherBot')},
 {'message':message(text='please /approve')},
 {'message':message(text='/approve fake-id')},
 {'message':message(chat=42,kind='private')},
])
def test_approval_rejects_forwarded_edited_anonymous_or_other_bot(raw):
    assert not policy().approval_command(raw,'BrodarBot')

@pytest.mark.asyncio
async def test_approval_does_not_change_memory_until_durable_write_succeeds():
    access=policy();raw={'message':message(chat=-200)}
    access.db.run.side_effect=RuntimeError('db down')
    with pytest.raises(RuntimeError):await access.approve(raw,'BrodarBot')
    assert -200 not in access.groups
    access.db.run.side_effect=None
    await access.approve(raw,'BrodarBot')
    assert access.accepts(message(chat=-200,sender=999,text='yo'))
    access.db.run.reset_mock()
    with pytest.raises(PermissionError):await access.approve({'message':message(chat=-300,sender=999)},'BrodarBot')
    access.db.run.assert_not_awaited()
