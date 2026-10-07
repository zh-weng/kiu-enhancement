"""Headless Linux player entry for a Steam Gaming Mode shortcut.

Use the desktop Launcher once to select the user-owned game and authorize its
display preset. This entry reads those preferences without opening a Tk window.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import signal
import sys

from .launcher import ASPECTS, load_preferences, settings_path
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
    options = LauncherOptions(
        azahar=Path(values["azahar"]), game=Path(values["game"]),
        fps=values["fps"], aspect=ASPECTS[values["aspect"]], mouse=values["mouse"],
        sensitivity=values["sensitivity"], sync_layout=values["sync_layout"],
        single_screen=values["single_screen"], menu_route=values["menu_route"],
    )
    validate_options(options)
    return options


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--settings", type=Path, default=None,
                        help="the local preferences saved by the desktop Launcher")
    args = parser.parse_args(argv)
    try:
        if sys.platform != "linux":
            raise RuntimeError("This headless Gaming Mode entry requires Linux")
        path = args.settings or settings_path()
        try:
            options = load_options(path)
        except FileNotFoundError as exc:
            raise RuntimeError("No saved preferences; configure this profile with the desktop KIU Launcher first") from exc
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
