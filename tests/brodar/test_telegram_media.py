from types import SimpleNamespace
from uuid import uuid4

import pytest

from brodar.broker import ToolBroker,TurnCapability
from brodar.scope import Scope
from brodar.runtime import undelivered_artifacts,origin_report_needed
from brodar.telegram_media import send_media,send_album,chat_action,lookup,public_url


class Store:
    scope=Scope(1,-100,11)
    def __init__(self):self.rows={}
    async def delivery_begin(self,turn,operation,method,request):
        row=self.rows.setdefault(operation,{'id':operation,'method':method,'request':request,'status':'pending','topic_id':11})
        snapshot=dict(row)
        if row['status']=='pending':row['status']='sending'
        return snapshot
    async def delivery_finish(self,uid,status,response):self.rows[uid].update(status=status,response=response)


class Bot:
    username='BrodarBot'
    def __init__(self):self.calls=[]
    def __getattr__(self,name):
        async def call(**kwargs):
            self.calls.append((name,kwargs))
            if name=='send_media_group':return tuple(SimpleNamespace(message_id=100+i) for i in range(len(kwargs['media'])))
            return SimpleNamespace(message_id=100)
        return call


class Files:
    def __init__(self):self.uid=str(uuid4());self.body=b'file bytes';self.sensitive=False
    async def read(self,uid):
        if uid!=self.uid:raise ValueError('not this audience')
        if self.sensitive:raise PermissionError('sensitive')
        return {'filename':'image.png','mime_type':'image/png'},self.body


def broker():
    store=Store();bot=Bot();files=Files()
    source=ToolBroker(store,bot,artifacts=files)
    source.begin(TurnCapability(str(uuid4()),requester_id=42))
    return source,store,bot,files


@pytest.mark.asyncio
@pytest.mark.parametrize('kind',['photo','video','animation','audio','voice','video_note','sticker','document'])
async def test_all_native_media_formats_have_actual_delivery_receipts(kind):
    source,store,bot,files=broker()
    result=await send_media(source,kind,'artifact:'+files.uid,'','one')
    assert bot.calls[0][0]=='send_'+kind
    assert bot.calls[0][1]['chat_id']==-100 and bot.calls[0][1]['message_thread_id']==11
    assert result['message_id']==100
    assert undelivered_artifacts([{'artifact_id':files.uid}],list(store.rows.values()))==[]


@pytest.mark.asyncio
async def test_album_records_all_ids_and_delivers_each_artifact_without_repair_loop():
    source,store,bot,files=broker()
    result=await send_album(source,[{'kind':'photo','media':'artifact:'+files.uid},
        {'kind':'video','media':'file:real_file_id_123','caption':'another item'}],'album')
    assert result['message_ids']==[100,101]
    assert store.rows['album']['status']=='sent'
    assert undelivered_artifacts([{'artifact_id':files.uid}],list(store.rows.values()))==[]
    await send_album(source,[{'kind':'photo','media':'artifact:'+files.uid},
        {'kind':'video','media':'file:real_file_id_123','caption':'another item'}],'album')
    assert len(bot.calls)==1


@pytest.mark.asyncio
async def test_file_paths_sensitive_files_and_invalid_album_combinations_fail_before_send():
    source,store,bot,files=broker()
    with pytest.raises(ValueError):await send_media(source,'photo','file:/etc/passwd','','a')
    with pytest.raises(ValueError):await send_media(source,'photo','/etc/passwd','','b')
    with pytest.raises(ValueError):await send_album(source,[{'kind':'audio','media':'file:real_file_id_123'},
        {'kind':'photo','media':'file:real_file_id_456'}],'c')
    files.sensitive=True
    with pytest.raises(PermissionError):await send_media(source,'photo','artifact:'+files.uid,'','d')
    assert bot.calls==[]


@pytest.mark.asyncio
async def test_photo_uses_photo_method_and_options_cannot_redirect_delivery():
    source,store,bot,files=broker()
    with pytest.raises(ValueError):await send_media(source,'photo','artifact:'+files.uid,'','one',{'chat_id':42})
    await send_media(source,'photo','artifact:'+files.uid,'caption','two',{'has_spoiler':True},final=False)
    assert bot.calls[0][0]=='send_photo'
    assert bot.calls[0][1]['has_spoiler'] is True
    assert store.rows['two']['response']['final'] is False


@pytest.mark.asyncio
async def test_sticker_pack_link_returns_usable_ids_and_file_lookup_never_leaks_token_url():
    source,store,bot,files=broker()
    async def pack(name):
        assert name=='GayPackbyAli_yhyee'
        return SimpleNamespace(name=name,title='pack',stickers=[SimpleNamespace(file_id='real_file_id_123',emoji='😭',is_animated=False,is_video=False,width=512,height=512)])
    async def file(identifier):
        return SimpleNamespace(file_id=identifier,file_unique_id='unique',file_size=100,file_path='https://api.telegram.org/file/botSECRET/sticker.webp')
    bot.get_sticker_set=pack;bot.get_file=file
    result=await lookup(source,'sticker_set','https://t.me/addstickers/GayPackbyAli_yhyee')
    assert result['stickers'][0]['file_id']=='real_file_id_123'
    assert 'SECRET' not in str(await lookup(source,'file','real_file_id_123'))
    await send_media(source,'sticker','file:'+result['stickers'][0]['file_id'],'','sticker')
    assert bot.calls[-1][0]=='send_sticker'


@pytest.mark.asyncio
async def test_caption_edit_checks_message_ownership_and_chat_actions_cannot_configure_bot():
    source,store,bot,files=broker();checks=[]
    async def target(uid,own=False):checks.append((uid,own))
    source.message_target=target
    await chat_action(source,'edit_caption',{'message_id':100,'caption':'new caption'},'caption')
    assert checks==[(100,True)]
    assert bot.calls[-1][0]=='edit_message_caption'
    with pytest.raises(ValueError):await chat_action(source,'set_webhook',{'url':'https://example.org'},'config')
    with pytest.raises(ValueError):await chat_action(source,'location',{'latitude':91,'longitude':0},'place')


def test_private_media_urls_and_credentials_are_rejected():
    for value in ['file:///etc/passwd','https://127.0.0.1/private','https://user:pass@example.org/image.png','https://example.org/hat_'+'A'*30]:
        with pytest.raises((ValueError,PermissionError)):public_url(value)
