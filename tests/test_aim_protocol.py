"""Platform-neutral interfaces and fail-closed mailbox header validation."""
from __future__ import annotations

import json
import struct
import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import aim_protocol as abi  # noqa: E402


class FakeMemory:
    def __init__(self):
        self.headers = {
            mailbox.address: struct.pack("<II", mailbox.magic, mailbox.version)
            for mailbox in abi.MAILBOXES
        }
        self.reads = []
        self.writes = []
        self.closed = False

    def read(self, address, size):
        self.reads.append((address, size))
        return self.headers[address]

    def write(self, address, data):
        self.writes.append((address, data))

    def close(self):
        self.closed = True


class FakeInput:
    def start(self, timeout=5.0):
        return self

    def stop(self):
        pass


class FakeWindow:
    def focused(self):
        return True

    def cursor_position(self):
        return (-800, 20)

    def top_viewport(self):
        return None


class PlatformProtocolTests(unittest.TestCase):
    def test_import_needs_no_windows_backend_or_assembler(self):
        script = (
            "import sys; sys.path.insert(0, sys.argv[1]); import aim_protocol; "
            "blocked = {'ctypes', 'win_raw_mouse', 'guest_memory', 'native_fps_aim', "
            "'capstone', 'keystone'}; "
            "assert not blocked.intersection(sys.modules), blocked.intersection(sys.modules)"
        )
        result = subprocess.run(
            [sys.executable, "-c", script, str(ROOT / "tools")],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_interfaces_accept_structural_platform_adapters(self):
        self.assertIsInstance(FakeMemory(), abi.MemoryTransport)
        self.assertIsInstance(FakeInput(), abi.InputSource)
        self.assertIsInstance(FakeWindow(), abi.WindowContext)
        self.assertNotIsInstance(object(), abi.MemoryTransport)
        self.assertNotIsInstance(object(), abi.InputSource)
        self.assertNotIsInstance(object(), abi.WindowContext)
        window = FakeWindow()
        self.assertLess(window.cursor_position()[0], 0)
        self.assertIsNone(window.top_viewport())


class MailboxValidationTests(unittest.TestCase):
    def test_valid_headers_are_read_without_mutation(self):
        memory = FakeMemory()
        self.assertIsNone(abi.validate_mailboxes(memory))
        self.assertEqual(memory.reads, [(mailbox.address, 8) for mailbox in abi.MAILBOXES])
        self.assertEqual(memory.writes, [])
        self.assertFalse(memory.closed)

    def test_wrong_magic_is_rejected_in_either_mailbox(self):
        for mailbox in abi.MAILBOXES:
            with self.subTest(mailbox=mailbox.name):
                memory = FakeMemory()
                memory.headers[mailbox.address] = struct.pack("<II", 0, mailbox.version)
                with self.assertRaisesRegex(RuntimeError, f"{mailbox.name}: mailbox ABI mismatch"):
                    abi.validate_mailboxes(memory)
                self.assertEqual(memory.writes, [])

    def test_unsupported_version_is_not_silently_reinterpreted(self):
        for mailbox in abi.MAILBOXES:
            with self.subTest(mailbox=mailbox.name):
                memory = FakeMemory()
                memory.headers[mailbox.address] = struct.pack("<II", mailbox.magic, mailbox.version + 1)
                with self.assertRaisesRegex(RuntimeError, "ABI mismatch"):
                    abi.validate_mailboxes(memory)

    def test_truncated_or_oversized_header_is_rejected(self):
        mailbox = abi.GROUND
        for size in (*range(8), 9):
            with self.subTest(size=size):
                memory = FakeMemory()
                memory.headers[mailbox.address] = memory.headers[mailbox.address][:size]
                if size > 8:
                    memory.headers[mailbox.address] += bytes(size - 8)
                with self.assertRaisesRegex(RuntimeError, "ABI mismatch"):
                    abi.validate_mailboxes(memory)

    def test_stale_transport_failure_propagates_without_reinitializing(self):
        class StaleMemory(FakeMemory):
            def read(self, address, size):
                raise OSError("stale session")

        memory = StaleMemory()
        with self.assertRaisesRegex(OSError, "stale session"):
            abi.validate_mailboxes(memory)
        self.assertEqual(memory.writes, [])


class ManifestCompatibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import patch_aim_request as ground
        import pin_reticle as reticle

        cls.modules = {"ground": ground, "reticle": reticle}
        cls.manifest = json.loads((ROOT / "manifests/aim_protocol.json").read_text(encoding="utf-8"))

    def test_manifest_headers_match_protocol_and_guest_modules(self):
        self.assertEqual(self.manifest["schema_version"], 1)
        self.assertEqual(self.manifest["abi_version"], abi.ABI_VERSION)
        self.assertEqual(self.manifest["endianness"], "little")
        self.assertEqual(self.manifest["integer_type"], "u32")
        self.assertEqual(self.manifest["float_type"], "IEEE-754 binary32")
        self.assertEqual(set(self.manifest["mailboxes"]), {mailbox.name for mailbox in abi.MAILBOXES})
        for mailbox in abi.MAILBOXES:
            with self.subTest(mailbox=mailbox.name):
                record = self.manifest["mailboxes"][mailbox.name]
                module = self.modules[mailbox.name]
                self.assertEqual(self.manifest["code_sha256"], module.CODE_SHA256)
                self.assertEqual(int(record["address"], 16), mailbox.address)
                self.assertEqual(mailbox.address, module.MAILBOX_VA)
                self.assertEqual(record["size"], mailbox.size)
                self.assertEqual(mailbox.size, module.MAILBOX_SIZE)
                self.assertEqual(int(record["magic"], 16), mailbox.magic)
                self.assertEqual(mailbox.magic, module.MAGIC)
                self.assertEqual(record["version"], mailbox.version)
                self.assertEqual(mailbox.version, module.VERSION)
                image = module.mailbox_image()
                self.assertEqual(len(image), record["size"])
                self.assertEqual(image[:8], struct.pack("<II", mailbox.magic, mailbox.version))
                self.assertEqual(image[8:], bytes(record["size"] - 8))

    def test_every_manifest_field_matches_a_guest_module_offset(self):
        names = {
            "ground": {
                "magic": "MB_MAGIC", "version": "MB_VERSION", "gate": "MB_GATE",
                "yaw_radians": "MB_YAW", "pitch_radians": "MB_PITCH", "frames": "MB_FRAMES",
                "applied": "MB_APPLIED", "controller_guest_va": "MB_P",
            },
            "reticle": {
                "magic": "MB_MAGIC", "version": "MB_VERSION", "mode": "MB_MODE",
                "x_pixels": "MB_POS_X", "y_pixels": "MB_POS_Y", "reticle_guest_va": "MB_A",
                "controller_guest_va": "MB_P", "width_pixels": "MB_W", "height_pixels": "MB_H",
                "calls": "MB_CALLS", "applied": "MB_APPLIED", "centre_x": "MB_CX",
                "centre_y": "MB_CY", "actual_x": "MB_ACT_X", "actual_y": "MB_ACT_Y",
                "late_writes": "MB_LATE",
            },
        }
        for mailbox in abi.MAILBOXES:
            with self.subTest(mailbox=mailbox.name):
                module = self.modules[mailbox.name]
                mapping = names[mailbox.name]
                self.assertEqual(set(mapping.values()), {
                    name for name in vars(module) if name.startswith("MB_")
                })
                fields = self.manifest["mailboxes"][mailbox.name]["fields"]
                self.assertEqual(fields, {name: getattr(module, attr) for name, attr in mapping.items()})
                self.assertEqual(sorted(fields.values()), list(range(0, mailbox.size, 4)))


if __name__ == "__main__":
    unittest.main()
