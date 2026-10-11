"""Two-site idle-camera patch regressions; no game image is required."""
from contextlib import contextmanager
import hashlib
from pathlib import Path
import sys
import unittest
from unittest import mock

from capstone import Cs, CS_ARCH_ARM, CS_MODE_ARM

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kiu.experimental import patch_no_recenter as recenter


class Guest:
    def __init__(self):
        self.memory = {}
        self.writes = []
        self.resumes = 0
        for address, raw, _purpose in recenter.GUARDS:
            self.load(address, raw)
        for site in recenter.PATCH_SITES:
            self.load(site.address, site.expected)

    def load(self, address, raw):
        self.memory.update((address + offset, byte) for offset, byte in enumerate(raw))

    def read_mem(self, address, size):
        return bytes(self.memory.get(address + offset, 0) for offset in range(size))

    def write_mem(self, address, raw):
        self.writes.append((address, raw))
        self.load(address, raw)

    def cont_all(self):
        self.resumes += 1


class NoRecenterTests(unittest.TestCase):
    def test_only_two_idle_setter_calls_become_nops(self):
        decoder = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        self.assertEqual([site.address for site in recenter.PATCH_SITES],
                         [0x007CADEC, 0x007CAFFC])
        for site in recenter.PATCH_SITES:
            original, = decoder.disasm(site.expected, site.address)
            patched, = decoder.disasm(site.replacement, site.address)
            self.assertEqual((original.mnemonic, original.op_str), ("bl", "#0x6063e8"))
            self.assertEqual(patched.mnemonic, "nop")
        # The separate getter fingerprint prevents confusing idle mode 1
        # with a forced/active camera module at a similar-looking address.
        self.assertIn((0x00B0D3A4, bytes.fromhex("0100a0e31eff2fe1"), "idle mode 1 getter"),
                      recenter.GUARDS)

    def test_round_trip_preserves_all_other_state_and_never_resumes(self):
        guest = Guest()
        # Sentinel bytes represent timers, reticle visibility, input and forced
        # reset sites; this patch must never mutate any of them.
        for address in (0x006D76C8, 0x007033A0, 0x00704004, 0x00704160):
            guest.load(address, b"keep")
        before = dict(guest.memory)
        self.assertEqual(recenter.status(guest)["state"], "stock")
        installed = recenter.apply(guest)
        self.assertTrue(installed["patched"])
        self.assertEqual(installed["state"], "installed")
        self.assertEqual(guest.writes, [(site.address, site.replacement)
                                       for site in recenter.PATCH_SITES])
        for record in installed["sites"]:
            self.assertEqual(record["build_sha256"], recenter.CODE_SHA256)
            self.assertEqual(record["replacement_hex"], recenter.NOP.hex())
        self.assertTrue(recenter.revert(guest)["restored"])
        self.assertEqual(guest.memory, before)
        self.assertEqual(guest.resumes, 0)

    def test_each_guard_mismatch_refuses_before_any_write(self):
        for address, raw, _purpose in recenter.GUARDS:
            for installed in (False, True):
                with self.subTest(address=hex(address), installed=installed):
                    guest = Guest()
                    if installed:
                        recenter.apply(guest)
                        guest.writes.clear()
                    guest.load(address, bytes(byte ^ 0xFF for byte in raw))
                    state = recenter.status(guest)
                    self.assertEqual(state["state"], "unknown")
                    self.assertTrue(state["guard_mismatches"])
                    with self.assertRaisesRegex(RuntimeError, "unknown/mixed"):
                        (recenter.revert if installed else recenter.apply)(guest)
                    self.assertEqual(guest.writes, [])

    def test_mixed_and_foreign_sites_are_never_adopted(self):
        for site in recenter.PATCH_SITES:
            for value in (site.replacement, b"????", b"\x00" * 4):
                with self.subTest(address=hex(site.address), value=value):
                    guest = Guest()
                    guest.load(site.address, value)
                    self.assertEqual(recenter.status(guest)["state"], "unknown")
                    for operation in (recenter.apply, recenter.revert):
                        with self.assertRaises(RuntimeError):
                            operation(guest)
                    self.assertEqual(guest.writes, [])

    def test_unexpected_complete_state_does_not_write(self):
        guest = Guest()
        with self.assertRaisesRegex(RuntimeError, "expected installed"):
            recenter.revert(guest)
        self.assertEqual(guest.writes, [])
        recenter.apply(guest)
        guest.writes.clear()
        with self.assertRaisesRegex(RuntimeError, "expected stock"):
            recenter.apply(guest)
        self.assertEqual(guest.writes, [])

    def test_short_read_is_unknown_not_stock(self):
        guest = Guest()
        with mock.patch.object(guest, "read_mem", return_value=b""):
            self.assertEqual(recenter.status(guest)["state"], "unknown")
            with self.assertRaises(RuntimeError):
                recenter.apply(guest)
        self.assertEqual(guest.writes, [])

    def test_second_write_failure_rolls_back_apply_and_revert(self):
        for restore in (False, True):
            with self.subTest(restore=restore):
                guest = Guest()
                if restore:
                    recenter.apply(guest)
                before = dict(guest.memory)
                write = guest.write_mem
                calls = 0

                def fail_second(address, raw):
                    nonlocal calls
                    calls += 1
                    # Also simulate a debugger that performed the write but
                    # lost its reply: rollback must include the attempted site.
                    write(address, raw)
                    if calls == 2:
                        raise OSError("lost reply")

                with mock.patch.object(guest, "write_mem", side_effect=fail_second):
                    with self.assertRaisesRegex(OSError, "lost reply"):
                        (recenter.revert if restore else recenter.apply)(guest)
                self.assertEqual(guest.memory, before)
                self.assertEqual(guest.resumes, 0)

    def test_dropped_write_is_detected_and_previous_site_restored(self):
        guest = Guest()
        before = dict(guest.memory)
        write = guest.write_mem

        def drop_second(address, raw):
            if address != recenter.PATCH_SITES[1].address:
                write(address, raw)

        with mock.patch.object(guest, "write_mem", side_effect=drop_second):
            with self.assertRaisesRegex(RuntimeError, "write verification"):
                recenter.apply(guest)
        self.assertEqual(guest.memory, before)

    def test_foreign_bytes_during_failure_are_not_overwritten_by_rollback(self):
        guest = Guest()
        write = guest.write_mem
        second = recenter.PATCH_SITES[1]

        def foreign_second(address, raw):
            if address == second.address:
                guest.load(address, b"????")
                raise OSError("foreign write")
            write(address, raw)

        with mock.patch.object(guest, "write_mem", side_effect=foreign_second):
            with self.assertRaises(OSError) as failure:
                recenter.apply(guest)
        self.assertEqual(guest.read_mem(second.address, 4), b"????")
        first = recenter.PATCH_SITES[0]
        self.assertEqual(guest.read_mem(first.address, 4), first.expected)
        self.assertTrue(any("rollback refused" in note for note in failure.exception.__notes__))
        self.assertFalse(any(address == second.address for address, _raw in guest.writes))

    def test_cli_uses_one_stopped_guest_session_for_entire_transaction(self):
        guest = Guest()
        boundaries = []

        @contextmanager
        def session(port):
            boundaries.append(("stop", port))
            yield guest
            boundaries.append(("resume", recenter.status(guest)["state"]))

        with mock.patch.object(recenter, "guest_session", side_effect=session), \
                mock.patch("builtins.print"):
            self.assertEqual(recenter.main(["apply", "--port", "25000"]), 0)
        self.assertEqual(boundaries, [("stop", 25000), ("resume", "installed")])

    def test_optional_verified_private_image_matches_all_fingerprints(self):
        path = ROOT / "local" / "extracted" / "exefs_code.bin"
        if not path.is_file():
            self.skipTest("optional user-owned image is absent")
        image = path.read_bytes()
        self.assertEqual(hashlib.sha256(image).hexdigest(), recenter.CODE_SHA256)
        checks = [(site.address, site.expected, site.purpose) for site in recenter.PATCH_SITES]
        for address, expected, purpose in (*checks, *recenter.GUARDS):
            with self.subTest(address=hex(address), purpose=purpose):
                offset = address - 0x00100000
                self.assertEqual(image[offset:offset + len(expected)], expected)


if __name__ == "__main__":
    unittest.main()
