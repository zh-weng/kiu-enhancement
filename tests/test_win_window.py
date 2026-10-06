"""Window/DPI contracts with fake Win32 calls: no real windows or input."""
from __future__ import annotations

from contextlib import nullcontext
import ctypes
from pathlib import Path
import sys
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
if sys.platform == "win32":
    from kiu.backends.windows import win_window as window


@unittest.skipUnless(sys.platform == "win32", "Windows host adapter")
class DpiContextTests(unittest.TestCase):
    def test_per_monitor_context_restores_previous_context_on_normal_exit(self):
        with patch.object(window.user32, "SetThreadDpiAwarenessContext", side_effect=[17, 19]) as set_context:
            with window.per_monitor_dpi():
                self.assertEqual(set_context.call_count, 1)
        self.assertEqual([call.args[0].value for call in set_context.call_args_list],
                         [ctypes.c_void_p(-4).value, 17])
        self.assertIs(set_context.restype, ctypes.c_void_p)

    def test_per_monitor_v1_fallback_is_also_restored_on_exception(self):
        with patch.object(window.user32, "SetThreadDpiAwarenessContext", side_effect=[None, 17, 19]) as set_context:
            with self.assertRaisesRegex(OSError, "body failed"):
                with window.per_monitor_dpi():
                    raise OSError("body failed")
        self.assertEqual([call.args[0].value for call in set_context.call_args_list],
                         [ctypes.c_void_p(-4).value, ctypes.c_void_p(-3).value, 17])

    def test_context_setup_failure_does_not_enter_or_restore(self):
        with patch.object(window.user32, "SetThreadDpiAwarenessContext", return_value=None) as set_context:
            with self.assertRaisesRegex(RuntimeError, "Cannot establish"):
                with window.per_monitor_dpi():
                    self.fail("must not enter an unknown coordinate context")
        self.assertEqual(set_context.call_count, 2)

    def test_context_restore_failure_is_reported(self):
        with patch.object(window.user32, "SetThreadDpiAwarenessContext", side_effect=[17, None]):
            with self.assertRaisesRegex(RuntimeError, "Cannot restore"):
                with window.per_monitor_dpi():
                    pass


@unittest.skipUnless(sys.platform == "win32", "Windows host adapter")
class ClientRectTests(unittest.TestCase):
    def test_client_bounds_translate_both_corners_and_keep_negative_origin(self):
        def get_client(_hwnd, rect):
            rect._obj.left, rect._obj.top = 0, 0
            rect._obj.right, rect._obj.bottom = 800, 400
            return True

        def to_screen(_hwnd, point):
            point._obj.x -= 1920
            point._obj.y -= 240
            return True

        with patch.object(window.user32, "GetClientRect", side_effect=get_client), patch.object(
            window.user32, "ClientToScreen", side_effect=to_screen
        ) as convert:
            self.assertEqual(window.client_rect(7), (-1920, -240, 800, 400))
        self.assertEqual(convert.call_count, 2)

    def test_failed_client_query_raises_without_mapping_an_empty_rect(self):
        with patch.object(window.user32, "GetClientRect", return_value=False), patch.object(
            window.user32, "ClientToScreen"
        ) as convert:
            with self.assertRaises(OSError):
                window.client_rect(7)
        convert.assert_not_called()

    def test_failed_corner_conversion_raises(self):
        with patch.object(window.user32, "GetClientRect", return_value=True), patch.object(
            window.user32, "ClientToScreen", return_value=False
        ):
            with self.assertRaises(OSError):
                window.client_rect(7)


@unittest.skipUnless(sys.platform == "win32", "Windows host adapter")
class RenderRectTests(unittest.TestCase):
    def setUp(self):
        self.roots = [1]
        self.children = [2]
        self.pids = {1: 123, 2: 123}
        self.classes = {1: "Qt683QWindowIcon", 2: "Qt683QWindowOwnDCIcon"}
        self.visible = {1: True, 2: True}
        self.minimized = set()
        self.owners = {}
        self.native = MagicMock()
        self.native.GetWindow.side_effect = lambda hwnd, _kind: self.owners.get(hwnd, 0)
        self.native.IsWindowVisible.side_effect = lambda hwnd: self.visible.get(hwnd, False)
        self.native.IsIconic.side_effect = lambda hwnd: hwnd in self.minimized
        self.native.IsChild.side_effect = lambda root, child: root == 1 and child in self.children

        def get_pid(hwnd, pointer):
            pointer._obj.value = self.pids.get(hwnd, 0)
            return 1 if pointer._obj.value else 0

        def get_class(hwnd, buffer, _size):
            buffer.value = self.classes.get(hwnd, "")
            return len(buffer.value)

        def enumerate_roots(callback, _parameter):
            for hwnd in self.roots:
                callback(hwnd, None)
            return True

        def enumerate_children(_root, callback, _parameter):
            for hwnd in self.children:
                callback(hwnd, None)
            return True

        self.native.GetWindowThreadProcessId.side_effect = get_pid
        self.native.GetClassNameW.side_effect = get_class
        self.native.EnumWindows.side_effect = enumerate_roots
        self.native.EnumChildWindows.side_effect = enumerate_children
        mocked = patch.object(window, "user32", self.native)
        mocked.start()
        self.addCleanup(mocked.stop)
        dpi = patch.object(window, "per_monitor_dpi", side_effect=lambda: nullcontext())
        dpi.start()
        self.addCleanup(dpi.stop)
        client = patch.object(window, "client_rect", return_value=(-1200, -180, 1200, 600))
        self.client = client.start()
        self.addCleanup(client.stop)

    def test_unique_known_render_child_returns_its_client_not_main_client(self):
        self.assertEqual(window.render_rect(123), (-1200, -180, 1200, 600))
        self.client.assert_called_once_with(2)

    def test_ordinary_larger_children_are_not_selected_as_canvas(self):
        self.children += [3]
        self.pids[3], self.classes[3], self.visible[3] = 123, "Qt683QWindowIcon", True
        self.assertEqual(window.render_rect(123), (-1200, -180, 1200, 600))
        self.client.assert_called_once_with(2)

    def test_generic_vulkan_child_is_not_guessed_from_class_or_size(self):
        self.classes[2] = "Qt683QWindowIcon"
        self.assertIsNone(window.render_rect(123))
        self.client.assert_not_called()

    def test_no_children_returns_none(self):
        self.children = []
        self.assertIsNone(window.render_rect(123))
        self.client.assert_not_called()

    def test_multiple_render_children_are_refused_not_ranked_by_area(self):
        self.children += [3]
        self.pids[3], self.classes[3], self.visible[3] = 123, "Qt683QWindowOwnDCIcon", True
        self.assertIsNone(window.render_rect(123))
        self.client.assert_not_called()

    def test_multiple_unowned_main_windows_are_refused(self):
        self.roots += [3]
        self.pids[3], self.classes[3], self.visible[3] = 123, "Qt683QWindowIcon", True
        self.assertIsNone(window.render_rect(123))
        self.native.EnumChildWindows.assert_not_called()

    def test_hidden_foreign_or_owned_windows_are_ignored(self):
        self.roots += [3, 4, 5]
        self.pids.update({3: 123, 4: 456, 5: 123})
        self.classes.update({3: "Qt683QWindowIcon", 4: "Qt683QWindowIcon", 5: "Qt683QWindowIcon"})
        self.visible.update({3: False, 4: True, 5: True})
        self.owners[5] = 1
        self.assertEqual(window.render_rect(123), (-1200, -180, 1200, 600))

    def test_minimized_main_window_returns_none(self):
        self.minimized.add(1)
        self.assertIsNone(window.render_rect(123))
        self.client.assert_not_called()

    def test_hidden_or_foreign_render_child_returns_none(self):
        self.visible[2] = False
        self.assertIsNone(window.render_rect(123))
        self.visible[2], self.pids[2] = True, 456
        self.assertIsNone(window.render_rect(123))
        self.client.assert_not_called()

    def test_unknown_or_dialog_main_window_class_is_not_canvas(self):
        self.classes[1] = "Qt683QWindowToolSaveBits"
        self.assertIsNone(window.render_rect(123))
        self.client.assert_not_called()

    def test_zero_size_or_failed_client_query_returns_none(self):
        self.client.return_value = (10, 20, 0, 600)
        self.assertIsNone(window.render_rect(123))
        self.client.side_effect = OSError("window disappeared")
        self.assertIsNone(window.render_rect(123))

    def test_pid_discovery_race_returns_none(self):
        calls = {1: 0}

        def get_pid(hwnd, pointer):
            if hwnd == 1:
                calls[1] += 1
            pointer._obj.value = 456 if hwnd == 1 and calls[1] > 1 else self.pids[hwnd]
            return 1

        self.native.GetWindowThreadProcessId.side_effect = get_pid
        self.assertIsNone(window.render_rect(123))
        self.client.assert_not_called()

    def test_unknown_dpi_context_returns_none(self):
        with patch.object(window, "per_monitor_dpi", side_effect=RuntimeError("unsupported")):
            self.assertIsNone(window.render_rect(123))
        self.native.EnumWindows.assert_not_called()

    def test_invalid_pid_is_rejected_before_window_discovery(self):
        for pid in (None, "123", True, 0, -1):
            with self.subTest(pid=pid), self.assertRaises(ValueError):
                window.render_rect(pid)
        self.native.EnumWindows.assert_not_called()


if __name__ == "__main__":
    unittest.main()
