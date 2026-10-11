"""Public cheat exports are reproducible without possessing a game image."""
from __future__ import annotations

import contextlib
import hashlib
import io
import json
import re
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiu import export_cheats as export  # noqa: E402


EXPECTED_SECTIONS = (
    "60 FPS - Enable", "60 FPS - Restore 30 FPS",
    "Widescreen 16-9 - Enable", "Widescreen 21-9 - Enable",
    "Widescreen - Restore 5-3",
    "Menu Bottom-to-Top Native Route - Enable",
    "Menu Bottom-to-Top Native Route - Restore",
    "Native Mouse Guest Hooks - Enable", "Native Mouse Guest Hooks - Restore",
    "Ground Pitch Limit 60deg - Enable EXPERIMENTAL", "Ground Pitch Limit - Restore",
    "All Core Patches - Restore",
)


def patch_records(value):
    if isinstance(value, dict):
        if "original_hex" in value:
            yield value
        else:
            for child in value.values():
                yield from patch_records(child)
    elif isinstance(value, list):
        for child in value:
            yield from patch_records(child)


class PublicExportTests(unittest.TestCase):
    def test_build_without_rom_never_reads_an_image(self):
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("ROM access")):
            with mock.patch.object(export, "verify_image", side_effect=AssertionError("ROM verification")):
                text, manifest = export.build()
        self.assertEqual(re.findall(r"^\[(.*)\]$", text, re.MULTILINE), list(EXPECTED_SECTIONS))
        self.assertEqual(manifest["code_sha256"], export.CODE_SHA256)
        export.validate_cheat_text(text)

    def test_every_site_records_build_and_equal_sized_original_replacement(self):
        _, manifest = export.build()
        records = list(patch_records(manifest["features"]))
        self.assertGreater(len(records), 20)
        for record in records:
            with self.subTest(name=record["name"]):
                self.assertEqual(record["build_sha256"], export.CODE_SHA256)
                original = bytes.fromhex(record["original_hex"])
                replacement = bytes.fromhex(record["replacement_hex"])
                self.assertEqual(len(original), len(replacement))
                self.assertGreater(len(original), 0)
                self.assertEqual(len(original) % 4, 0)
                self.assertTrue("address" in record or "address_expression" in record)
                if "size" in record:
                    self.assertEqual(record["size"], len(original))

    def test_manifest_feature_actions_refer_to_generated_sections(self):
        text, manifest = export.build()
        names = set(re.findall(r"^\[(.*)\]$", text, re.MULTILINE))
        for feature in manifest["features"].values():
            for action in ("enable", "restore"):
                if action in feature:
                    self.assertIn(feature[action], names)
        mouse = manifest["features"]["native_mouse_guest_hooks"]
        self.assertFalse(mouse["standalone_cheat"])
        self.assertIn("preview", mouse["status"])
        self.assertIn("experimental", manifest["features"]["menu_bottom_to_top"]["status"])

    def test_repeated_builds_are_identical(self):
        first = export.build()
        self.assertEqual(export.build(), first)

    def test_basic_export_is_exactly_the_five_original_sections(self):
        full, full_manifest = export.build()
        with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("ROM access")):
            basic, manifest = export.build_basic()
        sections = re.findall(r"^\[(.*)\]$", basic, re.MULTILINE)
        self.assertEqual(sections, list(export.BASIC_SECTIONS))
        for name in sections:
            pattern = rf"(?ms)^\[{re.escape(name)}\].*?(?=^\[|\Z)"
            self.assertEqual(re.search(pattern, basic).group(), re.search(pattern, full).group())
        self.assertEqual(set(manifest["features"]), {"60_fps", "widescreen"})
        for name, feature in manifest["features"].items():
            self.assertEqual(feature, full_manifest["features"][name])
        self.assertEqual(manifest["generated_cheat"], export.TITLE_ID + ".txt")
        self.assertNotIn("excluded", manifest)
        export.validate_cheat_text(basic, expected_sections=5)

    def test_basic_cli_writes_only_fps_and_widescreen(self):
        with tempfile.TemporaryDirectory() as directory:
            output, manifest_path = Path(directory) / "basic.txt", Path(directory) / "patches.json"
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(export.main(["--basic", "--output", str(output),
                                              "--manifest", str(manifest_path)]), 0)
            text, manifest = export.build_basic()
            self.assertEqual(output.read_text(encoding="utf-8"), text)
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), manifest)

    def test_basic_optional_image_checks_only_the_selected_sites(self):
        image = mock.Mock(spec=Path)
        with mock.patch.object(export, "verify_image") as verify:
            export.build_basic(image)
        groups, checked_image = verify.call_args.args
        caves, entries = export.fps_patches()
        self.assertEqual(groups, [caves + entries, export.widescreen_patches(16 / 9),
                                  export.widescreen_patches(21 / 9)])
        self.assertIs(checked_image, image)

    def test_cli_writes_text_and_metadata_without_rom(self):
        with tempfile.TemporaryDirectory() as directory:
            text_path = Path(directory) / "cheats" / "kiu.txt"
            manifest_path = Path(directory) / "metadata" / "kiu.json"
            with mock.patch.object(Path, "read_bytes", side_effect=AssertionError("ROM access")):
                with contextlib.redirect_stdout(io.StringIO()):
                    result = export.main([
                        "--output", str(text_path), "--manifest", str(manifest_path),
                    ])
            self.assertEqual(result, 0)
            expected_text, expected_manifest = export.build()
            self.assertEqual(text_path.read_text(encoding="utf-8"), expected_text)
            self.assertEqual(json.loads(manifest_path.read_text(encoding="utf-8")), expected_manifest)

    def test_optional_wrong_image_is_rejected_before_outputs_are_written(self):
        image = mock.Mock(spec=Path)
        image.read_bytes.return_value = b"wrong build"
        with self.assertRaisesRegex(RuntimeError, "unexpected code SHA-256"):
            export.build(image)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "kiu.txt"
            manifest = Path(directory) / "kiu.json"
            with mock.patch.object(Path, "read_bytes", return_value=b"wrong build"):
                with self.assertRaisesRegex(RuntimeError, "unexpected code SHA-256"):
                    export.main([
                        "--verify-code", str(Path(directory) / "code.bin"),
                        "--output", str(output), "--manifest", str(manifest),
                    ])
            self.assertFalse(output.exists())
            self.assertFalse(manifest.exists())

    def test_validator_rejects_malformed_lines_or_missing_restore_section(self):
        text, _ = export.build()
        with self.assertRaisesRegex(RuntimeError, "invalid Gateway line"):
            export.validate_cheat_text(text + "not a code\n")
        partial = text[:text.index("[All Core Patches - Restore]")]
        with self.assertRaisesRegex(RuntimeError, "expected 12 cheat sections"):
            export.validate_cheat_text(partial)


class ImageVerificationTests(unittest.TestCase):
    def setUp(self):
        self.original = bytes.fromhex("11223344")
        self.site = export.StaticPatch("synthetic patch", export.CODE_BASE + 4,
                                       self.original, bytes.fromhex("55667788"))
        self.blob = bytes(4) + self.original + bytes(4)
        self.image = mock.Mock(spec=Path)
        self.image.read_bytes.return_value = self.blob

    def verify(self, groups):
        # Test byte preflight with an explicitly synthetic supported digest.
        digest = hashlib.sha256(self.blob).hexdigest()
        with mock.patch.object(export, "CODE_SHA256", digest):
            return export.verify_image(groups, self.image)

    def test_matching_sites_and_repeated_variant_originals_are_accepted(self):
        self.assertEqual(self.verify([[self.site], [self.site]]), self.blob)

    def test_hash_match_does_not_replace_per_site_byte_verification(self):
        mismatch = export.StaticPatch("changed stock", self.site.address, bytes(4), bytes(4))
        with self.assertRaisesRegex(RuntimeError, "stock mismatch"):
            self.verify([[mismatch]])

    def test_variant_original_byte_disagreement_is_rejected(self):
        conflict = export.StaticPatch("conflicting patch", self.site.address,
                                     bytes(4), bytes.fromhex("aabbccdd"))
        with self.assertRaisesRegex(RuntimeError, "inconsistent stock bytes"):
            self.verify([[self.site], [conflict]])

    def test_out_of_image_site_cannot_pass_as_empty_zero_space(self):
        outside = export.StaticPatch("outside image", export.CODE_BASE + len(self.blob),
                                    bytes(4), bytes.fromhex("11223344"))
        with self.assertRaisesRegex(RuntimeError, "stock mismatch"):
            self.verify([[outside]])


if __name__ == "__main__":
    unittest.main()
