import json
import stat

import httpx
import pytest

from brodar.storage_setup import setup_storage


@pytest.mark.asyncio
async def test_private_storage_setup_reuses_credentials_and_never_prints_them(tmp_path):
    calls=[];buckets=[]
    def handler(request):
        calls.append((request.method,request.url.path))
        assert request.headers['authorization']=='Bearer operator-test-key'
        if request.url.path.endswith('/storage'):
            return httpx.Response(200,json={'enabled':True,'s3_endpoint':'https://example.invalid','region':'eu-central-1'})
        if request.url.path.endswith('/buckets'):
            if request.method=='GET':return httpx.Response(200,json={'buckets':buckets})
            bucket=json.loads(request.content);assert bucket['access_level']=='private';buckets.append(bucket)
            return httpx.Response(201,json=bucket)
        data=json.loads(request.content);assert data['scopes']==['storage:read','storage:write']
        return httpx.Response(201,json={'token_id':'access-test-key','s3_secret_access_key':'private-test-secret'})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        path=tmp_path/'storage.json'
        first=await setup_storage('operator-test-key','project','branch',path,client=client)
        second=await setup_storage('operator-test-key','project','branch',path,client=client)
    assert not first['credential_reused'] and second['credential_reused']
    assert sum(method=='POST' and path.endswith('/credentials') for method,path in calls)==1
    assert stat.S_IMODE(path.stat().st_mode)==0o600
    assert 'private-test-secret' not in str(first) and 'access-test-key' not in str(first)


@pytest.mark.asyncio
async def test_unavailable_storage_never_creates_credentials(tmp_path):
    calls=[]
    def handler(request):calls.append(request.method);return httpx.Response(200,json={'enabled':False})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        with pytest.raises(ValueError,match='not enabled'):
            await setup_storage('test-key','project','branch',tmp_path/'storage.json',client=client)
    assert calls==['GET']
