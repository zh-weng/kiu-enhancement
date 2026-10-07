"""Gaming Mode reuses verified launch sessions, without Tk or a game image."""
from pathlib import Path
import signal
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu import linux_play as play
from kiu.launcher import DEFAULTS


class LinuxPlayTests(unittest.TestCase):
    def values(self, **updates):
        return {**DEFAULTS, "azahar": "/apps/azahar.AppImage", "game": "/games/user.cci",
                "display_consent": True, **updates}

    def test_saved_options_keep_auto_single_screen_and_menu_opt_in(self):
        with mock.patch.object(play, "load_preferences", return_value=self.values()), \
                mock.patch.object(play, "validate_options") as validate:
            options = play.load_options(Path("prefs.json"))
        self.assertEqual(options.aspect, "auto")
        self.assertTrue(options.single_screen)
        self.assertTrue(options.mouse)
        self.assertFalse(options.menu_route)
        validate.assert_called_once_with(options)

    def test_headless_entry_does_not_grant_display_consent(self):
        with mock.patch.object(play, "load_preferences", return_value=self.values(display_consent=False)), \
                mock.patch.object(play, "validate_options") as validate:
            with self.assertRaisesRegex(RuntimeError, "not been authorized"):
                play.load_options(Path("prefs.json"))
            validate.assert_not_called()

    def test_no_display_preset_needs_no_display_consent(self):
        with mock.patch.object(play, "load_preferences", return_value=self.values(
                display_consent=False, sync_layout=False, single_screen=False, aspect="16:9")), \
                mock.patch.object(play, "validate_options"):
            options = play.load_options(Path("prefs.json"))
        self.assertFalse(options.sync_layout)

    def run_main(self, *, error=None, interrupt=False, argv=None):
        session = mock.Mock(error=error)
        handlers = {}

        def register(signum, handler):
            previous = handlers.get(signum, "previous-handler")
            handlers[signum] = handler
            return previous

        def run():
            if interrupt:
                handlers[signal.SIGTERM](signal.SIGTERM, None)

        session.run.side_effect = run
        with mock.patch.object(play.sys, "platform", "linux"), \
                mock.patch.object(play, "settings_path", return_value=Path("default.json")), \
                mock.patch.object(play, "load_options", return_value="options") as load, \
                mock.patch.object(play, "LauncherSession", return_value=session) as factory, \
                mock.patch.object(play.signal, "signal", side_effect=register) as register_mock:
            result = play.main(argv or [])
        return SimpleNamespace(result=result, session=session, load=load,
                               factory=factory, register=register_mock, handlers=handlers)

    def test_natural_close_runs_session_and_restores_original_signal_handlers(self):
        result = self.run_main()
        self.assertEqual(result.result, 0)
        result.load.assert_called_once_with(Path("default.json"))
        result.session.run.assert_called_once_with()
        result.session.stop.assert_not_called()
        self.assertEqual(result.handlers, {signal.SIGINT: "previous-handler",
                                          signal.SIGTERM: "previous-handler"})

    def test_signal_requests_owned_restore_never_emulator_termination(self):
        result = self.run_main(interrupt=True, argv=["--settings", "chosen.json"])
        self.assertEqual(result.result, 0)
        result.load.assert_called_once_with(Path("chosen.json"))
        result.session.stop.assert_called_once_with(restore=True)
        result.session.process.terminate.assert_not_called()
        result.session.process.kill.assert_not_called()

    def test_session_error_is_nonzero(self):
        self.assertEqual(self.run_main(error="failed").result, 1)

    def test_missing_preferences_returns_actionable_error_without_launch(self):
        with mock.patch.object(play.sys, "platform", "linux"), \
                mock.patch.object(play, "load_options", side_effect=FileNotFoundError), \
                mock.patch.object(play, "LauncherSession") as factory, \
                mock.patch.object(play.sys, "stderr") as error:
            self.assertEqual(play.main([]), 1)
            factory.assert_not_called()
            self.assertIn("desktop KIU Launcher", "".join(str(call.args[0]) for call in error.write.call_args_list))


if __name__ == "__main__":
    unittest.main()
