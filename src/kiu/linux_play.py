"""Headless Linux player entry for a Steam Gaming Mode shortcut.

Use the desktop Launcher once to select the user-owned game and authorize its
display preset. This entry reads those preferences without opening a Tk window.
"""
from __future__ import annotations

import argparse
from dataclasses import replace
from pathlib import Path
import signal
import sys

from .launcher import load_preferences, options_from_preferences, settings_path
from .launcher_core import LauncherOptions, LauncherSession, validate_options


def load_options(path: Path) -> LauncherOptions:
    values = load_preferences(path)
    if values["sync_layout"] and not values["display_consent"]:
        raise RuntimeError(
            "Display preset has not been authorized. Open the desktop KIU Launcher "
            "and approve this profile's display preset before using Gaming Mode."
        )
    if not values["azahar"] or not values["game"]:
        raise ValueError("Select Azahar and your own game in the desktop KIU Launcher first")
    options = options_from_preferences(values)
    validate_options(options)
    return options


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=None,
                        help="the local preferences saved by the desktop Launcher")
    parser.add_argument("--experimental-pitch-degrees", type=float, default=None,
                        help="opt-in ground pitch boundary; rendered range remains experimental (1-89 degrees)")
    parser.add_argument("--experimental-no-recenter", action="store_true",
                        help="opt-in cancellation of ground battle idle camera recentering; unvalidated preview")
    parser.add_argument("--experimental-button-dash", action="store_true",
                        help="opt-in HID Debug-button dash instead of stick flick; bind button_debug in Azahar")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux":
            raise RuntimeError("This headless Gaming Mode entry requires Linux")
        path = args.settings or settings_path()
        try:
            options = load_options(path)
        except FileNotFoundError as exc:
            raise RuntimeError("No saved preferences; configure this profile with the desktop KIU Launcher first") from exc
        if args.experimental_pitch_degrees is not None:
            options = replace(options, experimental_pitch_degrees=args.experimental_pitch_degrees)
            validate_options(options)
        if args.experimental_no_recenter:
            options = replace(options, experimental_no_recenter=True)
            validate_options(options)
        if args.experimental_button_dash:
            options = replace(options, experimental_button_dash=True)
            validate_options(options)
        session = LauncherSession(options, lambda text: print(f"[kiu] {text}", flush=True))
        previous = {}

        def stop(_signum, _frame):
            # This stops input and restores only this session's patches. It
            # never terminates Azahar or rolls back its saved configuration.
            session.stop(restore=True)

        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                previous[signum] = signal.signal(signum, stop)
            session.run()
        finally:
            for signum, handler in previous.items():
                signal.signal(signum, handler)
        return 1 if session.error else 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"kiu: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
