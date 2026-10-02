"""Companion lifecycle and whole-selection preflight, without an emulator."""
from __future__ import annotations

import contextlib
import hashlib
import io
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiu import gdbclient  # noqa: E402
from kiu import cli  # noqa: E402
from kiu.patches import patch_widescreen as wide  # noqa: E402


class FakePatchModule:
    aspect_scale = staticmethod(wide.aspect_scale)
    bottom_aspect_scale = staticmethod(wide.bottom_aspect_scale)

    def __init__(self, name, events, state="stock"):
        self.name = name
        self.events = events
        self.resume_options = []
        self.state = {"state": state, "hook_matches": True}
        if name == "fps":
            self.state["interval"] = 0 if state == "installed" else 2

    def status(self, gdb):
        self.events.append(("status", self.name))
        return dict(self.state)

    def apply(self, gdb, *options, resume=True):
        self.events.append(("apply", self.name, *options))
        self.state["state"] = "installed"
        if self.name == "fps":
            self.state["interval"] = 0
        if self.name in ("aim", "pin"):
            self.resume_options.append(("apply", resume))
            if resume:
                gdb.cont_all()
        return {"installed": self.name}

    def revert(self, gdb, *, resume=True):
        self.events.append(("revert", self.name))
        self.state["state"] = "stock"
        if self.name == "fps":
            self.state["interval"] = 2
        if self.name in ("aim", "pin"):
            self.resume_options.append(("revert", resume))
            if resume:
                gdb.cont_all()
        return {"restored": self.name}

    def set_aspect(self, gdb, aspect):
        self.events.append(("set_aspect", self.name, aspect))
        return {"aspect": aspect}


def fake_gdb(stop="T05"):
    return SimpleNamespace(
        send=mock.Mock(return_value=stop),
        cont_all=mock.Mock(),
        sock=SimpleNamespace(close=mock.Mock()),
    )


class GuestSessionTests(unittest.TestCase):
    def test_body_failure_resumes_and_closes(self):
        gdb = fake_gdb()
        with mock.patch.object(gdbclient, "GdbClient", return_value=gdb) as connect:
            with self.assertRaisesRegex(RuntimeError, "patch failed"):
                with cli.guest_session(12345) as active:
                    self.assertIs(active, gdb)
                    raise RuntimeError("patch failed")
        connect.assert_called_once_with(port=12345, timeout=20)
        gdb.send.assert_called_once_with("?")
        gdb.cont_all.assert_called_once_with()
        gdb.sock.close.assert_called_once_with()

    def test_invalid_stop_never_yields_and_still_cleans_up(self):
        gdb = fake_gdb("OK")
        with mock.patch.object(gdbclient, "GdbClient", return_value=gdb):
            with self.assertRaisesRegex(RuntimeError, "stopped guest"):
                with cli.guest_session(24689):
                    self.fail("an unverified running guest must not be yielded")
        gdb.cont_all.assert_called_once_with()
        gdb.sock.close.assert_called_once_with()

    def test_handshake_failure_still_cleans_up(self):
        gdb = fake_gdb()
        gdb.send.side_effect = OSError("connection lost")
        with mock.patch.object(gdbclient, "GdbClient", return_value=gdb):
            with self.assertRaisesRegex(OSError, "connection lost"):
                with cli.guest_session(24689):
                    self.fail("failed handshake must not yield")
        gdb.cont_all.assert_called_once_with()
        gdb.sock.close.assert_called_once_with()

    def test_resume_failure_does_not_leak_socket(self):
        gdb = fake_gdb()
        gdb.cont_all.side_effect = OSError("resume failed")
        with mock.patch.object(gdbclient, "GdbClient", return_value=gdb):
            with self.assertRaisesRegex(OSError, "resume failed"):
                with cli.guest_session(24689):
                    pass
        gdb.sock.close.assert_called_once_with()


class RuntimeCommandTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.loaded = {
            name: FakePatchModule(name, self.events)
            for name in ("fps", "wide", "aim", "pin", "menu")
        }
        self.gdb = fake_gdb()

    def run_command(self, arguments):
        args = cli.parser().parse_args(arguments)
        with mock.patch.object(cli, "modules", return_value=self.loaded):
            with mock.patch.object(gdbclient, "GdbClient", return_value=self.gdb):
                return cli.runtime_command(args)

    def writes(self):
        return [event for event in self.events if event[0] != "status"]

    def test_apply_checks_entire_selection_before_first_write(self):
        result = self.run_command([
            "apply", "--aspect", str(21 / 9), "--mouse-hooks", "--menu-route",
        ])
        names = ["fps", "wide", "aim", "pin", "menu"]
        self.assertEqual(self.events[:5], [("status", name) for name in names])
        self.assertEqual(self.writes(), [
            ("apply", "fps"), ("apply", "wide", 21 / 9),
            ("apply", "aim"), ("apply", "pin"), ("apply", "menu"),
        ])
        self.assertEqual(list(result), names)
        for name in ("aim", "pin"):
            self.assertEqual(self.loaded[name].resume_options, [("apply", False)])
        self.gdb.cont_all.assert_called_once_with()
        self.gdb.sock.close.assert_called_once_with()

    def test_default_apply_does_not_install_preview_hooks(self):
        result = self.run_command(["apply"])
        self.assertEqual(list(result), ["fps", "wide"])
        self.assertEqual(self.writes(), [("apply", "fps"), ("apply", "wide", 16 / 9)])

    def test_invalid_aspect_fails_before_connecting_or_writing(self):
        for aspect in ("nan", "inf", "-inf", "0", "-1"):
            with self.subTest(aspect=aspect):
                self.events.clear()
                args = cli.parser().parse_args(["apply", f"--aspect={aspect}", "--mouse-hooks"])
                with mock.patch.object(cli, "modules", return_value=self.loaded):
                    with mock.patch.object(gdbclient, "GdbClient") as connect:
                        with self.assertRaisesRegex(ValueError, "finite positive"):
                            cli.runtime_command(args)
                connect.assert_not_called()
                self.assertEqual(self.events, [])
                self.assertEqual(self.writes(), [])

    def test_mixed_last_component_prevents_all_writes(self):
        self.loaded["menu"].state["state"] = "mixed"
        with self.assertRaisesRegex(RuntimeError, "menu: unknown/mixed"):
            self.run_command(["apply", "--mouse-hooks", "--menu-route"])
        self.assertEqual(self.writes(), [])
        self.assertEqual(len(self.events), 5)
        self.gdb.cont_all.assert_called_once_with()
        self.gdb.sock.close.assert_called_once_with()

    def test_wrong_installed_hook_version_prevents_all_writes(self):
        self.loaded["pin"].state.update(state="installed", hook_matches=False)
        with self.assertRaisesRegex(RuntimeError, "another hook version"):
            self.run_command(["apply", "--mouse-hooks"])
        self.assertEqual(self.writes(), [])

    def test_scheduler_disagreement_prevents_all_writes(self):
        for state, interval in (("stock", 0), ("installed", 2)):
            with self.subTest(state=state):
                self.events.clear()
                self.loaded["fps"].state.update(state=state, interval=interval)
                with self.assertRaisesRegex(RuntimeError, "scheduler interval"):
                    self.run_command(["apply"])
                self.assertEqual(self.writes(), [])

    def test_restore_disconnects_in_reverse_component_order(self):
        for module in self.loaded.values():
            module.state["state"] = "installed"
        self.loaded["fps"].state["interval"] = 0
        result = self.run_command(["restore"])
        self.assertEqual(list(result), ["menu", "pin", "aim", "wide", "fps"])
        self.assertEqual(self.writes(), [
            ("revert", name) for name in ("menu", "pin", "aim", "wide", "fps")
        ])
        for name in ("aim", "pin"):
            self.assertEqual(self.loaded[name].resume_options, [("revert", False)])
        self.gdb.cont_all.assert_called_once_with()

    def test_restore_stock_components_has_no_writes(self):
        result = self.run_command(["restore"])
        self.assertEqual(self.writes(), [])
        self.assertTrue(all(value == {"already_stock": True} for value in result.values()))

    def test_repeat_apply_only_updates_installed_widescreen_aspect(self):
        self.loaded["fps"].state.update(state="installed", interval=0)
        self.loaded["wide"].state["state"] = "installed"
        result = self.run_command(["apply", "--aspect", "2.3333333333333335"])
        self.assertEqual(self.writes(), [("set_aspect", "wide", 21 / 9)])
        self.assertEqual(result["fps"], {"already_installed": True})

    def test_status_reports_unknown_state_without_mutation(self):
        self.loaded["menu"].state["state"] = "unknown"
        result = self.run_command(["status"])
        self.assertEqual(result["menu"]["state"], "unknown")
        self.assertEqual(self.writes(), [])


class BuildVerificationTests(unittest.TestCase):
    def test_wrong_hash_is_rejected_with_actual_hash(self):
        image = mock.Mock(spec=Path)
        image.read_bytes.return_value = b"unsupported game build"
        digest = hashlib.sha256(image.read_bytes.return_value).hexdigest()
        with self.assertRaisesRegex(RuntimeError, digest):
            cli.verify_build(image)

    def test_supported_digest_is_returned(self):
        image = mock.Mock(spec=Path)
        image.read_bytes.return_value = b"synthetic unit fixture"
        digest = hashlib.sha256(image.read_bytes.return_value).hexdigest()
        with mock.patch.object(cli, "CODE_SHA256", digest):
            self.assertEqual(cli.verify_build(image), digest)

    def test_main_reports_missing_file_as_failure(self):
        stderr = io.StringIO()
        with mock.patch.object(Path, "read_bytes", side_effect=FileNotFoundError("missing code.bin")):
            with contextlib.redirect_stderr(stderr):
                self.assertEqual(cli.main(["verify-build", "missing-code.bin"]), 1)
        self.assertIn("missing code.bin", stderr.getvalue())


if __name__ == "__main__":
    unittest.main()
