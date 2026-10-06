"""Synthetic INI and temporary-directory tests; no installed emulator settings."""
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kiu import azahar_display as display


class DisplayPresetTests(unittest.TestCase):
    def test_defaults_and_selected_aspects(self):
        for aspect, width in ((None, 800), (5 / 3, 800), (16 / 9, 853), (21 / 9, 1120)):
            with self.subTest(aspect=aspect):
                global_bytes, title_bytes, spec = display.prepare_preset(b"", b"", aspect)
                self.assertIn(f"custom_top_width={width}\n".encode(), global_bytes)
                self.assertIn(b"custom_top_width\\default=false\n", global_bytes)
                self.assertIn(b"layout_option=6\n", title_bytes)
                self.assertIn(b"layout_option\\use_global=false\n", title_bytes)
                self.assertIn(b"swap_screen=false\n", title_bytes)
                self.assertIn(b"upright_screen=false\n", title_bytes)
                self.assertEqual(spec.top, (0, 0, width, 480))
                self.assertEqual(spec.bottom, (80, 500, 640, 480))
                self.assertEqual(spec.aspect, 5 / 3 if aspect is None else aspect)

    def test_only_width_changes_in_existing_global_layout(self):
        original = (b"[Layout]\ncustom_top_x=12\ncustom_top_y=34\n"
                    b"custom_top_width=1200\ncustom_top_width\\default=true\n"
                    b"custom_top_height=600\ncustom_bottom_x=900\n"
                    b"custom_bottom_y=520\ncustom_bottom_width=240\n"
                    b"custom_bottom_height=180\ncustom_second_layer_opacity=65\n"
                    b"screen_top_stretch=true\n[Renderer]\nresolution_factor=3\n")
        global_bytes, _title_bytes, spec = display.prepare_preset(original, b"", 16 / 9)
        expected = original.replace(b"custom_top_width=1200", b"custom_top_width=1067").replace(
            b"custom_top_width\\default=true", b"custom_top_width\\default=false")
        self.assertEqual(global_bytes, expected)
        self.assertEqual(spec.top, (12, 34, 1067, 600))
        self.assertEqual(spec.bottom, (900, 520, 240, 180))

    def test_true_default_flags_use_official_values_not_stored_values(self):
        original = (b"[Layout]\ncustom_top_x=99\ncustom_top_x\\default=true\n"
                    b"custom_top_y=90\ncustom_top_y\\default=1\n"
                    b"custom_top_height=300\ncustom_top_height\\default=true\n"
                    b"custom_bottom_width=200\ncustom_bottom_width\\default=true\n")
        global_bytes, _, spec = display.prepare_preset(original, b"", 16 / 9)
        self.assertEqual(spec.top, (0, 0, 853, 480))
        self.assertEqual(spec.bottom, (80, 500, 640, 480))
        self.assertTrue(global_bytes.startswith(original))

    def test_non_target_bytes_bom_crlf_comments_and_spacing_are_preserved(self):
        original = (b"\xef\xbb\xbf; comment\r\n[Layout] ; layout note\r\n"
                    b"  custom_top_width = 1200  ; preferred\r\n"
                    b"custom_top_width\\default = true # flag note\r\n"
                    b"custom_top_height=480\r\n\r\n[UI]\r\n"
                    b'geometry="@ByteArray(\\0\\xff)"\r\n[Core]\r\ncpu_clock_percentage=200')
        global_bytes, _, _ = display.prepare_preset(original, b"", 21 / 9)
        expected = original.replace(b"= 1200  ;", b"= 1120  ;").replace(
            b"= true #", b"= false #")
        self.assertEqual(global_bytes, expected)

    def test_missing_keys_are_inserted_inside_layout_not_the_next_section(self):
        original = b"[Layout]\n; existing\n\n[Audio]\nvolume=0.9\n"
        global_bytes, _, _ = display.prepare_preset(original, b"", None)
        self.assertLess(global_bytes.index(b"custom_top_width="), global_bytes.index(b"[Audio]"))
        self.assertTrue(global_bytes.endswith(b"[Audio]\nvolume=0.9\n"))

    def test_absent_layout_and_missing_final_newline_are_handled(self):
        original = b"; note\r\n[Core]\r\ncpu_clock_percentage=100"
        global_bytes, _, _ = display.prepare_preset(original, b"", None)
        self.assertTrue(global_bytes.startswith(original + b"\r\n[Layout]\r\n"))
        self.assertNotIn(b"\n", global_bytes.replace(b"\r\n", b""))

    def test_title_overrides_keep_other_game_settings_and_clear_stale_defaults(self):
        original = (b"[Layout]\nlayout_option=3\nlayout_option\\default=true\n"
                    b"layout_option\\use_global=true\nswap_screen=true\n"
                    b"swap_screen\\default=true\nswap_screen\\use_global=true\n"
                    b"upright_screen=true\nupright_screen\\use_global=true\n"
                    b"large_screen_proportion=5\n[Utility]\ndump_textures=false\n")
        _, title_bytes, _ = display.prepare_preset(b"", original, None)
        expected = original.replace(b"layout_option=3", b"layout_option=6").replace(
            b"=true", b"=false")
        self.assertEqual(title_bytes, expected)
        self.assertTrue(title_bytes.endswith(b"[Utility]\ndump_textures=false\n"))

    def test_quoted_geometry_and_boolean_values_are_supported(self):
        original = b'[Layout]\ncustom_top_height="300"\ncustom_top_height\\default="false"\n'
        _, _, spec = display.prepare_preset(original, b"", 16 / 9)
        self.assertEqual(spec.top, (0, 0, 533, 300))
        quoted = b'[Layout]\ncustom_top_width = "800" ; preserved quote\n'
        replacement, _, _ = display.prepare_preset(quoted, b"", 21 / 9)
        self.assertIn(b'custom_top_width = "1120" ; preserved quote\n', replacement)

    def test_duplicate_sections_and_keys_are_rejected_before_replacement(self):
        for source in (b"[Layout]\n[Layout]\n", b"[Core]\n[core]\n",
                       b"[Layout]\ncustom_top_height=480\ncustom_top_height=600\n",
                       b"[Layout]\ncustom_top_height=480\nCUSTOM_TOP_HEIGHT=600\n",
                       b"[Layout]\ncustom_top_height=480\n%63ustom_top_height=600\n",
                       b"[Core]\nvalue=1\nvalue=2\n",
                       b"[Layout]\ncustom_top_height\\default=false\ncustom_top_height/default=true\n"):
            with self.subTest(source=source), self.assertRaisesRegex(ValueError, "Duplicate"):
                display.prepare_preset(source, b"", None)

    def test_invalid_geometry_flags_and_title_values_are_refused(self):
        for key, value in (("custom_top_height", "nan"), ("custom_top_height", "-1"),
                           ("custom_top_height", "65536"), ("custom_top_height", "0"),
                           ("custom_top_x", "1.5"), ("custom_top_width", "bogus"),
                           ("custom_bottom_height", "broken"),
                           ("custom_second_layer_opacity", "101"),
                           ("custom_top_height\\default", "perhaps")):
            source = f"[Layout]\n{key}={value}\n".encode()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                display.prepare_preset(source, b"", 16 / 9)
        for key, value in (("layout_option", "7"), ("layout_option", "custom"),
                           ("layout_option\\use_global", "maybe"),
                           ("swap_screen", "yes"), ("upright_screen\\default", "other")):
            source = f"[Layout]\n{key}={value}\n".encode()
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                display.prepare_preset(b"", source, 16 / 9)

    def test_invalid_aspect_text_encoding_and_structure_are_refused(self):
        for aspect in (True, False, "16:9", float("nan"), float("inf"), -1, 0, 1000, 1e-6):
            with self.subTest(aspect=aspect), self.assertRaises(ValueError):
                display.prepare_preset(b"", b"", aspect)
        for source in (b"\xff", b"[Layout]\ncustom_top_height=480\0\n",
                       b"[Layout", b"[Layout]\ninvalid bare line\n", b"[layout]\n",
                       b"[Layout]\nCUSTOM_TOP_HEIGHT=480\n",
                       b"[General]\nLayout\\custom_top_height=480\n"):
            with self.subTest(source=source), self.assertRaises(ValueError):
                display.prepare_preset(source, b"", None)

    def test_preparation_is_idempotent(self):
        first = display.prepare_preset(b"[Core]\nkey=1\n", b"", 16 / 9)
        second = display.prepare_preset(first[0], first[1], 16 / 9)
        self.assertEqual(first, second)

    def test_stereo_off_accepts_global_or_title_defaults_without_mutating_3d(self):
        cases = (
            (b"[Layout]\nrender_3d=6\nrender_3d\\default=true\n", b""),
            (b"[Layout]\nrender_3d=1\n", b"[Layout]\nrender_3d=0\nrender_3d\\use_global=false\n"),
            (b"[Layout]\nrender_3d=0\n", b"[Layout]\nrender_3d=6\nrender_3d\\use_global=true\n"),
            (b"[Layout]\nrender_3d=0\n", b"[Layout]\nrender_3d=6\n"),
            (b"[Layout]\nrender_3d=1\n", b"[Layout]\nrender_3d=6\nrender_3d\\default=true\nrender_3d\\use_global=false\n"),
        )
        for global_source, title_source in cases:
            with self.subTest(global_source=global_source, title_source=title_source):
                global_bytes, title_bytes, _ = display.prepare_preset(global_source, title_source, 16 / 9)
                self.assertTrue(global_bytes.startswith(global_source))
                self.assertTrue(title_bytes.startswith(title_source))

    def test_effective_non_off_stereo_is_refused(self):
        for mode in range(1, 7):
            stereo = f"[Layout]\nrender_3d={mode}\n".encode()
            cases = ((stereo, b""),
                     (stereo, b"[Layout]\nrender_3d=0\nrender_3d\\use_global=true\n"),
                     (b"[Layout]\nrender_3d=0\n", stereo + b"render_3d\\use_global=false\n"))
            for global_source, title_source in cases:
                with self.subTest(mode=mode, title_source=title_source), self.assertRaisesRegex(ValueError, "3D Off"):
                    display.prepare_preset(global_source, title_source, 16 / 9)

    def test_malformed_stereo_mode_or_flags_are_refused(self):
        for source in (b"[Layout]\nrender_3d=7\n", b"[Layout]\nrender_3d=broken\n",
                       b"[Layout]\nrender_3d=-1\n", b"[Layout]\nrender_3d\\default=maybe\n"):
            for global_source, title_source in ((source, b""), (b"", source)):
                with self.subTest(source=source), self.assertRaises(ValueError):
                    display.prepare_preset(global_source, title_source, None)
        with self.assertRaises(ValueError):
            display.prepare_preset(b"", b"[Layout]\nrender_3d\\use_global=perhaps\n", None)

    def test_desktop_top_uses_raw_pixels_and_accepts_negative_desktop_origins(self):
        spec = display.LayoutSpec((12, 34, 1120, 480), (80, 500, 640, 480), 21 / 9)
        self.assertEqual(spec.desktop_top((-1920, -100, 1200, 1000)), (-1908, -66, 1120, 480))
        # No additional 150% DPI multiplication and no width-based rescaling.
        self.assertEqual(spec.desktop_top((100, 200, 1800, 1500)), (112, 234, 1120, 480))

    def test_clipped_or_invalid_top_mapping_returns_unknown(self):
        spec = display.LayoutSpec((12, 34, 1120, 480), (80, 500, 640, 480), 21 / 9)
        for rectangle in ((0, 0, 1131, 1000), (0, 0, 1200, 513), (0, 0, 0, 1),
                          (0, 0, float("inf"), 1000), None, (0, 0), (True, 0, 1200, 1000)):
            with self.subTest(rectangle=rectangle):
                self.assertIsNone(spec.desktop_top(rectangle))
        for top in ((0, 0, 0, 480), (-1, 0, 800, 480), (0, 0, 800.0, 480)):
            with self.subTest(top=top):
                self.assertIsNone(display.LayoutSpec(top, spec.bottom, spec.aspect).desktop_top((0, 0, 1800, 1000)))


class PersistentPresetTests(unittest.TestCase):
    def fixture(self, root):
        global_path = root / "qt-config.ini"
        title_path = root / "custom" / f"{display.TITLE_ID}.ini"
        title_path.parent.mkdir()
        global_bytes = b"[Layout]\ncustom_top_height=480\n[Core]\ncpu_clock_percentage=100\n"
        title_bytes = b"[Utility]\ndump_textures=false\n"
        global_path.write_bytes(global_bytes)
        title_path.write_bytes(title_bytes)
        return global_path, title_path, global_bytes, title_bytes

    def test_save_persists_preset_and_creates_no_backup_or_snapshot(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gp, tp, gb, tb = self.fixture(root)
            expected = display.prepare_preset(gb, tb, 21 / 9)
            self.assertEqual(display.save_preset(root, 21 / 9), expected[2])
            self.assertEqual((gp.read_bytes(), tp.read_bytes()), expected[:2])
            self.assertEqual(sorted(str(path.relative_to(root)) for path in root.rglob("*") if path.is_file()),
                             sorted([str(tp.relative_to(root)), str(gp.relative_to(root))]))
            with patch.object(display, "_atomic_replace") as write:
                display.save_preset(root, 21 / 9)
            write.assert_not_called()

    def test_missing_files_are_created_with_defaults(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "config"
            spec = display.save_preset(root, None)
            self.assertEqual(spec.top, (0, 0, 800, 480))
            self.assertTrue((root / "qt-config.ini").is_file())
            self.assertTrue((root / "custom" / f"{display.TITLE_ID}.ini").is_file())

    def test_malformed_second_file_never_changes_first_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gp, tp, gb, _ = self.fixture(root)
            tp.write_bytes(b"[Layout]\nswap_screen=perhaps\n")
            with self.assertRaises(ValueError), patch.object(display, "_atomic_replace") as write:
                display.save_preset(root, 16 / 9)
            self.assertEqual(gp.read_bytes(), gb)
            write.assert_not_called()

    def test_non_off_stereo_never_writes_either_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gp, tp, _, tb = self.fixture(root)
            gb = b"[Layout]\ncustom_top_height=480\nrender_3d=1\n"
            gp.write_bytes(gb)
            with self.assertRaisesRegex(ValueError, "3D Off"), patch.object(display, "_atomic_replace") as write:
                display.save_preset(root, 16 / 9)
            self.assertEqual((gp.read_bytes(), tp.read_bytes()), (gb, tb))
            write.assert_not_called()

    def test_second_write_failure_rolls_back_only_own_first_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gp, tp, gb, tb = self.fixture(root)
            real_write = display._atomic_replace

            def write(path, content):
                if path == tp:
                    raise PermissionError("test second write failure")
                real_write(path, content)

            with patch.object(display, "_atomic_replace", side_effect=write), self.assertRaises(PermissionError):
                display.save_preset(root, 16 / 9)
            self.assertEqual((gp.read_bytes(), tp.read_bytes()), (gb, tb))

    def test_failed_new_title_rolls_back_new_global_without_backup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            real_write = display._atomic_replace

            def write(path, content):
                if path.name == f"{display.TITLE_ID}.ini":
                    raise PermissionError("test failure")
                real_write(path, content)

            with patch.object(display, "_atomic_replace", side_effect=write), self.assertRaises(PermissionError):
                display.save_preset(root, 16 / 9)
            self.assertFalse(any(path.is_file() for path in root.rglob("*")))

    def test_failure_after_second_replace_rolls_back_both_own_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gp, tp, gb, tb = self.fixture(root)
            real_write = display._atomic_replace

            def write(path, content):
                real_write(path, content)
                if path == tp and b"layout_option=6" in content:
                    raise OSError("test error after replacement")

            with patch.object(display, "_atomic_replace", side_effect=write), self.assertRaises(OSError):
                display.save_preset(root, 16 / 9)
            self.assertEqual((gp.read_bytes(), tp.read_bytes()), (gb, tb))

    def test_one_rollback_error_does_not_skip_other_owned_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gp, tp, gb, _ = self.fixture(root)
            real_write = display._atomic_replace
            real_read = display._read_optional
            blocked = False

            def write(path, content):
                nonlocal blocked
                real_write(path, content)
                if path == tp and b"layout_option=6" in content:
                    blocked = True
                    raise OSError("test error after replacement")

            def read(path):
                if blocked and path == tp:
                    raise PermissionError("test denied rollback read")
                return real_read(path)

            with patch.object(display, "_atomic_replace", side_effect=write), patch.object(
                display, "_read_optional", side_effect=read
            ), self.assertRaisesRegex(RuntimeError, "rollback was incomplete"):
                display.save_preset(root, 16 / 9)
            self.assertEqual(gp.read_bytes(), gb)

    def test_concurrent_title_edit_is_preserved_and_own_global_is_rolled_back(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gp, tp, gb, _ = self.fixture(root)
            real_write = display._atomic_replace
            user_edit = b"[Utility]\ndump_textures=true\n"

            def write(path, content):
                real_write(path, content)
                if path == gp and b"custom_top_width=" in content:
                    tp.write_bytes(user_edit)

            with patch.object(display, "_atomic_replace", side_effect=write), self.assertRaisesRegex(RuntimeError, "changed"):
                display.save_preset(root, 16 / 9)
            self.assertEqual((gp.read_bytes(), tp.read_bytes()), (gb, user_edit))

    def test_concurrent_global_edit_is_not_overwritten_by_rollback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gp, tp, _, tb = self.fixture(root)
            real_write = display._atomic_replace
            user_edit = b"[Core]\ncpu_clock_percentage=150\n"

            def write(path, content):
                real_write(path, content)
                if path == gp:
                    gp.write_bytes(user_edit)

            with patch.object(display, "_atomic_replace", side_effect=write), self.assertRaisesRegex(RuntimeError, "changed"):
                display.save_preset(root, 16 / 9)
            self.assertEqual((gp.read_bytes(), tp.read_bytes()), (user_edit, tb))

    def test_atomic_replace_failure_leaves_no_pending_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            gp, tp, gb, tb = self.fixture(root)
            with patch.object(display.os, "replace", side_effect=PermissionError("test denied")), self.assertRaises(PermissionError):
                display.save_preset(root, 16 / 9)
            self.assertEqual((gp.read_bytes(), tp.read_bytes()), (gb, tb))
            self.assertEqual(list(root.rglob(".kiu-display-*")), [])


if __name__ == "__main__":
    unittest.main()
