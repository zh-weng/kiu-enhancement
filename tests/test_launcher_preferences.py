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
            self.assertEqual(json.loads(target.read_text())["version"], 1)
            self.assertEqual([p.name for p in target.parent.iterdir()], ["preferences.json"])

    def test_invalid_values_are_refused(self):
        for update in ({"version": 2}, {"fps": "yes"}, {"mouse": 1}, {"game": "bad\0path"},
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


if __name__ == "__main__":
    unittest.main()
