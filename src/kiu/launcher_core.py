"""A single, cancellable Windows launch session for stock Azahar.

The GUI owns preferences and presentation.  This module owns only the process
it launches, checked injection, and restoration of patches installed by that
session. A caller may explicitly request a persistent display preset; no
performance, input, or storage settings are changed. It never closes Azahar.
"""
from __future__ import annotations

from collections.abc import Callable
from contextlib import contextmanager
from dataclasses import dataclass
import math
from pathlib import Path
import socket
import struct
import subprocess
import sys
import threading
import time

from .cli import checked_states, guest_session
from .game_build import VerificationCancelled, verify_game

STARTUP_TIMEOUT = 60.0
POLL_INTERVAL = 0.2
GDB_TIMEOUT = 0.5


@dataclass(frozen=True)
class LauncherOptions:
    azahar: Path
    game: Path
    fps: bool = True
    aspect: float | str | None = 16 / 9
    mouse: bool = True
    sensitivity: float = 0.0025
    port: int = 24689
    sync_layout: bool = False
    single_screen: bool = False
    menu_route: bool = False


def validate_options(options: LauncherOptions) -> None:
    """Validate the current backend's options without importing Windows APIs."""
    if sys.platform != "win32":
        raise RuntimeError("The Launcher backend currently requires Windows; use the CLI on other platforms")
    if options.aspect == "auto":
        if not options.single_screen:
            raise ValueError("Automatic aspect requires single-screen fullscreen mode")
    elif options.aspect is not None and (
        type(options.aspect) not in (int, float)
        or not math.isfinite(options.aspect)
        or not any(math.isclose(options.aspect, value, rel_tol=0, abs_tol=1e-6)
                   for value in (5 / 3, 16 / 9, 21 / 9))
    ):
        raise ValueError("Aspect must be off, 5:3, 16:9, or 21:9")
    if not math.isfinite(options.sensitivity) or options.sensitivity <= 0:
        raise ValueError("Mouse sensitivity must be a finite positive number")
    if type(options.port) is not int or not 1 <= options.port <= 65535:
        raise ValueError("GDB port must be an integer between 1 and 65535")
    if type(options.sync_layout) is not bool:
        raise ValueError("Display preset selection must be a boolean")
    if any(type(value) is not bool for value in (options.single_screen, options.menu_route)):
        raise ValueError("Single-screen and menu injection selections must be boolean")
    if options.single_screen and not options.sync_layout:
        raise ValueError("Single-screen fullscreen requires the display preset option")
    azahar = Path(options.azahar)
    if azahar.name.lower() != "azahar.exe" or not azahar.is_file():
        raise ValueError("Select the stock Azahar executable named azahar.exe")
    if not Path(options.game).is_file():
        raise ValueError("Select your own decrypted CCI/3DS game file")


def _patch_modules():
    from .patches import patch_60fps, patch_widescreen, patch_aim_request, pin_reticle
    return {"fps": patch_60fps, "wide": patch_widescreen,
            "aim": patch_aim_request, "pin": pin_reticle}


def _port_owner(port: int) -> int | None:
    # Loaded only after validate_options accepted the Windows backend.
    from .backends.windows.guest_memory import listening_pid
    return listening_pid(port)


def _require_free_port(port: int) -> None:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        if hasattr(socket, "SO_EXCLUSIVEADDRUSE"):
            probe.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        try:
            probe.bind(("127.0.0.1", port))
        except OSError as exc:
            raise RuntimeError(
                f"GDB port {port} is in use; close the previous Azahar session first"
            ) from exc


def _run_mouse(argv, **options):
    from .backends.windows.native_fps_aim import main
    return main(argv, **options)


def _save_display(options):
    from .backends.windows.display_preset import save_display
    return save_display(Path(options.azahar), options.aspect, single_screen=options.single_screen)


def _menu_module():
    from .experimental import menu_render_pass
    return menu_render_pass


def _top_rect_provider(layout):
    from .backends.windows.display_preset import top_rect_provider
    return top_rect_provider(layout)


@contextmanager
def _external_program_dll_path():
    """Do not let a frozen launcher's bundled DLLs shadow Azahar's DLLs.

    PyInstaller changes SetDllDirectoryW for its own process. Windows children
    inherit that search path, so clear it only around Popen and restore the
    previous directory even if launching fails. Source and portable imports
    never load Windows APIs here.
    """
    if sys.platform != "win32" or not getattr(sys, "frozen", False):
        yield
        return
    import ctypes
    from ctypes import wintypes

    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    get_directory = kernel.GetDllDirectoryW
    get_directory.argtypes = (wintypes.DWORD, wintypes.LPWSTR)
    get_directory.restype = wintypes.DWORD
    set_directory = kernel.SetDllDirectoryW
    set_directory.argtypes = (wintypes.LPCWSTR,)
    set_directory.restype = wintypes.BOOL
    ctypes.set_last_error(0)
    size = get_directory(0, None)
    if not size and ctypes.get_last_error():
        raise ctypes.WinError(ctypes.get_last_error())
    previous = None
    if size:
        buffer = ctypes.create_unicode_buffer(size + 1)
        copied = get_directory(len(buffer), buffer)
        if not copied or copied >= len(buffer):
            raise OSError("Cannot read the frozen launcher's DLL directory")
        previous = buffer.value
    if not set_directory(None):
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        yield
    finally:
        if not set_directory(previous):
            raise ctypes.WinError(ctypes.get_last_error())


class LauncherSession:
    def __init__(self, options: LauncherOptions, on_status: Callable[[str], None]):
        self.options = options
        self.on_status = on_status
        self.thread: threading.Thread | None = None
        self.process: subprocess.Popen | None = None
        self.error: str | None = None
        self.restore_requested = True
        self._stop_event = threading.Event()
        self._monitor_done = threading.Event()
        self._running = False
        self._used = False
        self._lock = threading.Lock()
        self._loaded = {}
        self._owned: list[str] = []
        self._aspect = options.aspect

    @property
    def alive(self) -> bool:
        return self._running or (self.thread is not None and self.thread.is_alive())

    def start(self):
        if self.thread is not None or self._used:
            raise RuntimeError("Create a new LauncherSession for each launch")
        self.thread = threading.Thread(target=self.run, name="kiu-launcher", daemon=False)
        self.thread.start()
        return self

    def stop(self, restore: bool = True) -> None:
        self.restore_requested = restore
        self._stop_event.set()

    def _status(self, message: str) -> None:
        self.on_status(message)

    def _check_cancel(self) -> None:
        if self._stop_event.is_set():
            raise VerificationCancelled("Launch session stopped")

    def _process_running(self) -> bool:
        return self.process is not None and self.process.poll() is None

    def _require_owner(self) -> bool:
        owner = _port_owner(self.options.port)
        if owner is None:
            return False
        if self.process is None or owner != self.process.pid:
            raise RuntimeError("GDB port belongs to another process; refusing to attach")
        return True

    def _check_mouse_abi(self, gdb, names) -> None:
        for name in names:
            if name in ("aim", "pin"):
                module = self._loaded[name]
                expected = struct.pack("<II", module.MAGIC, module.VERSION)
                if gdb.read_mem(module.MAILBOX_VA, len(expected)) != expected:
                    raise RuntimeError(f"{name}: mailbox ABI mismatch; refusing input writes")

    def _check_cold_fps_code(self, gdb) -> bool:
        """Return whether known static FPS bytes are stock, never guess them."""
        fps = self._loaded["fps"]
        sites = [gdb.read_mem(site.address, len(site.expected)) for site in fps.PATCH_SITES]
        caves = [gdb.read_mem(address, len(blob)) for address, blob, _label in fps.CAVES]
        stock = all(raw == site.expected for raw, site in zip(sites, fps.PATCH_SITES)) \
            and all(raw == bytes(len(blob)) for raw, (_address, blob, _label) in zip(caves, fps.CAVES))
        installed = all(raw == site.replacement for raw, site in zip(sites, fps.PATCH_SITES)) \
            and all(raw == blob for raw, (_address, blob, _label) in zip(caves, fps.CAVES))
        if not (stock or installed):
            raise RuntimeError("fps: unknown/mixed code bytes; refusing cold-start retry")
        return stock

    def _check_display_projection(self, states) -> None:
        if not self.options.sync_layout or states["wide"]["state"] != "installed":
            return
        actual = states["wide"].get("aspect")
        if (self._aspect is None or type(actual) not in (int, float)
                or not math.isfinite(actual)
                or not math.isclose(actual, self._aspect, rel_tol=0, abs_tol=1e-5)):
            raise RuntimeError("Existing widescreen patch does not match the selected display preset; restart without that patch")

    def _install(self) -> None:
        selected = (["fps"] if self.options.fps else []) \
            + (["wide"] if self.options.aspect is not None else []) \
            + (["aim", "pin"] if self.options.mouse else [])
        if self.options.menu_route:
            selected.append("menu")
        checked = selected + (["wide"] if self.options.sync_layout and "wide" not in selected else [])
        deadline = time.monotonic() + STARTUP_TIMEOUT
        self._status("等待 Azahar 和游戏初始化")
        while time.monotonic() < deadline:
            self._check_cancel()
            returncode = self.process.poll()
            if returncode is not None:
                if returncode != 0:
                    raise RuntimeError(f"Azahar exited during initialization with return code {returncode}")
                raise VerificationCancelled("Azahar closed before the game initialized")
            if not self._require_owner():
                self._stop_event.wait(POLL_INTERVAL)
                continue
            try:
                with guest_session(self.options.port, timeout=GDB_TIMEOUT) as gdb:
                    if not self._require_owner():
                        raise RuntimeError("Cannot verify the connected GDB process owner")
                    fps = self._loaded["fps"]
                    scheduler = fps._u32(gdb, fps.SCHED_SLOT)
                    nominal = fps._u32(gdb, scheduler + fps.SCHED_NOMINAL_OFF) if scheduler else None
                    if scheduler == 0 or nominal == 60:
                        stock = self._check_cold_fps_code(gdb)
                        if nominal == 60 and not stock:
                            raise RuntimeError("fps: installed patch with constructor nominal 60 is not a cold stock state")
                        states = checked_states(gdb, [name for name in checked if name != "fps"], self._loaded)
                        self._check_display_projection(states)
                        self._check_mouse_abi(gdb, [name for name in states
                                                    if states[name]["state"] == "installed"])
                        # The context manager resumes all threads before retry:
                        # cold boot creates a nominal-60 scheduler, then game
                        # initialization selects 30. Never write during either
                        # transient; unknown values still fail below.
                    else:
                        fps._scheduler(gdb)  # Validate the expected nominal 30 Hz field.
                        states = checked_states(gdb, checked, self._loaded)
                        self._check_display_projection(states)
                        self._check_mouse_abi(gdb, [name for name in selected
                                                    if states[name]["state"] == "installed"])
                        self._check_cancel()
                        for name in selected:
                            if states[name]["state"] == "installed":
                                self._status(f"保留已有 {name} 补丁，不更改其设置")
                                continue
                            self._check_cancel()
                            self._owned.append(name)
                            module = self._loaded[name]
                            if name == "wide":
                                module.apply(gdb, self._aspect)
                            elif name in ("aim", "pin"):
                                module.apply(gdb, resume=False)
                            else:
                                module.apply(gdb)
                            self._status(f"已安装 {name} 增强")
                        if self.options.mouse:
                            self._check_mouse_abi(gdb, ("aim", "pin"))
                            self._loaded["aim"].set_gate(gdb, 0)
                            self._loaded["pin"].set_mode(gdb, 0)
                        return
            except OSError:
                # Connection refusal / socket timeout can precede stub readiness.
                # Never retry semantic or byte-check errors, or a partial install.
                if self._owned:
                    raise
            self._stop_event.wait(POLL_INTERVAL)
        raise RuntimeError("Azahar/game initialization timed out after 60 seconds")

    def _monitor(self) -> None:
        while not self._monitor_done.wait(POLL_INTERVAL):
            if not self._process_running():
                self._stop_event.set()
                return

    def _restore_owned(self) -> None:
        if not self.restore_requested or not self._owned or not self._process_running():
            return
        if not self._require_owner():
            raise RuntimeError("Cannot verify GDB ownership; restoration refused")
        self._status("正在恢复本次 Launcher 安装的增强")
        deadline = time.monotonic() + 2.0
        while True:
            busy = False
            with guest_session(self.options.port, timeout=GDB_TIMEOUT) as gdb:
                if not self._require_owner():
                    raise RuntimeError("Cannot verify connected GDB ownership; restoration refused")
                states = checked_states(gdb, self._owned, self._loaded)
                self._check_mouse_abi(gdb, [name for name in self._owned
                                          if states[name]["state"] == "installed"])
                if "menu" in states and states["menu"]["state"] == "installed":
                    busy = not self._loaded["menu"].can_revert(gdb)
                if not busy:
                    for name in reversed(self._owned):
                        if states[name]["state"] == "installed":
                            module = self._loaded[name]
                            if name in ("aim", "pin"):
                                module.revert(gdb, resume=False)
                            else:
                                module.revert(gdb)
                    self._owned.clear()
                    return
            # Resume a redirected pass to its post/restore boundary before retry.
            if time.monotonic() >= deadline:
                raise RuntimeError("Menu render pass did not reach a safe restore boundary; restart the game")
            time.sleep(POLL_INTERVAL)

    def run(self) -> None:
        """Synchronous entry used by the worker and deterministic tests."""
        with self._lock:
            if self._used:
                raise RuntimeError("Create a new LauncherSession for each launch")
            self._used = self._running = True
        monitor = None
        layout = None
        try:
            validate_options(self.options)
            self._check_cancel()
            verify_game(Path(self.options.game), cancelled=self._stop_event.is_set, progress=self._status)
            self._check_cancel()
            _require_free_port(self.options.port)
            if self.options.sync_layout:
                self._status("正在保存单屏全屏 preset" if self.options.single_screen else "正在保存双屏比例 preset")
                layout = _save_display(self.options)
                if self.options.aspect == "auto":
                    self._aspect = layout.aspect
                self._check_cancel()
            azahar = Path(self.options.azahar).resolve()
            game = Path(self.options.game).resolve()
            self._status("正在启动 Azahar")
            with _external_program_dll_path():
                self.process = subprocess.Popen(
                    # Some official Windows builds reject the long spelling.
                    [str(azahar), "-g", str(self.options.port),
                     *(["-f"] if self.options.single_screen else []), str(game)],
                    cwd=azahar.parent, shell=False,
                )
            self._loaded = _patch_modules()
            if self.options.menu_route:
                self._loaded["menu"] = _menu_module()
            self._install()
            self._check_cancel()
            if self.options.mouse:
                monitor = threading.Thread(target=self._monitor, name="kiu-launch-monitor", daemon=False)
                monitor.start()
                # Never map an unverified Launcher layout to the entire desktop.
                # CLI callers retain their explicitly selected mapping behavior.
                rectangle = _top_rect_provider(layout) if layout is not None else lambda _pid: None
                _run_mouse(
                    ["--port", str(self.options.port), "--sensitivity", str(self.options.sensitivity)],
                    stop_event=self._stop_event, status_callback=self._status,
                    auto_activate=True, wait_for_battle=True, expected_pid=self.process.pid,
                    rect_provider=rectangle,
                )
            else:
                self._status("增强已启用；关闭 Azahar 或点击停止可结束此会话")
                while self._process_running() and not self._stop_event.wait(POLL_INTERVAL):
                    pass
        except VerificationCancelled as exc:
            self._status(str(exc))
        except Exception as exc:
            if self.process is not None and self.process.poll() == 0:
                self._status("Azahar 已关闭")
            else:
                self.error = str(exc)
                self._status(f"Launcher 错误：{self.error}")
        finally:
            self._stop_event.set()
            self._monitor_done.set()
            if monitor is not None:
                monitor.join()
            returncode = self.process.poll() if self.process is not None else None
            if returncode not in (None, 0) and self.error is None:
                self.error = f"Azahar exited with return code {returncode}"
                self._status(f"Launcher 错误：{self.error}")
            try:
                self._restore_owned()
            except Exception as exc:
                message = f"Enhancement restoration refused or failed: {exc}; restart the game"
                self.error = f"{self.error}; {message}" if self.error else message
                self._status(message)
            self._running = False
            self._status("Launcher 会话已停止；未关闭 Azahar")
