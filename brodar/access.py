"""Host-only Telegram admission; usernames/admin roles never establish ownership."""
from .telegram_context import ordinary_message

class ChatAccess:
    def __init__(self,cfg,db):
        self.cfg,self.db=cfg,db
        self.groups={chat for chat in cfg.allowed_chats if chat<0}
        self.audience='access:'+cfg.instance_id

    def audiences(self):return self.groups|set(self.cfg.owner_ids)

    def accepts(self,message):
        if not ordinary_message(message):return False
        chat=message.get('chat') or {};sender=message.get('from') or {}
        if chat.get('type')=='private':
            return not message.get('sender_chat') and not sender.get('is_bot') and sender.get('id') in self.cfg.owner_ids and chat.get('id')==sender.get('id')
        return chat.get('type') in {'group','supergroup'} and chat.get('id') in self.groups

    def approval_command(self,raw,username):
        message=raw.get('message') or {}
        chat=message.get('chat') or {};sender=message.get('from') or {}
        text=(message.get('text') or '').strip()
        valid={'/approve'}
        if username:valid.add('/approve@'+username.lower())
        return bool(ordinary_message(message) and chat.get('type') in {'group','supergroup'} and
            text.lower() in valid and sender.get('id') in self.cfg.owner_ids and not sender.get('is_bot') and
            not message.get('sender_chat') and not message.get('edit_date') and
            not any(message.get(k) for k in ('forward_origin','forward_from','forward_sender_name')))

    async def load(self):
        def read():
            with self.db.transaction(self.audience) as c:
                c.execute('INSERT INTO brodar_v2.audience_registry(audience,instance_id,chat_id) VALUES (%s,%s,0) ON CONFLICT DO NOTHING',(self.audience,self.cfg.instance_id))
                c.execute('SELECT chat_id FROM brodar_v2.group_access WHERE audience=%s',(self.audience,))
                return [row[0] for row in c.fetchall()]
        self.groups.update(await self.db.run(read))

    async def approve(self,raw,username):
        # Authenticate again here so a caller cannot bypass the dispatch check.
        if not self.approval_command(raw,username):raise PermissionError('Only a verified owner group /approve can enable a group')
        message=raw['message'];chat_id=message['chat']['id'];owner=message['from']['id']
        def save():
            with self.db.transaction(self.audience) as c:
                c.execute('INSERT INTO brodar_v2.group_access(audience,chat_id,approved_by) VALUES (%s,%s,%s) ON CONFLICT(audience,chat_id) DO NOTHING',
                    (self.audience,chat_id,owner))
        await self.db.run(save)
        self.groups.add(chat_id)
