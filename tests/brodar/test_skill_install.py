import json
import subprocess
from pathlib import Path

import pytest

from brodar.skill_install import manage,skill_directory
from brodar.owner_tools import job_notification
from brodar.skills import SkillLibrary


def put_skill(root,path):
    target=root/path
    target.mkdir(parents=True)
    (target/'SKILL.md').write_text('---\nname: slides\n---\nBuild a deck.')
    return target


def test_unique_nested_skill_and_name_selection(tmp_path):
    only=put_skill(tmp_path,'skills/slides')
    assert skill_directory(tmp_path,'slides-skill','.')==(only,'skills/slides')
    put_skill(tmp_path,'skills/other')
    assert skill_directory(tmp_path,'slides-skill','.')==(only,'skills/slides')
    with pytest.raises(ValueError,match='Multiple skills.*skills/other.*skills/slides'):
        skill_directory(tmp_path,'unknown','.')
    with pytest.raises(ValueError,match='directory unavailable'):
        skill_directory(tmp_path,'slides','wrong')


def test_symlink_skill_directory_is_never_selected(tmp_path):
    put_skill(tmp_path,'real')
    (tmp_path/'alias').symlink_to(tmp_path/'real',target_is_directory=True)
    with pytest.raises(ValueError,match='symlinks'):
        skill_directory(tmp_path,'slides','alias')


def test_nested_install_and_update_record_actual_path_and_commit(tmp_path,monkeypatch):
    repo=tmp_path/'upstream';repo.mkdir();put_skill(repo,'skills/slides')
    subprocess.run(['git','init','-q',str(repo)],check=True)
    subprocess.run(['git','-C',str(repo),'add','.'],check=True)
    subprocess.run(['git','-C',str(repo),'-c','user.name=Fixture','-c','user.email=fixture@example.invalid','commit','-qm','fixture'],check=True)
    actual=subprocess.check_output
    url='https://github.com/fixture/slides-skill'
    def local_clone(argv,**kw):
        return actual([str(repo) if x==url else x for x in argv],**kw)
    monkeypatch.setattr(subprocess,'check_output',local_clone)
    root=tmp_path/'installed'
    receipt=manage(root,'install','slides-skill',url)
    assert receipt['subdirectory']=='skills/slides' and len(receipt['commit'])==40
    assert (root/'slides-skill/SKILL.md').is_file()
    assert json.loads((root/'slides-skill/.brodar-source.json').read_text())==receipt
    assert manage(root,'update','slides-skill')==receipt
    assert not list(root.glob('.install-*'))


def test_skill_notification_reports_receipt_or_failure_instead_of_only_exit_code():
    payload={'argv':['python','-m','brodar.skill_install','install','slides']}
    result=job_notification('job','done',0,payload,json.dumps({'name':'slides','subdirectory':'skills/slides','commit':'abc123456789dead'}))
    assert 'skills/slides' in result and 'abc123456789' in result
    failed=job_notification('job','failed',1,payload,'Skill install failed: Multiple skills found; choose subdirectory: a, b')
    assert 'Multiple skills' in failed
    assert job_notification('job','done',0,{'argv':['python','-c','print(4)']},'4')=='owner task done (exit 0). job job'


@pytest.mark.asyncio
async def test_installed_skills_survive_new_process_and_resolve_unique_names(tmp_path):
    root=tmp_path/'workspaces/old-reply-thread/owner/skills'
    package=put_skill(root,'slides-skill')
    (package/'run.mjs').write_text('console.log(4)')
    put_skill(tmp_path/'workspaces/old-reply-thread/owner/skill-sources','scratch-only')
    library=SkillLibrary([tmp_path/'empty'])
    library.register_owner_roots(tmp_path)
    rows=await library.list('slides')
    assert rows['total']==1
    by_name=await library.read('slides-skill')
    assert by_name['skill_id']==rows['skills'][0]['id']
    assert (await library.read('slides-skill','run.mjs'))['text']=='console.log(4)'
    assert (await library.list('scratch-only'))['total']==0
    put_skill(tmp_path/'other','slides-skill');library.roots.append(tmp_path/'other');library.updated=0
    with pytest.raises(ValueError,match='Ambiguous'):await library.read('slides-skill')
    assert (await library.read(by_name['skill_id']))['text']
