"""Reject credential/private/runtime material in the built wheel."""
import re
import sys
import zipfile
from pathlib import PurePosixPath
from urllib.parse import urlsplit

for filename in sys.argv[1:]:
    with zipfile.ZipFile(filename) as archive:
        names=archive.namelist()
        for name in names:
            parts=PurePosixPath(name).parts
            if any(p in {'.git','.env','runtime.env','profile.json','private-config-recovery.fernet','auth.json','.credentials.json','recovery-key.json'} for p in parts):
                raise SystemExit('Private file included in wheel: '+name)
        for required in ['brodar/assets/persona.md','brodar/assets/harness.md','brodar/migrations/014_file_lifecycle.sql','brodar/service_supervisor.py','brodar/onboarding.py']:
            if required not in names:raise SystemExit('Missing packaged asset: '+required)
    print('Package inventory passed:',filename)
