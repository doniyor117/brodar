#!/bin/sh
# Remove only this source installation. Keep all user/remote data.
set -eu
if [ "${1:-}" = --help ] || [ "${1:-}" = -h ]; then
    echo 'Usage: sh uninstall.sh [--profile PATH]'
    echo 'Stops the managed worker, removes its matching startup launcher and source-installed CLI/venv.'
    echo 'Keeps configuration, chats, files, cloud resources, recovery keys, brain CLIs and this checkout.'
    echo 'If using systemd, first run: systemctl --user disable --now brodar'
    exit 0
fi
BRODAR_ENV_DIR=${BRODAR_INSTALL_VENV:-"$HOME/.local/share/brodar/venv"}
BRODAR_LAUNCHER="$HOME/.local/bin/brodar"
if [ ! -f "$BRODAR_ENV_DIR/.brodar-install" ] || [ "$(cat "$BRODAR_ENV_DIR/.brodar-install")" != brodar-source-install-v1 ]; then
    echo 'No marked source installation found. Refusing to delete an unrecognized environment.' >&2
    echo 'For an older install, run sh install.sh --no-onboard first; use the same BRODAR_INSTALL_VENV.' >&2
    exit 1
fi
"$BRODAR_ENV_DIR/bin/brodar" service stop "$@"
"$BRODAR_ENV_DIR/bin/brodar" service uninstall "$@"
# Validate both paths before deleting either one. Data/config are never purged.
"$BRODAR_ENV_DIR/bin/python" - "$BRODAR_ENV_DIR" "$BRODAR_LAUNCHER" <<'PY'
import os,shlex,shutil,sys
from pathlib import Path
env=Path(sys.argv[1]).resolve();launcher=Path(sys.argv[2])
if env in {Path('/'),Path.home().resolve(),(Path.home()/'.local').resolve(),(Path.home()/'.local/share/brodar').resolve()}:
    raise SystemExit('Unsafe installation path; refusing removal')
body='\nexec '+shlex.quote(str(Path(sys.argv[1])/'bin/brodar'))+' "$@"\n'
expected={'#!/bin/sh'+body,'#!'+(shutil.which('sh') or '/bin/sh')+body}
if launcher.exists():
    if launcher.is_symlink() or launcher.read_text() not in expected:raise SystemExit('CLI launcher belongs to another install; refusing removal')
    launcher.unlink()
shutil.rmtree(env)
print('Brodar installation removed. Configuration, runtime files and cloud data were kept.')
PY
