"""Windows preview adapter for native mouse aiming, mailbox ABI 1.

Ground mode feeds raw relative deltas into the game's rotation-request
consumer. Air mode maps the physical cursor onto the game's absolute reticle
position; ``--map relative`` offers an explicit alternative. Auto mode uses
guest hook activity and the recorded controller, not a fixed heap address.

GDB installs and verifies guest hooks. Subsequent mailbox updates use a
nonce-verified process-memory mapping to avoid per-update GDB stops. The
current host read-modify-write can race with guest consume-and-clear; loading
a savestate can invalidate the mapping. Stop and restart the helper after a
savestate load. Technical findings and limits live in docs/KNOWLEDGE.md.

F4 toggles aiming while the emulator has focus. ESC quits.
"""
from __future__ import annotations

import argparse
import ctypes
import ctypes.wintypes
import json
import math
import os
import struct
import sys
import threading
import time
from pathlib import Path
from typing import Callable

from ...gdbclient import GdbClient
from .guest_memory import GuestMemory, listening_pid, nonce_bytes
from .win_raw_mouse import RawMouseInput
from ...aim_protocol import validate_mailboxes
from ...aim_core import as_float, map_point, sense

from . import win_window as window
from ...patches import patch_aim_request as aim
from ...patches import pin_reticle as pin

user32 = ctypes.windll.user32
try:                       # physical pixels, so the map is not DPI-scaled
    ctypes.windll.shcore.SetProcessDpiAwareness(2)
except BaseException:
    pass

SM_XVIRTUALSCREEN, SM_YVIRTUALSCREEN = 76, 77
SM_CXVIRTUALSCREEN, SM_CYVIRTUALSCREEN = 78, 79
CACHE = Path(os.environ.get("LOCALAPPDATA", str(Path.home() / "AppData/Local"))) / "KIUEnhancements/guest_anchor.json"


def anchor_memory(gdb: GdbClient, verbose: bool = True, port: int = 24689,
                  expected_pid: int | None = None) -> GuestMemory:
    """Find where the mailboxes live in the host, reusing a cached offset.

    The cached offset is always re-verified against a fresh nonce.  It does go
    stale: loading a savestate moved it once during development, and a stale
    offset reads plausible-looking rubbish rather than failing.
    """
    memory = GuestMemory.attach(port=port)
    try:
        if expected_pid is not None and memory.pid != expected_pid:
            raise RuntimeError(f"emulator memory belongs to PID {memory.pid}, "
                               f"expected PID {expected_pid}; refusing to anchor")
        scratch = aim.MAILBOX_VA + 0x40
        nonce = nonce_bytes(int(time.time()) & 0xFFFF)
        gdb.write_mem(scratch, nonce)
        gdb.cont_all()

        try:
            cached = json.loads(CACHE.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            cached = {}
        if not isinstance(cached, dict):
            cached = {}
        offset = cached.get("offset")
        if (cached.get("pid") == memory.pid and type(offset) is int
                and 0 <= offset < (1 << 64)):
            memory._offset = offset
            try:
                verified = memory.verify(scratch, nonce)
            except (OSError, ValueError, OverflowError):
                verified = False
            if verified:
                if verbose:
                    print(f"[aim] cached anchor, host offset 0x{memory._offset:x}")
                return memory
            memory._offset = None
            if verbose:
                print("[aim] the cached anchor is stale; re-scanning")

        if verbose:
            print("[aim] scanning the emulator for the mailboxes (takes ~30s) ...")
        started = time.perf_counter()
        host = memory.anchor(scratch, nonce)
        if verbose:
            print(f"[aim] anchored at host 0x{host:x} in {time.perf_counter()-started:.0f}s")
        try:
            CACHE.parent.mkdir(parents=True, exist_ok=True)
            CACHE.write_text(json.dumps({"pid": memory.pid, "offset": memory._offset}),
                             encoding="utf-8")
        except OSError as exc:
            if verbose:
                print(f"[aim] could not save the optional anchor cache: {exc}")
        return memory
    except BaseException:
        memory.close()
        raise


def virtual_screen() -> tuple[int, int, int, int]:
    return (user32.GetSystemMetrics(SM_XVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_YVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CXVIRTUALSCREEN),
            user32.GetSystemMetrics(SM_CYVIRTUALSCREEN))


def cursor_position() -> tuple[int, int]:
    point = ctypes.wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(point))
    return point.x, point.y


def _check_gdb_mailbox(gdb: GdbClient, module, name: str) -> None:
    expected = struct.pack("<II", module.MAGIC, module.VERSION)
    if gdb.read_mem(module.MAILBOX_VA, len(expected)) != expected:
        raise RuntimeError(f"{name}: mailbox ABI mismatch; refusing to write input gates")


def mapped_rect(kind: str, span: float, aspect: float,
                pid: int | None = None) -> tuple[float, float, float, float]:
    """The desktop rectangle that covers the whole top screen.

    Its aspect is forced to the top screen's, so horizontal and vertical
    movement get the same scale; stretching it to the desktop's shape instead
    would make the reticle faster in one axis than the other.
    """
    if kind == "viewport":
        # Azahar's side-by-side layout puts the 400x240 and 320x240 screens at
        # equal height, so the pair is 3h wide and the top screen takes 5/9 of
        # it.  Derived, not measured -- another layout will not match, which is
        # why --map screen is the default.
        hwnd = window.find_azahar(pid=pid)
        ox, oy, cw, ch = window.client_rect(hwnd)
        height = cw / 3.0
        width = cw * 5.0 / 9.0
        top = oy + 20 + max(0.0, (ch - 46 - height) / 2.0)
        return (float(ox), top, width, height)

    x, y, w, h = virtual_screen()
    width = w * span
    height = width * aspect
    if height > h:                     # never taller than the desktop
        height = h * span
        width = height / aspect
    return (x + (w - width) / 2.0, y + (h - height) / 2.0, width, height)


class _Stopped(Exception):
    """Cooperative stop, including before raw input has started."""


def main(argv: list[str] | None = None, *, stop_event: threading.Event | None = None,
         status_callback: Callable[[str], None] | None = None,
         auto_activate: bool = False, wait_for_battle: bool = False,
         expected_pid: int | None = None) -> int:
    """Run the CLI or a cancellable GUI worker without changing the guest ABI.

    GUI callers can wait in menus and activate on a focused, live battle. F4
    remains the user's desired-state switch: explicitly switching off survives
    later focus changes. The existing process-memory anchor scan is blocking;
    a stop requested during that scan is honored as soon as it returns.
    Status callbacks are called on this worker thread, not on the GUI thread.
    A launcher can bind injection to its own Azahar PID. Port ownership is
    checked before/after connecting, and the memory handle must match it.
    """
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=24689)
    parser.add_argument("--mode", choices=("auto", "look", "point"), default="auto",
                        help="look drives camera rotation (ground battle), "
                             "point drives the reticle (air battle)")
    parser.add_argument("--sensitivity", type=float, default=0.0025,
                        help="look mode: radians of view rotation per mouse count")
    parser.add_argument("--map", dest="mapping", default="screen",
                        choices=("screen", "viewport", "relative"),
                        help="point mode: how the cursor becomes a reticle "
                             "position.  screen maps the desktop onto the top "
                             "screen, centre to centre; viewport maps the "
                             "emulator's own top-screen rectangle so the cursor "
                             "sits on the reticle; relative integrates raw "
                             "deltas the way this used to")
    parser.add_argument("--map-span", type=float, default=1.0,
                        help="with --map screen, the fraction of the desktop "
                             "that spans the full top screen; below 1.0 the "
                             "reticle moves further per centimetre of mouse")
    parser.add_argument("--point-sensitivity", type=float, default=0.4,
                        help="point mode: screen pixels per mouse count.  The "
                             "reticle is bounded by the 400 px screen, so travel "
                             "past the edge is discarded; 0.08 puts a screen "
                             "width at about 16 cm of mouse at 800 dpi, where "
                             "0.35 -- the old default -- put it at 3.6 cm")
    parser.add_argument("--hz", type=float, default=250.0)
    parser.add_argument("--invert-x", action="store_true")
    parser.add_argument("--invert-y", action="store_true",
                        help="flip vertical look; the default is mouse down "
                             "looks down")
    args = parser.parse_args(argv)
    if expected_pid is not None and (type(expected_pid) is not int or expected_pid <= 0):
        raise ValueError("expected_pid must be a positive integer")

    last_status = None

    def notify(status: str) -> None:
        nonlocal last_status
        if status == last_status:
            return
        last_status = status
        if status_callback is not None:
            try:
                status_callback(status)
            except Exception:
                # A closed GUI or a display callback must not skip cleanup.
                pass

    def check_stop() -> None:
        if stop_event is not None and stop_event.is_set():
            raise _Stopped

    def require_owner() -> None:
        if expected_pid is not None:
            owner = listening_pid(args.port)
            if owner != expected_pid:
                raise RuntimeError(f"GDB port {args.port} belongs to PID {owner}, "
                                   f"expected PID {expected_pid}; refusing injection")

    def pause(seconds: float) -> None:
        if stop_event is None:
            time.sleep(seconds)
        elif stop_event.wait(seconds):
            raise _Stopped

    if stop_event is not None and stop_event.is_set():
        notify("complete")
        return 0

    lock = threading.Lock()
    live = {"on": False, "quit": False, "dx": 0.0, "dy": 0.0,
            "wanted": auto_activate, "battle_active": False,
            "px": 200.0, "py": 120.0, "counts": 0,
            # Per aiming session, so a fast flick and a slow drag of the same
            # physical distance can be compared directly rather than by feel.
            "session_counts": 0, "session_travel": 0.0, "session_start": 0.0,
            "session_demanded": 0.0, "mode": args.mode}

    def focused() -> bool:
        try:
            return user32.GetForegroundWindow() == window.find_azahar(
                pid=memory.pid if memory is not None else None)
        except BaseException:
            return False

    def on_escape() -> None:
        with lock:
            live["quit"] = True

    notify("connecting")
    require_owner()
    gdb = GdbClient(port=args.port, timeout=20)
    gdb_owner_verified = expected_pid is None
    memory = None
    mailboxes_validated = False
    source = None
    was_on = False
    try:
        check_stop()
        require_owner()
        gdb_owner_verified = True
        check_stop()
        gdb.send("?")
        components = (("rotation-request", aim), ("reticle", pin))
        states = {name: module.status(gdb) for name, module in components}
        for name, module in components:
            state = states[name]
            if state["state"] not in ("stock", "installed"):
                raise RuntimeError(f"{name}: unknown/mixed hook bytes; refusing to overwrite")
            if state["state"] == "installed":
                if not state["hook_matches"]:
                    raise RuntimeError(f"a different build of the {name} hook is installed")
                _check_gdb_mailbox(gdb, module, name)
        for name, module in components:
            if states[name]["state"] == "stock":
                print(f"[aim] installing the {name} hook")
                module.apply(gdb, resume=False)
                _check_gdb_mailbox(gdb, module, name)
        aim.set_gate(gdb, 0)
        pin.set_mode(gdb, 0)
        gdb.cont_all()

        check_stop()
        notify("anchoring")
        memory = anchor_memory(gdb, port=args.port, expected_pid=expected_pid)
        if expected_pid is not None and memory.pid != expected_pid:
            raise RuntimeError(f"anchored memory belongs to PID {memory.pid}, "
                               f"expected PID {expected_pid}; refusing input writes")
        validate_mailboxes(memory)
        mailboxes_validated = True
        gdb.sock.close()
        check_stop()

        def clear_inputs() -> None:
            # A stale/foreign header must not receive even a cleanup write.
            validate_mailboxes(memory)
            memory.write(aim.MAILBOX_VA + aim.MB_GATE, struct.pack("<I", 0))
            memory.write(aim.MAILBOX_VA + aim.MB_YAW, struct.pack("<ff", 0.0, 0.0))
            memory.write(pin.MAILBOX_VA + pin.MB_MODE, struct.pack("<I", 0))

        def dimensions() -> tuple[float, float]:
            return (
                as_float(struct.unpack("<I", memory.read(pin.MAILBOX_VA + pin.MB_W, 4))[0]),
                as_float(struct.unpack("<I", memory.read(pin.MAILBOX_VA + pin.MB_H, 4))[0]),
            )

        def valid_dimensions(size: tuple[float, float]) -> bool:
            return all(math.isfinite(value) and value > 0.0 for value in size)

        # Settle on a mode before starting, but keep watching: a session that
        # runs from an air battle into a ground one has to follow it.
        seen_calls, sensed = sense(memory)
        last_activity = float("-inf")
        saw_battle = False
        if args.mode == "auto" and not wait_for_battle:
            for _ in range(20):
                check_stop()
                calls, sensed = sense(memory)
                if calls != seen_calls:
                    last_activity = time.perf_counter()
                    seen_calls = calls
                    saw_battle = True
                    break
                seen_calls = calls
                pause(0.1)
            else:
                print("[aim] the reticle hook is not being called -- no battle "
                      "is running; assuming " + sensed)
        live["mode"] = sensed if args.mode == "auto" else args.mode
        mode = live["mode"]
        width, height = dimensions()
        centre = (width / 2.0, height / 2.0)
        if live["mode"] == "point" and not valid_dimensions((width, height)) and not wait_for_battle:
            raise RuntimeError("the reticle hook has not seen a frame yet; "
                               "is a battle actually running?")

        # Positive yaw request turns the view left, so a rightward mouse is negative.
        sx = args.sensitivity * (1.0 if args.invert_x else -1.0)
        # A positive pitch request raises P+0x2B0, but the view pitches *down*
        # with it -- the stored angle is not the direction the camera looks, so
        # this sign comes from watching the screen rather than from the field.
        sy = args.sensitivity * (-1.0 if args.invert_y else 1.0)
        px_x = args.point_sensitivity * (-1.0 if args.invert_x else 1.0)
        px_y = args.point_sensitivity * (-1.0 if args.invert_y else 1.0)

        def on_delta(dx: int, dy: int) -> None:
            with lock:
                if not live["on"]:
                    return
                if live["mode"] == "look":
                    live["dx"] += dx * sx
                    live["dy"] += dy * sy
                    live["session_travel"] += abs(dx * sx) + abs(dy * sy)
                    live["session_demanded"] += abs(dx * sx) + abs(dy * sy)
                elif args.mapping == "relative":
                    before = (live["px"], live["py"])
                    live["px"] = min(max(live["px"] + dx * px_x, 0.0), width)
                    live["py"] = min(max(live["py"] + dy * px_y, 0.0), height)
                    live["session_travel"] += (abs(live["px"] - before[0])
                                               + abs(live["py"] - before[1]))
                    # What the screen bound threw away.  Worth reporting, but
                    # it is not the explanation for "the same mouse distance
                    # moves it less when moved faster" -- measured over 65 s at
                    # 0.4 px/count it was 3%, because back-and-forth motion
                    # accumulates demanded travel without ever reaching an edge.
                    live["session_demanded"] += abs(dx * px_x) + abs(dy * px_y)
                else:
                    # Absolute: the position comes from the cursor in the tick
                    # loop, so a delta here is only worth counting.
                    pass
                live["counts"] += abs(dx) + abs(dy)
                live["session_counts"] += abs(dx) + abs(dy)

        def edge_loss() -> tuple[float, float, float]:
            """Demanded, applied and discarded travel for the current session."""
            demanded = live["session_demanded"]
            travel = live["session_travel"]
            # Both sides are float sums of the same deltas, so when nothing is
            # clamped they differ only by rounding -- which printed as "-0%".
            return demanded, travel, max(0.0, demanded - travel)

        def report_session() -> None:
            if not live["session_counts"]:
                return
            demanded, travel, lost = edge_loss()
            seconds = time.perf_counter() - live["session_start"]
            unit = "rad" if live["mode"] == "look" else "px"
            setting = (args.sensitivity if live["mode"] == "look"
                       else args.point_sensitivity)
            line = (f"[aim] session: {live['session_counts']} counts, "
                    f"{demanded:.1f} {unit} demanded over {seconds:.2f}s "
                    f"at {setting:g} {unit}/count")
            if live["mode"] == "point" and args.mapping != "relative":
                line = (f"[aim] session: {live['session_counts']} counts over "
                        f"{seconds:.2f}s, absolute map -- no accumulation, so "
                        f"no travel to lose")
            elif live["mode"] == "point":
                share = 100.0 * lost / demanded if demanded else 0.0
                line += (f"; {travel:.1f} px applied, {lost:.1f} px "
                         f"({share:.0f}%) discarded at the screen edge")
                if share > 20.0:
                    line += "  <- lower --point-sensitivity"
            print(line)

        def on_f4() -> None:
            with lock:
                if not live["wanted"] and not focused():
                    print("[aim] the emulator window is not focused; not aiming")
                    return
                live["wanted"] = not live["wanted"]
                live["dx"] = live["dy"] = 0.0
                if live["mode"] == "look" or args.mapping == "relative":
                    live["px"], live["py"] = centre
                if live["wanted"]:
                    live["session_counts"] = 0
                    live["session_travel"] = 0.0
                    live["session_demanded"] = 0.0
                    live["session_start"] = time.perf_counter()
                else:
                    report_session()
                    live["on"] = False

        rect = (0.0, 0.0, 1.0, 1.0)
        if args.mapping != "relative" and valid_dimensions((width, height)):
            rect = mapped_rect(args.mapping, args.map_span, height / width, pid=memory.pid)

        check_stop()
        try:
            source = RawMouseInput(on_delta, on_f4=on_f4,
                                   on_escape=on_escape if stop_event is None else None).start(timeout=5.0)
        except RuntimeError as exc:
            # The usual cause is a second copy of this tool holding F4, which
            # surfaces as an opaque WinError 1409 several frames down.
            if "1409" in str(exc.__cause__ or ""):
                raise RuntimeError(
                    "F4 is already registered as a hotkey -- another copy of "
                    "this tool is probably still running; quit it with ESC "
                    "first") from exc
            raise
        if not valid_dimensions((width, height)):
            print("[aim] waiting for a battle; input gates remain off")
        elif live["mode"] == "look":
            print(f"[aim] LOOK (ground): {args.sensitivity:g} rad per count "
                  f"({args.sensitivity*57.2958:.3f} deg)")
        elif args.mapping == "relative":
            print(f"[aim] POINT (air), integrating deltas: "
                  f"{args.point_sensitivity:g} screen px per count, "
                  f"reticle bounded to {width:g}x{height:g}")
        else:
            rx, ry, rw, rh = rect
            print(f"[aim] POINT (air), absolute: desktop rect "
                  f"({rx:.0f},{ry:.0f}) {rw:.0f}x{rh:.0f} maps onto "
                  f"{width:g}x{height:g}, centre to centre "
                  f"({rw/width:.1f} desktop px per reticle px)")
        quit_label = "ESC = quit" if stop_event is None else "stop from the launcher"
        print(f"[aim] {args.hz:g} Hz through process memory. F4 = aim, {quit_label}. "
              "The cursor is not moved and no button is held.")

        period = 1.0 / max(args.hz, 1.0)
        report = time.perf_counter()
        last_sense = float("-inf")
        sensed_calls = seen_calls
        sent = 0
        while True:
            check_stop()
            now = time.perf_counter()
            if now - last_sense >= 0.25:
                validate_mailboxes(memory)
                last_sense = now
                calls, sensed = sense(memory)
                size = dimensions()
                progressed = calls != sensed_calls
                if progressed:
                    last_activity = now
                    saw_battle = True
                active = valid_dimensions(size) and now - last_activity < 1.0
                next_mode = sensed if args.mode == "auto" else args.mode
                if active and (next_mode != live["mode"] or size != (width, height)):
                    # Stand the old injection down before the new one starts,
                    # or a stale gate keeps feeding a mode nobody is in.
                    clear_inputs()
                    width, height = size
                    centre = (width / 2.0, height / 2.0)
                    if args.mapping != "relative":
                        rect = mapped_rect(args.mapping, args.map_span,
                                           height / width, pid=memory.pid)
                    with lock:
                        live["mode"] = next_mode
                        live["on"] = False
                        live["dx"] = live["dy"] = 0.0
                        live["px"], live["py"] = centre
                    was_on = False
                    print(f"[aim] battle type changed -> "
                          f"{'LOOK (ground)' if sensed == 'look' else 'POINT (air)'}")
                with lock:
                    live["battle_active"] = active
                sensed_calls = calls

            if live["battle_active"] and live["mode"] == "point" and args.mapping != "relative":
                cx, cy = cursor_position()
                with lock:
                    live["px"], live["py"] = map_point(rect, width, height, (cx, cy))

            with lock:
                if live["quit"]:
                    if live["on"]:
                        report_session()
                    break
                wanted = live["wanted"]
                active = live["battle_active"]
                dx, dy = live["dx"], live["dy"]
                live["dx"] = live["dy"] = 0.0
                px, py = live["px"], live["py"]

            has_focus = focused() if wanted and active else False
            on = bool(wanted and active and has_focus)
            if wanted and active and not has_focus and not auto_activate:
                # Preserve the CLI's manual re-enable after focus loss.
                with lock:
                    live["wanted"] = False
            with lock:
                live["on"] = on
                if not on:
                    live["dx"] = live["dy"] = 0.0

            if on != was_on:
                validate_mailboxes(memory)
                if on:
                    with lock:
                        live["session_counts"] = 0
                        live["session_travel"] = 0.0
                        live["session_demanded"] = 0.0
                        live["session_start"] = now
                        if args.mapping == "relative" or live["mode"] == "look":
                            live["px"], live["py"] = centre
                            px, py = centre
                    if live["mode"] == "point" and args.mapping != "relative":
                        rect = mapped_rect(args.mapping, args.map_span,
                                           height / width, pid=memory.pid)
                        px, py = map_point(rect, width, height, cursor_position())
                else:
                    report_session()
                if live["mode"] == "look":
                    memory.write(aim.MAILBOX_VA + aim.MB_GATE,
                                 struct.pack("<I", 1 if on else 0))
                    if not on:
                        memory.write(aim.MAILBOX_VA + aim.MB_YAW,
                                     struct.pack("<ff", 0.0, 0.0))
                else:
                    if on:
                        memory.write(pin.MAILBOX_VA + pin.MB_POS_X,
                                     struct.pack("<ff", px, py))
                    memory.write(pin.MAILBOX_VA + pin.MB_MODE,
                                 struct.pack("<I", 2 if on else 0))
                print("[aim] AIMING" if on else "[aim] off")
                was_on = on

            notify("active" if on else "off" if not wanted else
                   "inactive" if saw_battle else "waiting")
            if on:
                validate_mailboxes(memory)
                if live["mode"] == "look" and (dx or dy):
                    # Read/add/write preserves pending travel only if the guest
                    # does not consume between these calls. This exchange is
                    # not atomic; a host/guest race can lose or repeat deltas.
                    pending = struct.unpack(
                        "<ff", memory.read(aim.MAILBOX_VA + aim.MB_YAW, 8))
                    memory.write(aim.MAILBOX_VA + aim.MB_YAW,
                                 struct.pack("<ff", pending[0] + dx, pending[1] + dy))
                    sent += 1
                elif live["mode"] == "point":
                    memory.write(pin.MAILBOX_VA + pin.MB_POS_X,
                                 struct.pack("<ff", px, py))
                    sent += 1

            if now - report >= 5.0:
                with lock:
                    counts = live["counts"]
                    live["counts"] = 0
                if live["mode"] == "look":
                    frames, applied, p = struct.unpack(
                        "<III", memory.read(aim.MAILBOX_VA + aim.MB_FRAMES, 12))
                    tail = f"consumed={frames} applied={applied} P=0x{p:08x}"
                else:
                    calls, applied = struct.unpack(
                        "<II", memory.read(pin.MAILBOX_VA + pin.MB_CALLS, 8))
                    ax, ay = struct.unpack(
                        "<ff", memory.read(pin.MAILBOX_VA + pin.MB_ACT_X, 8))
                    demanded, _, lost = edge_loss()
                    share = 100.0 * lost / demanded if demanded else 0.0
                    tail = (f"clamp calls={calls} applied={applied} "
                            f"commanded=({px:.0f},{py:.0f}) actual=({ax:.0f},{ay:.0f})")
                    if args.mapping == "relative":
                        tail += f" edge loss {share:.0f}%"
                print(f"[aim] {'on ' if on else 'off'} packets={sent} "
                      f"counts={counts} {tail}")
                sent, report = 0, now
            pause(period)
    except (KeyboardInterrupt, _Stopped):
        pass
    finally:
        if source is not None:
            try:
                source.stop()
            except BaseException:
                pass
        if memory is not None:
            try:
                if mailboxes_validated:
                    validate_mailboxes(memory)
                    memory.write(aim.MAILBOX_VA + aim.MB_GATE, struct.pack("<I", 0))
                    memory.write(aim.MAILBOX_VA + aim.MB_YAW, struct.pack("<ff", 0.0, 0.0))
                    memory.write(pin.MAILBOX_VA + pin.MB_MODE, struct.pack("<I", 0))
            except BaseException:
                pass
            finally:
                try:
                    memory.close()
                except BaseException:
                    pass
        try:
            if gdb_owner_verified:
                gdb.cont_all()
        except BaseException:
            pass
        finally:
            gdb.sock.close()
    print("[aim] done")
    notify("complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
