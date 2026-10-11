"""Player packaging boundaries are tested without PyInstaller or any game."""
from __future__ import annotations

import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock

from tools import build_launcher as launcher


def fixture(root):
    for name in launcher.PLAYER_TEXT:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("public fixture\n", encoding="utf-8")
    bundle = root / "local/build/launcher/dist" / launcher.APP_NAME
    for name in ("KIU Launcher.exe", "_internal/base_library.zip",
                 "_internal/_tcl_data/init.tcl", "_internal/_tk_data/tk.tcl",
                 "_internal/capstone/lib/capstone.dll"):
        path = bundle / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"bundled component")
    return bundle


class LauncherBuildTests(unittest.TestCase):
    def test_command_keeps_build_artifacts_local_and_excludes_assembler(self):
        command = launcher.pyinstaller_command(Path("workspace"))
        self.assertIn("--onedir", command)
        self.assertIn("--windowed", command)
        self.assertNotIn("--onefile", command)
        self.assertIn("kiu_launcher_payloads", command)
        for name in ("keystone", "kiu.export_cheats", "tools"):
            self.assertIn(name, command)
        for name in ("kiu.experimental.patch_pitch_limit", "kiu.experimental.patch_no_recenter",
                     "kiu.experimental.patch_button_dash"):
            self.assertNotIn(name, launcher.EXCLUDED_MODULES)
        self.assertNotIn("kiu.cli", command)
        for flag in ("--workpath", "--specpath", "--distpath"):
            self.assertIn("local", Path(command[command.index(flag) + 1]).parts)
        self.assertNotIn("--add-data", command)

    def test_build_environment_does_not_mutate_process_configuration(self):
        before = dict(os.environ)
        env = launcher.build_environment(Path("workspace"), Path("workspace/local/build-tools"))
        self.assertEqual(os.environ, before)
        self.assertIn("local", Path(env["PYINSTALLER_CONFIG_DIR"]).parts)
        self.assertTrue(env["PYTHONPATH"].startswith(str(Path("workspace/local/build-tools"))))

    def test_generated_payloads_match_canonical_without_rom(self):
        namespace = {}
        exec(launcher.hook_payload_source(), namespace)
        canonical = json.loads((launcher.ROOT / "manifests/gateway_cheats.json").read_text(encoding="utf-8"))
        sites = {int(site["address"], 16): site["replacement_hex"]
                 for site in canonical["features"]["native_mouse_guest_hooks"]["sites"]}
        self.assertEqual(namespace["BUILD_SHA256"], canonical["code_sha256"])
        for name, address in (("GROUND_HOOK", 0x00C95100), ("RETICLE_HOOK", 0x00C95F00),
                              ("RETICLE_LATE_HOOK", 0x00C95200)):
            self.assertEqual(namespace[name].hex(), sites[address])
        menu_sites = {int(site["address"], 16): site["replacement_hex"]
                      for site in canonical["features"]["menu_bottom_to_top"]["sites"]}
        self.assertEqual(namespace["MENU_PRE_HOOK"].hex(), menu_sites[0x00C95640])
        self.assertEqual(namespace["MENU_POST_HOOK"].hex(), menu_sites[0x00C95700])

    def test_stale_canonical_payload_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "manifests").mkdir()
            canonical = json.loads((launcher.ROOT / "manifests/gateway_cheats.json").read_text(encoding="utf-8"))
            site = next(site for site in canonical["features"]["native_mouse_guest_hooks"]["sites"]
                        if int(site["address"], 16) == 0x00C95100)
            site["replacement_hex"] = "wrong"
            (root / "manifests/gateway_cheats.json").write_text(json.dumps(canonical), encoding="utf-8")
            with self.assertRaisesRegex(launcher.LauncherBuildError, "differ from canonical"):
                launcher.hook_payload_source(root)

    def test_player_pack_uses_only_bundle_and_public_text_allowlist(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            bundle = fixture(root)
            (root / "local/game.cci").write_bytes(b"private game")
            (root / "local/settings.json").write_text("private path", encoding="utf-8")
            files = launcher.player_files(bundle, root, licenses={"licenses/example.txt": b"license"})
            self.assertIn("KIU Launcher.exe", files)
            self.assertIn("docs/KNOWLEDGE.md", files)
            self.assertIn("licenses/example.txt", files)
            self.assertNotIn("local/settings.json", files)
            self.assertTrue(all(not name.startswith("local/") for name in files))
            self.assertTrue(all(not name.endswith(".cci") for name in files))

    def test_incomplete_tk_or_capstone_bundle_is_refused(self):
        for missing in ("_internal/_tcl_data/init.tcl", "_internal/capstone/lib/capstone.dll"):
            with self.subTest(missing=missing), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle = fixture(root)
                (bundle / missing).unlink()
                with self.assertRaises(launcher.LauncherBuildError):
                    launcher.player_files(bundle, root, licenses={})

    def test_rom_assembler_and_unexpected_top_level_output_are_refused(self):
        for extra in ("_internal/game.bin", "_internal/keystone.dll", "private.json"):
            with self.subTest(extra=extra), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                bundle = fixture(root)
                path = bundle / extra
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"must not be published")
                with self.assertRaises(launcher.LauncherBuildError):
                    launcher.player_files(bundle, root, licenses={})


if __name__ == "__main__":
    unittest.main()
