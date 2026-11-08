from telegram import Update

from brodar.telegram_context import ordinary_message,public_payload,describe_message
from brodar.telegram_context import topic_id


def test_reply_chain_id_does_not_split_ordinary_group_or_route_typing_into_it():
    raw={'update_id':1,'message':{'message_id':20,'date':1,'message_thread_id':12,
        'chat':{'id':-100,'type':'supergroup'},'text':'try again',
        'reply_to_message':{'message_id':12,'date':1,'chat':{'id':-100,'type':'supergroup'},'text':'hello'}}}
    message=Update.de_json(raw,None).to_dict()['message']
    assert message['message_thread_id']==12
    assert topic_id(message)==0
    assert topic_id(dict(message,is_topic_message=True))==12
    assert topic_id(dict(message,chat={'type':'supergroup','is_forum':True}))==12
    assert topic_id({'chat':{'type':'private'},'message_thread_id':12})==0
    assert topic_id({'chat':{'type':'private','is_forum':True},'message_thread_id':12})==12


def test_new_sdk_unknown_recipient_fields_do_not_become_public_context():
    raw={'update_id':1,'message':{'message_id':0,'date':1,'chat':{'id':-100,'type':'supergroup'},
        'text':'private text','receiver_user':{'id':42,'is_bot':False,'first_name':'test'},'ephemeral_message_id':8}}
    update=Update.de_json(raw,None)
    assert not ordinary_message(update.to_dict()['message'])
    assert 'private text' not in str(public_payload(update.to_dict()))


def test_private_reply_payload_is_omitted_but_normal_text_remains():
    raw={'message':{'text':'ordinary public message','reply_to_message':{
        'text':'private reply text','ephemeral_message_id':8}}}
    safe=public_payload(raw)
    assert safe['message']['text']=='ordinary public message'
    assert 'private reply text' not in str(safe)


def test_service_events_do_not_masquerade_as_unreadable_attachments():
    text,service=describe_message({'delete_chat_photo':True})
    assert service and 'delete_chat_photo' in text and 'attachment' not in text
    assert describe_message({'text':'yo','group_chat_created':False})==('yo',False)
    assert describe_message({'sticker':{'emoji':'💀'},'delete_chat_photo':False})==('[sticker: 💀]',False)


def test_captioned_media_keeps_safe_file_reference_for_lazy_import():
    text,service=describe_message({'caption':'look at this','photo':[{'file_id':'actualCaseSensitiveID','file_size':12345}]})
    assert text.startswith('look at this') and 'actualCaseSensitiveID' in text and not service
    assert 'bytes: 12345' in text and 'import_file' in text
