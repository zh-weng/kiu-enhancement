"""Distribution and resource cleanup tests; no emulator or input is touched."""
from __future__ import annotations

import json
import struct
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

if sys.platform == "win32":
    from kiu.backends.windows import guest_memory  # noqa: E402
    from kiu.backends.windows import native_fps_aim as driver  # noqa: E402


class FakeMemory:
    def __init__(self) -> None:
        self.pid = 123
        self._offset = None
        self.closed = False
        self.scan_count = 0
        self.verify_error = None
        self.scan_error = None

    def verify(self, _address, _nonce) -> bool:
        if self.verify_error:
            raise self.verify_error
        return False

    def anchor(self, _address, _nonce) -> int:
        self.scan_count += 1
        if self.scan_error:
            raise self.scan_error
        self._offset = 0x100000000
        return self._offset + _address

    def close(self) -> None:
        self.closed = True


def valid_gdb_headers(gdb) -> None:
    def read(address, size):
        for module in (driver.aim, driver.pin):
            if address == module.MAILBOX_VA and size == 8:
                return struct.pack("<II", module.MAGIC, module.VERSION)
        return bytes(size)
    gdb.read_mem.side_effect = read


@unittest.skipUnless(sys.platform == "win32", "Windows process-memory/input adapter")
class NativeRuntimeTests(unittest.TestCase):
    def test_attach_propagates_the_selected_gdb_port(self) -> None:
        with patch.object(guest_memory, "find_process", return_value=123) as find:
            with patch.object(guest_memory.GuestMemory, "__init__", return_value=None):
                guest_memory.GuestMemory.attach(port=13579)
        find.assert_called_once_with("azahar.exe", 13579)

    def test_broken_or_wrong_shape_cache_is_ignored(self) -> None:
        for cached in ("broken json", "[]", "123", '{"pid":123,"offset":"invalid"}'):
            with self.subTest(cached=cached), tempfile.TemporaryDirectory() as directory:
                cache = Path(directory) / "cache.json"
                cache.write_text(cached, encoding="utf-8")
                memory = FakeMemory()
                with patch.object(driver, "CACHE", cache), patch.object(
                    driver.GuestMemory, "attach", return_value=memory
                ) as attach:
                    result = driver.anchor_memory(MagicMock(), verbose=False, port=13579)
                self.assertIs(result, memory)
                self.assertEqual(memory.scan_count, 1)
                self.assertFalse(memory.closed)
                self.assertEqual(json.loads(cache.read_text())["pid"], memory.pid)
                attach.assert_called_once_with(port=13579)

    def test_cached_offset_read_failure_triggers_rescan(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            cache = Path(directory) / "cache.json"
            cache.write_text(json.dumps({"pid": 123, "offset": 32}), encoding="utf-8")
            memory = FakeMemory()
            memory.verify_error = OSError("stale address")
            with patch.object(driver, "CACHE", cache), patch.object(
                driver.GuestMemory, "attach", return_value=memory
            ):
                self.assertIs(driver.anchor_memory(MagicMock(), verbose=False), memory)
            self.assertEqual(memory.scan_count, 1)

    def test_failed_anchor_closes_the_process_handle(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = FakeMemory()
            memory.scan_error = RuntimeError("ambiguous nonce")
            with patch.object(driver, "CACHE", Path(directory) / "missing.json"), patch.object(
                driver.GuestMemory, "attach", return_value=memory
            ):
                with self.assertRaisesRegex(RuntimeError, "ambiguous nonce"):
                    driver.anchor_memory(MagicMock(), verbose=False)
            self.assertTrue(memory.closed)

    def test_cache_write_failure_does_not_break_the_valid_anchor(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            memory = FakeMemory()
            with patch.object(driver, "CACHE", Path(directory) / "missing.json"), patch.object(
                driver.GuestMemory, "attach", return_value=memory
            ), patch.object(Path, "write_text", side_effect=OSError("read-only cache")):
                self.assertIs(driver.anchor_memory(MagicMock(), verbose=False), memory)
            self.assertFalse(memory.closed)

    def test_main_failure_resumes_and_closes_gdb(self) -> None:
        gdb = MagicMock()
        with patch.object(driver, "GdbClient", return_value=gdb), patch.object(
            driver.aim, "status", side_effect=RuntimeError("foreign patch")
        ):
            with self.assertRaisesRegex(RuntimeError, "foreign patch"):
                driver.main(["--port", "13579"])
        gdb.cont_all.assert_called_once()
        gdb.sock.close.assert_called_once()

    def test_mailbox_validation_failure_only_closes_memory(self) -> None:
        gdb = MagicMock()
        valid_gdb_headers(gdb)
        memory = MagicMock()
        memory.read.return_value = bytes(8)
        with patch.object(driver, "GdbClient", return_value=gdb), patch.object(
            driver.aim, "status", return_value={"state": "installed", "hook_matches": True}
        ), patch.object(
            driver.pin, "status", return_value={"state": "installed", "hook_matches": True}
        ), patch.object(driver, "anchor_memory", return_value=memory):
            with self.assertRaisesRegex(RuntimeError, "ABI mismatch"):
                driver.main([])
        memory.write.assert_not_called()
        memory.close.assert_called_once()
        gdb.sock.close.assert_called_once()

    def test_installed_mailbox_bad_header_refuses_all_gate_writes(self) -> None:
        for module in (driver.aim, driver.pin):
            for bad_header in (bytes(8), struct.pack("<II", module.MAGIC, module.VERSION + 1)):
                with self.subTest(module=module.__name__, header=bad_header.hex()):
                    gdb = MagicMock()
                    valid_gdb_headers(gdb)
                    valid_read = gdb.read_mem.side_effect
                    gdb.read_mem.side_effect = lambda address, size: (
                        bad_header if address == module.MAILBOX_VA else valid_read(address, size)
                    )
                    with patch.object(driver, "GdbClient", return_value=gdb), patch.object(
                        driver.aim, "status", return_value={"state": "installed", "hook_matches": True}
                    ), patch.object(
                        driver.pin, "status", return_value={"state": "installed", "hook_matches": True}
                    ), patch.object(driver, "anchor_memory") as anchor:
                        with self.assertRaisesRegex(RuntimeError, "refusing to write input gates"):
                            driver.main([])
                    gdb.write_mem.assert_not_called()
                    anchor.assert_not_called()
                    gdb.sock.close.assert_called_once()

    def test_main_cleanup_closes_handle_even_when_gate_write_fails(self) -> None:
        gdb = MagicMock()
        valid_gdb_headers(gdb)
        memory = MagicMock()

        def read(address, size):
            if address == driver.aim.MAILBOX_VA:
                return struct.pack("<II", driver.aim.MAGIC, driver.aim.VERSION)
            if address == driver.pin.MAILBOX_VA:
                return struct.pack("<II", driver.pin.MAGIC, driver.pin.VERSION)
            if address == driver.pin.MAILBOX_VA + driver.pin.MB_W:
                return struct.pack("<f", 400)
            if address == driver.pin.MAILBOX_VA + driver.pin.MB_H:
                return struct.pack("<f", 240)
            return bytes(size)

        memory.read.side_effect = read
        memory.write.side_effect = OSError("process exited")
        source = MagicMock()

        def raw_input(_delta, *, on_f4, on_escape):
            on_escape()
            return source

        source.start.return_value = source
        with patch.object(driver, "GdbClient", return_value=gdb), patch.object(
            driver.aim, "status", return_value={"state": "installed", "hook_matches": True}
        ), patch.object(
            driver.pin, "status", return_value={"state": "installed", "hook_matches": True}
        ), patch.object(driver, "anchor_memory", return_value=memory), patch.object(
            driver, "RawMouseInput", side_effect=raw_input
        ), patch.object(driver, "mapped_rect", return_value=(0, 0, 400, 240)):
            self.assertEqual(driver.main(["--mode", "look"]), 0)
        memory.close.assert_called_once()
        source.stop.assert_called_once()
        self.assertGreaterEqual(gdb.cont_all.call_count, 1)
        self.assertGreaterEqual(gdb.sock.close.call_count, 1)


if __name__ == "__main__":
    unittest.main()
