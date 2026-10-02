"""Runtime hooks must work without a distributable game image.

The fake guest exercises the real apply/revert methods, including refusing
foreign bytes before writing. Only the explicit source-image integration test
needs a locally prepared, non-distributed code.bin.
"""
from __future__ import annotations

import hashlib
import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import patch_aim_request as aim  # noqa: E402
import patch_pitch_limit as pitch  # noqa: E402
import pin_reticle as pin  # noqa: E402


class FakeGdb:
    def __init__(self, regions: tuple[tuple[int, bytes], ...]) -> None:
        self.memory: dict[int, int] = {}
        self.writes: list[tuple[int, bytes]] = []
        self.resumes = 0
        for address, raw in regions:
            self.load(address, raw)

    def load(self, address: int, raw: bytes) -> None:
        self.memory.update((address + offset, byte) for offset, byte in enumerate(raw))

    def read_mem(self, address: int, size: int) -> bytes:
        return bytes(self.memory.get(address + offset, 0) for offset in range(size))

    def write_mem(self, address: int, raw: bytes) -> None:
        self.writes.append((address, raw))
        self.load(address, raw)

    def cont_all(self) -> None:
        self.resumes += 1


def aim_guest() -> FakeGdb:
    return FakeGdb(((aim.CALL_SITE, aim.CALL_ORIGINAL),
                    (aim.CALL_SITE - 8, aim.P_SETUP_ORIGINAL)))


def pin_guest() -> FakeGdb:
    return FakeGdb(((pin.CALL_SITE, pin.CALL_ORIGINAL),
                    (pin.LATE_SITE, pin.LATE_ORIGINAL)))


def pitch_guest() -> FakeGdb:
    return FakeGdb(((pitch.UPPER_VA, pitch.STOCK_UPPER),
                    (pitch.LOWER_VA, pitch.STOCK_LOWER),
                    (pitch.UPPER_MOVE_VA, pitch.STOCK_UPPER_MOVE),
                    (pitch.LOWER_MOVE_VA, pitch.STOCK_LOWER_MOVE),
                    *tuple(zip(pitch.LOADERS, pitch.LOADER_ORIGINALS))))


class RuntimeDistributionTests(unittest.TestCase):
    def test_runtime_imports_do_not_read_any_game_image(self) -> None:
        script = """
import importlib
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
with patch.object(Path, 'read_bytes', side_effect=AssertionError('unexpected file dependency')):
    for name in sys.argv[2:]:
        importlib.import_module(name)
assert 'mouse_look_live' not in sys.modules
assert 'numpy' not in sys.modules
"""
        names = ["patch_aim_request", "pin_reticle", "patch_pitch_limit", "menu_render_pass"]
        dependencies = ["gdbclient"]
        if sys.platform == "win32":
            names.append("native_fps_aim")
            dependencies += ["guest_memory", "win_raw_mouse", "win_window", "aim_protocol", "aim_core"]
        with tempfile.TemporaryDirectory() as directory:
            clean_tools = Path(directory) / "tools"
            clean_tools.mkdir()
            for name in dependencies + names:
                shutil.copy2(ROOT / "tools" / f"{name}.py", clean_tools)
            result = subprocess.run(
                [sys.executable, "-c", script, str(clean_tools), *names],
                cwd=directory, text=True, capture_output=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_rotation_request_apply_and_revert_without_image(self) -> None:
        guest = aim_guest()
        result = aim.apply(guest)
        self.assertEqual(aim.status(guest)["state"], "installed")
        self.assertTrue(all(site["build_sha256"] == aim.CODE_SHA256
                            for site in result["sites"]))
        self.assertTrue(aim.revert(guest)["restored"])
        self.assertEqual(guest.read_mem(aim.HOOK_VA, len(aim.assemble_hook())),
                         bytes(len(aim.assemble_hook())))

    def test_shared_session_can_install_and_restore_without_resuming(self) -> None:
        for module, guest in ((aim, aim_guest()), (pin, pin_guest())):
            module.apply(guest, resume=False)
            module.revert(guest, resume=False)
            self.assertEqual(guest.resumes, 0)

    def test_stock_status_requires_original_context_and_empty_caves(self) -> None:
        for module, factory, addresses in (
            (aim, aim_guest, (aim.HOOK_VA, aim.CALL_SITE - 8)),
            (pin, pin_guest, (pin.LATE_SITE, pin.HOOK_VA, pin.LATE_HOOK_VA)),
        ):
            for address in addresses:
                with self.subTest(module=module.__name__, address=hex(address)):
                    guest = factory()
                    self.assertEqual(module.status(guest)["state"], "stock")
                    guest.load(address, b"????")
                    self.assertEqual(module.status(guest)["state"], "unknown")
                    self.assertFalse(module.status(guest)["hook_matches"])
                    self.assertEqual(guest.writes, [])

    def test_installed_status_requires_all_sites_and_caves(self) -> None:
        for module, factory, addresses in (
            (aim, aim_guest, (aim.HOOK_VA, aim.CALL_SITE - 8)),
            (pin, pin_guest, (pin.CALL_SITE, pin.LATE_SITE, pin.HOOK_VA, pin.LATE_HOOK_VA)),
        ):
            for address in addresses:
                with self.subTest(module=module.__name__, address=hex(address)):
                    guest = factory()
                    module.apply(guest, resume=False)
                    guest.load(address, b"????")
                    self.assertEqual(module.status(guest)["state"], "unknown")
                    self.assertFalse(module.status(guest)["hook_matches"])

    def test_cli_real_status_prevents_all_writes_for_a_foreign_later_component(self) -> None:
        import gdbclient
        import kiu_cli as cli
        import menu_render_pass as menu

        for owner, address, raw in (
            ("aim", aim.HOOK_VA, b"????"),
            ("aim", aim.CALL_SITE - 8, b"????"),
            ("pin", pin.LATE_SITE, b"????"),
            ("pin", pin.LATE_HOOK_VA, b"????"),
            ("menu", menu.PRE_SITE + 4, b"????"),
            ("menu", menu.ACTIVE_MARKER, struct.pack("<I", 1)),
        ):
            with self.subTest(owner=owner, address=hex(address)):
                guest = aim_guest()
                guest.load(pin.CALL_SITE, pin.CALL_ORIGINAL)
                guest.load(pin.LATE_SITE, pin.LATE_ORIGINAL)
                guest.load(menu.PRE_SITE, menu.THUNK_ORIGINAL)
                guest.load(menu.POST_SITE, menu.THUNK_ORIGINAL)
                guest.load(address, raw)
                guest.send = lambda _command: "T05"
                guest.sock = SimpleNamespace(close=lambda: None)
                early = SimpleNamespace(
                    status=lambda _g: {"state": "stock", "interval": 2},
                    apply=lambda _g, *_args: guest.write_mem(0xDEAD, b"unexpected early write"),
                    aspect_scale=lambda aspect: aspect,
                    bottom_aspect_scale=lambda aspect: aspect,
                )
                loaded = {"fps": early, "wide": early, "aim": aim, "pin": pin, "menu": menu}
                args = cli.parser().parse_args(["apply", "--mouse-hooks", "--menu-route"])
                with patch.object(cli, "modules", return_value=loaded), patch.object(
                    gdbclient, "GdbClient", return_value=guest
                ):
                    with self.assertRaisesRegex(RuntimeError, f"{owner}: unknown/mixed"):
                        cli.runtime_command(args)
                self.assertEqual(guest.writes, [])

    def test_rotation_request_rejects_foreign_setup_before_write(self) -> None:
        guest = aim_guest()
        guest.load(aim.CALL_SITE - 8, b"????")
        with self.assertRaisesRegex(RuntimeError, "argument setup"):
            aim.apply(guest)
        self.assertEqual(guest.writes, [])

    def test_rotation_request_revert_rejects_foreign_cave(self) -> None:
        guest = aim_guest()
        aim.apply(guest)
        guest.load(aim.HOOK_VA, b"????")
        guest.writes.clear()
        with self.assertRaisesRegex(RuntimeError, "cave"):
            aim.revert(guest)
        self.assertEqual(guest.writes, [])

    def test_reticle_apply_and_revert_without_image(self) -> None:
        guest = pin_guest()
        result = pin.apply(guest)
        self.assertTrue(pin.status(guest)["hook_matches"])
        self.assertEqual(len(result["sites"]), 4)
        self.assertTrue(pin.revert(guest)["restored"])

    def test_reticle_rejects_foreign_late_site_before_write(self) -> None:
        guest = pin_guest()
        guest.load(pin.LATE_SITE, b"????")
        with self.assertRaisesRegex(RuntimeError, "late site"):
            pin.apply(guest)
        self.assertEqual(guest.writes, [])

    def test_reticle_revert_rejects_foreign_late_site(self) -> None:
        guest = pin_guest()
        pin.apply(guest)
        guest.load(pin.LATE_SITE, b"????")
        guest.writes.clear()
        with self.assertRaisesRegex(RuntimeError, "refusing to restore"):
            pin.revert(guest)
        self.assertEqual(guest.writes, [])

    def test_pitch_set_retarget_and_restore_without_image(self) -> None:
        guest = pitch_guest()
        result = pitch.set_limit(guest, 88)
        self.assertEqual(len(result["sites"]), 4)
        self.assertAlmostEqual(pitch.set_limit(guest, 85)["limit_deg"], 85, places=4)
        self.assertTrue(pitch.revert(guest)["stock"])

    def test_pitch_rejects_foreign_loader_before_mutation(self) -> None:
        for operation in (lambda g: pitch.set_limit(g, 88), pitch.revert):
            guest = pitch_guest()
            guest.load(pitch.LOADERS[0], b"????")
            with self.assertRaisesRegex(RuntimeError, "loader"):
                operation(guest)
            self.assertEqual(guest.writes, [])

    def test_pitch_rejects_foreign_stock_bounds_before_mutation(self) -> None:
        guest = pitch_guest()
        guest.load(pitch.UPPER_VA, b"????")
        with self.assertRaisesRegex(RuntimeError, "foreign bound"):
            pitch.set_limit(guest, 88)
        self.assertEqual(guest.writes, [])

    def test_pitch_rejects_nonfinite_limit_before_mutation(self) -> None:
        for degrees in (float("nan"), float("inf")):
            guest = pitch_guest()
            with self.assertRaises(ValueError):
                pitch.set_limit(guest, degrees, force=True)
            self.assertEqual(guest.writes, [])

    def test_declared_bytes_against_optional_prepared_image(self) -> None:
        path = ROOT / "local/extracted/exefs_code.bin"
        if not path.is_file():
            self.skipTest("optional developer integration: prepare the USA-v0 code image")
        code = path.read_bytes()
        self.assertEqual(hashlib.sha256(code).hexdigest(), aim.CODE_SHA256)
        signatures = (
            (aim.CALL_SITE, aim.CALL_ORIGINAL),
            (aim.CALL_SITE - 8, aim.P_SETUP_ORIGINAL),
            (pin.CALL_SITE, pin.CALL_ORIGINAL),
            (pin.LATE_SITE, pin.LATE_ORIGINAL),
            (pitch.UPPER_VA, pitch.STOCK_UPPER),
            (pitch.LOWER_VA, pitch.STOCK_LOWER),
            (pitch.UPPER_MOVE_VA, pitch.STOCK_UPPER_MOVE),
            (pitch.LOWER_MOVE_VA, pitch.STOCK_LOWER_MOVE),
            *tuple(zip(pitch.LOADERS, pitch.LOADER_ORIGINALS)),
        )
        for address, expected in signatures:
            offset = address - aim.CODE_BASE
            self.assertEqual(code[offset:offset + len(expected)], expected, hex(address))


if __name__ == "__main__":
    unittest.main()
