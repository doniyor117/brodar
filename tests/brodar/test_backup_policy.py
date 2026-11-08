import json
from types import SimpleNamespace
from unittest.mock import AsyncMock,patch
import pytest
from brodar.backups import prune_managed

@pytest.mark.asyncio
async def test_pruning_preserves_legacy_pinned_incomplete_and_validates_kept_snapshots():
    manifests=[{'backup_id':'20261101T000000Z-a','retention_managed':True},
        {'backup_id':'20261102T000000Z-a','retention_managed':True},
        {'backup_id':'20261103T000000Z-a','retention_managed':True},
        {'backup_id':'20261001T000000Z-a'},
        {'backup_id':'20261104T000000Z-a','retention_managed':True,'pinned':True}]
    files={}
    for item in manifests:
        prefix='snapshots/'+item['backup_id']+'/'
        files[prefix+'manifest.json']=json.dumps(item).encode();files[prefix+'snapshot.fernet']=b'encrypted'
    files['snapshots/incomplete/object']=b'partial'
    objects=SimpleNamespace(backups_bucket='backups',inventory=AsyncMock(return_value=[{'key':key,'bytes':len(value)} for key,value in files.items()]),get=AsyncMock(side_effect=lambda key,**_:files[key]),delete=AsyncMock())
    with patch('brodar.backups.load_backup',AsyncMock(return_value=({'objects':{}},{},None))) as load:
        assert await prune_managed(None,objects)=={'pruned':1}
        assert load.await_count==2
    deleted=[call.args[0] for call in objects.delete.await_args_list]
    assert deleted==['snapshots/20261101T000000Z-a/manifest.json','snapshots/20261101T000000Z-a/snapshot.fernet']

@pytest.mark.asyncio
async def test_failed_backup_validation_never_prunes():
    files={f'snapshots/2026110{i}T000000Z-a/manifest.json':json.dumps({'backup_id':f'2026110{i}T000000Z-a','retention_managed':True}).encode() for i in range(1,4)}
    objects=SimpleNamespace(backups_bucket='b',inventory=AsyncMock(return_value=[{'key':key,'bytes':1} for key in files]),get=AsyncMock(side_effect=lambda key,**_:files[key]),delete=AsyncMock())
    with patch('brodar.backups.load_backup',AsyncMock(side_effect=ValueError('checksum failed'))):
        with pytest.raises(ValueError):await prune_managed(None,objects)
    objects.delete.assert_not_awaited()
