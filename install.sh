#!/bin/sh
# Native source install. Run from a reviewed checkout; no shell-piped downloads.
set -eu
# Help must work before Python, a virtual environment or any credentials exist.
if [ "${1:-}" = "--help" ] || [ "${1:-}" = "-h" ]; then
    cat <<'HELP'
Usage: sh install.sh [--no-onboard] [onboarding options]
Install Brodar from this checkout into ~/.local/share/brodar/venv.
Creates ~/.local/bin/brodar, then opens the resumable setup wizard.
  --no-onboard    Install/update only; leave the running worker untouched
  --plain         Use simple prompts (recommended over SSH)
  --resume        Continue an interrupted setup
  --profile PATH  Set up a separate instance
  --help          Show this help without creating an environment
Set BRODAR_INSTALL_VENV to choose a different installation directory.
Afterward: export PATH="$HOME/.local/bin:$PATH"
Wizard: brodar onboard (or brodar onboard --plain over SSH)
Run: brodar service start | brodar tui | brodar --help
Stop: brodar service stop. Remove installation: sh uninstall.sh
HELP
    exit 0
fi
BRODAR_NO_ONBOARD=false
if [ "${1:-}" = "--no-onboard" ]; then BRODAR_NO_ONBOARD=true; shift; fi
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
# Ownership marker lets uninstall refuse to erase an unrelated environment.
printf '%s\n' 'brodar-source-install-v1' > "$BRODAR_ENV_DIR/.brodar-install"
mkdir -p "$HOME/.local/bin"
BRODAR_LAUNCHER="$HOME/.local/bin/brodar"
# Avoid shell interpolation of paths inside the installed launcher.
"$BRODAR_ENV_DIR/bin/python" - "$BRODAR_LAUNCHER" "$BRODAR_ENV_DIR/bin/brodar" <<'PY'
import os,shlex,shutil,sys
with open(sys.argv[1],'w') as output:
    output.write('#!'+(shutil.which('sh') or '/bin/sh')+'\nexec '+shlex.quote(sys.argv[2])+' "$@"\n')
os.chmod(sys.argv[1],0o700)
PY
printf '%s\n' 'Installed. Run: export PATH="$HOME/.local/bin:$PATH"' 'Help: brodar --help | Dashboard: brodar tui | Background: brodar service start'
if [ "$BRODAR_NO_ONBOARD" = true ]; then exit 0; fi
exec "$BRODAR_ENV_DIR/bin/brodar" onboard "$@"
