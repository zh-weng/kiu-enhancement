"""Distribution and resource cleanup tests; no emulator or input is touched."""
from __future__ import annotations

from contextlib import ExitStack
import json
import struct
import subprocess
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


class TickEvent:
    """An Event and clock controlled by the test, with no real sleeps."""

    def __init__(self, stop_at=2.0):
        self.now = 0.0
        self.stop_at = stop_at
        self.stopped = False
        self.on_tick = lambda _now: None

    def is_set(self):
        return self.stopped

    def set(self):
        self.stopped = True

    def wait(self, seconds):
        self.now = round(self.now + seconds, 8)
        self.on_tick(self.now)
        if self.now >= self.stop_at:
            self.set()
        return self.stopped


class SessionMemory:
    """Live mailbox data driven by a deterministic artificial guest clock."""

    def __init__(self, clock):
        self.clock = clock
        self.pid = 123
        self.closed = False
        self.calls = lambda now: int(now * 60)
        self.controller = lambda _now: 0
        self.size = lambda _now: (400.0, 240.0)
        self.bad_header_at = float("inf")
        self.writes = []

    def read(self, address, size):
        now = self.clock.now
        for module in (driver.aim, driver.pin):
            if address == module.MAILBOX_VA and size == 8:
                if now >= self.bad_header_at:
                    return bytes(8)
                return struct.pack("<II", module.MAGIC, module.VERSION)
        if address == driver.pin.MAILBOX_VA + driver.pin.MB_CALLS:
            return struct.pack("<II", self.calls(now), 0)
        if address == driver.pin.MAILBOX_VA + driver.pin.MB_P:
            return struct.pack("<I", self.controller(now))
        if address == driver.pin.MAILBOX_VA + driver.pin.MB_W:
            return struct.pack("<f", self.size(now)[0])
        if address == driver.pin.MAILBOX_VA + driver.pin.MB_H:
            return struct.pack("<f", self.size(now)[1])
        return bytes(size)

    def write(self, address, data):
        self.writes.append((self.clock.now, address, data))

    def close(self):
        self.closed = True


@unittest.skipUnless(sys.platform == "win32", "Windows process-memory/input adapter")
class NativeRuntimeTests(unittest.TestCase):
    def test_process_lookup_console_helpers_do_not_show_a_window(self) -> None:
        with patch("subprocess.run") as run:
            run.return_value.stdout = '"azahar.exe","123","Console","1","1 K"\n'
            self.assertEqual(guest_memory.find_process(), 123)
        self.assertEqual(run.call_args.kwargs["creationflags"],
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))
        with patch("subprocess.run") as run:
            run.return_value.stdout = "TCP 127.0.0.1:24689 0.0.0.0:0 LISTENING 123\n"
            self.assertEqual(guest_memory.listening_pid(), 123)
        self.assertEqual(run.call_args.kwargs["creationflags"],
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_window_lookup_tasklist_does_not_show_a_window(self) -> None:
        with patch.object(driver.window.subprocess, "run") as run:
            run.return_value.stdout = ""
            with self.assertRaisesRegex(RuntimeError, "no azahar.exe"):
                driver.window.find_azahar()
        self.assertEqual(run.call_args.kwargs["creationflags"],
                         getattr(subprocess, "CREATE_NO_WINDOW", 0))

    def test_attach_propagates_the_selected_gdb_port(self) -> None:
        with patch.object(guest_memory, "find_process", return_value=123) as find:
            with patch.object(guest_memory.GuestMemory, "__init__", return_value=None):
                guest_memory.GuestMemory.attach(port=13579)
        find.assert_called_once_with("azahar.exe", 13579)

    def test_wrong_port_owner_is_refused_before_connecting(self) -> None:
        for owner in (None, 999):
            with self.subTest(owner=owner), patch.object(
                driver, "listening_pid", return_value=owner
            ) as lookup, patch.object(driver, "GdbClient") as connect:
                with self.assertRaisesRegex(RuntimeError, "expected PID 123"):
                    driver.main(["--port", "13579"], expected_pid=123)
            lookup.assert_called_once_with(13579)
            connect.assert_not_called()

    def test_port_owner_change_at_connection_has_no_guest_commands_or_writes(self) -> None:
        gdb = MagicMock()
        with patch.object(driver, "listening_pid", side_effect=[123, 999]), patch.object(
            driver, "GdbClient", return_value=gdb
        ), patch.object(driver.aim, "status") as status:
            with self.assertRaisesRegex(RuntimeError, "expected PID 123"):
                driver.main([], expected_pid=123)
        status.assert_not_called()
        gdb.send.assert_not_called()
        gdb.read_mem.assert_not_called()
        gdb.write_mem.assert_not_called()
        gdb.cont_all.assert_not_called()
        gdb.sock.close.assert_called_once()

    def test_wrong_attached_memory_pid_is_refused_before_nonce_write(self) -> None:
        memory = FakeMemory()
        gdb = MagicMock()
        with patch.object(driver.GuestMemory, "attach", return_value=memory):
            with self.assertRaisesRegex(RuntimeError, "expected PID 999"):
                driver.anchor_memory(gdb, verbose=False, expected_pid=999)
        self.assertTrue(memory.closed)
        self.assertEqual(memory.scan_count, 0)
        gdb.write_mem.assert_not_called()
        gdb.cont_all.assert_not_called()

    def test_wrong_anchored_pid_has_no_mailbox_reads_or_process_writes(self) -> None:
        memory = MagicMock(pid=999)
        gdb = MagicMock()
        valid_gdb_headers(gdb)
        with patch.object(driver, "listening_pid", return_value=123), patch.object(
            driver, "GdbClient", return_value=gdb
        ), patch.object(driver.aim, "status", return_value={
            "state": "installed", "hook_matches": True,
        }), patch.object(driver.pin, "status", return_value={
            "state": "installed", "hook_matches": True,
        }), patch.object(driver, "anchor_memory", return_value=memory) as anchor, patch.object(
            driver, "RawMouseInput"
        ) as source:
            with self.assertRaisesRegex(RuntimeError, "expected PID 123"):
                driver.main([], expected_pid=123)
        anchor.assert_called_once_with(gdb, port=24689, expected_pid=123)
        memory.read.assert_not_called()
        memory.write.assert_not_called()
        memory.close.assert_called_once()
        source.assert_not_called()

    def test_invalid_expected_pid_is_refused_without_connecting(self) -> None:
        for value in (True, 0, -1, "123"):
            with self.subTest(value=value), patch.object(driver, "GdbClient") as connect:
                with self.assertRaisesRegex(ValueError, "positive integer"):
                    driver.main([], expected_pid=value)
            connect.assert_not_called()

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


@unittest.skipUnless(sys.platform == "win32", "Windows process-memory/input adapter")
class LauncherAimTests(unittest.TestCase):
    def setUp(self):
        self.clock = TickEvent()
        self.memory = SessionMemory(self.clock)
        self.focus = lambda _now: True
        self.source = MagicMock()
        self.source.start.return_value = self.source
        self.callbacks = {}
        self.statuses = []

    def run_worker(self, *, arguments=None, status_callback=None, anchor=None,
                   auto_activate=True, expected_pid=None):
        def source(_delta, *, on_f4, on_escape):
            self.callbacks.update(delta=_delta, f4=on_f4, escape=on_escape)
            return self.source

        gdb = MagicMock()
        valid_gdb_headers(gdb)
        with ExitStack() as stack:
            stack.enter_context(patch.object(driver, "GdbClient", return_value=gdb))
            stack.enter_context(patch.object(driver.aim, "status", return_value={
                "state": "installed", "hook_matches": True,
            }))
            stack.enter_context(patch.object(driver.pin, "status", return_value={
                "state": "installed", "hook_matches": True,
            }))
            stack.enter_context(patch.object(driver, "anchor_memory",
                                              side_effect=anchor or (lambda *_a, **_k: self.memory)))
            stack.enter_context(patch.object(driver, "RawMouseInput", side_effect=source))
            stack.enter_context(patch.object(driver.time, "perf_counter",
                                              side_effect=lambda: self.clock.now))
            stack.enter_context(patch.object(driver.window, "find_azahar", return_value=7))
            stack.enter_context(patch.object(driver.user32, "GetForegroundWindow",
                                              side_effect=lambda: 7 if self.focus(self.clock.now) else 8))
            stack.enter_context(patch.object(driver, "cursor_position", return_value=(200, 60)))
            self.mapping = stack.enter_context(patch.object(
                driver, "mapped_rect",
                side_effect=lambda _kind, _span, aspect, **_kw: (0, 0, 400, 400 * aspect),
            ))
            return driver.main(
                ["--hz", "4", *(arguments or [])], stop_event=self.clock,
                status_callback=status_callback or self.statuses.append,
                auto_activate=auto_activate, wait_for_battle=True,
                expected_pid=expected_pid,
            )

    def enabled_gates(self):
        return [write for write in self.memory.writes if
                (write[1] == driver.aim.MAILBOX_VA + driver.aim.MB_GATE
                 and write[2] == struct.pack("<I", 1)) or
                (write[1] == driver.pin.MAILBOX_VA + driver.pin.MB_MODE
                 and write[2] == struct.pack("<I", 2))]

    def test_menu_wait_can_be_cancelled_without_enabling_any_gate(self):
        self.memory.calls = lambda _now: 0
        self.memory.size = lambda _now: (0.0, 0.0)
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual(self.enabled_gates(), [])
        self.assertIn("waiting", self.statuses)
        self.assertEqual(self.statuses[-1], "complete")
        self.assertIsNone(self.callbacks["escape"])
        self.assertTrue(self.memory.closed)
        self.source.stop.assert_called_once()

    def test_matching_expected_pid_checks_port_before_and_after_connecting(self):
        with patch.object(driver, "listening_pid", return_value=self.memory.pid) as lookup:
            self.assertEqual(self.run_worker(expected_pid=self.memory.pid), 0)
        self.assertEqual(lookup.call_args_list, [
            ((24689,), {}), ((24689,), {}),
        ])
        self.assertTrue(self.enabled_gates())
        self.assertTrue(self.memory.closed)

    def test_legacy_cli_call_does_not_require_a_port_owner_lookup(self):
        with patch.object(driver, "listening_pid", side_effect=AssertionError("unexpected lookup")) as lookup:
            self.assertEqual(self.run_worker(), 0)
        lookup.assert_not_called()

    def test_menu_wait_activates_only_after_guest_hook_activity(self):
        self.memory.calls = lambda now: 0 if now < 0.5 else int((now - 0.5) * 60) + 1
        self.memory.size = lambda now: (0.0, 0.0) if now < 0.5 else (400.0, 240.0)
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual(self.enabled_gates()[0][0], 0.5)
        self.assertIn("waiting", self.statuses)
        self.assertIn("active", self.statuses)
        self.assertTrue(self.memory.closed)

    def test_focus_loss_stops_input_and_refocus_reactivates_auto_mode(self):
        self.focus = lambda now: not 0.75 <= now < 1.25
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual([write[0] for write in self.enabled_gates()], [0.25, 1.25])
        positions = [write for write in self.memory.writes
                     if write[1] == driver.pin.MAILBOX_VA + driver.pin.MB_POS_X]
        self.assertFalse(any(0.75 <= write[0] < 1.25 for write in positions))
        self.assertIn("inactive", self.statuses)

    def test_user_f4_off_survives_focus_loss_and_refocus(self):
        self.focus = lambda now: not 1.0 <= now < 1.25
        self.clock.on_tick = lambda now: self.callbacks["f4"]() if now == 0.75 else None
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual([write[0] for write in self.enabled_gates()], [0.25])
        self.assertIn("off", self.statuses)

    def test_manual_mode_still_requires_f4_before_opening_a_gate(self):
        self.clock.on_tick = lambda now: self.callbacks["f4"]() if now == 0.75 else None
        self.assertEqual(self.run_worker(auto_activate=False), 0)
        self.assertEqual([write[0] for write in self.enabled_gates()], [0.75])

    def test_stopped_guest_activity_turns_the_gate_off(self):
        self.memory.calls = lambda now: int(min(now, 0.5) * 60)
        self.clock.stop_at = 2.0
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual([write[0] for write in self.enabled_gates()], [0.25])
        self.assertIn("inactive", self.statuses)
        positions = [write for write in self.memory.writes
                     if write[1] == driver.pin.MAILBOX_VA + driver.pin.MB_POS_X]
        self.assertFalse(any(write[0] >= 1.5 for write in positions))

    def test_air_ground_transition_refreshes_the_dimensions_mapping_and_centre(self):
        self.memory.controller = lambda now: 0x14000000 if now < 0.75 else 0
        self.memory.size = lambda now: (400.0, 240.0) if now < 0.75 else (800.0, 240.0)
        self.assertEqual(self.run_worker(), 0)
        self.assertEqual([(write[0], write[1]) for write in self.enabled_gates()], [
            (0.25, driver.aim.MAILBOX_VA + driver.aim.MB_GATE),
            (0.75, driver.pin.MAILBOX_VA + driver.pin.MB_MODE),
        ])
        self.assertIn(0.3, [call.args[2] for call in self.mapping.call_args_list])
        positions = [write for write in self.memory.writes
                     if write[0] == 0.75 and write[1] == driver.pin.MAILBOX_VA + driver.pin.MB_POS_X]
        self.assertTrue(positions)
        self.assertTrue(all(struct.unpack("<ff", write[2]) == (400.0, 120.0)
                            for write in positions))

    def test_foreign_header_stops_without_even_cleanup_writes_to_it(self):
        self.memory.bad_header_at = 0.75
        with self.assertRaisesRegex(RuntimeError, "ABI mismatch"):
            self.run_worker()
        self.assertFalse(any(write[0] >= 0.75 for write in self.memory.writes))
        self.assertTrue(self.memory.closed)
        self.source.stop.assert_called_once()

    def test_stop_requested_during_anchor_is_honored_before_starting_raw_input(self):
        def anchor(*_args, **_kwargs):
            self.clock.set()
            return self.memory

        self.assertEqual(self.run_worker(anchor=anchor), 0)
        self.source.start.assert_not_called()
        self.assertEqual(self.enabled_gates(), [])
        self.assertTrue(self.memory.closed)
        self.assertEqual(self.statuses[-1], "complete")

    def test_display_callback_failure_cannot_skip_cleanup(self):
        def broken_display(_status):
            raise RuntimeError("GUI closed")

        self.assertEqual(self.run_worker(status_callback=broken_display), 0)
        self.assertTrue(self.memory.closed)
        self.source.stop.assert_called_once()

    def test_already_cancelled_start_does_not_connect(self):
        self.clock.set()
        with patch.object(driver, "GdbClient") as connect:
            self.assertEqual(driver.main([], stop_event=self.clock,
                                         status_callback=self.statuses.append), 0)
        connect.assert_not_called()
        self.assertEqual(self.statuses, ["complete"])


if __name__ == "__main__":
    unittest.main()
