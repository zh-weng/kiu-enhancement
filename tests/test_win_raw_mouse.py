from __future__ import annotations

import ctypes
import os
import queue
import sys
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import win_raw_mouse as raw_mouse  # noqa: E402


def make_mouse_packet(dx: int, dy: int, *, absolute: bool = False) -> bytes:
    raw = raw_mouse.RAWINPUT()
    raw.header.dwType = raw_mouse.RIM_TYPEMOUSE
    raw.header.dwSize = ctypes.sizeof(raw_mouse.RAWINPUT)
    raw.mouse.usFlags = raw_mouse.MOUSE_MOVE_ABSOLUTE if absolute else 0
    raw.mouse.lLastX = dx
    raw.mouse.lLastY = dy
    return bytes(raw)


class PacketApi:
    def __init__(self, packets: list[bytes | BaseException]):
        self.packets = list(packets)
        self.handles: list[int] = []

    def read_raw_input(self, handle: int) -> bytes:
        self.handles.append(handle)
        packet = self.packets.pop(0)
        if isinstance(packet, BaseException):
            raise packet
        return packet


class FakeMessageApi:
    """In-memory Win32 message queue; it never calls the operating system."""

    def __init__(self) -> None:
        self.messages: queue.Queue[tuple[int, int, int, int] | None] = queue.Queue()
        self.wndproc = None
        self.hwnd = 0x1234
        self.destroyed = False
        self.class_registered = False
        self.class_unregistered = False
        self.raw_devices: list[tuple[int, int, int, int]] = []
        self.hotkeys_registered: list[tuple[int, int, int, int]] = []
        self.hotkeys_unregistered: list[tuple[int, int]] = []

    def get_module_handle(self) -> int:
        return 0x1000

    def register_class(self, window_class: raw_mouse.WNDCLASSEXW) -> int:
        self.wndproc = window_class.lpfnWndProc
        self.class_registered = True
        return 7

    def unregister_class(self, class_name: str, instance: int) -> None:
        self.class_unregistered = True

    def create_message_window(self, class_name: str, instance: int) -> int:
        return self.hwnd

    def destroy_window(self, hwnd: int) -> None:
        if self.destroyed:
            return
        self.destroyed = True
        assert self.wndproc is not None
        self.wndproc(hwnd, raw_mouse.WM_DESTROY, 0, 0)

    def def_window_proc(self, hwnd: int, message: int, wparam: int, lparam: int) -> int:
        return 0x55AA

    def register_raw_input_device(self, device: raw_mouse.RAWINPUTDEVICE) -> None:
        self.raw_devices.append(
            (
                int(device.usUsagePage),
                int(device.usUsage),
                int(device.dwFlags),
                int(device.hwndTarget or 0),
            )
        )

    def read_raw_input(self, raw_input_handle: int) -> bytes:
        raise AssertionError("thread lifecycle test does not send WM_INPUT")

    def register_hotkey(self, hwnd: int, hotkey_id: int, modifiers: int, vk: int) -> None:
        self.hotkeys_registered.append((hwnd, hotkey_id, modifiers, vk))

    def unregister_hotkey(self, hwnd: int, hotkey_id: int) -> None:
        self.hotkeys_unregistered.append((hwnd, hotkey_id))

    def get_message(self, message: raw_mouse.MSG) -> int:
        item = self.messages.get(timeout=2.0)
        if item is None:
            return 0
        hwnd, kind, wparam, lparam = item
        message.hwnd = hwnd
        message.message = kind
        message.wParam = wparam
        message.lParam = lparam
        return 1

    def translate_message(self, message: raw_mouse.MSG) -> None:
        pass

    def dispatch_message(self, message: raw_mouse.MSG) -> int:
        assert self.wndproc is not None
        return int(
            self.wndproc(
                message.hwnd,
                message.message,
                message.wParam,
                message.lParam,
            )
        )

    def post_message(self, hwnd: int, message: int, wparam: int, lparam: int) -> None:
        self.messages.put((hwnd, message, wparam, lparam))

    def post_quit_message(self, exit_code: int) -> None:
        self.messages.put(None)


class RawInputLayoutTests(unittest.TestCase):
    def test_x64_windows_structure_layouts_are_exact(self) -> None:
        self.assertEqual(ctypes.sizeof(ctypes.c_void_p), 8)
        self.assertEqual(ctypes.sizeof(raw_mouse.WNDCLASSEXW), 80)
        self.assertEqual(ctypes.sizeof(raw_mouse.MSG), 48)
        self.assertEqual(ctypes.sizeof(raw_mouse.RAWINPUTDEVICE), 16)
        self.assertEqual(ctypes.sizeof(raw_mouse.RAWINPUTHEADER), 24)
        self.assertEqual(ctypes.sizeof(raw_mouse.RAWMOUSE), 24)
        self.assertEqual(ctypes.sizeof(raw_mouse.RAWINPUT), 48)
        self.assertEqual(raw_mouse.RAWINPUT.header.offset, 0)
        self.assertEqual(raw_mouse.RAWINPUT.data.offset, 24)
        self.assertEqual(raw_mouse.RAWMOUSE.lLastX.offset, 12)
        self.assertEqual(raw_mouse.RAWMOUSE.lLastY.offset, 16)

    @unittest.skipUnless(os.name == "nt", "Win32 DLL prototypes are Windows-only")
    def test_every_called_win32_function_has_explicit_prototype(self) -> None:
        api = raw_mouse._Win32Api()
        expected_arg_counts = {
            "RegisterClassExW": 1,
            "UnregisterClassW": 2,
            "CreateWindowExW": 12,
            "DestroyWindow": 1,
            "DefWindowProcW": 4,
            "RegisterRawInputDevices": 3,
            "GetRawInputData": 5,
            "RegisterHotKey": 4,
            "UnregisterHotKey": 2,
            "GetMessageW": 4,
            "TranslateMessage": 1,
            "DispatchMessageW": 1,
            "PostMessageW": 4,
            "PostQuitMessage": 1,
            "GetModuleHandleW": 1,
        }
        for name, count in expected_arg_counts.items():
            function = getattr(api, name)
            self.assertIsNotNone(function.argtypes, name)
            self.assertEqual(len(function.argtypes), count, name)
        self.assertIs(api.GetRawInputData.restype, raw_mouse.UINT)
        self.assertIs(api.DefWindowProcW.restype, raw_mouse.LRESULT)
        self.assertIs(api.CreateWindowExW.restype, raw_mouse.HWND)
        self.assertIsNone(api.PostQuitMessage.restype)


class RawInputDecodeTests(unittest.TestCase):
    def test_decodes_signed_relative_delta(self) -> None:
        packet = raw_mouse.decode_raw_mouse_packet(make_mouse_packet(-17, 29))
        self.assertEqual(packet, raw_mouse.RawMousePacket(-17, 29, False))

    def test_marks_absolute_packet(self) -> None:
        packet = raw_mouse.decode_raw_mouse_packet(
            make_mouse_packet(32768, 16384, absolute=True)
        )
        self.assertEqual(packet, raw_mouse.RawMousePacket(32768, 16384, True))

    def test_non_mouse_packet_returns_none(self) -> None:
        raw = raw_mouse.RAWINPUT()
        raw.header.dwType = 1
        raw.header.dwSize = ctypes.sizeof(raw_mouse.RAWINPUT)
        self.assertIsNone(raw_mouse.decode_raw_mouse_packet(bytes(raw)))

    def test_truncation_and_inconsistent_declared_size_are_rejected(self) -> None:
        with self.assertRaisesRegex(ValueError, "shorter"):
            raw_mouse.decode_raw_mouse_packet(bytes(8))

        packet = bytearray(make_mouse_packet(1, 2))
        header = raw_mouse.RAWINPUTHEADER.from_buffer(packet)
        header.dwSize = len(packet) + 4
        with self.assertRaisesRegex(ValueError, "declares"):
            raw_mouse.decode_raw_mouse_packet(packet)


class RawMouseInputBehaviorTests(unittest.TestCase):
    def test_relative_packets_are_delivered_and_absolute_packets_are_counted_only(self) -> None:
        seen: list[tuple[int, int]] = []
        api = PacketApi(
            [
                make_mouse_packet(4, -7),
                make_mouse_packet(1000, 2000, absolute=True),
                make_mouse_packet(0, 0),
            ]
        )
        source = raw_mouse.RawMouseInput(lambda dx, dy: seen.append((dx, dy)), api=api)

        source._handle_raw_input(10)
        source._handle_raw_input(11)
        source._handle_raw_input(12)

        self.assertEqual(seen, [(4, -7)])
        self.assertEqual(api.handles, [10, 11, 12])
        self.assertEqual(
            source.stats(),
            raw_mouse.RawMouseStats(
                raw_input_messages=3,
                relative_packets=2,
                absolute_packets_ignored=1,
                zero_delta_packets=1,
            ),
        )

    def test_read_parse_and_callback_errors_are_contained_and_counted(self) -> None:
        reports: list[BaseException] = []

        def fail_callback(dx: int, dy: int) -> None:
            raise LookupError("consumer failed")

        api = PacketApi(
            [
                OSError("read failed"),
                bytes(4),
                make_mouse_packet(1, 2),
            ]
        )
        source = raw_mouse.RawMouseInput(
            fail_callback,
            on_error=reports.append,
            api=api,
        )
        source._handle_raw_input(1)
        source._handle_raw_input(2)
        source._handle_raw_input(3)

        stats = source.stats()
        self.assertEqual(stats.read_errors, 1)
        self.assertEqual(stats.malformed_packets, 1)
        self.assertEqual(stats.callback_errors, 1)
        self.assertEqual(stats.relative_packets, 1)
        self.assertEqual(len(reports), 3)
        self.assertIsInstance(source.last_error, LookupError)

    def test_f4_and_escape_dispatch_distinct_callbacks(self) -> None:
        seen: list[str] = []
        source = raw_mouse.RawMouseInput(
            lambda _dx, _dy: None,
            on_f4=lambda: seen.append("f4"),
            on_escape=lambda: seen.append("escape"),
            api=PacketApi([]),
        )
        source._window_proc(1, raw_mouse.WM_HOTKEY, raw_mouse.HOTKEY_F4_ID, 0)
        source._window_proc(1, raw_mouse.WM_HOTKEY, raw_mouse.HOTKEY_ESCAPE_ID, 0)

        self.assertEqual(seen, ["f4", "escape"])
        self.assertEqual(source.stats().f4_hotkeys, 1)
        self.assertEqual(source.stats().escape_hotkeys, 1)

    def test_start_stop_registers_inputs_hotkeys_and_cleans_up(self) -> None:
        api = FakeMessageApi()
        source = raw_mouse.RawMouseInput(lambda _dx, _dy: None, api=api)
        self.assertIs(source.start(timeout=1.0), source)
        self.assertTrue(source.running)
        self.assertEqual(source.hwnd, api.hwnd)

        self.assertTrue(api.class_registered)
        self.assertEqual(
            api.raw_devices[0],
            (
                raw_mouse.HID_USAGE_PAGE_GENERIC,
                raw_mouse.HID_USAGE_GENERIC_MOUSE,
                raw_mouse.RIDEV_INPUTSINK,
                api.hwnd,
            ),
        )
        self.assertEqual(
            api.hotkeys_registered,
            [
                (api.hwnd, raw_mouse.HOTKEY_F4_ID, raw_mouse.MOD_NOREPEAT, raw_mouse.VK_F4),
                (
                    api.hwnd,
                    raw_mouse.HOTKEY_ESCAPE_ID,
                    raw_mouse.MOD_NOREPEAT,
                    raw_mouse.VK_ESCAPE,
                ),
            ],
        )

        # Concurrent stop calls are serialized and remain idempotent.
        failures: list[BaseException] = []

        def stop_source() -> None:
            try:
                source.stop(timeout=1.0)
            except BaseException as exc:  # pragma: no cover - failure capture
                failures.append(exc)

        stoppers = [threading.Thread(target=stop_source) for _ in range(4)]
        for stopper in stoppers:
            stopper.start()
        for stopper in stoppers:
            stopper.join(1.0)

        self.assertEqual(failures, [])
        self.assertFalse(source.running)
        self.assertIsNone(source.hwnd)
        self.assertTrue(api.destroyed)
        self.assertTrue(api.class_unregistered)
        self.assertEqual(api.raw_devices[-1][2:], (raw_mouse.RIDEV_REMOVE, 0))
        self.assertCountEqual(
            api.hotkeys_unregistered,
            [
                (api.hwnd, raw_mouse.HOTKEY_F4_ID),
                (api.hwnd, raw_mouse.HOTKEY_ESCAPE_ID),
            ],
        )

    def test_hotkey_can_request_stop_from_message_thread(self) -> None:
        api = FakeMessageApi()
        stopped_from_callback = threading.Event()
        holder: dict[str, raw_mouse.RawMouseInput] = {}

        def stop_on_escape() -> None:
            holder["source"].stop(timeout=1.0)
            stopped_from_callback.set()

        source = raw_mouse.RawMouseInput(
            lambda _dx, _dy: None,
            on_escape=stop_on_escape,
            api=api,
        )
        holder["source"] = source
        source.start(timeout=1.0)
        api.post_message(api.hwnd, raw_mouse.WM_HOTKEY, raw_mouse.HOTKEY_ESCAPE_ID, 0)

        self.assertTrue(stopped_from_callback.wait(1.0))
        deadline = time.monotonic() + 1.0
        while source.running and time.monotonic() < deadline:
            time.sleep(0.005)
        source.stop(timeout=1.0)
        self.assertFalse(source.running)
        self.assertEqual(source.stats().escape_hotkeys, 1)


if __name__ == "__main__":
    unittest.main()
