from __future__ import annotations

import hashlib
import struct
import sys
import unittest
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone.arm import ARM_OP_IMM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiu.patches import patch_widescreen as patch  # noqa: E402


class FakeGdb:
    def __init__(self) -> None:
        self.memory: dict[int, int] = {}
        for site in patch.PROJECTION_UPLOAD_SITES:
            self.load(site, patch.PROJECTION_UPLOAD_ORIGINAL)
        for cave in patch.PROJECTION_CAVES:
            self.load(cave, bytes(patch.PROJECTION_CAVE_SIZE))
        self.load(
            patch.BOTTOM_EXTENSION_CAVE,
            bytes(patch.BOTTOM_EXTENSION_CAVE_SIZE),
        )

    def load(self, address: int, data: bytes) -> None:
        self.memory.update((address + offset, value) for offset, value in enumerate(data))

    def read_mem(self, address: int, size: int) -> bytes:
        return bytes(self.memory.get(address + offset, 0) for offset in range(size))

    def write_mem(self, address: int, data: bytes) -> None:
        self.load(address, data)


class WidescreenProjectionPatchTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        image = ROOT / "local/extracted/exefs_code.bin"
        cls.code = image.read_bytes() if image.exists() else None
        cls.md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        cls.md.detail = True

    def original(self, address: int, size: int) -> bytes:
        start = address - 0x00100000
        return self.code[start : start + size]

    def test_authoritative_build_upload_tail_and_cave(self) -> None:
        if self.code is None:
            self.skipTest("optional integration check: provide your own code.bin in local/extracted")
        self.assertEqual(hashlib.sha256(self.code).hexdigest(), patch.CODE_SHA256)
        for site in patch.PROJECTION_UPLOAD_SITES:
            self.assertEqual(self.original(site, 4), patch.PROJECTION_UPLOAD_ORIGINAL)
        for cave in patch.PROJECTION_CAVES:
            self.assertEqual(
                self.original(cave, patch.PROJECTION_CAVE_SIZE),
                bytes(patch.PROJECTION_CAVE_SIZE),
            )
        self.assertEqual(
            self.original(
                patch.BOTTOM_EXTENSION_CAVE,
                patch.BOTTOM_EXTENSION_CAVE_SIZE,
            ),
            bytes(patch.BOTTOM_EXTENSION_CAVE_SIZE),
        )

    def test_redirect_and_cave_target_only_perspective_projection(self) -> None:
        site = patch.PROJECTION_UPLOAD_SITES[0]
        cave_address = patch.PROJECTION_CAVES[0]
        redirect = patch.encode_branch(site, cave_address)
        instruction = next(self.md.disasm(redirect, site))
        self.assertEqual(instruction.mnemonic, "b")
        self.assertEqual(instruction.operands[0].type, ARM_OP_IMM)
        self.assertEqual(instruction.operands[0].imm, cave_address)

        cave = patch.build_cave(cave_address, site + 4, 21 / 9)
        instructions = list(
            self.md.disasm(cave[: patch.PERSPECTIVE_MARKER_OFFSET], cave_address)
        )
        self.assertEqual(
            [(instruction.mnemonic, instruction.op_str) for instruction in instructions],
            [
                ("ldr", "r0, [r5]"),
                ("ldr", "r2, [r0, #0x40]"),
                ("ldr", "r3, [pc, #0x30]"),
                ("cmp", "r2, r3"),
                ("bne", "#0xc9587c"),
                ("vldr", "s0, [r0, #0x28]"),
                ("vldr", "s2, [r0, #0x14]"),
                ("vldr", "s4, [pc, #0x20]"),
                ("vmul.f32", "s4, s2, s4"),
                ("vcmp.f32", "s0, s4"),
                ("vmrs", "apsr_nzcv, fpscr"),
                ("ble", "#0xc9588c"),
                ("vldr", "s2, [pc, #0x10]"),
                ("vmul.f32", "s0, s0, s2"),
                ("vstr", "s0, [r0, #0x28]"),
                ("b", "#0x8dec84"),
            ],
        )
        self.assertEqual(
            struct.unpack(
                "<f", cave[patch.PERSPECTIVE_MARKER_OFFSET:patch.TOP_RATIO_CUTOFF_OFFSET]
            )[0],
            -1.0,
        )
        self.assertAlmostEqual(
            struct.unpack(
                "<f", cave[patch.TOP_RATIO_CUTOFF_OFFSET:patch.SCALE_LITERAL_OFFSET]
            )[0],
            patch.TOP_RATIO_CUTOFF,
        )
        self.assertGreater(-3 / 5, patch.TOP_RATIO_CUTOFF)
        self.assertLessEqual(-3 / 4, patch.TOP_RATIO_CUTOFF)
        self.assertAlmostEqual(
            struct.unpack(
                "<f",
                cave[patch.SCALE_LITERAL_OFFSET:patch.SCALE_LITERAL_OFFSET + 4],
            )[0],
            5 / 7,
            places=7,
        )
        tail = list(
            self.md.disasm(cave[76:92], cave_address + 76)
        )
        self.assertEqual(
            [(instruction.mnemonic, instruction.op_str) for instruction in tail],
            [
                ("stmdb", "sp!, {lr}"),
                ("bl", "#0xc95800"),
                ("ldm", "sp!, {lr}"),
                ("b", "#0x8dec84"),
            ],
        )

        extension = patch.build_bottom_extension(21 / 9)
        extension_instructions = list(
            self.md.disasm(extension[:32], patch.BOTTOM_EXTENSION_CAVE)
        )
        self.assertEqual(
            [(instruction.mnemonic, instruction.op_str) for instruction in extension_instructions],
            [
                ("ldr", "r2, [pc, #0x18]"),
                ("ldr", "r2, [r2]"),
                ("cmp", "r2, #1"),
                ("bne", "#0xc9581c"),
                ("vldr", "s2, [pc, #0xc]"),
                ("vmul.f32", "s0, s0, s2"),
                ("vstr", "s0, [r0, #0x28]"),
                ("bx", "lr"),
            ],
        )
        self.assertEqual(
            struct.unpack("<I", extension[32:36])[0], patch.MENU_RENDER_MARKER
        )
        self.assertAlmostEqual(
            struct.unpack(
                "<f",
                extension[
                    patch.BOTTOM_SCALE_LITERAL_OFFSET:patch.BOTTOM_SCALE_LITERAL_OFFSET + 4
                ],
            )[0],
            4 / 7,
            places=7,
        )

    def test_apply_change_aspect_status_and_exact_revert(self) -> None:
        gdb = FakeGdb()
        result = patch.apply(gdb, 21 / 9)
        self.assertTrue(result["patched"])
        self.assertEqual(len(result["patch_sites"]), 2)
        self.assertTrue(
            all(site["build_sha256"] == patch.CODE_SHA256 for site in result["patch_sites"])
        )
        self.assertAlmostEqual(result["projection_x_scale"], 5 / 7, places=7)
        self.assertAlmostEqual(result["menu_bottom_projection_x_scale"], 4 / 7, places=7)
        self.assertAlmostEqual(patch.status(gdb)["aspect"], 21 / 9, places=6)

        patch.set_aspect(gdb, 16 / 9)
        self.assertAlmostEqual(patch.status(gdb)["aspect"], 16 / 9, places=6)
        self.assertAlmostEqual(
            patch.status(gdb)["menu_bottom_projection_x_scale"], 3 / 4, places=6
        )
        result = patch.revert(gdb)
        self.assertTrue(result["restored"])
        self.assertEqual(patch.status(gdb)["state"], "stock")
        self.assertEqual(
            [gdb.read_mem(cave, patch.PROJECTION_CAVE_SIZE) for cave in patch.PROJECTION_CAVES],
            [bytes(patch.PROJECTION_CAVE_SIZE)] * 2,
        )
        self.assertEqual(
            gdb.read_mem(
                patch.BOTTOM_EXTENSION_CAVE,
                patch.BOTTOM_EXTENSION_CAVE_SIZE,
            ),
            bytes(patch.BOTTOM_EXTENSION_CAVE_SIZE),
        )

    def test_rejects_invalid_aspects_and_occupied_cave(self) -> None:
        for aspect in (0.0, -1.0, float("inf"), float("nan")):
            with self.subTest(aspect=aspect):
                with self.assertRaises(ValueError):
                    patch.build_cave(
                        patch.PROJECTION_CAVES[0], patch.PROJECTION_UPLOAD_SITES[0] + 4, aspect
                    )
        gdb = FakeGdb()
        gdb.write_mem(patch.PROJECTION_CAVES[0] + 4, b"\x01")
        with self.assertRaisesRegex(RuntimeError, "mismatch"):
            patch.apply(gdb, 21 / 9)


if __name__ == "__main__":
    unittest.main()
