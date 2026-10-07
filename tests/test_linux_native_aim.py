"""ROM-free Linux transport/input/worker safety regressions."""
from __future__ import annotations

import ctypes
import errno
import struct
import sys
import threading
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu.aim_protocol import MAILBOXES
from kiu.backends.linux import guest_memory as gm, native_fps_aim as native, x11


class Memory:
    def __init__(self, bad=False, controller=0):
        self.writes, self.closed, self.calls = [], False, 0
        self.bad, self.controller = bad, controller

    def read(self, address, size):
        for box in MAILBOXES:
            if address == box.address and size == 8:
                return b"badhead!" if self.bad else struct.pack("<II", box.magic, box.version)
        pin = native.pin
        if address == pin.MAILBOX_VA + pin.MB_CALLS:
            self.calls += 1
            return struct.pack("<II", self.calls, 0)
        if address == pin.MAILBOX_VA + pin.MB_P:
            return struct.pack("<I", self.controller)
        if address == pin.MAILBOX_VA + pin.MB_W:
            return struct.pack("<ff", 400, 240)
        return b"\0" * size

    def write(self, address, data):
        self.writes.append((address, data))

    def close(self):
        self.closed = True


class ShortRun(threading.Event):
    def __init__(self):
        super().__init__()
        self.waits = 0

    def wait(self, timeout=None):
        self.waits += 1
        if self.waits >= 3:
            self.set()
            return True
        return False


class LinuxInputTests(unittest.TestCase):
    def test_actual_steamos_gamescope_wl_process_is_recognized(self):
        def no_display(context):
            context.root = 1

        for name, expected in (("gamescope", 77), ("gamescope-wl", 77), ("steamwebhelper", None)):
            with patch.object(x11.X11Connection, "__init__", no_display), \
                 patch.object(x11.X11Connection, "property", return_value=[77]), \
                 patch.object(Path, "read_text", return_value=name + "\n"), \
                 patch.object(x11.X11WindowContext, "_find_gamescope_control", return_value=None):
                self.assertEqual(x11.X11WindowContext(42).gamescope_pid, expected)

    def gaming_context(self):
        context = object.__new__(x11.X11WindowContext)
        context.lock, context.pid, context.root = threading.RLock(), 42, 1
        context.gamescope_pid, context.name = 77, ":1"
        context.atom = lambda name: 99
        context.screen_size = lambda: (1280, 800)
        tree = {1: [10], 10: [20], 20: []}
        context.children = lambda window: tree.get(window, [])
        context.viewable = lambda window: True
        context.rectangle = lambda window: (0.0, 0.0, 1280.0, 800.0)
        properties = {(10, "_NET_WM_PID"): [42]}
        context.property = lambda window, name: properties.get((window, name), [])
        return context, properties

    def test_gamescope_mapped_full_canvas_without_ewmh_list(self):
        context, _ = self.gaming_context()
        self.assertEqual(context.client_rect(), (0.0, 0.0, 1280.0, 800.0))
        context.gamescope_pid = None
        self.assertIsNone(context.client_rect())  # Never enable this fallback on an ordinary desktop.

    def test_gamescope_unknown_foreign_clipped_or_unmapped_canvas_is_refused(self):
        context, properties = self.gaming_context()
        properties[(20, "_NET_WM_PID")] = [123]
        self.assertIsNone(context.client_rect())
        properties.pop((20, "_NET_WM_PID"))
        context.rectangle = lambda window: (0.0, 0.0, 1280.0, 700.0) if window == 20 else (0.0, 0.0, 1280.0, 800.0)
        self.assertIsNone(context.client_rect())
        context.rectangle = lambda window: (0.0, 0.0, 1280.0, 800.0)
        context.viewable = lambda window: window != 20
        self.assertIsNone(context.client_rect())
        properties[(10, "_NET_WM_PID")] = [123]
        self.assertIsNone(context.client_rect())

    def test_gamescope_global_overlay_focus_disables_local_game_focus(self):
        context, _ = self.gaming_context()
        control = Mock()
        control.lock, control.root = threading.RLock(), 1
        values = {"GAMESCOPE_PID": [77], "GAMESCOPE_KEYBOARD_FOCUS_DISPLAY": ":1", "GAMESCOPE_MOUSE_FOCUS_DISPLAY": ":1"}
        control.property.side_effect = lambda root, name, **kwargs: values.get(name)
        context.control = control
        self.assertTrue(context._gamescope_input_focused())
        values["GAMESCOPE_KEYBOARD_FOCUS_DISPLAY"] = ":0"
        self.assertFalse(context._gamescope_input_focused())
        values["GAMESCOPE_KEYBOARD_FOCUS_DISPLAY"] = ":1"
        values["GAMESCOPE_MOUSE_FOCUS_DISPLAY"] = None
        self.assertFalse(context._gamescope_input_focused())
        context.control = None
        self.assertFalse(context._gamescope_input_focused())

    def test_only_local_display_names_are_accepted(self):
        self.assertEqual(x11.local_display(b":1\0garbage"), ":1")
        self.assertEqual(x11.local_display("unix:12.0"), ":12")
        for value in (b"\xff", b":1evil", "example.com:0", ":", ""):
            self.assertIsNone(x11.local_display(value))

    def test_gamescope_cardinal_display_decodes_only_actual_ascii_prefix(self):
        # Actual SteamOS Gaming Mode probe: CARDINAL=12346,0,78 (':0\0').
        cardinals = (ctypes.c_ulong * 3)(12346, 0, 78)

        def get_property(*args):
            ctypes.cast(args[8], ctypes.POINTER(ctypes.c_int))[0] = 32
            ctypes.cast(args[9], ctypes.POINTER(ctypes.c_ulong))[0] = 3
            ctypes.cast(args[10], ctypes.POINTER(ctypes.c_ulong))[0] = 0
            ctypes.cast(args[11], ctypes.POINTER(ctypes.POINTER(ctypes.c_ubyte)))[0] = ctypes.cast(cardinals, ctypes.POINTER(ctypes.c_ubyte))
            return 0

        context = object.__new__(x11.X11Connection)
        context.display, context.atom = 1, lambda name: 2
        context.x = SimpleNamespace(XGetWindowProperty=get_property, XFree=Mock())
        self.assertEqual(context.property(1, "GAMESCOPE_KEYBOARD_FOCUS_DISPLAY", text=True), ":0")
        context.x.XFree.assert_called_once()

    def test_only_relative_source_axes_are_camera_counts(self):
        self.assertGreaterEqual(x11.XI_REQUIRED_VERSION, (2, 2))
        axes = [x11.ValuatorClass(), x11.ValuatorClass()]
        for number, axis in enumerate(axes):
            axis.type, axis.number, axis.mode = 2, number, 0
        pointers = (ctypes.POINTER(x11.AnyClass) * 2)(
            *(ctypes.cast(ctypes.pointer(axis), ctypes.POINTER(x11.AnyClass)) for axis in axes))
        device = x11.DeviceInfo()
        device.classes, device.num_classes = pointers, 2
        self.assertTrue(x11.relative_axes(device))
        axes[0].mode = 1
        self.assertFalse(x11.relative_axes(device))
        device.num_classes = 0
        self.assertFalse(x11.relative_axes(device))

    def test_linux_uses_f12_without_changing_windows_f4(self):
        from kiu import launcher
        self.assertEqual(x11.AIM_TOGGLE_KEYSYM, 0xFFC9)
        with patch.object(launcher.sys, "platform", "linux"):
            self.assertEqual(launcher.mouse_toggle_key(), "F12")
            self.assertIn("F12", launcher.mouse_status_text("active"))
            self.assertIn("F12", launcher.mouse_status_text("off"))
        with patch.object(launcher.sys, "platform", "win32"):
            self.assertEqual(launcher.mouse_toggle_key(), "F4")
            self.assertIn("F4", launcher.mouse_status_text("active"))
        self.assertEqual(launcher.mouse_status_text("path {toggle}"), "path {toggle}")

    def test_xlib_thread_initialization_is_once_and_failure_is_not_cached(self):
        library = Mock()
        library.XInitThreads.return_value = 1
        with patch.object(x11, "_XLIB", None), patch.object(x11.C, "CDLL", return_value=library) as load:
            self.assertIs(x11.initialize_xlib(), library)
            self.assertIs(x11.initialize_xlib(), library)
            load.assert_called_once_with("libX11.so.6")
            library.XInitThreads.assert_called_once()
        library.XInitThreads.return_value = 0
        with patch.object(x11, "_XLIB", None), patch.object(x11.C, "CDLL", return_value=library):
            with self.assertRaises(RuntimeError):
                x11.initialize_xlib()
            self.assertIsNone(x11._XLIB)

    def test_launcher_prepares_xlib_before_tk(self):
        from kiu import launcher
        with patch.object(launcher.sys, "platform", "linux"), patch.object(x11, "initialize_xlib") as initialize:
            self.assertIsNone(launcher.prepare_tk())
            initialize.assert_called_once()

    def test_xlib_errors_only_handle_owned_connections(self):
        previous = Mock(return_value=7)
        event = x11.XError()
        event.error_code = 3
        library = Mock()
        library.XSetErrorHandler.return_value = None
        with patch.object(x11, "_ERROR_HANDLER", None), patch.object(x11, "_PREVIOUS_HANDLER", None), \
             patch.object(x11, "_OWNED_DISPLAYS", {42: 0}):
            x11._install_error_handler(library)
            with patch.object(x11, "_PREVIOUS_HANDLER", previous):
                self.assertEqual(x11._ERROR_HANDLER(42, ctypes.byref(event)), 0)
                self.assertEqual(x11._OWNED_DISPLAYS[42], 3)
                previous.assert_not_called()
                self.assertEqual(x11._ERROR_HANDLER(99, ctypes.byref(event)), 7)
                previous.assert_called_once()

    def test_import_on_windows_does_not_load_linux_libraries(self):
        with patch.object(ctypes, "CDLL", side_effect=AssertionError("must be lazy")):
            import importlib
            importlib.reload(x11)
            importlib.reload(gm)

    def test_sparse_raw_axis_mask_uses_packed_values(self):
        mask = (ctypes.c_ubyte * 1)(0b10)
        values = (ctypes.c_double * 1)(2.75)
        event = x11.RawEvent()
        event.valuators = x11.Valuators(1, mask, values)
        event.raw_values = values
        self.assertEqual(x11.raw_delta(event), (0, 2.75))

    def test_raw_axes_ignore_scroll_and_nonfinite_motion(self):
        mask = (ctypes.c_ubyte * 1)(0b111)
        values = (ctypes.c_double * 3)(4, -3, 100)
        event = x11.RawEvent()
        event.valuators = x11.Valuators(1, mask, values)
        event.raw_values = values
        self.assertEqual(x11.raw_delta(event), (4, -3))
        values[0] = float("nan")
        self.assertEqual(x11.raw_delta(event), (0, 0))


class LinuxMemoryTests(unittest.TestCase):
    def bare(self, data=b"" * 0):
        memory = object.__new__(gm.GuestMemory)
        memory.pid, memory._closed = 42, False
        memory._offset = memory._region = None
        memory.regions = lambda: [gm.Region(0x100000, len(data))]
        memory._read_host = lambda address, size: data[address - 0x100000:address - 0x100000 + size]
        return memory

    def test_regions_only_include_read_write_mappings(self):
        text = "1000-2000 rw-p 0000 00:00 0\n2000-3000 r-xp 0 00:00 0\n3000-4000 ---p 0 00:00 0"
        self.assertEqual(gm.writable_regions(text), [gm.Region(0x1000, 0x1000)])

    def test_nonce_scan_checks_chunk_seams(self):
        nonce = bytes(range(16))
        memory = self.bare(b"X" * 30 + nonce + b"Y" * 80)
        host = memory.anchor(0x00F36140, nonce, chunk=40)
        self.assertEqual(host, 0x100000 + 30)
        self.assertTrue(memory.verify(0x00F36140, nonce, scratch=True))

    def test_nonce_scan_refuses_mirrored_ram(self):
        nonce = bytes(range(16))
        memory = self.bare(nonce + b"X" * 40 + nonce)
        with self.assertRaisesRegex(RuntimeError, "multiple"):
            memory.anchor(0x00F36140, nonce, chunk=40)
        self.assertIsNone(memory._offset)

    def test_transport_does_not_expose_code_or_other_guest_regions(self):
        memory = self.bare()
        memory._offset = 0x10000000
        memory._region = gm.Region(0x10F36000, 0x2000)
        self.assertEqual(memory.host_address(native.aim.MAILBOX_VA, 8), 0x10F36100)
        for address in (0x00100000, 0x14000000, 0x00F36140):
            with self.assertRaises(ValueError):
                memory.host_address(address, 8)

    def test_short_transfer_fails_instead_of_returning_partial_data(self):
        memory = self.bare()
        memory._check_identity = Mock()
        memory._libc = SimpleNamespace(process_vm_readv=Mock(return_value=3))
        with self.assertRaises(OSError) as failure:
            memory._transfer(0x1000, 8)
        self.assertEqual(failure.exception.errno, errno.EIO)

    def test_wrong_owner_refuses_before_attaching(self):
        with patch.object(gm, "listening_pid", return_value=10):
            with self.assertRaisesRegex(RuntimeError, "expected 42"):
                gm.GuestMemory.attach(expected_pid=42)

    def test_nonce_is_fresh(self):
        self.assertEqual(len(gm.nonce_bytes()), 16)
        self.assertNotEqual(gm.nonce_bytes(1), gm.nonce_bytes(1))


class LinuxWorkerTests(unittest.TestCase):
    def test_listener_reopening_waits_for_same_live_pid(self):
        stop = Mock()
        stop.is_set.return_value = False
        stop.wait.return_value = False
        with patch.object(native, "listening_pid", side_effect=[None, None, 42]), \
             patch.object(native, "process_identity", return_value="born"), \
             patch.object(native.time, "monotonic", side_effect=[0, 0.1, 0.2]):
            self.assertEqual(native.wait_for_owner(24689, 42, stop_event=stop), (42, "born"))
        self.assertEqual(stop.wait.call_count, 2)

    def test_listener_reopening_refuses_foreign_pid_immediately(self):
        with patch.object(native, "listening_pid", return_value=43), \
             patch.object(native, "process_identity") as identity:
            with self.assertRaisesRegex(RuntimeError, "expected 42"):
                native.wait_for_owner(24689, 42)
        identity.assert_not_called()

    def test_listener_reopening_refuses_pid_reuse(self):
        with patch.object(native, "listening_pid", return_value=None), \
             patch.object(native, "process_identity", return_value="new-born"):
            with self.assertRaisesRegex(RuntimeError, "reused"):
                native.wait_for_owner(24689, 42, expected_identity="old-born")

    def test_listener_reopening_is_bounded(self):
        with patch.object(native, "listening_pid", return_value=None), \
             patch.object(native, "process_identity", return_value="born"), \
             patch.object(native.time, "monotonic", side_effect=[0, 2.1]):
            with self.assertRaisesRegex(RuntimeError, "did not reopen"):
                native.wait_for_owner(24689, 42)

    def test_cleanup_refuses_foreign_mailbox(self):
        memory = Memory(bad=True)
        with self.assertRaises(RuntimeError):
            native.clear_inputs(memory)
        self.assertEqual(memory.writes, [])

    def test_valid_cleanup_closes_both_gates_and_pending_delta(self):
        memory = Memory()
        native.clear_inputs(memory)
        self.assertEqual(memory.writes, [
            (native.aim.MAILBOX_VA + native.aim.MB_GATE, struct.pack("<I", 0)),
            (native.aim.MAILBOX_VA + native.aim.MB_YAW, struct.pack("<ff", 0, 0)),
            (native.pin.MAILBOX_VA + native.pin.MB_MODE, struct.pack("<I", 0)),
        ])

    def test_bad_rects_do_not_get_guessed(self):
        for value in (None, (), (0, 0, 0, 20), (0, 0, 20, float("inf")), ("x", 0, 20, 20)):
            self.assertIsNone(native.valid_rect(value))
        self.assertEqual(native.valid_rect((-50, 0, 100, 200)), (-50, 0, 100, 200))

    def test_wrong_owner_never_connects_gdb(self):
        with patch.object(native, "listening_pid", return_value=10), patch.object(native, "GdbClient") as gdb:
            with self.assertRaises(RuntimeError):
                native.main([], expected_pid=42)
        gdb.assert_not_called()

    def run_air(self, rect):
        memory, statuses = Memory(), []
        gdb = Mock()
        gdb.read_mem.side_effect = lambda address, size: memory.read(address, size)
        window = Mock()
        window.focused.return_value = True
        window.cursor_position.return_value = (200, 120)
        window.top_viewport.return_value = rect
        installed = {"state": "installed", "hook_matches": True}
        source = Mock()
        source.start.return_value = source
        with patch.object(native, "listening_pid", return_value=42), \
             patch.object(native, "process_identity", return_value="born"), \
             patch.object(native, "GdbClient", return_value=gdb), \
             patch.object(native.aim, "status", return_value=installed), \
             patch.object(native.pin, "status", return_value=installed), \
             patch.object(native.aim, "set_gate"), patch.object(native.pin, "set_mode"), \
             patch.object(native, "anchor_memory", return_value=memory), \
             patch.object(native, "X11WindowContext", return_value=window), \
             patch.object(native, "XIRawMouse", return_value=source):
            native.main([], expected_pid=42, auto_activate=True, wait_for_battle=True,
                        rect_provider=lambda pid: rect, stop_event=ShortRun(), status_callback=statuses.append)
        self.assertTrue(memory.closed)
        source.stop.assert_called_once()
        window.close.assert_called_once()
        return memory.writes, statuses

    def test_unknown_air_viewport_keeps_input_gate_off(self):
        writes, statuses = self.run_air(None)
        self.assertIn("viewport_missing", statuses)
        self.assertNotIn((native.pin.MAILBOX_VA + native.pin.MB_MODE, struct.pack("<I", 2)), writes)

    def test_known_air_viewport_enables_then_cleans_owned_inputs(self):
        writes, statuses = self.run_air((0, 0, 400, 240))
        self.assertIn("active", statuses)
        self.assertIn((native.pin.MAILBOX_VA + native.pin.MB_MODE, struct.pack("<I", 2)), writes)
        self.assertEqual(writes[-1], (native.pin.MAILBOX_VA + native.pin.MB_MODE, struct.pack("<I", 0)))


if __name__ == "__main__":
    unittest.main()
