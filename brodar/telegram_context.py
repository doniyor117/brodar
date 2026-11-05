"""Visibility checks use API fields, including fields newer than the SDK."""


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
