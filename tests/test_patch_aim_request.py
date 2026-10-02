"""Register discipline for the rotation-request cave.

This one is entered from a call site that has already set up six arguments --
r0..r3 plus s0, s1 and s2 -- and the callee reads three more off the stack at
[sp, #0x60], [sp, #0x64] and [sp, #0x68].  So the cave may write exactly s0 and
s1, which is its whole purpose, and must leave everything else including the
stack pointer as the call site left it.  Getting that wrong would not crash; it
would feed the merge a wrong mode or a wrong dt and produce a camera that
almost works.
"""
from __future__ import annotations

import struct
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs  # noqa: E402

import patch_aim_request as aim  # noqa: E402


def _instructions():
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    blob = aim.assemble_hook()
    return [i for i in md.disasm(blob, aim.HOOK_VA)
            if i.address < aim.HOOK_VA + len(blob) - 8]


class RegisterDisciplineTests(unittest.TestCase):
    def test_the_integer_arguments_are_untouched(self) -> None:
        for insn in _instructions():
            written = {insn.reg_name(r) for r in insn.regs_access()[1]}
            self.assertFalse(written & {"r0", "r1", "r2", "r3"},
                             f"{insn.address:08x} {insn.mnemonic} {insn.op_str}")

    def test_only_s0_and_s1_are_written(self) -> None:
        for insn in _instructions():
            written = {insn.reg_name(r) or "" for r in insn.regs_access()[1]}
            floats = {n for n in written if n and n[0] in "sdq" and n[1:].isdigit()}
            self.assertTrue(floats <= {"s0", "s1"},
                            f"{insn.address:08x} {insn.mnemonic} {insn.op_str} "
                            f"writes {floats}; s2 is dt")

    def test_the_stack_is_balanced_before_the_tail_branch(self) -> None:
        mnemonics = [i.mnemonic for i in _instructions()]
        self.assertEqual(mnemonics.count("push"), mnemonics.count("pop"))
        self.assertEqual(mnemonics[0], "push")
        self.assertEqual(mnemonics[-2:], ["pop", "b"],
                         "the callee reads arguments off the stack, so sp must be "
                         "restored before entering it")

    def test_it_tails_rather_than_calls_so_sp_matches_the_call_site(self) -> None:
        blob = aim.assemble_hook()
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        tail = next(i for i in md.disasm(blob, aim.HOOK_VA)
                    if i.address == aim.HOOK_VA + len(blob) - 12)
        self.assertEqual((tail.mnemonic, tail.op_str), ("b", f"#0x{aim.MERGE:x}"))


class LayoutTests(unittest.TestCase):
    def test_the_cave_fits(self) -> None:
        self.assertLessEqual(aim.HOOK_VA + len(aim.assemble_hook()), aim.HOOK_LIMIT)

    def test_the_pool_words_are_the_mailbox_and_magic(self) -> None:
        blob = aim.assemble_hook()          # _check_pool runs inside
        self.assertEqual(struct.unpack_from("<II", blob, len(blob) - 8),
                         (aim.MAILBOX_VA, aim.MAGIC))

    def test_the_call_site_is_the_bl_to_the_merge_in_the_stock_image(self) -> None:
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        insn = next(md.disasm(aim.CALL_ORIGINAL, aim.CALL_SITE))
        self.assertEqual((insn.mnemonic, insn.op_str), ("bl", f"#0x{aim.MERGE:x}"))

    def test_the_call_site_really_does_put_P_in_r1(self) -> None:
        # The cave recovers P as r1 - 0x278; that only holds because of the
        # instruction four words earlier.
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        insn = next(md.disasm(aim.P_SETUP_ORIGINAL, aim.CALL_SITE - 8))
        self.assertEqual((insn.mnemonic, insn.op_str),
                         ("add", f"r1, r4, #0x{aim.P_FROM_R1:x}"))


@unittest.skipUnless(sys.platform == "win32", "Windows process-memory adapter")
class GuestMemoryTests(unittest.TestCase):
    def test_a_short_nonce_is_refused(self) -> None:
        from guest_memory import GuestMemory, nonce_bytes
        self.assertEqual(len(nonce_bytes(0)), 16)
        self.assertNotEqual(nonce_bytes(1), nonce_bytes(2))
        memory = GuestMemory.__new__(GuestMemory)
        memory.handle = None
        with self.assertRaises(ValueError):
            memory.anchor(0, b"tooshort")

    def test_reading_before_anchoring_is_an_error_not_a_wrong_address(self) -> None:
        from guest_memory import GuestMemory
        memory = GuestMemory.__new__(GuestMemory)
        memory.handle = None
        memory._offset = None
        with self.assertRaises(RuntimeError):
            memory.host_address(0x00F36100)


if __name__ == "__main__":
    unittest.main()
