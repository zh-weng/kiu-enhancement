"""Linux display adapter checks without X11, emulator, or game data."""
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu.backends.linux import display_preset as display


class LinuxDisplayTests(unittest.TestCase):
    def test_xdg_profile_is_used_without_touching_global_environment(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with mock.patch.dict(display.os.environ, {"XDG_CONFIG_HOME": str(root / "profile")}):
                self.assertEqual(display.config_directory(root / "azahar.AppImage"),
                                 root / "profile" / "azahar-emu")

    def test_portable_user_directory_takes_precedence(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "user").mkdir()
            self.assertEqual(display.config_directory(root / "azahar"), root / "user" / "config")

    def test_relative_xdg_config_is_refused(self):
        with mock.patch.dict(display.os.environ, {"XDG_CONFIG_HOME": "relative/profile"}):
            with self.assertRaisesRegex(ValueError, "absolute"):
                display.config_directory(Path("/absent/azahar.AppImage"))

    def test_running_native_and_appimage_processes_refuse_display_save(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "42").mkdir()
            (proc / "self").mkdir()
            for name in ("azahar", "azahar-qt", "azahar.AppImage"):
                with self.subTest(name=name):
                    (proc / "42" / "cmdline").write_bytes(f"/apps/{name}\0game.cci\0".encode())
                    with self.assertRaisesRegex(RuntimeError, "Azahar"):
                        display.require_emulator_closed(proc)
            (proc / "42" / "cmdline").write_bytes(b"python3\0run_launcher.py\0")
            display.require_emulator_closed(proc)

    def test_exiting_process_does_not_block_display_save(self):
        with tempfile.TemporaryDirectory() as directory:
            proc = Path(directory)
            (proc / "42").mkdir()
            display.require_emulator_closed(proc)

    def test_auto_single_screen_uses_display_ratio(self):
        fake_x11 = SimpleNamespace(fullscreen_size=mock.Mock(return_value=(1280, 800)))
        with mock.patch.dict(sys.modules, {"kiu.backends.linux.x11": fake_x11}), \
                mock.patch.object(display, "require_emulator_closed"), \
                mock.patch.object(display, "config_directory", return_value=Path("profile")), \
                mock.patch("kiu.azahar_display.save_preset", return_value="layout") as save:
            self.assertEqual(display.save_display(Path("azahar.AppImage"), "auto", single_screen=True), "layout")
            save.assert_called_once_with(Path("profile"), 16 / 10, display_size=(1280, 800))

    def test_unknown_render_rectangle_remains_unknown(self):
        fake_x11 = SimpleNamespace(render_rect=mock.Mock(return_value=None))
        layout = mock.Mock()
        with mock.patch.dict(sys.modules, {"kiu.backends.linux.x11": fake_x11}):
            rectangle = display.top_rect_provider(layout)
            self.assertIsNone(rectangle(42))
            layout.desktop_top.assert_not_called()
            fake_x11.render_rect.return_value = (0, 0, 1280, 800)
            rectangle(42)
            layout.desktop_top.assert_called_once_with((0, 0, 1280, 800))


if __name__ == "__main__":
    unittest.main()
