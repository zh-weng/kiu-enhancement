"""Preferences never touch emulator settings or require a game image."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from kiu.launcher import checked_preferences, load_preferences, save_preferences, DEFAULTS


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


if __name__ == "__main__":
    unittest.main()
