"""Source lifecycle checks use disposable homes, never the actual deployment."""
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import pytest

ROOT=Path(__file__).resolve().parents[2]


def test_installer_help_requires_no_python_or_environment(tmp_path):
    shell=shutil.which('sh')
    tools=tmp_path/'tools';tools.mkdir();(tools/'cat').symlink_to(shutil.which('cat'))
    result=subprocess.run([shell,str(ROOT/'install.sh'),'--help'],env={'HOME':str(tmp_path),'PATH':str(tools)},text=True,capture_output=True)
    assert result.returncode==0 and '--no-onboard' in result.stdout
    assert list(tmp_path.iterdir())==[tools]


def test_uninstall_removes_only_marked_install_and_preserves_user_files(tmp_path):
    env=tmp_path/'.local/share/brodar/venv';(env/'bin').mkdir(parents=True)
    (env/'.brodar-install').write_text('brodar-source-install-v1\n')
    (env/'bin/python').symlink_to(sys.executable)
    shell=shutil.which('sh')
    worker=env/'bin/brodar';worker.write_text('#!'+shell+'\nexit 0\n');worker.chmod(0o700)
    import shlex
    launcher=tmp_path/'.local/bin/brodar';launcher.parent.mkdir(parents=True)
    launcher.write_text('#!/bin/sh\nexec '+shlex.quote(str(worker))+' "$@"\n')
    config=tmp_path/'.config/brodar';config.mkdir(parents=True);(config/'keep').write_text('keep')
    (env.parent/'files').mkdir();(env.parent/'files/keep').write_text('keep')
    result=subprocess.run([shell,str(ROOT/'uninstall.sh')],env={**os.environ,'HOME':str(tmp_path),'BRODAR_INSTALL_VENV':str(env)},text=True,capture_output=True)
    assert result.returncode==0,result.stderr
    assert not env.exists() and not launcher.exists()
    assert (config/'keep').read_text()=='keep' and (env.parent/'files/keep').exists()


def test_uninstall_refuses_unmarked_environment(tmp_path):
    env=tmp_path/'venv';env.mkdir();(env/'keep').write_text('keep')
    result=subprocess.run([shutil.which('sh'),str(ROOT/'uninstall.sh')],env={**os.environ,'HOME':str(tmp_path),'BRODAR_INSTALL_VENV':str(env)},capture_output=True)
    assert result.returncode!=0 and (env/'keep').exists()


def test_service_launcher_removal_is_profile_scoped(tmp_path,monkeypatch):
    from brodar.service import service
    monkeypatch.setenv('HOME',str(tmp_path))
    cfg=SimpleNamespace(runtime_dir=tmp_path/'runtime',profile_path=tmp_path/'profile.json')
    monkeypatch.setattr('brodar.control.socket_path',lambda cfg:tmp_path/'socket')
    launcher=tmp_path/'.termux/boot/brodar';launcher.parent.mkdir(parents=True)
    launcher.write_text('brodar --profile '+str(tmp_path/'other.json'))
    with pytest.raises(RuntimeError,match='another profile'):service(cfg,'uninstall')
    assert launcher.exists()
    launcher.write_text('brodar --profile '+str(cfg.profile_path))
    assert service(cfg,'uninstall')['data_deleted'] is False
    assert not launcher.exists()
