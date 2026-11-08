#!/bin/sh
# Native source install. Run from a reviewed checkout; no shell-piped downloads.
set -eu
BRODAR_SOURCE_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
BRODAR_ENV_DIR=${BRODAR_INSTALL_VENV:-"$HOME/.local/share/brodar/venv"}
case "${PREFIX:-}" in
  /data/data/com.termux/files/usr)
    command -v python >/dev/null || { echo 'Install native Termux Python: pkg install python'; exit 1; }
    echo 'Native Termux: if Pillow/cryptography need build dependencies, use pkg install clang rust libjpeg-turbo libpng libffi openssl pkg-config'
    ;;
  *) command -v python3 >/dev/null || { echo 'Python 3.11+ is required.'; exit 1; } ;;
esac
BRODAR_PYTHON=$(command -v python3 || command -v python)
"$BRODAR_PYTHON" -c 'import sys; assert sys.version_info >= (3,11), "Python 3.11+ is required"'
umask 077
"$BRODAR_PYTHON" -m venv "$BRODAR_ENV_DIR"
"$BRODAR_ENV_DIR/bin/python" -m pip install "$BRODAR_SOURCE_DIR"
mkdir -p "$HOME/.local/bin"
BRODAR_LAUNCHER="$HOME/.local/bin/brodar"
# Avoid shell interpolation of paths inside the installed launcher.
"$BRODAR_ENV_DIR/bin/python" - "$BRODAR_LAUNCHER" "$BRODAR_ENV_DIR/bin/brodar" <<'PY'
import os,shlex,sys
with open(sys.argv[1],'w') as output:
    output.write('#!/bin/sh\nexec '+shlex.quote(sys.argv[2])+' "$@"\n')
os.chmod(sys.argv[1],0o700)
PY
printf '%s\n' 'Installed. Add ~/.local/bin to PATH if needed.'
exec "$BRODAR_ENV_DIR/bin/brodar" onboard "$@"
