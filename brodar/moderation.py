"""Bind moderation to fresh Telegram requests and current platform rights."""

import re
from dataclasses import dataclass
from datetime import datetime,timedelta,timezone

from telegram import ChatPermissions


@dataclass(frozen=True)
class Request:
    message_id:str
    actor_id:int
    action:str
    target:int
    minutes:int|None=None


def requests(batch,bot_username=None):
    found=[]
    latest={str(row['message_id']):row for row in batch}
    for row in latest.values():
        message=row['payload'].get('message') or row['payload'].get('edited_message') or {}
        actor=message.get('from') or {}
        if (message.get('sender_chat') or message.get('forward_origin') or
                message.get('forward_from') or not actor.get('id') or actor.get('is_bot')):continue
        text=message.get('text','').strip()
        match=re.fullmatch(r'/?(ban|mute|delete)(?:@([A-Za-z0-9_]+))?(?:\s+(\d+))?(?:\s+(\d+))?',text,re.I)
        if not match:continue
        action,recipient,number,duration=match.groups();action=action.lower()
        if recipient and (not bot_username or recipient.lower()!=bot_username.lower()):continue
        reply=message.get('reply_to_message') or {}
        if action=='delete':
            target=int(number) if number else reply.get('message_id')
            if duration:continue
            minutes=None
        else:
            reply_actor=reply.get('from') or {}
            if reply.get('sender_chat'):continue
            if action=='mute' and reply_actor.get('id') and number and not duration:
                target=reply_actor['id'];minutes=int(number)
            else:
                target=int(number) if number else reply_actor.get('id')
                minutes=int(duration) if duration else (10 if action=='mute' else None)
            if action=='ban' and duration:continue
            if minutes is not None and not 1<=minutes<=1440:continue
        if not target:continue
        found.append(Request(str(row['message_id']),int(actor['id']),action,int(target),minutes))
    return found


async def authorize(bot,scope,request):
    if scope.chat_id>=0:raise PermissionError('Moderation requires a Telegram group')
    actor,own=await bot.get_chat_member(scope.chat_id,request.actor_id),await bot.get_chat_member(scope.chat_id,scope.bot_id)
    right='can_delete_messages' if request.action=='delete' else 'can_restrict_members'
    def permitted(member):
        return member.status=='creator' or (member.status=='administrator' and bool(getattr(member,right,False)))
    if not permitted(actor):raise PermissionError('The current requester lacks the required Telegram admin right')
    if not permitted(own):raise PermissionError('Brodar lacks the required Telegram admin right')
    if request.action!='delete':
        if request.target in {scope.bot_id,request.actor_id}:raise PermissionError('This request cannot target the bot or requester')
        target=await bot.get_chat_member(scope.chat_id,request.target)
        if target.status in {'creator','administrator'}:raise PermissionError('Cannot moderate another administrator')


async def perform(bot,scope,request):
    if request.action=='delete':return await bot.delete_message(chat_id=scope.chat_id,message_id=request.target)
    if request.action=='ban':return await bot.ban_chat_member(chat_id=scope.chat_id,user_id=request.target,revoke_messages=False)
    return await bot.restrict_chat_member(chat_id=scope.chat_id,user_id=request.target,
        permissions=ChatPermissions(can_send_messages=False),use_independent_chat_permissions=True,
        until_date=datetime.now(timezone.utc)+timedelta(minutes=request.minutes))
