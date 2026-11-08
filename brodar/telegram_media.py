"""Native Telegram media and ordinary chat actions, using scoped receipts."""
from __future__ import annotations

import math
import re
import asyncio
import io
import secrets
import time
from collections import OrderedDict
from uuid import UUID
from urllib.parse import urlsplit

from telegram import (InputFile,InputMediaPhoto,InputMediaVideo,InputMediaAudio,
                      InputMediaDocument,InlineKeyboardButton,InlineKeyboardMarkup)

from .redaction import scrub,scrub_object
from .write_fence import WRITE_FENCE

KINDS={'photo','video','animation','audio','voice','video_note','sticker','document'}
CAPTIONED=KINDS-{'video_note','sticker'}
MEDIA_OPTIONS={
    'photo':{'has_spoiler','show_caption_above_media'},
    'video':{'has_spoiler','show_caption_above_media','supports_streaming','duration','width','height'},
    'animation':{'has_spoiler','show_caption_above_media','duration','width','height'},
    'audio':{'duration','performer','title'},'voice':{'duration'},
    'video_note':{'duration','length'},'sticker':{'emoji'},'document':{'disable_content_type_detection'},
}
COMMON={'disable_notification','protect_content','parse_mode'}


def sticker_set_name(identifier):
    if identifier.startswith('https://t.me/addstickers/'):
        identifier=urlsplit(identifier).path.rsplit('/',1)[-1]
    if not re.fullmatch(r'[A-Za-z0-9_]{1,120}',identifier):raise ValueError('Use an actual sticker set name')
    return identifier


async def sticker_preview(broker,identifier,offset=0):
    """Return a transient numbered contact sheet and its exact file-ID mapping."""
    cap=broker.require()
    if offset<0:raise ValueError('Sticker offset must be nonnegative')
    pack=await broker.bot.get_sticker_set(sticker_set_name(identifier))
    selected=pack.stickers[offset:offset+20]
    cache=getattr(broker,'_sticker_cache',None)
    if cache is None:cache=broker._sticker_cache=OrderedDict()
    now=time.monotonic()
    for key in list(cache):
        if cache[key]['expires']<now:del cache[key]
    preview_id=secrets.token_urlsafe(9)
    if not selected:raise ValueError('No stickers at this offset')
    semaphore=asyncio.Semaphore(4)

    def check():
        fence=WRITE_FENCE.get()
        if fence:fence()
        if broker.capability is not cap:raise PermissionError('Sticker preview turn ended')

    async def fetch(index,sticker):
        ref=secrets.token_urlsafe(9)
        entry={'index':index,'file_id':sticker.file_id,'emoji':sticker.emoji,
               'selection_ref':ref,'preview_id':preview_id,'file_unique_id':getattr(sticker,'file_unique_id',''),
               'preview':'thumbnail' if sticker.is_animated or sticker.is_video else 'artwork'}
        cache[ref]={'entry':entry,'name':pack.name,'sticker':sticker,'expires':now+86400,
                    'audience':broker.store.scope.audience,'snapshot':tuple(s.file_id for s in pack.stickers)}
        while len(cache)>2560:cache.popitem(last=False)
        target=getattr(sticker,'thumbnail',None) if sticker.is_animated or sticker.is_video else sticker
        if target is None:return dict(entry,preview='unavailable'),None
        async with semaphore:
            check()
            try:
                async with asyncio.timeout(15):
                    file=await broker.bot.get_file(target.file_id)
                    if (file.file_size or 0)>2_000_000:raise ValueError('Preview exceeds 2 MB')
                    body=bytes(await file.download_as_bytearray())
                    if len(body)>2_000_000:raise ValueError('Preview exceeds 2 MB')
            except Exception as exc:
                check()
                return dict(entry,preview='unavailable',error=type(exc).__name__),None
            check()
            return entry,body

    async with asyncio.timeout(60):
        images=await asyncio.gather(*(fetch(offset+i+1,s) for i,s in enumerate(selected)))
    check()

    def render():
        from PIL import Image,ImageDraw
        columns=min(4,len(images));cell_w,cell_h=360,392
        sheet=Image.new('RGB',(columns*cell_w,math.ceil(len(images)/columns)*cell_h),'#e4e4e4')
        draw=ImageDraw.Draw(sheet)
        for i,(entry,body) in enumerate(images):
            x,y=(i%columns)*cell_w,(i//columns)*cell_h
            draw.rectangle((x+2,y+2,x+cell_w-3,y+cell_h-3),outline='#999999',width=2)
            if body:
                try:
                    with Image.open(io.BytesIO(body)) as image:
                        if image.width*image.height>4_000_000:raise ValueError('Preview dimensions too large')
                        image=image.convert('RGBA');image.thumbnail((cell_w-16,cell_h-44))
                        sheet.paste(image,(x+(cell_w-image.width)//2,y+8+(cell_h-44-image.height)//2),image)
                except Exception:
                    entry['preview']='unavailable'
            if entry['preview']=='unavailable':draw.text((x+16,y+150),'preview unavailable',fill='black',font_size=20)
            label=str(entry['index'])+(' (thumbnail)' if entry['preview']=='thumbnail' else '')
            draw.text((x+12,y+cell_h-32),label,fill='black',font_size=24)
        output=io.BytesIO();sheet.save(output,format='PNG')
        return output.getvalue()

    png=await asyncio.to_thread(render)
    check()
    return {'preview_id':preview_id,'name':pack.name,'title':pack.title,'total':len(pack.stickers),
            'next_offset':offset+20 if len(pack.stickers)>offset+20 else None,
            'stickers':[entry for entry,_ in images]},png


def clean_text(text,limit=1024):
    if not isinstance(text,str) or len(text)>limit:raise ValueError(f'Text must contain at most {limit} characters')
    return scrub(text)


def public_url(value):
    parsed=urlsplit(value)
    if parsed.scheme!='https' or not parsed.hostname or parsed.username or parsed.password:
        raise ValueError('Use a public HTTPS media URL without embedded credentials')
    if scrub(value)!=value:raise PermissionError('Credential-like URLs cannot be sent')
    if parsed.hostname.lower() in {'localhost','localhost.localdomain'} or parsed.hostname.endswith('.local'):
        raise ValueError('Use a public media URL')
    import ipaddress
    try:
        address=ipaddress.ip_address(parsed.hostname)
        if not address.is_global:raise ValueError('Use a public media URL')
    except ValueError as exc:
        if 'Use a public' in str(exc):raise
    return value


def options_for(kind,options):
    if kind not in KINDS:raise ValueError('Unsupported Telegram media kind')
    options=dict(options or {})
    if set(options)-(MEDIA_OPTIONS[kind]|COMMON|{'buttons'}):raise ValueError('Unsupported options for '+kind)
    for key,value in options.items():
        if key in {'duration','width','height','length'}:
            if isinstance(value,bool) or not isinstance(value,int) or not 0<=value<=100000:raise ValueError('Invalid media dimension/duration')
        elif key in {'performer','title','emoji'}:
            options[key]=clean_text(value,200)
        elif key=='parse_mode':
            if kind not in CAPTIONED or value not in {'HTML','Markdown','MarkdownV2'}:raise ValueError('Unsupported caption parse mode')
        elif key!='buttons' and not isinstance(value,bool):raise ValueError('Media flags must be booleans')
    buttons=options.pop('buttons',None)
    if buttons:
        if not isinstance(buttons,list) or len(buttons)>8:raise ValueError('Use up to eight rows of URL buttons')
        rows=[]
        for row in buttons:
            if not isinstance(row,list) or not 1<=len(row)<=8:raise ValueError('Invalid URL button row')
            rows.append([InlineKeyboardButton(clean_text(item['text'],64),url=public_url(item['url'])) for item in row])
        options['reply_markup']=InlineKeyboardMarkup(rows)
    return options


async def reserve_mentions(broker,text,operation):
    if not broker.loop_guard:return
    from .routing import peer_key
    cap=broker.require(visible=True)
    for username in set(re.findall(r'@([A-Za-z0-9_]*bot)\b',text,re.I)):
        if username.lower()==(getattr(broker.bot,'username','') or '').lower():continue
        peer=peer_key({'username':username},username)
        if peer!=cap.peer_bot_id:await broker.loop_guard.reserve(peer,broker.store.scope.chat_id,f'media-mention:{peer}:{cap.turn_id}:{operation}')


async def media_input(broker,kind,media):
    if kind not in KINDS:raise ValueError('Choose photo, video, animation, audio, voice, video_note, sticker or document')
    if not isinstance(media,str) or len(media)>2000:raise ValueError('Invalid media reference')
    if media.startswith('artifact:'):
        if broker.artifacts is None:raise RuntimeError('Durable artifact storage unavailable')
        uid=str(UUID(media[9:]))
        row,body=await broker.artifacts.read(uid)
        if kind=='photo' and len(body)>10_000_000:raise ValueError('Telegram photos must be at most 10 MB; use a smaller photo or send as a document')
        return InputFile(body,filename=row['filename']),{'artifact_id':uid,'filename':row['filename']},len(body)
    if media.startswith('file:'):
        file_id=media[5:]
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,600}',file_id):raise ValueError('Use an actual Telegram file_id, not a local path')
        return file_id,{'file_id':file_id},0
    if kind=='video_note':raise ValueError('Telegram video notes require an uploaded artifact or Telegram file_id')
    return public_url(media),{'url':media},0


async def send_media(broker,kind,media,caption,operation,options=None,reply_to=None,final=True):
    cap=broker.require(visible=True)
    caption=clean_text(caption)
    if caption and kind not in CAPTIONED:raise ValueError('Telegram stickers and round video notes do not have captions')
    kwargs=options_for(kind,options)
    await reserve_mentions(broker,caption,operation)
    request_options=scrub_object(options or {})
    value,reference,_=await media_input(broker,kind,media)
    if broker.capability is not cap:raise PermissionError('Media sending turn ended')
    if reply_to is not None:await broker.message_target(reply_to)
    kwargs.update(chat_id=broker.store.scope.chat_id,**{kind:value})
    if caption:kwargs['caption']=caption
    if reply_to is not None:kwargs['reply_to_message_id']=reply_to
    if broker.store.scope.topic_id:kwargs['message_thread_id']=broker.store.scope.topic_id
    async def send():return await getattr(broker.bot,'send_'+kind)(**kwargs)
    request=dict(reference,kind=kind,caption=caption,media=media,options=request_options,reply_to_message_id=reply_to)
    return await broker.deliver(cap,'send_'+kind,request,operation,send,final=final)


async def send_album(broker,items,operation,reply_to=None,final=True):
    cap=broker.require(visible=True)
    if not 2<=len(items)<=10:raise ValueError('Telegram albums contain 2..10 items')
    kinds={item.get('kind') for item in items}
    if not (kinds<={'photo','video'} or kinds=={'audio'} or kinds=={'document'}):
        raise ValueError('Albums support photos/videos together, or audio-only, or document-only')
    classes={'photo':InputMediaPhoto,'video':InputMediaVideo,'audio':InputMediaAudio,'document':InputMediaDocument}
    album=[];saved=[];total=0
    for item in items:
        if set(item)-{'kind','media','caption','options'}:raise ValueError('Unexpected album item fields')
        kind=item['kind'];caption=clean_text(item.get('caption',''))
        options=options_for(kind,item.get('options'))
        if set(options)&{'disable_notification','protect_content','reply_markup'}:raise ValueError('Album options belong to individual media captions/metadata only')
        value,reference,size=await media_input(broker,kind,item['media'])
        total+=size
        if total>100_000_000:raise ValueError('Upload at most 100 MB per album on this phone; split into smaller albums')
        album.append(classes[kind](media=value,caption=caption or None,**options))
        saved.append(dict(reference,kind=kind,caption=caption,media=item['media'],options=scrub_object(item.get('options') or {})))
        await reserve_mentions(broker,caption,f'{operation}:{len(saved)}')
    if broker.capability is not cap:raise PermissionError('Album sending turn ended')
    if broker.loop_guard and cap.peer_bot_id:
        for index in range(1,len(items)):
            await broker.loop_guard.reserve(cap.peer_bot_id,broker.store.scope.chat_id,f'{cap.turn_id}:{operation}:album-extra:{index}')
    kwargs={'chat_id':broker.store.scope.chat_id,'media':album}
    if broker.store.scope.topic_id:kwargs['message_thread_id']=broker.store.scope.topic_id
    if reply_to is not None:
        await broker.message_target(reply_to);kwargs['reply_to_message_id']=reply_to
    async def send():return await broker.bot.send_media_group(**kwargs)
    return await broker.deliver(cap,'send_media_group',{'items':saved,'reply_to_message_id':reply_to},operation,send,final=final)


async def chat_action(broker,action,parameters,operation):
    """Ordinary current-chat actions; configuration/admin tools stay separate."""
    cap=broker.require(visible=True);p=dict(parameters)
    allowed={
        'location':{'latitude','longitude','horizontal_accuracy','live_period','heading','proximity_alert_radius'},
        'venue':{'latitude','longitude','title','address','foursquare_id','google_place_id'},
        'contact':{'phone_number','first_name','last_name','vcard'},
        'dice':{'emoji'},'edit_caption':{'message_id','caption','parse_mode','show_caption_above_media'},
        'stop_live_location':{'message_id'},'edit_live_location':{'message_id','latitude','longitude','heading','horizontal_accuracy','proximity_alert_radius'},
        'stop_poll':{'message_id'},'copy':{'message_id','caption'},'forward':{'message_id'},
    }
    if action not in allowed or set(p)-allowed[action]:raise ValueError('Unsupported chat action or parameters')
    required={'location':{'latitude','longitude'},'venue':{'latitude','longitude','title','address'},
        'contact':{'phone_number','first_name'},'edit_caption':{'message_id'},
        'edit_live_location':{'message_id','latitude','longitude'},'stop_live_location':{'message_id'},
        'stop_poll':{'message_id'},'copy':{'message_id'},'forward':{'message_id'}}
    if required.get(action,set())-p.keys():raise ValueError('Missing required Telegram action parameters')
    if 'message_id' in p and (isinstance(p['message_id'],bool) or not isinstance(p['message_id'],int) or p['message_id']<1):raise ValueError('Use an actual positive message_id')
    if 'parse_mode' in p and p['parse_mode'] not in {'HTML','Markdown','MarkdownV2'}:raise ValueError('Invalid parse mode')
    if scrub_object(p)!=p:raise PermissionError('Credential-like content cannot be sent')
    for name in ['latitude','longitude']:
        if name in p and (isinstance(p[name],bool) or not isinstance(p[name],(int,float)) or not math.isfinite(p[name]) or abs(p[name])>(90 if name=='latitude' else 180)):
            raise ValueError('Invalid coordinates')
    for name in ['caption','title','address','first_name','last_name','vcard','phone_number']:
        if name in p:p[name]=clean_text(p[name],4096 if name=='vcard' else 1024)
    if action=='dice' and p.get('emoji','🎲') not in {'🎲','🎯','🏀','⚽','🎳','🎰'}:raise ValueError('Unsupported dice emoji')
    if action in {'edit_caption','stop_live_location','edit_live_location','stop_poll'}:
        await broker.message_target(p.get('message_id'),own=True)
    if action in {'copy','forward'}:
        await broker.message_target(p.get('message_id'))
        # Same-conversation copying/forwarding only. Explicit routing handles groups.
        p['from_chat_id']=broker.store.scope.chat_id
    methods={'location':'send_location','venue':'send_venue','contact':'send_contact','dice':'send_dice',
        'edit_caption':'edit_message_caption','stop_live_location':'stop_message_live_location',
        'edit_live_location':'edit_message_live_location','stop_poll':'stop_poll','copy':'copy_message','forward':'forward_message'}
    method=methods[action];kwargs=dict(p,chat_id=broker.store.scope.chat_id)
    if method.startswith('send_') or action in {'copy','forward'}:
        if broker.store.scope.topic_id:kwargs['message_thread_id']=broker.store.scope.topic_id
    async def send():
        value=await getattr(broker.bot,method)(**kwargs)
        # stop_poll returns Poll, which is an acknowledged action, not Message.
        return True if action=='stop_poll' else value
    return await broker.deliver(cap,method,dict(p,action=action),operation,send,final=True)


async def lookup(broker,kind,identifier,offset=0):
    cap=broker.require()
    if kind in {'file','import_file'}:
        if not re.fullmatch(r'[A-Za-z0-9_-]{8,600}',identifier):raise ValueError('Use an actual Telegram file_id')
        value=await broker.bot.get_file(identifier)
        # File.file_path contains the bot token. Never serialize the raw object.
        result={'file_id':value.file_id,'file_unique_id':value.file_unique_id,'file_size':value.file_size}
        if kind=='import_file':
            broker.require(visible=True)
            if broker.artifacts is None:raise RuntimeError('Durable artifact storage unavailable')
            if (value.file_size or 0)>20_000_000:raise ValueError('Telegram cloud download limit is 20 MB')
            import hashlib,mimetypes
            from pathlib import PurePosixPath
            extension=PurePosixPath(urlsplit(value.file_path or '').path).suffix
            if not re.fullmatch(r'\.[A-Za-z0-9]{1,8}',extension):extension='.bin'
            filename='telegram-'+value.file_unique_id+extension
            body=bytes(await value.download_as_bytearray())
            if len(body)>20_000_000:raise ValueError('Telegram download exceeds 20 MB')
            if broker.capability is not cap:raise PermissionError('Media import turn ended')
            mime=mimetypes.guess_type(filename)[0] or 'application/octet-stream'
            result['artifact']=await broker.artifacts.put(turn_id=cap.turn_id,operation_id='telegram-import:'+identifier[:120],
                filename=filename,mime=mime,body=body,request_hash=hashlib.sha256(body).hexdigest(),metadata={'source':'telegram-file','file_id':identifier})
        return result
    if kind=='sticker_set':
        value=await broker.bot.get_sticker_set(sticker_set_name(identifier))
        if offset<0:raise ValueError('Sticker offset must be nonnegative')
        return {'name':value.name,'title':value.title,'total':len(value.stickers),'next_offset':offset+50 if len(value.stickers)>offset+50 else None,
            'stickers':[{'file_id':s.file_id,'emoji':s.emoji,'is_animated':s.is_animated,'is_video':s.is_video,'width':s.width,'height':s.height} for s in value.stickers[offset:offset+50]]}
    if kind=='custom_emoji':
        identifiers=identifier.split(',')
        if not 1<=len(identifiers)<=100 or any(not value.isdigit() for value in identifiers):raise ValueError('Use 1..100 comma-separated custom emoji IDs')
        return [{'custom_emoji_id':s.custom_emoji_id,'file_id':s.file_id,'emoji':s.emoji} for s in await broker.bot.get_custom_emoji_stickers(identifiers)]
    raise ValueError('Choose file, import_file, sticker_set or custom_emoji')


def selected_sticker(broker,ref):
    entry=getattr(broker,'_sticker_cache',{}).get(ref)
    if not entry or entry['expires']<time.monotonic() or entry['audience']!=broker.store.scope.audience:
        raise ValueError('Sticker selection expired or belongs to another audience; preview the pack again')
    return entry


async def sticker_candidate(broker,ref):
    cap=broker.require();cached=selected_sticker(broker,ref);sticker=cached['sticker']
    target=getattr(sticker,'thumbnail',None) if sticker.is_animated or sticker.is_video else sticker
    if target is None:raise ValueError('No candidate artwork is available')
    async with asyncio.timeout(15):
        file=await broker.bot.get_file(target.file_id)
        if (file.file_size or 0)>2_000_000:raise ValueError('Preview exceeds 2 MB')
        body=bytes(await file.download_as_bytearray())
    if len(body)>2_000_000:raise ValueError('Preview exceeds 2 MB')
    if broker.capability is not cap:raise PermissionError('Sticker inspection turn ended')
    def render():
        from PIL import Image
        with Image.open(io.BytesIO(body)) as image:
            if image.width*image.height>4_000_000:raise ValueError('Preview dimensions too large')
            image=image.convert('RGBA');image.thumbnail((1024,1024))
            output=io.BytesIO();image.save(output,format='PNG');return output.getvalue()
    return dict(cached['entry'],name=cached['name']),await asyncio.to_thread(render)


async def send_selected_sticker(broker,ref,operation,reply_to=None,final=True):
    cap=broker.require(visible=True);cached=selected_sticker(broker,ref)
    pack=await broker.bot.get_sticker_set(cached['name'])
    if tuple(s.file_id for s in pack.stickers)!=cached['snapshot']:
        raise ValueError('Sticker pack changed; preview it again before selecting')
    if broker.capability is not cap:raise PermissionError('Sticker send turn ended')
    entry=cached['entry']
    receipt=await send_media(broker,'sticker','file:'+entry['file_id'],'',operation,reply_to=reply_to,final=final)
    return dict(receipt,selection_ref=ref,pack=cached['name'],index=entry['index'],file_unique_id=entry['file_unique_id'])
