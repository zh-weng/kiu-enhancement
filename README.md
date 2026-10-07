# KIU Launcher

An enhancement launcher for **Kid Icarus: Uprising** on Windows and Linux/XWayland, featuring true 60 FPS, perspective widescreen, and preview native mouse aiming.
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
Use Azahar's configured **Swap Screens** shortcut to switch between upper and lower screens; check its Hotkeys settings, since bindings vary by profile/version. **F11** toggles fullscreen in the supported profile.
**Pause mouse aiming (Windows F4 / Linux F12) before switching to the lower screen, then resume it after returning to the upper screen.** The mouse driver cannot currently detect Azahar's live screen-swap state.

The separate **menu-to-upper-screen injection** checkbox is experimental, off by default, and selected **before starting** a session. It uses an in-game render-pass hook, not F9 or a framebuffer copy. Stop and restore removes it only if the current session installed it.
It does not rearrange 2D menus or remap touch input. Some pages have unused black areas, and the physical lower screen can retain an older frame. If you need normal lower-screen touch interaction, stop, close Azahar, disable injection, and start again.

## Display settings and safety

Saving a display preset requires your approval and all Azahar instances to be closed. It does not change performance settings, key bindings, or saves.

- Single-screen mode selects SingleScreen for this game, changes shared upper-screen stretch/padding settings, and disables lower-screen stretching. Existing Custom geometry is preserved.
- Dual-screen mode changes the shared Custom upper-screen width to match the selected aspect, while preserving lower-screen geometry.
- These shared display settings can affect other games. Consent to an older preset does not automatically authorize the new one. Stop or launcher exit does not restore the preset.
- To return to the original display aspect, close Azahar, select original 5:3, and start again. This reapplies that aspect; it is not a restore of every previous display setting.

Single-screen borders are calculated using the primary monitor's physical dimensions. After changing monitors or leaving fullscreen, stop and restart to recalibrate; air-battle aiming stays inactive if the canvas dimensions no longer match. Dual-screen Custom geometry does not automatically fit a resized window.

On Windows, air-battle mouse aiming requires a recognizable **OpenGL upper-screen viewport**. Linux supports the verified fullscreen X11/XWayland canvas, including Vulkan; windowed geometry is deliberately not guessed. An unknown or clipped viewport, or disabled display synchronization, leaves air aiming inactive. Ground-battle relative aiming does not depend on this viewport.

Stereoscopic display must be **Off**. Other modes are rejected, not changed automatically. Do not change layouts, rotate screens, or enable 3D while enhancements are running. Native screen swapping remains available using the mouse-pause procedure above.
To change options or aspect, stop enhancements, close Azahar yourself, and start again. The launcher does not take over a session that has already been stopped, close the emulator, or uninstall pre-existing patches.

The mouse toggle pauses/resumes aiming only; it does not undo 60 FPS or widescreen patches and is not a substitute for stopping before a savestate load.
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
| Linux launcher and mouse backend | Source preview for X11/XWayland; no system security-policy changes |
| macOS launcher and mouse backend | Not implemented; platform-neutral core and interfaces are retained |

UI, packaging, and offline tests do not establish full-game compatibility, long-session input reliability, or support for every Azahar version.
Basic runtime checks cover patch installation, mouse standby in menus, and restoration of original bytes after stopping; they are not a complete playthrough or validation of every new display mode.

## Development and cheats

Run the source launcher with `python run_launcher.py`, or use the installed `kiu-launcher` entry point.
With Python 3.11+ installed, run these commands from the repository root:

```sh
python -m pip install -e .
python -m unittest discover -s tests
python run_launcher.py
```

### Steam Deck / Linux source preview

Configure the profile once in Desktop Mode with Python/Tk, `libX11` and `libXi` installed. Qt Azahar runs under XWayland (`xcb`), including Gaming Mode's Gamescope; native Qt Wayland is unsupported. Use an official Azahar AppImage compatible with repeated GDB connections (2126.1.1 is the deployment baseline); the old 2123.2 installation failed after its first cold-start debugger disconnect.

```sh
python3 -m venv .venv
.venv/bin/python -m pip install -e .
bash scripts/start-linux.sh
```

This script uses a separate profile at `local/profile/config`, leaving the existing EmuDeck configuration alone. To retain existing controls/storage settings, copy `~/.config/azahar-emu/qt-config.ini` to `local/profile/config/azahar-emu/qt-config.ini` before first use. Choose your own Azahar and exact decrypted game in the launcher, then approve its display preset. Auto aspect matches the Deck's 1280×800 display (16:10); menu injection stays off by default.

For Gaming Mode, add the headless entry to Steam after saving the profile:

```sh
chmod +x scripts/start-steamdeck.sh
steamos-add-to-steam "$PWD/scripts/start-steamdeck.sh"
```

Rename the new non-Steam entry as desired, then play it from Gaming Mode. It opens only Azahar, not the desktop Launcher. Startup diagnostics are in `local/steamdeck.log`. A saved, authorized display preset is required; this entry does not grant consent silently.

Choose Steam Input's **Gamepad with Mouse Trackpad** template for the entry. Suggested back buttons: **R4 = F12** (aim toggle), **L4 = the configured Swap Screens hotkey**. The tested dedicated Azahar profile uses **Ctrl+Tab** for screen swapping. Pause aiming before switching down, then resume on returning to the upper screen. Steam's default gamepad template does not provide the required mouse trackpad mapping.

Linux uses **F12** for mouse aiming, because stock Azahar uses F4 to pause emulation. Run Azahar from this launcher: normal Linux process-memory permissions permit access to its own child, not an unrelated emulator started elsewhere. No root, read-only-root disablement or ptrace-policy change is needed. Savestate recovery and mailbox timing limitations remain unchanged.

Gaming Mode checks cover direct Steam startup, patch installation, Gamescope focus and the 1280×800 fullscreen canvas, synthetic relative XI2 input, and session-owned restoration. They do not prove physical trackpad behavior, battle accuracy, or long-session reliability.

The CLI, source package, and wheel are companion development tools. See [Development](docs/DEVELOPMENT.md) for setup, verification, and packaging.
For static enhancements, separate Enable/Restore cheat codes are also available; see [Cheat codes](docs/GATEWAY_CHEATS.md).
Unchecking an option does not by itself undo runtime writes. Do not let cheats and the launcher manage the same patch simultaneously.

[Current status](STATUS.md) · [Technical knowledge](docs/KNOWLEDGE.md) · [Distribution architecture](docs/ARCHITECTURE.md)

Distribution artifacts contain no ROMs, game assets, extracted game code, saves, Azahar binaries, or private settings.
MIT covers this project's code only. Third-party components retain their own licenses, with relevant notices included in the player package.
