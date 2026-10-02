"""Invariants of the reticle cave that cannot be checked by running it.

The clamp is called with five live VFP arguments -- s0..s3 are the bounds and
s4 the vertical carry -- and with the reticle object and camera controller in
r0 and r1.  A cave that touched any of those would not crash; it would corrupt
the bounds and produce a plausible wrong answer, which is the failure mode this
project has the worst record against.  So the register discipline is asserted
rather than reasoned about.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs  # noqa: E402
from capstone.arm import ARM_OP_REG  # noqa: E402

import pin_reticle as pin  # noqa: E402


def _instructions():
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    blob = pin.assemble_hook()
    # The last three words are the tail branch and the two pool words.
    return [i for i in md.disasm(blob, pin.HOOK_VA)
            if i.address < pin.HOOK_VA + len(blob) - 8]


class RegisterDisciplineTests(unittest.TestCase):
    def test_nothing_writes_the_argument_registers(self) -> None:
        forbidden = {"r0", "r1", "r2", "r3"}
        for insn in _instructions():
            written = {insn.reg_name(r) for r in insn.regs_access()[1]}
            self.assertFalse(
                written & forbidden,
                f"{insn.address:08x} {insn.mnemonic} {insn.op_str} writes "
                f"{written & forbidden}, which the clamp reads as arguments")

    def test_no_vfp_register_is_touched_at_all(self) -> None:
        for insn in _instructions():
            names = {insn.reg_name(r) or "" for r in
                     insn.regs_access()[0] + insn.regs_access()[1]}
            floats = {n for n in names if n and n[0] in "sdq" and n[1:].isdigit()}
            self.assertFalse(
                floats,
                f"{insn.address:08x} {insn.mnemonic} {insn.op_str} touches {floats}; "
                "s0-s4 are the clamp's bounds and carry")

    def test_the_position_moves_as_a_word_not_a_float(self) -> None:
        # Copying it integer-side is what keeps the VFP file untouched.
        moves = [i for i in _instructions()
                 if i.mnemonic == "str" and i.op_str.endswith(f"[r0, #0x{pin.A_POS_X:x}]")]
        self.assertTrue(moves, "nothing writes the reticle x position")
        self.assertTrue(all(i.operands[0].type == ARM_OP_REG for i in moves))


class LayoutTests(unittest.TestCase):
    def test_the_cave_fits_and_tails_into_the_clamp(self) -> None:
        blob = pin.assemble_hook()          # _check_pool runs inside
        self.assertLessEqual(pin.HOOK_VA + len(blob), pin.HOOK_LIMIT)
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        tail = next(i for i in md.disasm(blob, pin.HOOK_VA)
                    if i.address == pin.HOOK_VA + len(blob) - 12)
        self.assertEqual((tail.mnemonic, tail.op_str), ("b", f"#0x{pin.CLAMP:x}"))

    def test_the_pool_words_are_reached_by_the_literal_loads(self) -> None:
        # Four separate times a hand-written [pc, #N] landed inside the code.
        blob = pin.assemble_hook()
        pool = struct.unpack_from("<II", blob, len(blob) - 8)
        self.assertEqual(pool, (pin.MAILBOX_VA, pin.MAGIC))

    def test_the_call_site_is_the_bl_to_the_clamp_in_the_stock_image(self) -> None:
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        insn = next(md.disasm(pin.CALL_ORIGINAL, pin.CALL_SITE))
        self.assertEqual(insn.mnemonic, "bl")
        self.assertEqual(insn.op_str, f"#0x{pin.CLAMP:x}")

    def test_the_branch_encoder_round_trips(self) -> None:
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        word = pin.encode_branch(pin.CALL_SITE, pin.HOOK_VA)
        insn = next(md.disasm(word, pin.CALL_SITE))
        self.assertEqual((insn.mnemonic, insn.op_str), ("bl", f"#0x{pin.HOOK_VA:x}"))


if __name__ == "__main__":
    unittest.main()
