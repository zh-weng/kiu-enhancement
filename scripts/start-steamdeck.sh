#!/usr/bin/env bash
# Steam shortcut entry: only Azahar opens a window, using the desktop profile.
set -euo pipefail
KIU_ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
if [[ ! -x "$KIU_ROOT/.venv/bin/python" ]]; then
    echo 'Create .venv and install the project first; see README.md.' >&2
    exit 1
fi
export XDG_CONFIG_HOME="${KIU_PROFILE_CONFIG:-$KIU_ROOT/local/profile/config}"
export QT_QPA_PLATFORM=xcb
mkdir -p "$KIU_ROOT/local"
# Desktop and Gaming Mode share launcher.json; explicit CLI overrides still work.
exec "$KIU_ROOT/.venv/bin/python" -m kiu.linux_play "$@" >>"$KIU_ROOT/local/steamdeck.log" 2>&1
