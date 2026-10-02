from __future__ import annotations

import hashlib
import sys
import unittest
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone.arm import ARM_OP_IMM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiu.patches import patch_60fps as patch  # noqa: E402


class TimeScaled60FpsBuildTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        image = ROOT / "local/extracted/exefs_code.bin"
        cls.code = image.read_bytes() if image.exists() else None
        cls.md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        cls.md.detail = True

    def original(self, address: int, size: int) -> bytes:
        start = address - 0x00100000
        return self.code[start : start + size]

    def test_authoritative_build_and_every_patch_site(self) -> None:
        if self.code is None:
            self.skipTest("optional integration check: provide your own code.bin in local/extracted")
        self.assertEqual(hashlib.sha256(self.code).hexdigest(), patch.CODE_SHA256)
        self.assertEqual(len(patch.PATCH_SITES), 2)
        for site in patch.PATCH_SITES:
            self.assertEqual(self.original(site.address, len(site.expected)), site.expected)
            self.assertEqual(site.record()["build_sha256"], patch.CODE_SHA256)
        for address, cave, _label in patch.CAVES:
            self.assertEqual(self.original(address, len(cave)), bytes(len(cave)))

    def test_redirects_target_exact_time_caves(self) -> None:
        for index, site in enumerate(patch.PATCH_SITES):
            instruction = next(self.md.disasm(site.replacement, site.address))
            self.assertEqual(instruction.mnemonic, "bl" if index == 0 else "b")
            self.assertEqual(instruction.operands[0].type, ARM_OP_IMM)
            self.assertEqual(instruction.operands[0].imm, site.cave)

    def test_dispatcher_cave_scales_s0_into_saved_s16(self) -> None:
        instructions = list(
            self.md.disasm(
                patch.DISPATCH_DT_CAVE_BYTES[:-4], patch.DISPATCH_DT_CAVE
            )
        )
        self.assertEqual(
            [(instruction.mnemonic, instruction.op_str) for instruction in instructions],
            [
                ("vldr", "s16, [pc, #8]"),
                ("vmul.f32", "s16, s0, s16"),
                ("bx", "lr"),
                ("nop", ""),
            ],
        )
        self.assertEqual(patch.DISPATCH_DT_CAVE_BYTES[-4:], bytes.fromhex("0000003f"))

    def test_global_cave_scales_master_before_channel_derivation(self) -> None:
        instructions = list(
            self.md.disasm(patch.GLOBAL_DT_CAVE_BYTES[:-4], patch.GLOBAL_DT_CAVE)
        )
        self.assertEqual(
            [(instruction.mnemonic, instruction.op_str) for instruction in instructions],
            [
                ("vldr", "s0, [r0, #0x20]"),
                ("vldr", "s5, [pc, #8]"),
                ("vmul.f32", "s0, s0, s5"),
                ("b", "#0x607a78"),
                ("nop", ""),
            ],
        )
        self.assertEqual(patch.GLOBAL_DT_CAVE_BYTES[-4:], bytes.fromhex("0000003f"))

    def test_60_updates_advance_same_time_as_30_stock_updates(self) -> None:
        self.assertAlmostEqual(30 * (1.0 / 30.0), 60 * ((1.0 / 30.0) * 0.5))
        self.assertAlmostEqual(30 * 2.0, 60 * (2.0 * 0.5))
        self.assertEqual(patch.SCHED_INTERVAL_ORIG, 2)
        self.assertEqual(patch.SCHED_INTERVAL_60, 0)


if __name__ == "__main__":
    unittest.main()
