# KIU Launcher

An enhancement launcher for **Kid Icarus: Uprising** on Windows, featuring true 60 FPS, perspective widescreen, and preview native mouse aiming.
It works with official Azahar builds. No game or emulator is bundled; project code is licensed under MIT.

Only the **USA v0** build is supported: Title ID `0004000000030100`, product code `CTR-P-AKDE`.
The launcher verifies the complete decrypted CCI and code hashes before launch. Other regions, updates, encrypted images, and modified images are rejected.
See the [build manifest](manifests/title.json) for the exact supported image.

## Getting started

1. For a packaged Windows preview build, extract the entire `kiu-launcher-windows-preview.zip` and run `KIU Launcher.exe`. Python is not required for the packaged build; keep its `_internal` folder alongside the executable. To run from source, see [Development](docs/DEVELOPMENT.md).
2. Select the official `azahar.exe` and your own decrypted game image (`.cci` or `.3ds`).
3. The default is **single-screen fullscreen with Auto aspect**: the game projection matches the primary monitor's aspect ratio and fills the screen. You can instead select original 5:3, 16:9, or 21:9, with borders where needed. Disable single-screen fullscreen to use the dual-screen Custom layout.
4. Keep display configuration synchronization enabled. Close all Azahar instances, then approve saving the display preset when prompted on first use. The launcher verifies the game, saves the layout, starts Azahar, and installs the selected enhancements. Mouse aiming activates automatically during battle while the game window has focus.
5. Use **Stop and restore enhancements** to stop mouse input and restore patches installed by the current session. It does **not** close Azahar or undo the saved display preset.

The launcher remembers its own local preferences. Full-image verification and the first mouse-memory lookup can take some time; Stop can cancel startup.
The launcher UI and most linked documentation are currently in Chinese.

Do not run another GDB client or continuously applied cheats for the same patches alongside the launcher. The default debugger port, `24689`, must be free and must not be exposed to an untrusted network.

## Screen controls and experimental menus

Single-screen fullscreen uses Azahar's native SingleScreen layout and fullscreen launch option; it is **not** menu injection.
Azahar's native **F9** shortcut switches between the upper and lower screens; use your own binding if you changed it. **F11** toggles fullscreen.
**Pause mouse aiming with F4 before switching to the lower screen, then resume it after returning to the upper screen.** The mouse driver cannot currently detect Azahar's live screen-swap state.

The separate **menu-to-upper-screen injection** checkbox is experimental, off by default, and selected **before starting** a session. It uses an in-game render-pass hook, not F9 or a framebuffer copy. Stop and restore removes it only if the current session installed it.
It does not rearrange 2D menus or remap touch input. Some pages have unused black areas, and the physical lower screen can retain an older frame. If you need normal lower-screen touch interaction, stop, close Azahar, disable injection, and start again.

## Display settings and safety

Saving a display preset requires your approval and all Azahar instances to be closed. It does not change performance settings, key bindings, or saves.

- Single-screen mode selects SingleScreen for this game, changes shared upper-screen stretch/padding settings, and disables lower-screen stretching. Existing Custom geometry is preserved.
- Dual-screen mode changes the shared Custom upper-screen width to match the selected aspect, while preserving lower-screen geometry.
- These shared display settings can affect other games. Consent to an older preset does not automatically authorize the new one. Stop or launcher exit does not restore the preset.
- To return to the original display aspect, close Azahar, select original 5:3, and start again. This reapplies that aspect; it is not a restore of every previous display setting.

Single-screen borders are calculated using the primary monitor's physical dimensions. After changing monitors or leaving fullscreen, stop and restart to recalibrate; air-battle aiming stays inactive if the canvas dimensions no longer match. Dual-screen Custom geometry does not automatically fit a resized window.

Air-battle mouse aiming currently requires a recognizable **OpenGL upper-screen viewport**. An unknown or clipped viewport, or disabled display synchronization, leaves air aiming inactive; it never falls back to mapping the entire desktop. Ground-battle relative aiming does not depend on this viewport.

Stereoscopic display must be **Off**. Other modes are rejected, not changed automatically. Do not change layouts, rotate screens, or enable 3D while enhancements are running. Native screen swapping remains available using the F4 procedure above.
To change options or aspect, stop enhancements, close Azahar yourself, and start again. The launcher does not take over a session that has already been stopped, close the emulator, or uninstall pre-existing patches.

F4 pauses/resumes the mouse only; it does not undo 60 FPS or widescreen patches and is not a substitute for stopping before a savestate load.
The mouse mailbox still has timing races. Savestates can restore hooks/gates or invalidate the host-memory mapping; **automatic savestate recovery is not implemented**.
**Stop enhancements before loading a savestate.** If unknown or mixed patch states are detected, restart the game completely rather than forcing an overwrite.

## Preview status

| Feature | Current status |
| --- | --- |
| True 60 FPS and 16:9 / 21:9 battle widescreen | Implemented; emulator runtime verification is recorded |
| Windows launcher | Preview: game verification, launch, installation, and session-owned restoration |
| Single-screen fullscreen / aspect synchronization | Preview: consent-based persistent presets; offline regressions, with additional in-game validation still needed |
| Native mouse aiming | Preview: camera rotation on the ground, reticle positioning in the air |
| Menu-to-upper-screen injection | Opt-in experiment; full-width 2D layout and touch interaction remain unfinished |
| Linux/macOS launcher and mouse backends | Not implemented; platform-neutral core and interfaces are retained |

UI, packaging, and offline tests do not establish full-game compatibility, long-session input reliability, or support for every Azahar version.
Basic runtime checks cover patch installation, mouse standby in menus, and restoration of original bytes after stopping; they are not a complete playthrough or validation of every new display mode.

## Development and cheats

Run the source launcher with `python run_launcher.py`, or use the installed `kiu-launcher` entry point.
With Python 3.11+ installed, run these commands from the repository root (the launcher currently requires Windows):

```sh
python -m pip install -e .
python -m unittest discover -s tests
python run_launcher.py
```

The CLI, source package, and wheel are companion development tools. See [Development](docs/DEVELOPMENT.md) for setup, verification, and packaging.
For static enhancements, separate Enable/Restore cheat codes are also available; see [Cheat codes](docs/GATEWAY_CHEATS.md).
Unchecking an option does not by itself undo runtime writes. Do not let cheats and the launcher manage the same patch simultaneously.

[Current status](STATUS.md) · [Technical knowledge](docs/KNOWLEDGE.md) · [Distribution architecture](docs/ARCHITECTURE.md)

Distribution artifacts contain no ROMs, game assets, extracted game code, saves, Azahar binaries, or private settings.
MIT covers this project's code only. Third-party components retain their own licenses, with relevant notices included in the player package.
