#!/usr/bin/env bash
# User-space launcher; the dedicated profile leaves EmuDeck's settings alone.
set -euo pipefail
KIU_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [[ ! -x "$KIU_ROOT/.venv/bin/python" ]]; then
    echo 'Create .venv and install the project first; see README.md.' >&2
    exit 1
fi
export XDG_CONFIG_HOME="${KIU_PROFILE_CONFIG:-$KIU_ROOT/local/profile/config}"
export QT_QPA_PLATFORM=xcb
exec "$KIU_ROOT/.venv/bin/python" "$KIU_ROOT/run_launcher.py" "$@"
