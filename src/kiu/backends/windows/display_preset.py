"""Windows paths and window binding for an explicitly saved display preset.

INI editing and rectangle math live in the platform-neutral azahar_display
module. No game, input, or Windows DLL is accessed when importing this file.
"""
from __future__ import annotations

import csv
import io
from pathlib import Path
import subprocess


def config_directory(azahar: Path) -> Path:
    portable = azahar.resolve().parent / "user"
    if portable.is_dir():
        return portable / "config"
    import ctypes
    from ctypes import wintypes
    shell = ctypes.WinDLL("shell32", use_last_error=True)
    folder = shell.SHGetFolderPathW
    folder.argtypes = (wintypes.HWND, ctypes.c_int, wintypes.HANDLE,
                       wintypes.DWORD, wintypes.LPWSTR)
    folder.restype = ctypes.c_long
    result = ctypes.create_unicode_buffer(260)
    if folder(None, 0x1A, None, 0, result) != 0 or not result.value:
        raise OSError("Cannot locate Azahar's roaming configuration directory")
    return Path(result.value) / "Azahar" / "config"


def require_emulator_closed() -> None:
    """Qt saves in-memory settings; never edit behind a running instance."""
    result = subprocess.run(
        ["tasklist", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, check=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    rows = list(csv.reader(io.StringIO(result.stdout)))
    if not rows or any(len(row) != 5 or not row[1].isdigit() for row in rows):
        raise RuntimeError("Cannot verify that all Azahar instances are closed")
    for row in rows:
        if row[0].casefold() == "azahar.exe":
            raise RuntimeError("请先关闭所有 Azahar 实例，再从启动器保存画面比例并开始。")


def fullscreen_size():
    from . import win_window
    with win_window.per_monitor_dpi():
        width = win_window.user32.GetSystemMetrics(0)
        height = win_window.user32.GetSystemMetrics(1)
    if width <= 0 or height <= 0:
        raise OSError("Cannot read the fullscreen display size")
    return width, height


def save_display(azahar: Path, aspect, *, single_screen=False):
    from ...azahar_display import save_preset
    require_emulator_closed()
    if single_screen:
        size = fullscreen_size()
        selected = size[0] / size[1] if aspect == "auto" else aspect
        return save_preset(config_directory(azahar), selected, display_size=size)
    return save_preset(config_directory(azahar), aspect)


def top_rect_provider(layout):
    """Bind the saved raw-pixel geometry to this process's render surface."""
    from . import win_window

    def rectangle(pid: int):
        canvas = win_window.render_rect(pid)
        return layout.desktop_top(canvas) if canvas is not None else None

    return rectangle
