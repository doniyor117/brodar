"""Visibility checks use API fields, including fields newer than the SDK."""

import re

_NAMES=re.compile(r'(?<!\w)(?:brodar(?:\s+simon)?|simon(?:\s+bro)?|samy)(?!\w)',re.I)


def addressed(message,bot_id,username):
    text=message.get('text') or message.get('caption') or ''
    mention=bool(username and re.search(r'@'+re.escape(username)+r'(?!\w)',text,re.I))
    reply=(message.get('reply_to_message') or {}).get('from') or {}
    return bool((message.get('chat') or {}).get('type')=='private' or mention or
                reply.get('id')==bot_id or _NAMES.search(text))


def ordinary_message(message):
    # Recipient-only, business and guest contexts can share a numeric chat ID
    # with a public bot conversation. They need their own audience mapping.
    return not (message.get('receiver_user') is not None or 'ephemeral_message_id' in message or
                message.get('guest_query_id') or message.get('business_connection_id'))


def public_payload(value):
    if isinstance(value,list):return [public_payload(item) for item in value]
    if not isinstance(value,dict):return value
    if not ordinary_message(value):return {'private_context_omitted':True}
    return {key:public_payload(item) for key,item in value.items()}


def describe_message(message):
    """Return useful text and whether this is a service event, not a chat turn."""
    reference=''
    for kind in ['photo','document','sticker','voice','audio','video','video_note','animation']:
        item=message.get(kind)
        if isinstance(item,list):item=item[-1] if item else None
        if isinstance(item,dict) and item.get('file_id'):
            reference='\n[Telegram '+kind+'; file_id: '+item['file_id']
            if item.get('file_size') is not None:reference+='; bytes: '+str(item['file_size'])
            reference+='; import with telegram_media_lookup(kind=import_file) when needed]'
            break
    if message.get('text') is not None:return message['text']+reference,False
    if message.get('caption') is not None:return message['caption']+reference,False
    if message.get('photo'):return '[photo received]'+reference,False
    if message.get('document'):return '[document received: '+message['document'].get('file_name','document')+']'+reference,False
    if message.get('sticker'):return '[sticker: '+message['sticker'].get('emoji','no emoji metadata')+']'+reference,False
    if message.get('poll'):
        poll=message['poll'];return '[poll: '+poll.get('question','')+'; '+', '.join(option.get('text','') for option in poll.get('options',[]))+']',False
    if message.get('dice'):return '[dice: '+str(message['dice'].get('emoji',''))+' '+str(message['dice'].get('value',''))+']',False
    for kind in ['voice','audio','video','video_note','animation']:
        if message.get(kind):return '['+kind+' received; no transcript or visual interpretation available]'+reference,False
    for kind in ['contact','location','venue']:
        if message.get(kind):return '['+kind+' shared: '+str(message[kind])+']',False
    fields=['new_chat_members','left_chat_member','new_chat_title','new_chat_photo','delete_chat_photo',
            'group_chat_created','supergroup_chat_created','channel_chat_created','migrate_to_chat_id','migrate_from_chat_id',
            'pinned_message','forum_topic_created','forum_topic_edited','forum_topic_closed','forum_topic_reopened',
            'video_chat_started','video_chat_ended','video_chat_scheduled','message_auto_delete_timer_changed']
    active=[key for key in fields if message.get(key)]
    return '[telegram service event: '+(', '.join(active) or 'message without text/media')+']',True
