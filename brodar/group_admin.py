"""Typed Telegram admin operations tied to exact fresh human commands and live rights."""
import json,re

# API methods and permitted arguments. No arbitrary Telegram API dispatcher.
ACTIONS={
 'pin':('pin_chat_message','can_pin_messages',{'message_id','disable_notification'}),
 'unpin':('unpin_chat_message','can_pin_messages',{'message_id'}),
 'unpin_all':('unpin_all_chat_messages','can_pin_messages',set()),
 'unban':('unban_chat_member','can_restrict_members',{'user_id','only_if_banned'}),
 'unmute':('restrict_chat_member','can_restrict_members',{'user_id'}),
 'set_title':('set_chat_title','can_change_info',{'title'}),
 'delete_photo':('delete_chat_photo','can_change_info',set()),
 'set_photo':('set_chat_photo','can_change_info',{'artifact_id'}),
 'set_description':('set_chat_description','can_change_info',{'description'}),
 'set_permissions':('set_chat_permissions','can_restrict_members',{'permissions','use_independent_chat_permissions'}),
 'promote':('promote_chat_member','can_promote_members',{'user_id','is_anonymous','can_manage_chat','can_delete_messages','can_manage_video_chats','can_restrict_members','can_promote_members','can_change_info','can_invite_users','can_pin_messages','can_manage_topics'}),
 'admin_title':('set_chat_administrator_custom_title','can_promote_members',{'user_id','custom_title'}),
 'approve_join':('approve_chat_join_request','can_invite_users',{'user_id'}),
 'decline_join':('decline_chat_join_request','can_invite_users',{'user_id'}),
 'create_invite':('create_chat_invite_link','can_invite_users',{'name','expire_date','member_limit','creates_join_request'}),
 'revoke_invite':('revoke_chat_invite_link','can_invite_users',{'invite_link'}),
 'create_topic':('create_forum_topic','can_manage_topics',{'name','icon_color','icon_custom_emoji_id'}),
 'close_topic':('close_forum_topic','can_manage_topics',{'message_thread_id'}),
 'reopen_topic':('reopen_forum_topic','can_manage_topics',{'message_thread_id'}),
 'delete_topic':('delete_forum_topic','can_manage_topics',{'message_thread_id'}),
}

def command_parameters(text,action,parameters,username=None,reply_to=None):
    if action not in ACTIONS:raise ValueError('Unsupported admin action')
    match=re.fullmatch(r'/([a-z_]+)(?:@([A-Za-z0-9_]+))?(?:\s+([\s\S]+))?',text.strip())
    if not match or match[1]!=action or (match[2] and match[2].lower()!=(username or '').lower()):
        raise PermissionError('Use a fresh explicit /action command from a Telegram admin')
    raw=match[3] or ''
    if raw.startswith('{'):
        expected=json.loads(raw)
    elif action in {'pin','unpin'}:
        if raw:expected={'message_id':int(raw)}
        elif reply_to:expected={'message_id':reply_to}
        elif action=='unpin':expected={}
        else:raise ValueError('Reply to a message with /pin or provide its numeric message ID')
    elif action in {'unban','unmute','approve_join','decline_join'}:expected={'user_id':int(raw)}
    elif action in {'set_title','set_description','create_topic'}:expected={{'set_title':'title','set_description':'description','create_topic':'name'}[action]:raw}
    elif not raw:expected={}
    else:raise ValueError('Use JSON parameters for this action')
    if expected!=parameters:raise PermissionError('Action parameters differ from the fresh human command')
    if set(parameters)-ACTIONS[action][2]:raise ValueError('Unsupported action parameters')

async def perform(broker,request_id,action,parameters,operation):
    cap=broker.require(visible=True);request=broker.fresh_request(request_id)
    scope=broker.store.scope
    if scope.chat_id>=0:raise PermissionError('Admin actions require a group')
    command_parameters(request.text,action,parameters,getattr(broker.bot,'username',None),getattr(request,'reply_to',None))
    method,right,_=ACTIONS[action]
    actor=await broker.bot.get_chat_member(scope.chat_id,request.actor)
    own=await broker.bot.get_chat_member(scope.chat_id,scope.bot_id)
    def permitted(member):return member.status=='creator' or (member.status=='administrator' and bool(getattr(member,right,False)))
    if not permitted(actor) or not permitted(own):raise PermissionError('Requester and bot need the current Telegram admin right: '+right)
    kwargs=dict(parameters)
    from telegram import ChatPermissions
    if action=='unmute':
        chat=await broker.bot.get_chat(scope.chat_id)
        kwargs['permissions']=chat.permissions or ChatPermissions.all_permissions()
        kwargs['use_independent_chat_permissions']=True
    if action=='set_photo':
        if broker.artifacts is None:raise RuntimeError('Artifact storage unavailable')
        row,body=await broker.artifacts.read(kwargs.pop('artifact_id'))
        if row['mime_type'] not in {'image/jpeg','image/png'} or len(body)>10_000_000:raise ValueError('Use a JPEG/PNG artifact under 10 MB')
        from telegram import InputFile
        kwargs['photo']=InputFile(body,filename=row['filename'])
    if action=='set_permissions':kwargs['permissions']=ChatPermissions.de_json(parameters['permissions'],broker.bot)
    async def send():return await getattr(broker.bot,method)(chat_id=scope.chat_id,**kwargs)
    async def preflight():
        current=broker.fresh_request(request_id)
        if current is not request:raise PermissionError('Admin request withdrawn')
    # Some APIs return objects without message_id; normalize to True and expose public result separately.
    outcome={}
    async def normalized():
        value=await send()
        if value is False:return False
        if hasattr(value,'to_dict'):outcome.update(value.to_dict())
        return True
    receipt=await broker.deliver(cap,'group_admin',{'action':action,'parameters':parameters,'request_id':request_id},operation,normalized,preflight=preflight)
    return {'receipt':receipt,'result':outcome}

async def info(broker,kind,user_id=None,offset=0,limit=50):
    broker.require();scope=broker.store.scope
    if scope.chat_id>=0:raise PermissionError('Group information requires a group')
    if kind=='chat':
        chat=await broker.bot.get_chat(scope.chat_id)
        return {'id':chat.id,'title':chat.title,'type':chat.type,'description':chat.description,
            'member_count':await broker.bot.get_chat_member_count(scope.chat_id)}
    def public(member):
        user=member.user
        return {'user_id':user.id,'name':user.full_name,'username':user.username,'is_bot':user.is_bot,'status':member.status,
            'rights':{k:v for k,v in member.to_dict().items() if k.startswith('can_')}}
    if kind=='admins':return [public(m) for m in await broker.bot.get_chat_administrators(scope.chat_id,api_kwargs={'return_bots':True})]
    if kind=='member':
        if user_id is None:raise ValueError('user_id required')
        return public(await broker.bot.get_chat_member(scope.chat_id,user_id))
    if kind!='observed':raise ValueError('Use chat, admins, member or observed')
    if offset<0:raise ValueError('Invalid offset')
    def read():
        with broker.store.db.transaction(scope.audience,readonly=True) as c:
            c.execute('SELECT DISTINCT ON(sender_id) sender_id,sender_name FROM brodar_v2.messages WHERE audience=%s AND sender_id IS NOT NULL ORDER BY sender_id,received_at DESC LIMIT %s OFFSET %s',(scope.audience,max(1,min(limit,100)),offset))
            return [{'user_id':r[0],'last_observed_name':r[1]} for r in c.fetchall()]
    return {'observed_members':await broker.store.db.run(read),'complete_roster':False,
        'telegram_member_count':await broker.bot.get_chat_member_count(scope.chat_id),'offset':offset}
