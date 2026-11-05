from telegram import Update

from brodar.telegram_context import ordinary_message,public_payload


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
