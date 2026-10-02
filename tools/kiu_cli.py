"""Small companion for stock Azahar. No emulator configuration is overwritten."""
from __future__ import annotations

import argparse
import hashlib
import json
import sys
from contextlib import contextmanager
from pathlib import Path

# Existing patch modules remain runnable as individual development tools.
sys.path.insert(0, str(Path(__file__).resolve().parent))

TITLE_ID = "0004000000030100"
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"


def verify_build(path: Path) -> str:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    if digest != CODE_SHA256:
        raise RuntimeError(f"unsupported code.bin SHA-256: {digest}; USA v0 required")
    return digest


def modules():
    import patch_60fps as fps
    import patch_widescreen as wide
    import patch_aim_request as aim
    import pin_reticle as pin
    import menu_render_pass as menu
    return {"fps": fps, "wide": wide, "aim": aim, "pin": pin, "menu": menu}


@contextmanager
def guest_session(port: int):
    from gdbclient import GdbClient
    gdb = GdbClient(port=port, timeout=20)
    try:
        stop = gdb.send("?")
        if not stop.startswith(("S", "T")):
            raise RuntimeError(f"GDB did not report a stopped guest: {stop!r}")
        yield gdb
    finally:
        try:
            gdb.cont_all()
        finally:
            gdb.sock.close()


def checked_states(gdb, selected, loaded):
    """Preflight the whole selection before any mutation, never address-only."""
    states = {name: loaded[name].status(gdb) for name in selected}
    for name, state in states.items():
        if state["state"] not in ("stock", "installed"):
            raise RuntimeError(f"{name}: unknown/mixed bytes; refusing to overwrite")
        if state["state"] == "installed" and state.get("hook_matches") is False:
            raise RuntimeError(f"{name}: another hook version is installed")
        if name == "fps":
            expected = 0 if state["state"] == "installed" else 2
            if state["interval"] != expected:
                raise RuntimeError("fps: scheduler interval disagrees with code state; restart game")
    return states


def runtime_command(args):
    loaded = modules()
    if args.command == "apply":
        loaded["wide"].aspect_scale(args.aspect)
        loaded["wide"].bottom_aspect_scale(args.aspect)
    selected = ["fps", "wide"]
    if args.command in ("status", "restore") or args.mouse_hooks:
        selected += ["aim", "pin"]
    if args.command in ("status", "restore") or args.menu_route:
        selected += ["menu"]
    with guest_session(args.port) as gdb:
        if args.command == "status":
            return {name: loaded[name].status(gdb) for name in selected}
        states = checked_states(gdb, selected, loaded)
        result = {}
        order = list(reversed(selected)) if args.command == "restore" else selected
        for name in order:
            module = loaded[name]
            if args.command == "restore":
                result[name] = ((module.revert(gdb, resume=False) if name in ("aim", "pin")
                                 else module.revert(gdb)) if states[name]["state"] == "installed"
                                else {"already_stock": True})
            elif states[name]["state"] == "stock":
                result[name] = (module.apply(gdb, args.aspect) if name == "wide"
                                else module.apply(gdb, resume=False) if name in ("aim", "pin")
                                else module.apply(gdb))
            elif name == "wide":
                result[name] = module.set_aspect(gdb, args.aspect)
            else:
                result[name] = {"already_installed": True}
        return result


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    sub = result.add_subparsers(dest="command", required=True)
    check = sub.add_parser("verify-build", help="SHA-256 check your own extracted code.bin")
    check.add_argument("code_bin", type=Path)
    for command in ("apply", "restore", "status"):
        item = sub.add_parser(command, help=f"{command} verified runtime patches via local GDB")
        item.add_argument("--port", type=int, default=24689)
        if command == "apply":
            item.add_argument("--aspect", type=float, default=16 / 9)
            item.add_argument("--mouse-hooks", action="store_true", help="install preview guest hooks, no input")
            item.add_argument("--menu-route", action="store_true", help="EXPERIMENTAL, does not remap touch")
        else:
            item.set_defaults(mouse_hooks=False, menu_route=False)
    aim = sub.add_parser("aim", help="Windows preview mouse helper; run apply separately for FPS/widescreen")
    aim.add_argument("arguments", nargs=argparse.REMAINDER, help="mouse options after --; see aim -- --help")
    export = sub.add_parser("export-cheats", help="generate enable/restore codes, no ROM required")
    export.add_argument("arguments", nargs=argparse.REMAINDER, help="export options after --")
    return result


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        if args.command == "verify-build":
            print(json.dumps({"title_id": TITLE_ID, "code_sha256": verify_build(args.code_bin)}))
        elif args.command in ("aim", "export-cheats"):
            options = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
            if args.command == "aim":
                if sys.platform != "win32":
                    raise RuntimeError("the current mouse helper requires Windows; cheats do not")
                from native_fps_aim import main as run
            else:
                from export_gateway_cheats import main as run
            return run(options)
        else:
            print(json.dumps(runtime_command(args), indent=2))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"kiu: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
