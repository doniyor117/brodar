"""Reproducible operator setup of private Neon object storage."""

import json
import os
from pathlib import Path
import re

import httpx


async def setup_storage(api_key,project,branch,destination,*,client=None):
    if not api_key:raise ValueError('NEON_API_KEY is missing from the private operator environment')
    if not all(re.fullmatch(r'[a-z0-9-]{1,60}',value or '') for value in [project,branch]):
        raise ValueError('Explicit Neon project and branch IDs are required')
    path=Path(destination).expanduser()
    own=client is None
    client=client or httpx.AsyncClient(timeout=45)
    base=f'https://console.neon.tech/api/v2/projects/{project}/branches/{branch}'
    async def request(method,suffix,payload=None):
        response=await client.request(method,base+suffix,headers={'Authorization':'Bearer '+api_key},json=payload)
        if not response.is_success:raise RuntimeError(f'Neon storage setup failed ({method} {suffix}, HTTP {response.status_code})')
        return response.json()
    try:
        state=await request('GET','/storage')
        if not state.get('enabled') or not state.get('s3_endpoint'):
            raise ValueError('Storage is not enabled/available on this branch; check the project region and Neon setup')
        existing=json.loads(path.read_text()) if path.exists() else None
        if existing and (existing['endpoint']!=state['s3_endpoint'] or existing['region']!=state['region']):
            raise ValueError('Existing private configuration points at a different branch; use a separate file')
        names=[existing['artifacts_bucket'],existing['backups_bucket']] if existing else ['brodar-artifacts','brodar-backups']
        buckets={bucket['name']:bucket for bucket in (await request('GET','/buckets'))['buckets']}
        for name in names:
            if name in buckets:
                if buckets[name].get('access_level')!='private':raise ValueError('An existing Brodar bucket is public; use a private bucket')
            else:await request('POST','/buckets',{'name':name,'access_level':'private'})
        if existing:return {'storage_config':str(path),'private_buckets':names,'credential_reused':True}
        credential=await request('POST','/credentials',{'scopes':['storage:read','storage:write'],
                                                     'principal_type':'user','name':'brodar-object-storage'})
        config={'endpoint':state['s3_endpoint'],'region':state['region'],
                'access_key':credential['token_id'],'secret_key':credential['s3_secret_access_key'],
                'artifacts_bucket':names[0],'backups_bucket':names[1]}
        path.parent.mkdir(mode=0o700,parents=True,exist_ok=True)
        fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
        with os.fdopen(fd,'w') as f:
            json.dump(config,f,indent=2);f.flush();os.fsync(f.fileno())
        return {'storage_config':str(path),'private_buckets':names,'credential_reused':False,'credentials_printed':False}
    finally:
        if own:await client.aclose()
