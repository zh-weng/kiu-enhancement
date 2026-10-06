"""Fullscreen projection/display matching without a display or emulator."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu.azahar_display import prepare_single_preset, save_preset
from kiu.backends.windows import display_preset


class SingleScreenTests(unittest.TestCase):
    def test_matching_aspect_fills_native_single_screen(self):
        global_bytes, title, spec = prepare_single_preset(b"", b"", 16 / 9, (1920, 1080))
        self.assertIn(b"layout_option=1\n", title)
        self.assertIn(b"swap_screen=false", title)
        self.assertIn(b"screen_top_stretch=true", global_bytes)
        self.assertIn(b"screen_bottom_stretch=false", global_bytes)
        self.assertEqual(spec.top, (0, 0, 1920, 1080))
        self.assertEqual(spec.desktop_top((100, 200, 1920, 1080)), (100, 200, 1920, 1080))

    def test_selected_aspect_letterboxes_instead_of_distorting_on_16_10_monitor(self):
        global_bytes, _title, spec = prepare_single_preset(b"", b"", 16 / 9, (2560, 1600))
        self.assertEqual(spec.top, (0, 80, 2560, 1440))
        self.assertIn(b"screen_top_topbottom_padding=80", global_bytes)
        self.assertIsNone(spec.desktop_top((0, 0, 1920, 1080)))
        self.assertIsNone(spec.desktop_top((0, 0, 1200, 900)))

    def test_custom_dual_geometry_and_other_settings_are_preserved(self):
        original = b"[Renderer]\nresolution_factor=3\n[Layout]\ncustom_top_width=1120\ncustom_bottom_width=640\n"
        updated, _title, _spec = prepare_single_preset(original, b"", 21 / 9, (1920, 1080))
        self.assertTrue(updated.startswith(original))
        self.assertIn(b"screen_top_topbottom_padding=129", updated)

    def test_invalid_dimensions_and_stereo_are_refused(self):
        for size in ((0, 1080), (1920, False), (1920,), (65536, 1080)):
            with self.subTest(size=size), self.assertRaises(ValueError):
                prepare_single_preset(b"", b"", 16 / 9, size)
        with self.assertRaisesRegex(ValueError, "3D Off"):
            prepare_single_preset(b"[Layout]\nrender_3d=1\n", b"", 16 / 9, (1920, 1080))

    def test_persistent_single_preset_only_writes_tempdir(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            spec = save_preset(root, 16 / 9, display_size=(1920, 1080))
            self.assertEqual(spec.canvas_size, (1920, 1080))
            self.assertIn(b"layout_option=1", (root / "custom/0004000000030100.ini").read_bytes())

    def test_backend_auto_uses_physical_display_aspect(self):
        with mock.patch.object(display_preset, "require_emulator_closed"), \
                mock.patch.object(display_preset, "fullscreen_size", return_value=(2560, 1600)), \
                mock.patch.object(display_preset, "config_directory", return_value=Path("private/config")), \
                mock.patch("kiu.azahar_display.save_preset") as save:
            display_preset.save_display(Path("azahar.exe"), "auto", single_screen=True)
            save.assert_called_once_with(Path("private/config"), 16 / 10, display_size=(2560, 1600))


if __name__ == "__main__":
    unittest.main()
