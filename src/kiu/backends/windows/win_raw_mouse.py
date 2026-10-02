"""Windows Raw Input source for the native mouse-aim bridge.

This module deliberately stops at the host-input boundary.  It owns a
message-only window, receives relative mouse packets, and reports hotkeys; it
does not know about GDB, the guest mailbox, DSU, or Azahar.

``RawMouseInput`` invokes callbacks on its message-loop thread.  Consumers
should keep those callbacks short (normally just accumulating deltas under a
lock) and perform slow I/O on another thread.
"""

from __future__ import annotations

import ctypes
import os
import threading
from dataclasses import dataclass, replace
from typing import Callable, Final, Protocol


# Fixed-width aliases keep the layouts explicit even when this file is parsed
# on a non-Windows host.  The production runtime is 64-bit Windows.
BOOL = ctypes.c_int32
ATOM = ctypes.c_uint16
WORD = ctypes.c_uint16
USHORT = ctypes.c_uint16
UINT = ctypes.c_uint32
DWORD = ctypes.c_uint32
ULONG = ctypes.c_uint32
INT = ctypes.c_int32
LONG = ctypes.c_int32
HANDLE = ctypes.c_void_p
HINSTANCE = ctypes.c_void_p
HICON = ctypes.c_void_p
HCURSOR = ctypes.c_void_p
HBRUSH = ctypes.c_void_p
HMENU = ctypes.c_void_p
HWND = ctypes.c_void_p
LPVOID = ctypes.c_void_p
LPCWSTR = ctypes.c_wchar_p
WPARAM = ctypes.c_size_t
LPARAM = ctypes.c_ssize_t
LRESULT = ctypes.c_ssize_t

_WINFUNCTYPE = getattr(ctypes, "WINFUNCTYPE", ctypes.CFUNCTYPE)


WM_DESTROY: Final = 0x0002
WM_CLOSE: Final = 0x0010
WM_INPUT: Final = 0x00FF
WM_HOTKEY: Final = 0x0312

RIM_INPUT: Final = 0
RIM_TYPEMOUSE: Final = 0
RID_INPUT: Final = 0x10000003
RIDEV_REMOVE: Final = 0x00000001
RIDEV_INPUTSINK: Final = 0x00000100

HID_USAGE_PAGE_GENERIC: Final = 0x01
HID_USAGE_GENERIC_MOUSE: Final = 0x02

MOUSE_MOVE_ABSOLUTE: Final = 0x0001

MOD_NOREPEAT: Final = 0x4000
VK_ESCAPE: Final = 0x1B
VK_F4: Final = 0x73

HOTKEY_F4_ID: Final = 0x4B10
HOTKEY_ESCAPE_ID: Final = 0x4B11

UINT_ERROR: Final = 0xFFFFFFFF
_POINTER_MASK: Final = (1 << (ctypes.sizeof(ctypes.c_void_p) * 8)) - 1
HWND_MESSAGE: Final = ctypes.c_void_p((-3) & _POINTER_MASK)


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", HWND),
        ("message", UINT),
        ("wParam", WPARAM),
        ("lParam", LPARAM),
        ("time", DWORD),
        ("pt", POINT),
        ("lPrivate", DWORD),
    ]


WNDPROC = _WINFUNCTYPE(LRESULT, HWND, UINT, WPARAM, LPARAM)


class WNDCLASSEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", UINT),
        ("style", UINT),
        ("lpfnWndProc", WNDPROC),
        ("cbClsExtra", INT),
        ("cbWndExtra", INT),
        ("hInstance", HINSTANCE),
        ("hIcon", HICON),
        ("hCursor", HCURSOR),
        ("hbrBackground", HBRUSH),
        ("lpszMenuName", LPCWSTR),
        ("lpszClassName", LPCWSTR),
        ("hIconSm", HICON),
    ]


class RAWINPUTDEVICE(ctypes.Structure):
    _fields_ = [
        ("usUsagePage", USHORT),
        ("usUsage", USHORT),
        ("dwFlags", DWORD),
        ("hwndTarget", HWND),
    ]


class RAWINPUTHEADER(ctypes.Structure):
    _fields_ = [
        ("dwType", DWORD),
        ("dwSize", DWORD),
        ("hDevice", HANDLE),
        ("wParam", WPARAM),
    ]


class _RAWMOUSE_BUTTON_FIELDS(ctypes.Structure):
    _fields_ = [("usButtonFlags", USHORT), ("usButtonData", USHORT)]


class _RAWMOUSE_BUTTONS(ctypes.Union):
    _anonymous_ = ("fields",)
    _fields_ = [("ulButtons", ULONG), ("fields", _RAWMOUSE_BUTTON_FIELDS)]


class RAWMOUSE(ctypes.Structure):
    _anonymous_ = ("buttons",)
    _fields_ = [
        ("usFlags", USHORT),
        ("buttons", _RAWMOUSE_BUTTONS),
        ("ulRawButtons", ULONG),
        ("lLastX", LONG),
        ("lLastY", LONG),
        ("ulExtraInformation", ULONG),
    ]


class _RAWINPUT_DATA(ctypes.Union):
    _fields_ = [("mouse", RAWMOUSE)]


class RAWINPUT(ctypes.Structure):
    _anonymous_ = ("data",)
    _fields_ = [("header", RAWINPUTHEADER), ("data", _RAWINPUT_DATA)]


@dataclass(frozen=True)
class RawMousePacket:
    """Decoded movement fields from one ``RIM_TYPEMOUSE`` packet."""

    dx: int
    dy: int
    absolute: bool


@dataclass
class RawMouseStats:
    """Snapshot of input-source counters."""

    raw_input_messages: int = 0
    relative_packets: int = 0
    absolute_packets_ignored: int = 0
    zero_delta_packets: int = 0
    non_mouse_packets: int = 0
    malformed_packets: int = 0
    read_errors: int = 0
    callback_errors: int = 0
    f4_hotkeys: int = 0
    escape_hotkeys: int = 0


class Win32CallError(OSError):
    """A Win32 call failed; ``winerror`` contains ``GetLastError()``."""


def _win32_error(call: str) -> Win32CallError:
    error = ctypes.get_last_error()
    detail = ctypes.FormatError(error).strip() if error else "unknown error"
    exc = Win32CallError(error, f"{call} failed: {detail}")
    exc.winerror = error
    return exc


def decode_raw_mouse_packet(data: bytes | bytearray | memoryview) -> RawMousePacket | None:
    """Decode a copied ``RAWINPUT`` packet.

    Returns ``None`` for non-mouse input.  Truncated or internally inconsistent
    input raises ``ValueError`` so the caller can count malformed packets.
    """

    raw = bytes(data)
    header_size = ctypes.sizeof(RAWINPUTHEADER)
    if len(raw) < header_size:
        raise ValueError(f"RAWINPUT is shorter than its {header_size}-byte header")

    header = RAWINPUTHEADER.from_buffer_copy(raw)
    declared_size = int(header.dwSize)
    if declared_size < header_size:
        raise ValueError(f"RAWINPUT declares invalid size {declared_size}")
    if declared_size > len(raw):
        raise ValueError(
            f"RAWINPUT declares {declared_size} bytes but only {len(raw)} were copied"
        )
    if int(header.dwType) != RIM_TYPEMOUSE:
        return None

    required = header_size + ctypes.sizeof(RAWMOUSE)
    if declared_size < required or len(raw) < required:
        raise ValueError(f"mouse RAWINPUT is shorter than {required} bytes")
    mouse = RAWMOUSE.from_buffer_copy(raw, header_size)
    return RawMousePacket(
        dx=int(mouse.lLastX),
        dy=int(mouse.lLastY),
        absolute=bool(int(mouse.usFlags) & MOUSE_MOVE_ABSOLUTE),
    )


class _Api(Protocol):
    def get_module_handle(self) -> int: ...

    def register_class(self, window_class: WNDCLASSEXW) -> int: ...

    def unregister_class(self, class_name: str, instance: int) -> None: ...

    def create_message_window(self, class_name: str, instance: int) -> int: ...

    def destroy_window(self, hwnd: int) -> None: ...

    def def_window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int: ...

    def register_raw_input_device(self, device: RAWINPUTDEVICE) -> None: ...

    def read_raw_input(self, raw_input_handle: int) -> bytes: ...

    def register_hotkey(self, hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> None: ...

    def unregister_hotkey(self, hwnd: int, hotkey_id: int) -> None: ...

    def get_message(self, message: MSG) -> int: ...

    def translate_message(self, message: MSG) -> None: ...

    def dispatch_message(self, message: MSG) -> int: ...

    def post_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> None: ...

    def post_quit_message(self, exit_code: int) -> None: ...


class _Win32Api:
    """Thin typed wrapper over every Win32 function used by this module."""

    def __init__(self) -> None:
        if os.name != "nt":
            raise OSError("Windows Raw Input is only available on Windows")
        if ctypes.sizeof(ctypes.c_void_p) != 8:
            raise OSError("native mouse aim requires a 64-bit Python process")

        self.user32 = ctypes.WinDLL("user32", use_last_error=True)
        self.kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

        self.RegisterClassExW = self.user32.RegisterClassExW
        self.RegisterClassExW.argtypes = [ctypes.POINTER(WNDCLASSEXW)]
        self.RegisterClassExW.restype = ATOM

        self.UnregisterClassW = self.user32.UnregisterClassW
        self.UnregisterClassW.argtypes = [LPCWSTR, HINSTANCE]
        self.UnregisterClassW.restype = BOOL

        self.CreateWindowExW = self.user32.CreateWindowExW
        self.CreateWindowExW.argtypes = [
            DWORD,
            LPCWSTR,
            LPCWSTR,
            DWORD,
            INT,
            INT,
            INT,
            INT,
            HWND,
            HMENU,
            HINSTANCE,
            LPVOID,
        ]
        self.CreateWindowExW.restype = HWND

        self.DestroyWindow = self.user32.DestroyWindow
        self.DestroyWindow.argtypes = [HWND]
        self.DestroyWindow.restype = BOOL

        self.DefWindowProcW = self.user32.DefWindowProcW
        self.DefWindowProcW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        self.DefWindowProcW.restype = LRESULT

        self.RegisterRawInputDevices = self.user32.RegisterRawInputDevices
        self.RegisterRawInputDevices.argtypes = [
            ctypes.POINTER(RAWINPUTDEVICE),
            UINT,
            UINT,
        ]
        self.RegisterRawInputDevices.restype = BOOL

        self.GetRawInputData = self.user32.GetRawInputData
        self.GetRawInputData.argtypes = [
            HANDLE,
            UINT,
            LPVOID,
            ctypes.POINTER(UINT),
            UINT,
        ]
        self.GetRawInputData.restype = UINT

        self.RegisterHotKey = self.user32.RegisterHotKey
        self.RegisterHotKey.argtypes = [HWND, INT, UINT, UINT]
        self.RegisterHotKey.restype = BOOL

        self.UnregisterHotKey = self.user32.UnregisterHotKey
        self.UnregisterHotKey.argtypes = [HWND, INT]
        self.UnregisterHotKey.restype = BOOL

        self.GetMessageW = self.user32.GetMessageW
        self.GetMessageW.argtypes = [ctypes.POINTER(MSG), HWND, UINT, UINT]
        self.GetMessageW.restype = BOOL

        self.TranslateMessage = self.user32.TranslateMessage
        self.TranslateMessage.argtypes = [ctypes.POINTER(MSG)]
        self.TranslateMessage.restype = BOOL

        self.DispatchMessageW = self.user32.DispatchMessageW
        self.DispatchMessageW.argtypes = [ctypes.POINTER(MSG)]
        self.DispatchMessageW.restype = LRESULT

        self.PostMessageW = self.user32.PostMessageW
        self.PostMessageW.argtypes = [HWND, UINT, WPARAM, LPARAM]
        self.PostMessageW.restype = BOOL

        self.PostQuitMessage = self.user32.PostQuitMessage
        self.PostQuitMessage.argtypes = [INT]
        self.PostQuitMessage.restype = None

        self.GetModuleHandleW = self.kernel32.GetModuleHandleW
        self.GetModuleHandleW.argtypes = [LPCWSTR]
        self.GetModuleHandleW.restype = HINSTANCE

    @staticmethod
    def _handle(value: int | None) -> ctypes.c_void_p:
        return ctypes.c_void_p((int(value or 0)) & _POINTER_MASK)

    def get_module_handle(self) -> int:
        handle = self.GetModuleHandleW(None)
        if not handle:
            raise _win32_error("GetModuleHandleW")
        return int(handle)

    def register_class(self, window_class: WNDCLASSEXW) -> int:
        atom = self.RegisterClassExW(ctypes.byref(window_class))
        if not atom:
            raise _win32_error("RegisterClassExW")
        return int(atom)

    def unregister_class(self, class_name: str, instance: int) -> None:
        if not self.UnregisterClassW(class_name, self._handle(instance)):
            raise _win32_error("UnregisterClassW")

    def create_message_window(self, class_name: str, instance: int) -> int:
        hwnd = self.CreateWindowExW(
            0,
            class_name,
            class_name,
            0,
            0,
            0,
            0,
            0,
            HWND_MESSAGE,
            None,
            self._handle(instance),
            None,
        )
        if not hwnd:
            raise _win32_error("CreateWindowExW")
        return int(hwnd)

    def destroy_window(self, hwnd: int) -> None:
        if not self.DestroyWindow(self._handle(hwnd)):
            raise _win32_error("DestroyWindow")

    def def_window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        return int(
            self.DefWindowProcW(
                self._handle(hwnd),
                message,
                wparam,
                lparam,
            )
        )

    def register_raw_input_device(self, device: RAWINPUTDEVICE) -> None:
        if not self.RegisterRawInputDevices(
            ctypes.byref(device),
            1,
            ctypes.sizeof(RAWINPUTDEVICE),
        ):
            raise _win32_error("RegisterRawInputDevices")

    def read_raw_input(self, raw_input_handle: int) -> bytes:
        handle = self._handle(raw_input_handle)
        size = UINT(0)
        result = int(
            self.GetRawInputData(
                handle,
                RID_INPUT,
                None,
                ctypes.byref(size),
                ctypes.sizeof(RAWINPUTHEADER),
            )
        )
        if result == UINT_ERROR:
            raise _win32_error("GetRawInputData(size)")
        if result != 0:
            raise Win32CallError(
                0,
                f"GetRawInputData(size) returned {result}, expected zero",
            )
        if int(size.value) < ctypes.sizeof(RAWINPUTHEADER):
            raise Win32CallError(
                0,
                f"GetRawInputData reported undersized packet ({size.value} bytes)",
            )

        buffer = (ctypes.c_ubyte * int(size.value))()
        copied = int(
            self.GetRawInputData(
                handle,
                RID_INPUT,
                ctypes.cast(buffer, LPVOID),
                ctypes.byref(size),
                ctypes.sizeof(RAWINPUTHEADER),
            )
        )
        if copied == UINT_ERROR:
            raise _win32_error("GetRawInputData(data)")
        if copied > len(buffer):
            raise Win32CallError(
                0,
                f"GetRawInputData copied invalid length {copied} into {len(buffer)} bytes",
            )
        return bytes(buffer[:copied])

    def register_hotkey(self, hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> None:
        if not self.RegisterHotKey(self._handle(hwnd), hotkey_id, modifiers, vk):
            raise _win32_error("RegisterHotKey")

    def unregister_hotkey(self, hwnd: int, hotkey_id: int) -> None:
        if not self.UnregisterHotKey(self._handle(hwnd), hotkey_id):
            raise _win32_error("UnregisterHotKey")

    def get_message(self, message: MSG) -> int:
        result = int(self.GetMessageW(ctypes.byref(message), None, 0, 0))
        if result == -1:
            raise _win32_error("GetMessageW")
        return result

    def translate_message(self, message: MSG) -> None:
        self.TranslateMessage(ctypes.byref(message))

    def dispatch_message(self, message: MSG) -> int:
        return int(self.DispatchMessageW(ctypes.byref(message)))

    def post_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> None:
        if not self.PostMessageW(self._handle(hwnd), message, wparam, lparam):
            raise _win32_error("PostMessageW")

    def post_quit_message(self, exit_code: int) -> None:
        self.PostQuitMessage(exit_code)


DeltaCallback = Callable[[int, int], None]
HotkeyCallback = Callable[[], None]
ErrorCallback = Callable[[BaseException], None]


class RawMouseInput:
    """Thread-owned Raw Input window with relative-delta and hotkey callbacks."""

    def __init__(
        self,
        on_delta: DeltaCallback,
        *,
        on_f4: HotkeyCallback | None = None,
        on_escape: HotkeyCallback | None = None,
        on_error: ErrorCallback | None = None,
        api: _Api | None = None,
    ) -> None:
        if not callable(on_delta):
            raise TypeError("on_delta must be callable")
        self._on_delta = on_delta
        self._on_f4 = on_f4
        self._on_escape = on_escape
        self._on_error = on_error
        self._api: _Api = api if api is not None else _Win32Api()

        self._lifecycle_lock = threading.RLock()
        self._state_lock = threading.RLock()
        self._ready = threading.Event()
        self._stopped = threading.Event()
        self._thread: threading.Thread | None = None
        self._hwnd = 0
        self._stop_requested = False
        self._startup_error: BaseException | None = None
        self._thread_error: BaseException | None = None
        self._last_error: BaseException | None = None
        self._stats = RawMouseStats()
        self._window_destroyed = False
        self._wndproc_callback: WNDPROC | None = None

        self._class_name = f"KidIcarusRawMouse_{os.getpid()}_{id(self):x}"

    @property
    def running(self) -> bool:
        with self._state_lock:
            thread = self._thread
            return bool(
                thread
                and thread.is_alive()
                and self._ready.is_set()
                and self._startup_error is None
                and self._hwnd
            )

    @property
    def hwnd(self) -> int | None:
        with self._state_lock:
            return self._hwnd or None

    @property
    def last_error(self) -> BaseException | None:
        with self._state_lock:
            return self._last_error

    @property
    def thread_error(self) -> BaseException | None:
        with self._state_lock:
            return self._thread_error

    def stats(self) -> RawMouseStats:
        with self._state_lock:
            return replace(self._stats)

    def start(self, timeout: float = 5.0) -> "RawMouseInput":
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        with self._lifecycle_lock:
            with self._state_lock:
                if self._thread is not None and self._thread.is_alive():
                    if self._ready.is_set() and self._startup_error is None:
                        return self
                    raise RuntimeError("RawMouseInput is already starting")

                self._ready = threading.Event()
                self._stopped = threading.Event()
                self._stop_requested = False
                self._startup_error = None
                self._thread_error = None
                self._window_destroyed = False
                thread = threading.Thread(
                    target=self._thread_main,
                    name="KIU-RawMouseInput",
                    daemon=True,
                )
                self._thread = thread
                thread.start()

            if not self._ready.wait(timeout):
                self._request_stop(thread)
                thread.join(timeout)
                raise TimeoutError("timed out starting RawMouseInput")

            with self._state_lock:
                startup_error = self._startup_error
            if startup_error is not None:
                thread.join(timeout)
                raise RuntimeError("could not start RawMouseInput") from startup_error
            return self

    def stop(self, timeout: float = 5.0) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")

        with self._lifecycle_lock:
            with self._state_lock:
                thread = self._thread
            if thread is None:
                return

            self._request_stop(thread)
            if thread is threading.current_thread():
                return
            thread.join(timeout)
            if thread.is_alive():
                raise TimeoutError("timed out stopping RawMouseInput")
            with self._state_lock:
                if self._thread is thread:
                    self._thread = None

    def __enter__(self) -> "RawMouseInput":
        return self.start()

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        self.stop()

    def _request_stop(self, thread: threading.Thread) -> None:
        del thread  # Documents which generation of the worker the caller owns.
        with self._state_lock:
            self._stop_requested = True
            hwnd = self._hwnd
        if hwnd:
            try:
                self._api.post_message(hwnd, WM_CLOSE, 0, 0)
            except BaseException as exc:
                self._record_error(exc)

    def _thread_main(self) -> None:
        class_registered = False
        raw_registered = False
        f4_registered = False
        escape_registered = False
        instance = 0
        hwnd = 0

        try:
            self._wndproc_callback = WNDPROC(self._window_proc)
            instance = self._api.get_module_handle()
            window_class = WNDCLASSEXW()
            window_class.cbSize = ctypes.sizeof(WNDCLASSEXW)
            window_class.lpfnWndProc = self._wndproc_callback
            window_class.hInstance = instance
            window_class.lpszClassName = self._class_name
            self._api.register_class(window_class)
            class_registered = True

            hwnd = self._api.create_message_window(self._class_name, instance)
            with self._state_lock:
                self._hwnd = hwnd

            raw_device = RAWINPUTDEVICE(
                HID_USAGE_PAGE_GENERIC,
                HID_USAGE_GENERIC_MOUSE,
                RIDEV_INPUTSINK,
                HWND(hwnd),
            )
            self._api.register_raw_input_device(raw_device)
            raw_registered = True

            self._api.register_hotkey(hwnd, HOTKEY_F4_ID, MOD_NOREPEAT, VK_F4)
            f4_registered = True
            self._api.register_hotkey(hwnd, HOTKEY_ESCAPE_ID, MOD_NOREPEAT, VK_ESCAPE)
            escape_registered = True

            with self._state_lock:
                should_stop = self._stop_requested
            self._ready.set()
            if should_stop:
                self._api.post_message(hwnd, WM_CLOSE, 0, 0)

            message = MSG()
            while self._api.get_message(message) != 0:
                self._api.translate_message(message)
                self._api.dispatch_message(message)
        except BaseException as exc:
            with self._state_lock:
                if self._ready.is_set():
                    self._thread_error = exc
                else:
                    self._startup_error = exc
                self._last_error = exc
            self._notify_error(exc)
        finally:
            if escape_registered:
                self._cleanup(lambda: self._api.unregister_hotkey(hwnd, HOTKEY_ESCAPE_ID))
            if f4_registered:
                self._cleanup(lambda: self._api.unregister_hotkey(hwnd, HOTKEY_F4_ID))
            if raw_registered:
                remove_device = RAWINPUTDEVICE(
                    HID_USAGE_PAGE_GENERIC,
                    HID_USAGE_GENERIC_MOUSE,
                    RIDEV_REMOVE,
                    None,
                )
                self._cleanup(lambda: self._api.register_raw_input_device(remove_device))
            if hwnd and not self._window_destroyed:
                self._cleanup(lambda: self._api.destroy_window(hwnd))
            if class_registered:
                self._cleanup(lambda: self._api.unregister_class(self._class_name, instance))

            with self._state_lock:
                self._hwnd = 0
            self._wndproc_callback = None
            self._ready.set()
            self._stopped.set()

    def _window_proc(self, hwnd, message, wparam, lparam) -> int:
        hwnd_value = int(hwnd or 0)
        message_value = int(message)
        wparam_value = int(wparam)
        lparam_value = int(lparam)
        try:
            if message_value == WM_INPUT:
                self._handle_raw_input(lparam_value)
                if wparam_value == RIM_INPUT:
                    return self._api.def_window_proc(
                        hwnd_value,
                        message_value,
                        wparam_value,
                        lparam_value,
                    )
                return 0
            if message_value == WM_HOTKEY:
                self._handle_hotkey(wparam_value)
                return 0
            if message_value == WM_CLOSE:
                self._api.destroy_window(hwnd_value)
                return 0
            if message_value == WM_DESTROY:
                self._window_destroyed = True
                self._api.post_quit_message(0)
                return 0
        except BaseException as exc:
            self._record_error(exc)
            if message_value in {WM_INPUT, WM_HOTKEY}:
                return 0
        return self._api.def_window_proc(
            hwnd_value,
            message_value,
            wparam_value,
            lparam_value,
        )

    def _handle_raw_input(self, raw_input_handle: int) -> None:
        with self._state_lock:
            self._stats.raw_input_messages += 1
        try:
            data = self._api.read_raw_input(raw_input_handle)
        except BaseException as exc:
            with self._state_lock:
                self._stats.read_errors += 1
            self._record_error(exc)
            return

        try:
            packet = decode_raw_mouse_packet(data)
        except ValueError as exc:
            with self._state_lock:
                self._stats.malformed_packets += 1
            self._record_error(exc)
            return

        if packet is None:
            with self._state_lock:
                self._stats.non_mouse_packets += 1
            return
        if packet.absolute:
            with self._state_lock:
                self._stats.absolute_packets_ignored += 1
            return

        with self._state_lock:
            self._stats.relative_packets += 1
            if packet.dx == 0 and packet.dy == 0:
                self._stats.zero_delta_packets += 1
                return
        self._invoke_callback(self._on_delta, packet.dx, packet.dy)

    def _handle_hotkey(self, hotkey_id: int) -> None:
        if hotkey_id == HOTKEY_F4_ID:
            with self._state_lock:
                self._stats.f4_hotkeys += 1
            self._invoke_callback(self._on_f4)
        elif hotkey_id == HOTKEY_ESCAPE_ID:
            with self._state_lock:
                self._stats.escape_hotkeys += 1
            self._invoke_callback(self._on_escape)

    def _invoke_callback(self, callback, *args) -> None:
        if callback is None:
            return
        try:
            callback(*args)
        except BaseException as exc:
            with self._state_lock:
                self._stats.callback_errors += 1
            self._record_error(exc)

    def _notify_error(self, exc: BaseException) -> None:
        if self._on_error is None:
            return
        try:
            self._on_error(exc)
        except BaseException:
            # Error reporting cannot be allowed to kill the message thread.
            pass

    def _record_error(self, exc: BaseException) -> None:
        with self._state_lock:
            self._last_error = exc
        self._notify_error(exc)

    def _cleanup(self, callback: Callable[[], None]) -> None:
        try:
            callback()
        except BaseException as exc:
            self._record_error(exc)


__all__ = [
    "RawMouseInput",
    "RawMousePacket",
    "RawMouseStats",
    "Win32CallError",
    "decode_raw_mouse_packet",
]
