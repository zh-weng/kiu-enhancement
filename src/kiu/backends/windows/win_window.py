"""Azahar window geometry for the Windows mouse driver.

This module has no dependencies on image capture, older mouse hooks, or NumPy.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import csv
import io
import subprocess

user32 = ctypes.windll.user32
ENUM_PROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))


def find_azahar(pid: int | None = None) -> int:
    """Largest visible emulator window, optionally bound to an attached PID."""
    pids: set[int] = {pid} if pid is not None else set()
    if pid is None:
        output = subprocess.run(
            ["tasklist", "/FI", "IMAGENAME eq azahar.exe", "/FO", "CSV", "/NH"],
            capture_output=True, text=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        ).stdout
        for row in csv.reader(io.StringIO(output)):
            if len(row) >= 2 and row[0].casefold() == "azahar.exe":
                try:
                    pids.add(int(row[1]))
                except ValueError:
                    continue
    if not pids:
        raise RuntimeError("no azahar.exe process is running")

    best = [0, 0]

    def visit(hwnd, _):
        if not user32.IsWindowVisible(hwnd):
            return True
        pid = wintypes.DWORD()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
        if pid.value not in pids:
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        area = (rect.right - rect.left) * (rect.bottom - rect.top)
        if area > best[1]:
            best[0], best[1] = hwnd, area
        return True

    user32.EnumWindows(ENUM_PROC(visit), None)
    if not best[0]:
        raise RuntimeError("the emulator is running but has no visible window")
    return best[0]


def client_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(rect))
    origin = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(origin))
    return origin.x, origin.y, rect.right, rect.bottom
