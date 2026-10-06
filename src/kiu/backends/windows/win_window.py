"""Azahar window geometry for the Windows mouse driver.

This module has no dependencies on image capture, older mouse hooks, or NumPy.
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
from contextlib import contextmanager
import csv
import io
import re
import subprocess

user32 = ctypes.WinDLL("user32", use_last_error=True)
ENUM_PROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, ctypes.c_void_p)
user32.EnumWindows.argtypes = (ENUM_PROC, ctypes.c_void_p)
user32.EnumWindows.restype = wintypes.BOOL
user32.EnumChildWindows.argtypes = (wintypes.HWND, ENUM_PROC, ctypes.c_void_p)
user32.EnumChildWindows.restype = wintypes.BOOL
user32.GetForegroundWindow.restype = wintypes.HWND
user32.GetWindowThreadProcessId.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.DWORD))
user32.GetWindowThreadProcessId.restype = wintypes.DWORD
user32.IsWindowVisible.argtypes = (wintypes.HWND,)
user32.IsWindowVisible.restype = wintypes.BOOL
user32.GetWindowRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetClientRect.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.RECT))
user32.GetClientRect.restype = wintypes.BOOL
user32.ClientToScreen.argtypes = (wintypes.HWND, ctypes.POINTER(wintypes.POINT))
user32.ClientToScreen.restype = wintypes.BOOL
user32.GetClassNameW.argtypes = (wintypes.HWND, wintypes.LPWSTR, ctypes.c_int)
user32.GetClassNameW.restype = ctypes.c_int
user32.GetWindow.argtypes = (wintypes.HWND, wintypes.UINT)
user32.GetWindow.restype = wintypes.HWND
user32.IsIconic.argtypes = (wintypes.HWND,)
user32.IsIconic.restype = wintypes.BOOL
user32.IsChild.argtypes = (wintypes.HWND, wintypes.HWND)
user32.IsChild.restype = wintypes.BOOL

# Qt's Windows backend adds OwnDC only for OpenGL/MSWindowsOwnDC surfaces.
# Azahar's Vulkan child uses the same generic class as ordinary Qt widgets:
# it cannot be identified safely from that class alone. Unknown classes fail
# closed instead of treating the largest child/main-window client as a canvas.
QT_MAIN_CLASS = re.compile(r"Qt[56]\d*d?QWindowIcon(?:\{[\da-fA-F-]+\})?\Z")
QT_GL_CLASS = re.compile(r"Qt[56]\d*d?QWindowOwnDC(?:Icon)?(?:\{[\da-fA-F-]+\})?\Z")
GW_OWNER = 4


@contextmanager
def per_monitor_dpi():
    """Use physical coordinates on this thread, without changing Tk/process DPI.

    PMv1 is sufficient for physical coordinate queries if PMv2 is unavailable.
    Always restore the calling thread's previous context, including on failure.
    """
    try:
        set_context = user32.SetThreadDpiAwarenessContext
    except AttributeError as exc:
        raise RuntimeError("Per-monitor thread DPI awareness is unavailable") from exc
    set_context.argtypes = (ctypes.c_void_p,)
    set_context.restype = ctypes.c_void_p
    previous = set_context(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    if not previous:
        previous = set_context(ctypes.c_void_p(-3))  # PER_MONITOR_AWARE
    if not previous:
        raise RuntimeError("Cannot establish per-monitor thread DPI awareness")
    try:
        yield
    finally:
        if not set_context(ctypes.c_void_p(previous)):
            raise RuntimeError("Cannot restore the previous thread DPI awareness")


def _window_pid(hwnd: int) -> int | None:
    pid = wintypes.DWORD()
    if not user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid)):
        return None
    return pid.value


def _window_class(hwnd: int) -> str:
    name = ctypes.create_unicode_buffer(256)
    return name.value if user32.GetClassNameW(hwnd, name, len(name)) else ""


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
    """Client bounds in the caller's DPI coordinate space; check every API."""
    rect = wintypes.RECT()
    if not user32.GetClientRect(hwnd, ctypes.byref(rect)):
        raise ctypes.WinError(ctypes.get_last_error())
    start, end = wintypes.POINT(rect.left, rect.top), wintypes.POINT(rect.right, rect.bottom)
    if not user32.ClientToScreen(hwnd, ctypes.byref(start)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not user32.ClientToScreen(hwnd, ctypes.byref(end)):
        raise ctypes.WinError(ctypes.get_last_error())
    return start.x, start.y, end.x - start.x, end.y - start.y


def render_rect(pid: int) -> tuple[int, int, int, int] | None:
    """Physical client bounds of one positively identified native render child.

    Require a single visible/unowned Qt main window for this PID, and a single
    visible Qt OwnDC child beneath it. Generic Vulkan/software Qt children,
    detached/multiple windows, minimized windows and discovery races return
    None. This is the complete render canvas, not yet a top-screen viewport.
    """
    if type(pid) is not int or pid <= 0:
        raise ValueError("pid must be a positive integer")
    try:
        with per_monitor_dpi():
            roots = []

            def top(hwnd, _):
                if (user32.IsWindowVisible(hwnd) and _window_pid(hwnd) == pid
                        and not user32.GetWindow(hwnd, GW_OWNER)
                        and QT_MAIN_CLASS.fullmatch(_window_class(hwnd))):
                    roots.append(hwnd)
                return True

            if not user32.EnumWindows(ENUM_PROC(top), None):
                return None
            if len(roots) != 1 or user32.IsIconic(roots[0]):
                return None
            root = roots[0]
            children = []

            def child(hwnd, _):
                if (user32.IsWindowVisible(hwnd) and _window_pid(hwnd) == pid
                        and user32.IsChild(root, hwnd)
                        and QT_GL_CLASS.fullmatch(_window_class(hwnd))):
                    children.append(hwnd)
                return True

            user32.EnumChildWindows(root, ENUM_PROC(child), None)
            if len(children) != 1:
                return None
            hwnd = children[0]
            if (not user32.IsWindowVisible(root) or user32.IsIconic(root)
                    or _window_pid(root) != pid or not user32.IsWindowVisible(hwnd)
                    or _window_pid(hwnd) != pid or not user32.IsChild(root, hwnd)
                    or not QT_MAIN_CLASS.fullmatch(_window_class(root))
                    or not QT_GL_CLASS.fullmatch(_window_class(hwnd))):
                return None
            rect = client_rect(hwnd)
            return rect if rect[2] > 0 and rect[3] > 0 else None
    except (OSError, RuntimeError):
        # An unknown DPI context or disappearing HWND must not become a guess.
        return None
