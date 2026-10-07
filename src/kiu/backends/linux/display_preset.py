"""Linux configuration paths and X11 binding for the authorized display preset.

XDG_CONFIG_HOME is inherited unchanged. A deployment may use it to keep a
dedicated user-space Azahar profile separate from an existing installation.
"""
from __future__ import annotations

import os
from pathlib import Path


def config_directory(azahar: Path) -> Path:
    portable = azahar.resolve().parent / "user"
    if portable.is_dir():
        return portable / "config"
    base = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config")))
    if not base.is_absolute():
        raise ValueError("XDG_CONFIG_HOME must be an absolute path")
    return base / "azahar-emu"


def require_emulator_closed(proc_root=Path("/proc")) -> None:
    """Never edit Qt's configuration behind a running Azahar instance."""
    for directory in Path(proc_root).iterdir():
        if not directory.name.isdecimal():
            continue
        try:
            command = (directory / "cmdline").read_bytes().split(b"\0", 1)[0]
            name = os.fsdecode(command).rsplit("/", 1)[-1].casefold()
        except FileNotFoundError:
            continue  # The process exited while enumerating /proc.
        except OSError as exc:
            raise RuntimeError("Cannot verify that all Azahar instances are closed") from exc
        if name in ("azahar", "azahar-qt", "azahar.appimage"):
            raise RuntimeError("请先关闭所有 Azahar 实例，再从启动器保存画面比例并开始。")


def save_display(azahar: Path, aspect, *, single_screen=False):
    from ...azahar_display import save_preset
    require_emulator_closed()
    if single_screen:
        from .x11 import fullscreen_size
        size = fullscreen_size()
        selected = size[0] / size[1] if aspect == "auto" else aspect
        return save_preset(config_directory(azahar), selected, display_size=size)
    return save_preset(config_directory(azahar), aspect)


def top_rect_provider(layout):
    from .x11 import render_rect

    def rectangle(pid: int):
        canvas = render_rect(pid)
        return layout.desktop_top(canvas) if canvas is not None else None

    return rectangle
