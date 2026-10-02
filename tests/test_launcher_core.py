"""Launch lifecycle regressions without a game, GUI, or emulator process."""
from contextlib import contextmanager
import ctypes
from pathlib import Path
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu import launcher_core as core


class Patch:
    def __init__(self, name, log, state="stock"):
        self.name, self.log, self.state = name, log, state
        self.MAGIC, self.VERSION, self.MAILBOX_VA = 7, 1, 100 if name == "aim" else 200
        self.SCHED_SLOT = 300
        self.SCHED_NOMINAL_OFF = 0x50
        self.PATCH_SITES = [SimpleNamespace(address=1, expected=b"s", replacement=b"i")]
        self.CAVES = [(2, b"c", "test cave")]

    def status(self, gdb):
        self.log.append(("status", self.name))
        return {"state": self.state, "hook_matches": self.state == "installed",
                "interval": 0 if self.state == "installed" else 2}

    def apply(self, gdb, *args, **kwargs):
        self.log.append(("apply", self.name, args, kwargs))
        self.state = "installed"

    def revert(self, gdb, **kwargs):
        self.log.append(("revert", self.name, kwargs))
        self.state = "stock"

    def set_gate(self, gdb, value):
        self.log.append(("gate", value))

    def set_mode(self, gdb, value):
        self.log.append(("mode", value))

    def _u32(self, gdb, address):
        return gdb.scheduler if address == self.SCHED_SLOT else gdb.nominal

    def _scheduler(self, gdb):
        if gdb.nominal != 30:
            raise RuntimeError("scheduler nominal does not match 30")
        return gdb.scheduler


class LaunchTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        root = Path(self.directory.name)
        self.azahar, self.game = root / "azahar.exe", root / "game.cci"
        self.azahar.write_text("fake executable")
        self.game.write_text("fake game")
        self.options = core.LauncherOptions(self.azahar, self.game)
        self.messages, self.log = [], []
        self.modules = {name: Patch(name, self.log) for name in ("fps", "wide", "aim", "pin")}
        self.process = mock.Mock(pid=1234)
        self.process.poll.return_value = None
        self.connections = 0
        self.scheduler_values = [1000]
        self.nominal = 30
        self.nominal_values = None
        self.abi_valid = True
        self.cold_code = b"s"
        self.cold_cave = b"\0"
        self.patch(core.sys, "platform", "win32")
        self.verify = self.patch(core, "verify_game", return_value={"title_id": "verified"})
        self.popen = self.patch(core.subprocess, "Popen", return_value=self.process)
        self.free_port = self.patch(core, "_require_free_port")
        self.owner = self.patch(core, "_port_owner", return_value=self.process.pid)
        self.patch(core, "_patch_modules", return_value=self.modules)
        self.patch(core, "guest_session", side_effect=self.guest)
        self.driver = self.patch(core, "_run_mouse", side_effect=self.run_driver)
        self.patch(core, "POLL_INTERVAL", 0.005)

    def patch(self, target, name, *args, **kwargs):
        patcher = mock.patch.object(target, name, *args, **kwargs)
        self.addCleanup(patcher.stop)
        return patcher.start()

    @contextmanager
    def guest(self, port, *, timeout):
        self.connections += 1
        scheduler = self.scheduler_values[min(self.connections - 1, len(self.scheduler_values) - 1)]
        nominal = (self.nominal_values[min(self.connections - 1, len(self.nominal_values) - 1)]
                   if self.nominal_values else self.nominal)

        def read(address, size):
            if address in (100, 200):
                return struct.pack("<II", 7 if self.abi_valid else 99, 1)
            return self.cold_code if address == 1 else self.cold_cave

        gdb = SimpleNamespace(scheduler=scheduler, nominal=nominal, read_mem=read)
        self.log.append(("connect", self.connections))
        try:
            yield gdb
        finally:
            self.log.append(("resume", self.connections))

    def run_driver(self, args, **kwargs):
        self.log.append(("driver", kwargs))
        kwargs["stop_event"].set()
        self.log.append(("driver-cleanup",))
        return 0

    def session(self, **changes):
        options = core.LauncherOptions(**{**self.options.__dict__, **changes})
        return core.LauncherSession(options, self.messages.append)

    def test_verifies_before_launch_and_uses_plain_argument_list(self):
        session = self.session()
        self.verify.side_effect = lambda *args, **kwargs: self.assertFalse(self.popen.called)
        session.run()
        self.assertIsNone(session.error)
        self.popen.assert_called_once_with(
            [str(self.azahar), "-g", "24689", str(self.game)],
            cwd=self.azahar.parent, shell=False,
        )
        self.process.terminate.assert_not_called()
        self.process.kill.assert_not_called()

    def test_all_selected_states_are_preflighted_before_first_write(self):
        session = self.session()
        session.run()
        first = next(index for index, item in enumerate(self.log) if item[0] == "apply")
        names = [item[1] for item in self.log[:first] if item[0] == "status"]
        self.assertEqual(names, ["fps", "wide", "aim", "pin"])

    def test_restores_owned_hooks_after_driver_cleanup_in_reverse_order(self):
        session = self.session()
        session.run()
        restored = [item[1] for item in self.log if item[0] == "revert"]
        self.assertEqual(restored, ["pin", "aim", "wide", "fps"])
        cleanup = self.log.index(("driver-cleanup",))
        first_restore = next(i for i, item in enumerate(self.log) if item[0] == "revert")
        self.assertLess(cleanup, first_restore)
        self.assertEqual([item[-1] for item in self.log if item[:2] == ("apply", "aim")], [{"resume": False}])
        self.assertEqual(self.driver.call_args.kwargs["expected_pid"], self.process.pid)

    def test_existing_user_patches_are_reused_not_restored_or_changed(self):
        self.modules["fps"].state = self.modules["wide"].state = "installed"
        session = self.session()
        session.run()
        for action in ("apply", "revert"):
            self.assertEqual([item[1] for item in self.log if item[0] == action],
                             ["aim", "pin"] if action == "apply" else ["pin", "aim"])

    def test_options_only_install_selected_components(self):
        session = self.session(fps=False, aspect=None)
        session.run()
        self.assertEqual([item[1] for item in self.log if item[0] == "apply"], ["aim", "pin"])

    def test_mouse_disabled_does_not_load_driver_or_mouse_hooks(self):
        session = self.session(mouse=False)
        original = self.modules["wide"].apply

        def apply(*args, **kwargs):
            original(*args, **kwargs)
            session.stop()

        self.modules["wide"].apply = apply
        session.run()
        self.driver.assert_not_called()
        self.assertEqual([item[1] for item in self.log if item[0] == "apply"], ["fps", "wide"])

    def test_cold_null_scheduler_is_resumed_then_retried(self):
        self.scheduler_values = [0, 1000]
        session = self.session()
        session.run()
        self.assertIsNone(session.error)
        first_apply = next(i for i, item in enumerate(self.log) if item[0] == "apply")
        self.assertLess(self.log.index(("resume", 1)), first_apply)
        self.assertEqual(self.connections, 3)  # cold, install, restore

    def test_stock_constructor_nominal_60_resumes_until_game_nominal_30(self):
        self.nominal_values = [60, 30]
        session = self.session()
        session.run()
        self.assertIsNone(session.error)
        first_write = next(i for i, item in enumerate(self.log)
                           if item[0] in ("apply", "gate", "mode"))
        self.assertLess(self.log.index(("resume", 1)), first_write)
        self.assertEqual(self.connections, 3)

    def test_constructor_nominal_60_with_unknown_bytes_does_not_retry(self):
        self.nominal_values = [60, 30]
        self.cold_code = b"?"
        session = self.session()
        session.run()
        self.assertIn("unknown/mixed", session.error)
        self.assertEqual(self.connections, 1)
        self.assertFalse(any(item[0] == "apply" for item in self.log))

    def test_constructor_nominal_60_with_mixed_site_and_cave_does_not_retry(self):
        self.nominal_values = [60, 30]
        self.cold_code, self.cold_cave = b"i", b"\0"
        session = self.session()
        session.run()
        self.assertIn("unknown/mixed", session.error)
        self.assertEqual(self.connections, 1)

    def test_constructor_nominal_60_preflights_other_selected_hooks(self):
        self.nominal_values = [60, 30]
        self.modules["pin"].state = "unknown"
        session = self.session()
        session.run()
        self.assertIn("unknown/mixed", session.error)
        self.assertEqual(self.connections, 1)
        self.assertFalse(any(item[0] in ("apply", "gate", "mode") for item in self.log))

    def test_constructor_nominal_60_with_installed_fps_is_not_cold_stock(self):
        self.nominal_values = [60, 30]
        self.modules["fps"].state = "installed"
        self.cold_code, self.cold_cave = b"i", b"c"
        session = self.session()
        session.run()
        self.assertIn("not a cold stock state", session.error)
        self.assertEqual(self.connections, 1)
        self.assertFalse(any(item[0] == "apply" for item in self.log))

    def test_cold_start_unknown_static_bytes_are_not_retried(self):
        self.scheduler_values = [0, 1000]
        self.cold_code = b"?"
        session = self.session()
        session.run()
        self.assertIn("unknown/mixed", session.error)
        self.assertEqual(self.connections, 1)
        self.assertFalse(any(item[0] == "apply" for item in self.log))

    def test_cancel_during_cold_boot_resumes_without_installing(self):
        self.scheduler_values = [0]
        session = self.session()

        @contextmanager
        def cancelled_guest(*args, **kwargs):
            with self.guest(*args, **kwargs) as gdb:
                yield gdb
            session.stop()

        self.patch(core, "guest_session", side_effect=cancelled_guest)
        session.run()
        self.assertIsNone(session.error)
        self.assertIn(("resume", 1), self.log)
        self.assertFalse(any(item[0] == "apply" for item in self.log))

    def test_nonzero_unknown_scheduler_nominal_is_not_retried(self):
        self.nominal = 99
        session = self.session()
        session.run()
        self.assertIn("nominal", session.error)
        self.assertEqual(self.connections, 1)

    def test_mixed_hook_blocks_all_writes(self):
        self.modules["pin"].state = "unknown"
        session = self.session()
        session.run()
        self.assertIn("unknown/mixed", session.error)
        self.assertFalse(any(item[0] in ("apply", "gate", "mode", "revert") for item in self.log))

    def test_installed_mouse_abi_is_checked_before_installing_other_patches(self):
        self.modules["aim"].state = self.modules["pin"].state = "installed"
        self.abi_valid = False
        session = self.session()
        session.run()
        self.assertIn("ABI mismatch", session.error)
        self.assertFalse(any(item[0] == "apply" for item in self.log))

    def test_verification_failure_never_launches_emulator(self):
        self.verify.side_effect = ValueError("unsupported build")
        session = self.session()
        session.run()
        self.assertEqual(session.error, "unsupported build")
        self.popen.assert_not_called()

    def test_stop_before_start_does_not_launch_or_verify(self):
        session = self.session()
        session.stop()
        session.run()
        self.assertIsNone(session.error)
        self.verify.assert_not_called()
        self.popen.assert_not_called()

    def test_cancellation_from_verifier_does_not_launch(self):
        session = self.session()

        def verify(*args, **kwargs):
            session.stop()
            self.assertTrue(kwargs["cancelled"]())
            raise core.VerificationCancelled("cancelled")

        self.verify.side_effect = verify
        session.run()
        self.assertIsNone(session.error)
        self.popen.assert_not_called()

    def test_busy_port_does_not_launch_or_attach(self):
        self.free_port.side_effect = RuntimeError("port in use")
        session = self.session()
        session.run()
        self.assertIn("port in use", session.error)
        self.popen.assert_not_called()
        self.assertEqual(self.connections, 0)

    def test_other_process_owning_port_is_never_attached(self):
        self.owner.return_value = 9876
        session = self.session()
        session.run()
        self.assertIn("another process", session.error)
        self.assertEqual(self.connections, 0)

    def test_owner_change_at_connection_is_refused_before_writes(self):
        self.owner.side_effect = [1234, 9876]
        session = self.session()
        session.run()
        self.assertIn("another process", session.error)
        self.assertEqual(self.connections, 1)
        self.assertFalse(any(item[0] == "apply" for item in self.log))

    def test_startup_timeout_never_installs_or_kills_emulator(self):
        self.patch(core, "STARTUP_TIMEOUT", 0)
        session = self.session()
        session.run()
        self.assertIn("timed out", session.error)
        self.assertFalse(any(item[0] == "apply" for item in self.log))
        self.process.kill.assert_not_called()

    def test_nonzero_exit_during_cold_boot_is_an_error_with_return_code(self):
        self.process.poll.return_value = 3
        session = self.session()
        session.run()
        self.assertIn("initialization", session.error)
        self.assertIn("return code 3", session.error)
        self.assertEqual(self.connections, 0)
        self.driver.assert_not_called()

    def test_normal_close_during_cold_boot_is_not_an_error(self):
        self.process.poll.return_value = 0
        session = self.session()
        session.run()
        self.assertIsNone(session.error)
        self.assertEqual(self.connections, 0)

    def test_nonzero_emulator_exit_while_mouse_active_is_reported(self):
        def driver(*args, **kwargs):
            self.process.poll.return_value = 9

        self.driver.side_effect = driver
        session = self.session()
        session.run()
        self.assertIn("return code 9", session.error)
        self.assertFalse(any(item[0] == "revert" for item in self.log))

    def test_emulator_exit_does_not_reconnect_or_restore(self):
        def driver(*args, **kwargs):
            self.process.poll.return_value = 0
            kwargs["stop_event"].wait(1)
            self.assertTrue(kwargs["stop_event"].is_set())
            self.log.append(("driver-cleanup",))

        self.driver.side_effect = driver
        session = self.session()
        session.run()
        self.assertIsNone(session.error)
        self.assertEqual(self.connections, 1)
        self.assertFalse(any(item[0] == "revert" for item in self.log))

    def test_mouse_error_runs_restore_without_killing_emulator(self):
        self.driver.side_effect = RuntimeError("driver failed")
        session = self.session()
        session.run()
        self.assertIn("driver failed", session.error)
        self.assertEqual([item[1] for item in self.log if item[0] == "revert"], ["pin", "aim", "wide", "fps"])
        self.process.kill.assert_not_called()

    def test_unknown_state_during_restore_is_refused(self):
        def driver(*args, **kwargs):
            self.modules["pin"].state = "unknown"
            kwargs["stop_event"].set()

        self.driver.side_effect = driver
        session = self.session()
        session.run()
        self.assertIn("restoration refused", session.error)
        self.assertFalse(any(item[0] == "revert" for item in self.log))

    def test_unknown_mailbox_during_restore_is_not_cleared(self):
        def driver(*args, **kwargs):
            self.abi_valid = False
            kwargs["stop_event"].set()

        self.driver.side_effect = driver
        session = self.session()
        session.run()
        self.assertIn("ABI mismatch", session.error)
        self.assertFalse(any(item[0] == "revert" for item in self.log))

    def test_owner_change_at_restore_is_not_attached(self):
        self.owner.side_effect = [1234, 1234, 9876]
        session = self.session()
        session.run()
        self.assertIn("restoration refused", session.error)
        self.assertEqual(self.connections, 1)
        self.assertFalse(any(item[0] == "revert" for item in self.log))

    def test_explicit_stop_without_restore_leaves_patches(self):
        session = self.session()

        def driver(*args, **kwargs):
            session.stop(restore=False)

        self.driver.side_effect = driver
        session.run()
        self.assertFalse(session.restore_requested)
        self.assertFalse(any(item[0] == "revert" for item in self.log))

    def test_start_uses_joinable_non_daemon_worker(self):
        session = self.session()
        session.start()
        session.thread.join(2)
        self.assertFalse(session.thread.daemon)
        self.assertFalse(session.alive)
        self.assertIsNone(session.error)
        with self.assertRaises(RuntimeError):
            session.start()

    def test_bad_aspect_sensitivity_port_or_executable_are_refused(self):
        for changes in ({"aspect": float("nan")}, {"aspect": 2.0},
                        {"sensitivity": 0}, {"sensitivity": float("inf")},
                        {"port": 0}, {"port": True}, {"azahar": self.game}):
            with self.subTest(changes=changes):
                with self.assertRaises(ValueError):
                    core.validate_options(self.session(**changes).options)

    def test_portable_import_has_no_unsupported_platform_start(self):
        with mock.patch.object(core.sys, "platform", "linux"):
            with self.assertRaisesRegex(RuntimeError, "requires Windows"):
                core.validate_options(self.options)

    def dll_kernel(self, previous="frozen-runtime", get_error=0):
        kernel = mock.Mock()

        def get_directory(size, buffer):
            if size:
                buffer.value = previous
            return len(previous)

        kernel.GetDllDirectoryW.side_effect = get_directory
        kernel.SetDllDirectoryW.return_value = True
        self.patch(core.sys, "frozen", True, create=True)
        self.patch(ctypes, "WinDLL", return_value=kernel, create=True)
        self.patch(ctypes, "set_last_error", create=True)
        self.patch(ctypes, "get_last_error", return_value=get_error, create=True)
        self.patch(ctypes, "WinError", side_effect=lambda *_: OSError("DLL path failure"), create=True)
        return kernel

    def test_frozen_popen_uses_default_dll_path_then_restores_previous(self):
        kernel = self.dll_kernel()

        def popen(*args, **kwargs):
            self.assertEqual(kernel.SetDllDirectoryW.call_args_list, [mock.call(None)])
            return self.process

        self.popen.side_effect = popen
        session = self.session()
        session.run()
        self.assertIsNone(session.error)
        self.assertEqual(kernel.SetDllDirectoryW.call_args_list,
                         [mock.call(None), mock.call("frozen-runtime")])

    def test_frozen_dll_path_restores_when_popen_fails(self):
        kernel = self.dll_kernel()
        self.popen.side_effect = OSError("could not launch Azahar")
        session = self.session()
        session.run()
        self.assertIn("could not launch Azahar", session.error)
        self.assertIsNone(session.process)
        self.assertEqual(kernel.SetDllDirectoryW.call_args_list,
                         [mock.call(None), mock.call("frozen-runtime")])

    def test_frozen_dll_directory_read_failure_prevents_launch(self):
        kernel = self.dll_kernel(previous="", get_error=5)
        session = self.session()
        session.run()
        self.assertIn("DLL path failure", session.error)
        self.popen.assert_not_called()
        kernel.SetDllDirectoryW.assert_not_called()

    def test_unfrozen_dll_context_does_not_load_windows_apis(self):
        load_dll = self.patch(ctypes, "WinDLL", create=True)
        self.patch(core.sys, "frozen", False, create=True)
        with core._external_program_dll_path():
            pass
        load_dll.assert_not_called()


if __name__ == "__main__":
    unittest.main()
