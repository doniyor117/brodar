"""Private S3 storage. Paths and credentials never become model arguments."""

import asyncio
import json
from pathlib import Path

import boto3
from botocore.config import Config


class ObjectStore:
    def __init__(self,config):
        self.bucket=config['artifacts_bucket']
        self.backups_bucket=config['backups_bucket']
        self.client=boto3.client('s3',endpoint_url=config['endpoint'],region_name=config['region'],
            aws_access_key_id=config['access_key'],aws_secret_access_key=config['secret_key'],
            config=Config(signature_version='s3v4',s3={'addressing_style':'path'},
                retries={'total_max_attempts':2},connect_timeout=10,read_timeout=45))

    @classmethod
    def from_file(cls,path):
        if path is None or not Path(path).exists():return None
        return cls(json.loads(Path(path).read_text()))

    async def put(self,key,body,mime,*,bucket=None):
        await asyncio.to_thread(self.client.put_object,Bucket=bucket or self.bucket,Key=key,
            Body=body,ContentType=mime)

    async def get(self,key,*,bucket=None,max_bytes=20_000_000):
        def fetch():
            response=self.client.get_object(Bucket=bucket or self.bucket,Key=key)
            stream=response['Body']
            try:
                if response.get('ContentLength',0)>max_bytes:raise ValueError('Stored artifact exceeds the download limit')
                body=stream.read(max_bytes+1)
                if len(body)>max_bytes:raise ValueError('Stored artifact exceeds the download limit')
                return body
            finally:stream.close()
        return await asyncio.to_thread(fetch)

    async def delete(self,key,*,bucket=None):
        await asyncio.to_thread(self.client.delete_object,Bucket=bucket or self.bucket,Key=key)

    async def close(self):
        await asyncio.to_thread(self.client.close)
