"""First-use display consent without creating a GUI, game, or emulator."""
from pathlib import Path
import queue
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu import launcher


class ConsentTests(unittest.TestCase):
    def app(self, *, sync=True, consent=False):
        app = launcher.LauncherApp.__new__(launcher.LauncherApp)
        app.root, app.session = object(), None
        for key, value in {"azahar": "azahar.exe", "game": "game.cci", "fps": True,
                           "mouse": True, "aspect": "16:9", "sensitivity": "0.0025",
                           "sync_layout": sync, "single_screen": False, "menu_route": False,
                           "pitch_enabled": False, "pitch_degrees": "60",
                           "no_recenter": False, "button_dash": False}.items():
            setattr(app, key, SimpleNamespace(get=lambda value=value: value))
        app.display_consent = consent
        app.preferences = Path("unused/preferences.json")
        app.events = queue.Queue()
        app.set_busy, app.report = mock.Mock(), mock.Mock()
        return app

    def exercise(self, app, approved):
        messages, session = mock.Mock(), mock.Mock()
        messages.askokcancel.return_value = approved
        with mock.patch.dict(sys.modules, {"tkinter": SimpleNamespace(messagebox=messages)}), \
                mock.patch.object(launcher, "validate_options"), \
                mock.patch.object(launcher, "LauncherSession", return_value=session) as create, \
                mock.patch.object(launcher, "save_preferences") as save:
            app.start()
        return messages, session, create, save

    def test_declined_consent_never_starts_or_persists_authorization(self):
        app = self.app()
        messages, session, create, save = self.exercise(app, False)
        messages.askokcancel.assert_called_once()
        create.assert_not_called()
        session.start.assert_not_called()
        save.assert_not_called()
        self.assertFalse(app.display_consent)

    def test_approved_preset_is_explicit_and_persisted(self):
        app = self.app()
        messages, session, create, save = self.exercise(app, True)
        messages.askokcancel.assert_called_once()
        session.start.assert_called_once()
        self.assertTrue(create.call_args.args[0].sync_layout)
        self.assertTrue(save.call_args.args[1]["display_consent"])
        self.assertTrue(app.display_consent)

    def test_saved_consent_does_not_prompt_again(self):
        messages, session, _create, _save = self.exercise(self.app(consent=True), False)
        messages.askokcancel.assert_not_called()
        session.start.assert_called_once()

    def test_opt_out_does_not_request_or_grant_config_authority(self):
        app = self.app(sync=False)
        messages, session, create, save = self.exercise(app, False)
        messages.askokcancel.assert_not_called()
        session.start.assert_called_once()
        self.assertFalse(create.call_args.args[0].sync_layout)
        self.assertFalse(save.call_args.args[1]["display_consent"])


if __name__ == "__main__":
    unittest.main()
