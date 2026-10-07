"""X11/XWayland focus, fullscreen geometry and XI2 raw mouse input.

Run Azahar with QT_QPA_PLATFORM=xcb. Native Wayland windows are deliberately
unsupported: their PID, focus and viewport cannot be established through X11.
No pointer warps, grabs, desktop key injection or root permissions are used.
"""
from __future__ import annotations

import ctypes as C
import math
import re
import threading
from pathlib import Path


AIM_TOGGLE_KEYSYM = 0xFFC9  # F12; Azahar's stock F4 pauses emulation.
XI_REQUIRED_VERSION = (2, 2)  # libXi intentionally hides raw sourceid for older clients.
GAMESCOPE_PROCESS_NAMES = frozenset(("gamescope", "gamescope-wl"))


class XError(C.Structure):
    _fields_ = [("type", C.c_int), ("display", C.c_void_p), ("resource", C.c_ulong),
                ("serial", C.c_ulong), ("error_code", C.c_ubyte),
                ("request_code", C.c_ubyte), ("minor_code", C.c_ubyte)]


class WindowAttributes(C.Structure):
    _fields_ = [(name, C.c_int) for name in ("x", "y", "width", "height", "border_width", "depth")] + [
        ("visual", C.c_void_p), ("root", C.c_ulong), ("window_class", C.c_int),
        ("bit_gravity", C.c_int), ("win_gravity", C.c_int), ("backing_store", C.c_int),
        ("backing_planes", C.c_ulong), ("backing_pixel", C.c_ulong), ("save_under", C.c_int),
        ("colormap", C.c_ulong), ("map_installed", C.c_int), ("map_state", C.c_int),
        ("all_event_masks", C.c_long), ("your_event_mask", C.c_long), ("do_not_propagate_mask", C.c_long),
        ("override_redirect", C.c_int), ("screen", C.c_void_p)]


def local_display(value: str | bytes) -> str | None:
    if isinstance(value, bytes):
        try:
            value = value.split(b"\0", 1)[0].decode("ascii")
        except UnicodeError:
            return None
    match = re.fullmatch(r"(?:unix)?:(\d+)(?:\.\d+)?", value)
    return ":" + match.group(1) if match else None


_ERROR_TYPE = C.CFUNCTYPE(C.c_int, C.c_void_p, C.POINTER(XError))
_ERROR_HANDLER = None
_PREVIOUS_HANDLER = None
_OWNED_DISPLAYS: dict[int, int] = {}
_ERROR_LOCK = threading.RLock()
_XLIB = None


def initialize_xlib():
    """Call before Tk or any other Xlib client; retain the library for its life."""
    global _XLIB
    with _ERROR_LOCK:
        if _XLIB is None:
            library = C.CDLL("libX11.so.6")
            library.XInitThreads.argtypes = ()
            library.XInitThreads.restype = C.c_int
            if not library.XInitThreads():
                raise RuntimeError("Xlib thread initialization failed")
            _XLIB = library
        return _XLIB


def _install_error_handler(x) -> None:
    """A window disappearing during a query must not exit the whole Launcher.

    Only this adapter's Display connections are handled; Tk's connection keeps
    the original error handler. Xlib's handler is process-global, not per-window.
    """
    global _ERROR_HANDLER, _PREVIOUS_HANDLER
    with _ERROR_LOCK:
        if _ERROR_HANDLER is not None:
            return

        def handle(display, event):
            with _ERROR_LOCK:
                if display in _OWNED_DISPLAYS:
                    _OWNED_DISPLAYS[display] = event.contents.error_code
                    return 0
            return _PREVIOUS_HANDLER(display, event) if _PREVIOUS_HANDLER else 0

        _ERROR_HANDLER = _ERROR_TYPE(handle)
        x.XSetErrorHandler.argtypes = (_ERROR_TYPE,)
        x.XSetErrorHandler.restype = C.c_void_p
        previous = x.XSetErrorHandler(_ERROR_HANDLER)
        _PREVIOUS_HANDLER = C.cast(previous, _ERROR_TYPE) if previous else None


class Cookie(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
                ("display", C.c_void_p), ("extension", C.c_int), ("evtype", C.c_int),
                ("cookie", C.c_uint), ("data", C.c_void_p)]


class Event(C.Union):
    _fields_ = [("type", C.c_int), ("cookie", Cookie), ("pad", C.c_long * 24)]


class Valuators(C.Structure):
    _fields_ = [("mask_len", C.c_int), ("mask", C.POINTER(C.c_ubyte)),
                ("values", C.POINTER(C.c_double))]


class RawEvent(C.Structure):
    _fields_ = [("type", C.c_int), ("serial", C.c_ulong), ("send_event", C.c_int),
                ("display", C.c_void_p), ("extension", C.c_int), ("evtype", C.c_int),
                ("time", C.c_ulong), ("deviceid", C.c_int), ("sourceid", C.c_int),
                ("detail", C.c_int), ("flags", C.c_int), ("valuators", Valuators),
                ("raw_values", C.POINTER(C.c_double))]


class EventMask(C.Structure):
    _fields_ = [("deviceid", C.c_int), ("mask_len", C.c_int),
                ("mask", C.POINTER(C.c_ubyte))]


class AnyClass(C.Structure):
    _fields_ = [("type", C.c_int), ("sourceid", C.c_int)]


class ValuatorClass(C.Structure):
    _fields_ = [("type", C.c_int), ("sourceid", C.c_int), ("number", C.c_int),
                ("label", C.c_ulong), ("minimum", C.c_double), ("maximum", C.c_double),
                ("value", C.c_double), ("resolution", C.c_int), ("mode", C.c_int)]


class DeviceInfo(C.Structure):
    _fields_ = [("deviceid", C.c_int), ("name", C.c_char_p), ("use", C.c_int),
                ("attachment", C.c_int), ("enabled", C.c_int), ("num_classes", C.c_int),
                ("classes", C.POINTER(C.POINTER(AnyClass)))]


def relative_axes(device: DeviceInfo) -> bool:
    """Absolute touch/pen coordinates must never be treated as camera deltas."""
    modes = {}
    if not device.classes or not 0 <= device.num_classes <= 128:
        return False
    for index in range(device.num_classes):
        info = device.classes[index]
        if info and info.contents.type == 2:  # XIValuatorClass
            axis = C.cast(info, C.POINTER(ValuatorClass)).contents
            if axis.number in (0, 1):
                modes[axis.number] = axis.mode
    return modes == {0: 0, 1: 0}  # XIModeRelative


def raw_delta(event: RawEvent) -> tuple[float, float]:
    """XI2 raw_values are packed by the set bits, not indexed by axis number."""
    if not event.valuators.mask or not event.raw_values or not 0 <= event.valuators.mask_len <= 32:
        return 0.0, 0.0
    result = [0.0, 0.0]
    index = 0
    for axis in range(event.valuators.mask_len * 8):
        if event.valuators.mask[axis // 8] & (1 << (axis % 8)):
            if axis < 2:
                result[axis] = event.raw_values[index]
            index += 1
    return tuple(result) if all(math.isfinite(value) for value in result) else (0.0, 0.0)


class X11Connection:
    def __init__(self, display_name: str | None = None) -> None:
        self.lock = threading.RLock()
        self.x = initialize_xlib()
        signatures = {
            "XInitThreads": (C.c_int, ()),
            "XOpenDisplay": (C.c_void_p, (C.c_char_p,)),
            "XCloseDisplay": (C.c_int, (C.c_void_p,)),
            "XDefaultRootWindow": (C.c_ulong, (C.c_void_p,)),
            "XDefaultScreen": (C.c_int, (C.c_void_p,)),
            "XDisplayString": (C.c_char_p, (C.c_void_p,)),
            "XDisplayWidth": (C.c_int, (C.c_void_p, C.c_int)),
            "XDisplayHeight": (C.c_int, (C.c_void_p, C.c_int)),
            "XInternAtom": (C.c_ulong, (C.c_void_p, C.c_char_p, C.c_int)),
            "XGetWindowProperty": (C.c_int, (C.c_void_p, C.c_ulong, C.c_ulong, C.c_long,
                C.c_long, C.c_int, C.c_ulong, C.POINTER(C.c_ulong), C.POINTER(C.c_int),
                C.POINTER(C.c_ulong), C.POINTER(C.c_ulong), C.POINTER(C.POINTER(C.c_ubyte)))),
            "XFree": (C.c_int, (C.c_void_p,)),
            "XGetInputFocus": (C.c_int, (C.c_void_p, C.POINTER(C.c_ulong), C.POINTER(C.c_int))),
            "XQueryTree": (C.c_int, (C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong),
                C.POINTER(C.c_ulong), C.POINTER(C.POINTER(C.c_ulong)), C.POINTER(C.c_uint))),
            "XGetGeometry": (C.c_int, (C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong),
                C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_uint),
                C.POINTER(C.c_uint), C.POINTER(C.c_uint), C.POINTER(C.c_uint))),
            "XGetWindowAttributes": (C.c_int, (C.c_void_p, C.c_ulong, C.POINTER(WindowAttributes))),
            "XTranslateCoordinates": (C.c_int, (C.c_void_p, C.c_ulong, C.c_ulong,
                C.c_int, C.c_int, C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_ulong))),
            "XQueryPointer": (C.c_int, (C.c_void_p, C.c_ulong, C.POINTER(C.c_ulong),
                C.POINTER(C.c_ulong), C.POINTER(C.c_int), C.POINTER(C.c_int),
                C.POINTER(C.c_int), C.POINTER(C.c_int), C.POINTER(C.c_uint))),
            "XQueryExtension": (C.c_int, (C.c_void_p, C.c_char_p, C.POINTER(C.c_int),
                C.POINTER(C.c_int), C.POINTER(C.c_int))),
            "XPending": (C.c_int, (C.c_void_p,)),
            "XNextEvent": (C.c_int, (C.c_void_p, C.POINTER(Event))),
            "XGetEventData": (C.c_int, (C.c_void_p, C.POINTER(Cookie))),
            "XFreeEventData": (None, (C.c_void_p, C.POINTER(Cookie))),
            "XFlush": (C.c_int, (C.c_void_p,)),
            "XKeysymToKeycode": (C.c_ubyte, (C.c_void_p, C.c_ulong)),
            "XQueryKeymap": (C.c_int, (C.c_void_p, C.c_void_p)),
        }
        for name, (restype, argtypes) in signatures.items():
            function = getattr(self.x, name)
            function.restype, function.argtypes = restype, argtypes
        self.display = self.x.XOpenDisplay(display_name.encode() if display_name else None)
        if not self.display:
            raise RuntimeError("Cannot open X11 display; use the Deck desktop session and QT_QPA_PLATFORM=xcb")
        with _ERROR_LOCK:
            _OWNED_DISPLAYS[self.display] = 0
        _install_error_handler(self.x)
        self.root = self.x.XDefaultRootWindow(self.display)
        self.name = local_display(self.x.XDisplayString(self.display) or b"")

    def close(self) -> None:
        with self.lock:
            if self.display:
                self.x.XCloseDisplay(self.display)
                with _ERROR_LOCK:
                    _OWNED_DISPLAYS.pop(self.display, None)
                self.display = None

    def check_errors(self) -> None:
        with _ERROR_LOCK:
            code = _OWNED_DISPLAYS.get(self.display, 0)
            _OWNED_DISPLAYS[self.display] = 0
        # BadWindow/BadDrawable are expected when a queried window disappears.
        if code not in (0, 3, 9):
            raise RuntimeError(f"X11 request failed with error {code}")

    def atom(self, name: str) -> int:
        return self.x.XInternAtom(self.display, name.encode(), False)

    def property(self, window: int, name: str, *, text: bool = False):
        kind, fmt, count, remaining = C.c_ulong(), C.c_int(), C.c_ulong(), C.c_ulong()
        data = C.POINTER(C.c_ubyte)()
        status = self.x.XGetWindowProperty(self.display, window, self.atom(name), 0, 4096,
            False, 0, C.byref(kind), C.byref(fmt), C.byref(count), C.byref(remaining), C.byref(data))
        try:
            if status or not data or remaining.value:
                return None if text else []
            if text and fmt.value == 8:
                return local_display(C.string_at(data, count.value))
            if fmt.value != 32:
                return None if text else []
            values = C.cast(data, C.POINTER(C.c_ulong))
            if text:
                # Gamescope historically stores display strings in CARDINAL32;
                # only decode the NUL-terminated ASCII prefix, never junk after it.
                return local_display(b"".join((values[index] & 0xFFFFFFFF).to_bytes(4, "little") for index in range(count.value)))
            return [values[index] for index in range(count.value)]
        finally:
            if data:
                self.x.XFree(data)

    def parent(self, window: int) -> int:
        root, parent, count = C.c_ulong(), C.c_ulong(), C.c_uint()
        children = C.POINTER(C.c_ulong)()
        ok = self.x.XQueryTree(self.display, window, C.byref(root), C.byref(parent),
                              C.byref(children), C.byref(count))
        if children:
            self.x.XFree(children)
        return parent.value if ok else 0

    def children(self, window: int) -> list[int]:
        root, parent, count = C.c_ulong(), C.c_ulong(), C.c_uint()
        children = C.POINTER(C.c_ulong)()
        ok = self.x.XQueryTree(self.display, window, C.byref(root), C.byref(parent), C.byref(children), C.byref(count))
        try:
            return [children[index] for index in range(count.value)] if ok and children else []
        finally:
            if children:
                self.x.XFree(children)

    def viewable(self, window: int) -> bool:
        attributes = WindowAttributes()
        return bool(self.x.XGetWindowAttributes(self.display, window, C.byref(attributes)) and attributes.map_state == 2)

    def rectangle(self, window: int):
        root, child = C.c_ulong(), C.c_ulong()
        x, y, width, height, border, depth = C.c_int(), C.c_int(), C.c_uint(), C.c_uint(), C.c_uint(), C.c_uint()
        if not self.x.XGetGeometry(self.display, window, C.byref(root), C.byref(x), C.byref(y),
                C.byref(width), C.byref(height), C.byref(border), C.byref(depth)):
            return None
        if not self.x.XTranslateCoordinates(self.display, window, self.root, 0, 0, C.byref(x), C.byref(y), C.byref(child)):
            return None
        return (float(x.value), float(y.value), float(width.value), float(height.value)) if width.value and height.value else None

    def screen_size(self) -> tuple[int, int]:
        screen = self.x.XDefaultScreen(self.display)
        return self.x.XDisplayWidth(self.display, screen), self.x.XDisplayHeight(self.display, screen)


class X11WindowContext(X11Connection):
    def __init__(self, pid: int, rect_provider=None) -> None:
        super().__init__()
        self.pid, self.rect_provider = pid, rect_provider
        self.gamescope_pid, self.control = None, None
        compositor = self.property(self.root, "GAMESCOPE_PID")
        if len(compositor) == 1 and compositor[0] > 0:
            try:
                if (Path("/proc") / str(compositor[0]) / "comm").read_text().strip() in GAMESCOPE_PROCESS_NAMES:
                    self.gamescope_pid = compositor[0]
            except OSError:
                pass
        if self.gamescope_pid is not None:
            self.control = self._find_gamescope_control()

    def _find_gamescope_control(self):
        if self.property(self.root, "GAMESCOPE_XWAYLAND_SERVER_ID") == [0]:
            return self
        try:
            sockets = sorted(Path("/tmp/.X11-unix").glob("X[0-9]*"))
        except OSError:
            return None
        for socket in sockets:
            if not socket.name[1:].isdecimal():
                continue
            candidate = None
            try:
                candidate = X11Connection(":" + socket.name[1:])
                if (candidate.property(candidate.root, "GAMESCOPE_PID") == [self.gamescope_pid]
                        and candidate.property(candidate.root, "GAMESCOPE_XWAYLAND_SERVER_ID") == [0]):
                    return candidate
            except (OSError, RuntimeError):
                pass
            if candidate:
                candidate.close()
        return None

    def close(self) -> None:
        if getattr(self, "control", None) is not None and self.control is not self:
            self.control.close()
            self.control = None
        super().close()

    def _gamescope_input_focused(self) -> bool:
        if self.gamescope_pid is None:
            return True
        if self.control is None or self.name is None:
            return False
        with self.control.lock:
            if self.control.property(self.control.root, "GAMESCOPE_PID") != [self.gamescope_pid]:
                return False
            return all(self.control.property(self.control.root, prop, text=True) == self.name for prop in
                       ("GAMESCOPE_KEYBOARD_FOCUS_DISPLAY", "GAMESCOPE_MOUSE_FOCUS_DISPLAY"))

    def focused(self) -> bool:
        with self.lock:
            if not self._gamescope_input_focused():
                return False
            window, revert = C.c_ulong(), C.c_int()
            self.x.XGetInputFocus(self.display, C.byref(window), C.byref(revert))
            current = window.value
            for _ in range(32):
                if current in (0, 1, self.root):
                    return False
                owners = self.property(current, "_NET_WM_PID")
                if owners:
                    return owners == [self.pid]
                current = self.parent(current)
            return False

    def cursor_position(self) -> tuple[int, int]:
        with self.lock:
            root, child = C.c_ulong(), C.c_ulong()
            x, y, wx, wy, mask = C.c_int(), C.c_int(), C.c_int(), C.c_int(), C.c_uint()
            if not self.x.XQueryPointer(self.display, self.root, C.byref(root), C.byref(child),
                    C.byref(x), C.byref(y), C.byref(wx), C.byref(wy), C.byref(mask)):
                raise RuntimeError("X11 cursor position unavailable")
            return x.value, y.value

    def client_rect(self) -> tuple[float, float, float, float] | None:
        """Only a unique fullscreen emulator window is a known render surface.

        Windowed toolbars, screen swapping, unknown layouts and Qt dialogs must
        not be guessed. The Launcher maps this surface via its selected layout.
        """
        with self.lock:
            clients = self.property(self.root, "_NET_CLIENT_LIST")
            if self.gamescope_pid is not None:
                # Gamescope has its own window list rather than EWMH's list.
                clients = self.children(self.root)
            full = self.atom("_NET_WM_STATE_FULLSCREEN")
            windows = []
            screen_width, screen_height = self.screen_size()
            root_rect = (0.0, 0.0, float(screen_width), float(screen_height))
            for window in clients:
                if self.property(window, "_NET_WM_PID") != [self.pid] or not self.viewable(window):
                    continue
                fullscreen = full in self.property(window, "_NET_WM_STATE")
                if self.gamescope_pid is not None:
                    if self.rectangle(window) != root_rect:
                        continue
                    fullscreen = fullscreen or self._full_canvas_child(window, root_rect)
                if fullscreen:
                    windows.append(window)
            if len(windows) != 1:
                return None
            return self.rectangle(windows[0])

    def _full_canvas_child(self, window: int, rect) -> bool:
        # A root-sized Qt container alone is insufficient: follow mapped children
        # to a full-size leaf canvas. Reject chrome, clipped and foreign children.
        pending = [(child, 0) for child in self.children(window)]
        visited = 0
        while pending and visited < 256:
            child, depth = pending.pop()
            visited += 1
            if depth > 8 or not self.viewable(child) or self.rectangle(child) != rect:
                continue
            owner = self.property(child, "_NET_WM_PID")
            if owner and owner != [self.pid]:
                continue
            mapped = [item for item in self.children(child) if self.viewable(item)]
            if not mapped:
                return True
            pending.extend((item, depth + 1) for item in mapped)
        return False

    def top_viewport(self):
        return self.rect_provider(self.pid) if self.rect_provider else None


def render_rect(pid: int):
    context = X11WindowContext(pid)
    try:
        return context.client_rect()
    finally:
        context.close()


def fullscreen_size() -> tuple[int, int]:
    context = X11Connection()
    try:
        return context.screen_size()
    finally:
        context.close()


class XIRawMouse:
    """Unaccelerated XI2 deltas; hotkeys are observed, never globally grabbed."""
    def __init__(self, on_delta, on_toggle=None, on_quit=None) -> None:
        self.on_delta, self.on_toggle, self.on_quit = on_delta, on_toggle, on_quit
        self._stop, self._ready = threading.Event(), threading.Event()
        self._error = None
        self._thread = None

    def start(self, timeout: float = 5.0):
        self._thread = threading.Thread(target=self._run, name="kiu-xi2", daemon=True)
        self._thread.start()
        if not self._ready.wait(timeout):
            self.stop()
            raise RuntimeError("XI2 raw input startup timed out")
        if self._error:
            self.stop()
            raise RuntimeError("XI2 raw input failed") from self._error
        return self

    def stop(self) -> None:
        self._stop.set()
        if self._thread and self._thread is not threading.current_thread():
            self._thread.join(timeout=2.0)

    def check(self) -> None:
        if self._error:
            raise RuntimeError("XI2 raw input stopped unexpectedly") from self._error

    def _run(self) -> None:
        connection = None
        try:
            connection = X11Connection()
            x, display = connection.x, connection.display
            xi = C.CDLL("libXi.so.6")
            xi.XIQueryVersion.argtypes = (C.c_void_p, C.POINTER(C.c_int), C.POINTER(C.c_int))
            xi.XIQueryVersion.restype = C.c_int
            xi.XISelectEvents.argtypes = (C.c_void_p, C.c_ulong, C.POINTER(EventMask), C.c_int)
            xi.XISelectEvents.restype = C.c_int
            xi.XIQueryDevice.argtypes = (C.c_void_p, C.c_int, C.POINTER(C.c_int))
            xi.XIQueryDevice.restype = C.POINTER(DeviceInfo)
            xi.XIFreeDeviceInfo.argtypes = (C.POINTER(DeviceInfo),)
            xi.XIFreeDeviceInfo.restype = None
            opcode, event_base, error_base = C.c_int(), C.c_int(), C.c_int()
            if not x.XQueryExtension(display, b"XInputExtension", C.byref(opcode), C.byref(event_base), C.byref(error_base)):
                raise RuntimeError("XInput2 unavailable")
            major, minor = C.c_int(XI_REQUIRED_VERSION[0]), C.c_int(XI_REQUIRED_VERSION[1])
            if (xi.XIQueryVersion(display, C.byref(major), C.byref(minor))
                    or (major.value, minor.value) < XI_REQUIRED_VERSION):
                raise RuntimeError("XInput2 version 2.2 required for verified raw source axes")
            bits = (C.c_ubyte * 3)(2, 0, 2)  # XI_DeviceChanged + XI_RawMotion
            mask = EventMask(1, len(bits), bits)  # XIAllMasterDevices: no duplicate slave events
            if xi.XISelectEvents(display, connection.root, C.byref(mask), 1):
                raise RuntimeError("Cannot subscribe to XI2 raw mouse motion")
            x.XFlush(display)
            keys = [(x.XKeysymToKeycode(display, symbol), callback)
                    for symbol, callback in ((AIM_TOGGLE_KEYSYM, self.on_toggle), (0xFF1B, self.on_quit)) if callback]
            held = set()
            residue = [0.0, 0.0]
            sources = {}
            self._ready.set()
            while not self._stop.is_set():
                while not self._stop.is_set() and x.XPending(display):
                    event = Event()
                    x.XNextEvent(display, C.byref(event))
                    if event.type != 35 or event.cookie.extension != opcode.value:
                        continue
                    if event.cookie.evtype == 1:
                        sources.clear()  # Master switches source/classes; re-query modes.
                        residue[:] = [0.0, 0.0]
                        continue
                    if event.cookie.evtype != 17:
                        continue
                    if not x.XGetEventData(display, C.byref(event.cookie)):
                        continue
                    try:
                        raw = C.cast(event.cookie.data, C.POINTER(RawEvent)).contents
                        if raw.sourceid not in sources:
                            count = C.c_int()
                            devices = xi.XIQueryDevice(display, raw.sourceid, C.byref(count))
                            try:
                                sources[raw.sourceid] = bool(devices and count.value == 1 and relative_axes(devices[0]))
                            finally:
                                if devices:
                                    xi.XIFreeDeviceInfo(devices)
                        if not sources[raw.sourceid]:
                            continue
                        dx, dy = raw_delta(raw)
                        residue[0] += dx
                        residue[1] += dy
                        counts = int(residue[0]), int(residue[1])
                        residue[0] -= counts[0]
                        residue[1] -= counts[1]
                        if counts != (0, 0):
                            self.on_delta(*counts)
                    finally:
                        x.XFreeEventData(display, C.byref(event.cookie))
                keymap = (C.c_ubyte * 32)()
                x.XQueryKeymap(display, keymap)
                connection.check_errors()
                pressed = {code for code, _ in keys if code and keymap[code // 8] & (1 << (code % 8))}
                for code, callback in keys:
                    if code in pressed and code not in held:
                        callback()
                held = pressed
                self._stop.wait(0.005)
        except BaseException as exc:
            self._error = exc
            self._ready.set()
        finally:
            if connection:
                connection.close()
