"""Host binding regressions; never inspect or modify real emulator settings."""
import ctypes
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu.azahar_display import LayoutSpec
from kiu.backends.windows import display_preset as backend


class DisplayBackendTests(unittest.TestCase):
    def test_portable_profile_has_precedence_without_loading_windows(self):
        with tempfile.TemporaryDirectory() as directory:
            executable = Path(directory) / "azahar.exe"
            (executable.parent / "user").mkdir()
            with mock.patch.object(ctypes, "WinDLL", create=True) as load:
                self.assertEqual(backend.config_directory(executable), executable.parent / "user/config")
                load.assert_not_called()

    def test_installed_profile_uses_windows_roaming_folder_not_environment(self):
        shell = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)

            def folder(hwnd, identifier, token, flags, result):
                self.assertEqual(identifier, 0x1A)
                result.value = str(root / "roaming")
                return 0

            shell.SHGetFolderPathW.side_effect = folder
            with mock.patch.object(ctypes, "WinDLL", return_value=shell, create=True):
                self.assertEqual(backend.config_directory(root / "azahar.exe"), root / "roaming/Azahar/config")

    def test_failed_folder_lookup_is_not_guessed(self):
        shell = mock.Mock()
        shell.SHGetFolderPathW.return_value = -1
        with tempfile.TemporaryDirectory() as directory, \
                mock.patch.object(ctypes, "WinDLL", return_value=shell, create=True):
            with self.assertRaises(OSError):
                backend.config_directory(Path(directory) / "azahar.exe")

    def test_any_azahar_instance_blocks_preset(self):
        output = '"System","4","Services","0","1,024 K"\n"AZAHAR.EXE","123","Console","1","8 K"\n'
        with mock.patch.object(backend.subprocess, "run", return_value=SimpleNamespace(stdout=output)):
            with self.assertRaisesRegex(RuntimeError, "Azahar"):
                backend.require_emulator_closed()

    def test_no_azahar_processes_allows_preset(self):
        output = '"System","4","Services","0","1,024 K"\n'
        with mock.patch.object(backend.subprocess, "run", return_value=SimpleNamespace(stdout=output)) as run:
            backend.require_emulator_closed()
            self.assertTrue(run.call_args.kwargs["check"])

    def test_unrecognized_process_listing_is_not_treated_as_closed(self):
        for output in ("", "tasklist failed", '"azahar.exe","unknown","Console","1","8 K"'):
            with self.subTest(output=output), mock.patch.object(
                    backend.subprocess, "run", return_value=SimpleNamespace(stdout=output)):
                with self.assertRaises(RuntimeError):
                    backend.require_emulator_closed()

    def test_running_emulator_refuses_before_config_access(self):
        with mock.patch.object(backend, "require_emulator_closed", side_effect=RuntimeError("running")), \
                mock.patch.object(backend, "config_directory") as locate, \
                mock.patch("kiu.azahar_display.save_preset") as save:
            with self.assertRaisesRegex(RuntimeError, "running"):
                backend.save_display(Path("azahar.exe"), 16 / 9)
            locate.assert_not_called()
            save.assert_not_called()

    def test_provider_binds_canvas_to_top_and_refuses_missing_or_clipped(self):
        from kiu.backends import windows as package
        render = mock.Mock(side_effect=[(100, 200, 1000, 1000), None, (100, 200, 800, 1000)])
        windows = SimpleNamespace(render_rect=render)
        spec = LayoutSpec((10, 20, 853, 480), (80, 500, 640, 480), 16 / 9)
        with mock.patch.object(package, "win_window", windows, create=True):
            rectangle = backend.top_rect_provider(spec)
            self.assertEqual(rectangle(123), (110, 220, 853, 480))
            self.assertIsNone(rectangle(123))
            self.assertIsNone(rectangle(123))
            self.assertEqual(render.call_args_list, [mock.call(123)] * 3)


if __name__ == "__main__":
    unittest.main()
