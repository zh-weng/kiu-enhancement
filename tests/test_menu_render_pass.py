from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone.arm import ARM_OP_IMM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiu.experimental import menu_render_pass as patch  # noqa: E402


class FakeGdb:
    def __init__(self) -> None:
        self.memory: dict[int, int] = {}
        for site in (patch.PRE_SITE, patch.POST_SITE):
            self.load(site, patch.THUNK_ORIGINAL)
        for cave in (patch.PRE_CAVE, patch.POST_CAVE):
            self.load(cave, bytes(patch.CAVE_SIZE))
        self.load(patch.ACTIVE_MARKER, b"\0\0\0\0")

    def load(self, address: int, data: bytes) -> None:
        self.memory.update((address + offset, value) for offset, value in enumerate(data))

    def read_mem(self, address: int, size: int) -> bytes:
        return bytes(self.memory.get(address + offset, 0) for offset in range(size))

    def write_mem(self, address: int, data: bytes) -> None:
        self.load(address, data)


class MenuRenderPassTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        cls.md.detail = True

    def test_authoritative_build_sites_and_caves(self) -> None:
        if not patch.CODE_PATH.is_file():
            self.skipTest("optional developer integration: prepare the USA-v0 code image")
        code = patch._load_image()
        self.assertEqual(hashlib.sha256(code).hexdigest(), patch.CODE_SHA256)

    def test_redirects_and_cave_boundaries(self) -> None:
        for site, cave, replacement in (
            (patch.PRE_SITE, patch.PRE_CAVE, patch.PRE_REPLACEMENT),
            (patch.POST_SITE, patch.POST_CAVE, patch.POST_REPLACEMENT),
        ):
            instruction = next(self.md.disasm(replacement, site))
            self.assertEqual(instruction.mnemonic, "b")
            self.assertEqual(instruction.operands[0].type, ARM_OP_IMM)
            self.assertEqual(instruction.operands[0].imm, cave)
        self.assertEqual(len(patch.PRE_CAVE_BYTES), patch.CAVE_SIZE)
        self.assertEqual(len(patch.POST_CAVE_BYTES), patch.CAVE_SIZE)
        self.assertLessEqual(patch.POST_CAVE + patch.CAVE_SIZE, 0x00C957C0)

    def test_pre_sets_400_and_post_restores_320(self) -> None:
        pre = [
            (instruction.mnemonic, instruction.op_str)
            for instruction in self.md.disasm(patch.PRE_CAVE_BYTES, patch.PRE_CAVE)
        ]
        post = [
            (instruction.mnemonic, instruction.op_str)
            for instruction in self.md.disasm(patch.POST_CAVE_BYTES, patch.POST_CAVE)
        ]
        self.assertIn(("movw", "r3, #0x190"), pre)
        self.assertIn(("movw", "r3, #0x140"), post)
        self.assertIn(("moveq", "r2, #0"), pre)
        self.assertIn(("moveq", "r2, #0"), post)
        self.assertIn(("movw", "r2, #0x6820"), pre)
        self.assertIn(("movt", "r2, #0xf3"), pre)
        self.assertIn(("b", "#0x9167cc"), pre)
        self.assertIn(("b", "#0x916898"), post)

    def test_apply_status_and_exact_revert(self) -> None:
        gdb = FakeGdb()
        result = patch.apply(gdb)
        self.assertTrue(result["installed"])
        self.assertEqual(patch.status(gdb)["state"], "installed")
        self.assertTrue(all(site["build_sha256"] == patch.CODE_SHA256 for site in result["sites"]))
        result = patch.revert(gdb)
        self.assertTrue(result["restored"])
        self.assertEqual(patch.status(gdb)["state"], "stock")

    def test_stock_status_rejects_foreign_thunk_tail_or_active_marker(self) -> None:
        for address, raw in ((patch.PRE_SITE + 4, b"????"),
                             (patch.POST_SITE + 8, b"????"),
                             (patch.ACTIVE_MARKER, b"\1\0\0\0")):
            with self.subTest(address=hex(address)):
                gdb = FakeGdb()
                gdb.load(address, raw)
                before = dict(gdb.memory)
                self.assertEqual(patch.status(gdb)["state"], "mixed")
                with self.assertRaises(RuntimeError):
                    patch.apply(gdb)
                self.assertEqual(gdb.memory, before)

    def test_installed_status_and_revert_reject_foreign_thunk_tail(self) -> None:
        gdb = FakeGdb()
        patch.apply(gdb)
        gdb.load(patch.POST_SITE + 8, b"????")
        before = dict(gdb.memory)
        self.assertEqual(patch.status(gdb)["state"], "mixed")
        with self.assertRaisesRegex(RuntimeError, "thunk"):
            patch.revert(gdb)
        self.assertEqual(gdb.memory, before)


if __name__ == "__main__":
    unittest.main()
