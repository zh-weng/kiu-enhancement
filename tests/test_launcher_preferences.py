"""Preferences never touch emulator settings or require a game image."""
import json
from pathlib import Path
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kiu import launcher
from kiu.launcher import checked_preferences, load_preferences, save_preferences, DEFAULTS, options_from_preferences


class LauncherPreferencesTests(unittest.TestCase):
    def test_round_trip_local_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "preferences.json"
            values = {**DEFAULTS, "azahar": "chosen/azahar.exe", "game": "chosen/game.cci"}
            save_preferences(target, values)
            self.assertEqual(load_preferences(target), values)
            self.assertEqual(json.loads(target.read_text(encoding="utf-8"))["version"], 1)
            self.assertEqual([p.name for p in target.parent.iterdir()], ["preferences.json"])

    def test_invalid_values_are_refused(self):
        for update in ({"version": 2}, {"fps": "yes"}, {"mouse": 1}, {"game": "bad\0path"},
                       {"sync_layout": "yes"}, {"display_consent": 1},
                       {"experimental_pitch_enabled": 1}, {"experimental_no_recenter": "yes"},
                       {"experimental_button_dash": 1}, {"experimental_pitch_degrees": True},
                       {"experimental_pitch_degrees": 0}, {"experimental_pitch_degrees": 90},
                       {"experimental_pitch_degrees": float("nan")},
                       {"experimental_pitch_degrees": float("inf")},
                       {"aspect": "arbitrary"}, {"sensitivity": float("nan")},
                       {"sensitivity": float("inf")}, {"sensitivity": -1}, {"sensitivity": True}):
            with self.subTest(update=update), self.assertRaises(ValueError):
                checked_preferences({"version": 1, **DEFAULTS, **update})

    def test_invalid_update_does_not_replace_existing_preferences(self):
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "preferences.json"
            save_preferences(target, DEFAULTS)
            original = target.read_bytes()
            with self.assertRaises(ValueError):
                save_preferences(target, {**DEFAULTS, "sensitivity": 0})
            self.assertEqual(target.read_bytes(), original)

    def test_unknown_optional_fields_do_not_change_current_contract(self):
        self.assertEqual(checked_preferences({"version": 1, **DEFAULTS, "extra": "future"}), DEFAULTS)

    def test_old_preferences_do_not_grant_display_consent(self):
        old = {key: value for key, value in DEFAULTS.items()
               if key not in ("sync_layout", "display_consent")}
        loaded = checked_preferences({"version": 1, **old})
        self.assertTrue(loaded["sync_layout"])
        self.assertFalse(loaded["display_consent"])

    def test_old_custom_width_consent_does_not_authorize_single_screen_padding(self):
        loaded = checked_preferences({"version": 1, "display_consent": True})
        self.assertFalse(loaded["display_consent"])
        self.assertTrue(loaded["single_screen"])
        self.assertEqual(loaded["aspect"], "自动（全屏）")

    def test_old_preferences_leave_all_camera_movement_experiments_off(self):
        values = checked_preferences({"version": 1, "azahar": "azahar.exe", "game": "game.cci"})
        options = options_from_preferences(values)
        self.assertIsNone(options.experimental_pitch_degrees)
        self.assertFalse(options.experimental_no_recenter)
        self.assertFalse(options.experimental_button_dash)

    def test_experiments_round_trip_and_map_without_changing_other_options(self):
        values = {**DEFAULTS, "experimental_pitch_enabled": True, "experimental_pitch_degrees": 75,
                  "experimental_no_recenter": True, "experimental_button_dash": True}
        with tempfile.TemporaryDirectory() as directory:
            target = Path(directory) / "preferences.json"
            save_preferences(target, values)
            options = options_from_preferences(load_preferences(target))
        self.assertEqual(options.experimental_pitch_degrees, 75)
        self.assertTrue(options.experimental_no_recenter)
        self.assertTrue(options.experimental_button_dash)
        self.assertTrue(options.fps)
        self.assertEqual(options.aspect, "auto")
        self.assertFalse(options.menu_route)
        values["experimental_pitch_enabled"] = False
        self.assertIsNone(options_from_preferences(values).experimental_pitch_degrees)
        self.assertEqual(values["experimental_pitch_degrees"], 75)

    def test_gui_start_passes_and_saves_experiments(self):
        values = {**DEFAULTS, "azahar": "azahar.exe", "game": "game.cci", "display_consent": True,
                  "experimental_pitch_enabled": True, "experimental_pitch_degrees": 60,
                  "experimental_no_recenter": True, "experimental_button_dash": True}
        app = launcher.LauncherApp.__new__(launcher.LauncherApp)
        for attribute, key in (("azahar", "azahar"), ("game", "game"), ("fps", "fps"),
                ("mouse", "mouse"), ("aspect", "aspect"), ("sensitivity", "sensitivity"),
                ("sync_layout", "sync_layout"), ("single_screen", "single_screen"),
                ("menu_route", "menu_route"), ("pitch_enabled", "experimental_pitch_enabled"),
                ("pitch_degrees", "experimental_pitch_degrees"),
                ("no_recenter", "experimental_no_recenter"), ("button_dash", "experimental_button_dash")):
            setattr(app, attribute, mock.Mock(get=mock.Mock(return_value=values[key])))
        app.display_consent = True
        app.session = None
        app.events = mock.Mock()
        app.root = mock.Mock()
        app.set_busy = mock.Mock()
        messagebox = mock.Mock()
        with tempfile.TemporaryDirectory() as directory:
            app.preferences = Path(directory) / "preferences.json"
            with mock.patch.dict(sys.modules, {"tkinter": SimpleNamespace(messagebox=messagebox)}), \
                    mock.patch.object(launcher, "validate_options"), \
                    mock.patch.object(launcher, "LauncherSession") as factory:
                app.start()
            options = factory.call_args.args[0]
            self.assertEqual(options, options_from_preferences(load_preferences(app.preferences)))
            self.assertEqual(options.experimental_pitch_degrees, 60)
            self.assertTrue(options.experimental_no_recenter)
            self.assertTrue(options.experimental_button_dash)
            factory.return_value.start.assert_called_once_with()
            messagebox.showerror.assert_not_called()
            app.set_busy.assert_called_once_with(True)


if __name__ == "__main__":
    unittest.main()
