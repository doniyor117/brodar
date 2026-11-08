"""Deterministic Neon resource selection. Management credentials never enter a brain."""
from __future__ import annotations
import json
import re
from pathlib import Path
from .profile import atomic_private


def rows(value,key):
    if isinstance(value,list):return value
    result=value.get(key,[])
    if isinstance(result,dict):result=result.get('items',[])
    return result


def connection_uri(value):
    if isinstance(value,str) and value.startswith(('postgres://','postgresql://')):return value
    if isinstance(value,dict):
        for key in ('connection_uri','connection_string','uri','text'):
            if key in value:
                try:return connection_uri(value[key])
                except ValueError:pass
    raise ValueError('Neon did not return a recognized private Postgres URI; no credential was printed')


class NeonSetup:
    def __init__(self,engine,transport):self.engine,self.transport=engine,transport
    async def projects(self):return rows(await self.transport.call('list_projects',{}),'projects')
    async def describe(self,project):return await self.transport.call('describe_project',{'project_id':project})
    async def create(self,name,region,organization=None):
        resources=self.engine.journal['resources']
        if resources.get('project_id'):return resources['project_id']
        if resources.get('project_create_pending'):
            raise RuntimeError('Previous project creation outcome is unknown. Select the created project from the list; setup will not create a duplicate')
        arguments={'name':name,'region_id':region}
        if organization:arguments['org_id']=organization
        self.engine.resource('project_create_pending',True)
        value=await self.transport.call('create_project',arguments)
        project=value.get('project',value)
        uid=project.get('id') or value.get('projectId')
        if not uid:raise ValueError('Project creation returned no ID; inspect your Neon console before retrying')
        self.engine.resource('project_id',uid);self.engine.resource('project_create_pending',False)
        return uid
    async def provision(self,project,branch=None,database='neondb',role='brodar_runtime'):
        args={'project_id':project,'database_name':database}
        if branch:args['branch_id']=branch
        uri=connection_uri(await self.transport.call('get_connection_string',args))
        # Save privately before doing any remote role mutation. No URI in journal/console.
        admin=self.engine.directory/(self.engine.path.stem+'.admin-uri')
        atomic_private(admin,uri+'\n')
        self.engine.resource('project_id',project)
        if branch:self.engine.resource('branch_id',branch)
        result=await self.engine.provision_database(uri,role=role)
        self.engine.done('neon','provisioned')
        return result
    async def storage(self,branch):
        transport=self.transport
        arguments={'branch_id':branch,'project_id':self.engine.journal['resources']['project_id']}
        state=await transport.call('get_storage',arguments)
        if not state.get('enabled') or not state.get('s3_endpoint'):raise ValueError('Object storage unavailable on this branch; local files still work')
        path=self.engine.directory/(self.engine.path.stem+'.storage.json')
        if path.exists():
            existing=json.loads(path.read_text())
            if existing.get('endpoint')!=state['s3_endpoint']:raise ValueError('Storage target differs from the existing profile')
            self.engine.set('BRODAR_STORAGE_CONFIG',str(path));return {'storage':'reused'}
        buckets=rows(await transport.call('list_storage_buckets',arguments),'buckets')
        for name in ('brodar-artifacts','brodar-backups'):
            existing=next((b for b in buckets if b.get('name')==name),None)
            if existing and existing.get('access_level')!='private':raise ValueError('Existing bucket is not private')
            if not existing:await transport.call('create_storage_bucket',{**arguments,'name':name,'access_level':'private'})
        if self.engine.journal['resources'].get('storage_credential_pending'):
            raise RuntimeError('Prior credential return was interrupted. Revoke the unused Brodar credential in Neon before retrying; no duplicate was created')
        self.engine.resource('storage_credential_pending',True)
        value=await transport.call('create_credential',{**arguments,'scopes':['storage:read','storage:write'],'principal_type':'user','name':'brodar-object-storage'})
        config={'endpoint':state['s3_endpoint'],'region':state['region'],'access_key':value['token_id'],'secret_key':value['s3_secret_access_key'],'artifacts_bucket':'brodar-artifacts','backups_bucket':'brodar-backups'}
        atomic_private(path,json.dumps(config,indent=2));self.engine.resource('storage_credential_pending',False)
        self.engine.set('BRODAR_STORAGE_CONFIG',str(path));self.engine.done('storage','configured')
        return {'storage':'configured','secrets_printed':False}
