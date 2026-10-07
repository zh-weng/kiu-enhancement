"""Linux/XWayland native mouse preview, using unchanged guest mailbox ABI 1.

Azahar must be launched by this process for Linux process-memory permissions,
with QT_QPA_PLATFORM=xcb. F12 toggles aim while its window is focused. Unknown
top viewport disables absolute air aiming; ground/relative aiming uses raw XI2
counts. Restart after savestate load; the ABI 1 consume/write race remains.
"""
from __future__ import annotations

import argparse
import math
import struct
import threading
import time

from ...aim_core import map_point, sense
from ...aim_protocol import validate_mailboxes
from ...gdbclient import GdbClient
from ...patches import patch_aim_request as aim, pin_reticle as pin
from .guest_memory import GuestMemory, listening_pid, nonce_bytes, process_identity
from .x11 import X11WindowContext, XIRawMouse


def anchor_memory(gdb: GdbClient, verbose: bool = True, port: int = 24689,
                  expected_pid: int | None = None) -> GuestMemory:
    memory = GuestMemory.attach(port=port, expected_pid=expected_pid)
    try:
        scratch = aim.MAILBOX_VA + 0x40
        nonce = nonce_bytes()
        gdb.write_mem(scratch, nonce)
        gdb.cont_all()
        if verbose:
            print("[aim] finding Linux mailbox mapping ...")
        memory.anchor(scratch, nonce)
        return memory
    except BaseException:
        memory.close()
        raise


def clear_inputs(memory) -> None:
    """No cleanup writes through unverified/foreign mailbox headers."""
    validate_mailboxes(memory)
    memory.write(aim.MAILBOX_VA + aim.MB_GATE, struct.pack("<I", 0))
    memory.write(aim.MAILBOX_VA + aim.MB_YAW, struct.pack("<ff", 0.0, 0.0))
    memory.write(pin.MAILBOX_VA + pin.MB_MODE, struct.pack("<I", 0))


def valid_rect(value):
    try:
        if value is None or len(value) != 4:
            return None
        result = tuple(float(part) for part in value)
        if all(math.isfinite(part) for part in result) and result[2] > 0 and result[3] > 0:
            return result
    except (TypeError, ValueError, OverflowError):
        pass
    return None


class _Stopped(Exception):
    pass


def wait_for_owner(port: int, expected_pid: int | None, *, expected_identity: str | None = None,
                   timeout: float = 2.0, stop_event: threading.Event | None = None) -> tuple[int, str]:
    """Wait only for the same live emulator's deferred GDB listener reopening.

    A different owner, process exit or PID reuse is an immediate refusal. This
    does not retry an unknown process or relax any hook-byte verification.
    """
    stop = stop_event if stop_event is not None else threading.Event()
    deadline = time.monotonic() + timeout
    identity = expected_identity
    while True:
        if stop.is_set():
            raise _Stopped
        owner = listening_pid(port)
        if owner is not None and expected_pid is not None and owner != expected_pid:
            raise RuntimeError(f"GDB port {port} belongs to PID {owner}, expected {expected_pid}; refusing injection")
        pid = expected_pid if expected_pid is not None else owner
        if pid is None:
            raise RuntimeError(f"GDB port {port} has no owner to bind; refusing injection")
        try:
            current = process_identity(pid)
        except (OSError, ValueError, IndexError) as exc:
            raise RuntimeError("expected emulator exited; refusing GDB reconnect") from exc
        if identity is None:
            identity = current
        elif current != identity:
            raise RuntimeError("emulator PID was reused; refusing GDB reconnect")
        if owner == pid:
            return owner, identity
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise RuntimeError(f"GDB port {port} did not reopen for PID {pid}; refusing injection")
        if stop.wait(min(0.05, remaining)):
            raise _Stopped


def main(argv: list[str] | None = None, *, stop_event: threading.Event | None = None,
         status_callback=None, auto_activate: bool = False, wait_for_battle: bool = False,
         expected_pid: int | None = None, rect_provider=None) -> int:
    """Cancellable worker interface identical to the Windows Launcher adapter."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=24689)
    parser.add_argument("--sensitivity", type=float, default=0.0025)
    parser.add_argument("--point-sensitivity", type=float, default=0.4)
    parser.add_argument("--mode", choices=("auto", "look", "point"), default="auto")
    parser.add_argument("--map", dest="mapping", choices=("screen", "viewport", "relative"), default="viewport")
    parser.add_argument("--map-span", type=float, default=1.0)  # CLI compatibility; never guesses a viewport
    parser.add_argument("--hz", type=float, default=250.0)
    parser.add_argument("--invert-x", action="store_true")
    parser.add_argument("--invert-y", action="store_true")
    args = parser.parse_args(argv)
    if expected_pid is not None and (type(expected_pid) is not int or expected_pid <= 0):
        raise ValueError("expected_pid must be a positive integer")
    if rect_provider is not None and not callable(rect_provider):
        raise ValueError("rect_provider must be callable")
    if not all(math.isfinite(value) and value > 0 for value in (args.hz, args.sensitivity, args.point_sensitivity)):
        raise ValueError("aim rate/sensitivity must be finite and positive")
    stop = stop_event if stop_event is not None else threading.Event()
    last_status = None
    bound_pid, bound_identity = expected_pid, None

    def notify(value):
        nonlocal last_status
        if value != last_status:
            last_status = value
            if status_callback:
                try:
                    status_callback(value)
                except Exception:
                    pass

    def check_stop():
        if stop.is_set():
            raise _Stopped

    def require_owner():
        nonlocal bound_pid, bound_identity
        owner, identity = wait_for_owner(args.port, bound_pid, expected_identity=bound_identity, stop_event=stop)
        bound_pid, bound_identity = owner, identity
        return owner

    if stop.is_set():
        notify("complete")
        return 0
    gdb = memory = window = source = None
    owner_verified = mailboxes_validated = False
    lock = threading.Lock()
    live = {"wanted": auto_activate, "on": False, "dx": 0.0, "dy": 0.0}
    sx = args.sensitivity * (1 if args.invert_x else -1)
    sy = args.sensitivity * (-1 if args.invert_y else 1)
    try:
        notify("connecting")
        owner = require_owner()
        gdb = GdbClient(port=args.port, timeout=20)
        if require_owner() != owner:
            raise RuntimeError("GDB owner changed during connection")
        owner_verified = True
        check_stop()
        gdb.send("?")
        components = (("rotation-request", aim), ("reticle", pin))
        states = {name: module.status(gdb) for name, module in components}
        for name, module in components:
            state = states[name]
            if state["state"] not in ("stock", "installed") or state["state"] == "installed" and not state["hook_matches"]:
                raise RuntimeError(f"{name}: unknown/mixed hook bytes; refusing to overwrite")
            if state["state"] == "installed" and gdb.read_mem(module.MAILBOX_VA, 8) != struct.pack("<II", module.MAGIC, module.VERSION):
                raise RuntimeError(f"{name}: mailbox ABI mismatch")
        for name, module in components:
            if states[name]["state"] == "stock":
                module.apply(gdb, resume=False)
            if gdb.read_mem(module.MAILBOX_VA, 8) != struct.pack("<II", module.MAGIC, module.VERSION):
                raise RuntimeError(f"{name}: mailbox ABI mismatch")
        aim.set_gate(gdb, 0)
        pin.set_mode(gdb, 0)
        check_stop()
        notify("anchoring")
        memory = anchor_memory(gdb, port=args.port, expected_pid=owner)
        validate_mailboxes(memory)
        mailboxes_validated = True
        gdb.sock.close()
        window = X11WindowContext(owner, rect_provider)

        def toggle():
            if not window.focused():
                return
            with lock:
                live["wanted"] = not live["wanted"]
                live["on"] = False
                live["dx"] = live["dy"] = 0.0

        def quit_focused():
            if window.focused():
                stop.set()

        def delta(dx, dy):
            # No transport I/O on the XI2 callback thread.
            with lock:
                if live["on"]:
                    live["dx"] += dx
                    live["dy"] += dy

        source = XIRawMouse(delta, toggle, quit_focused if stop_event is None else None).start()
        print("[aim] Linux XI2 native mouse preview; F12 toggles aim while Azahar is focused")
        seen_calls, mode = sense(memory)
        last_activity = float("-inf")
        last_sense = last_focus = float("-inf")
        active = saw_battle = focused = was_on = False
        width, height, px, py = 400.0, 240.0, 200.0, 120.0
        rect = None
        while True:
            check_stop()
            source.check()
            now = time.perf_counter()
            if now - last_sense >= 0.25:
                validate_mailboxes(memory)
                calls, sensed = sense(memory)
                size = struct.unpack("<ff", memory.read(pin.MAILBOX_VA + pin.MB_W, 8))
                if calls != seen_calls:
                    last_activity, saw_battle = now, True
                seen_calls = calls
                valid_size = all(math.isfinite(value) and value > 0 for value in size)
                active = valid_size and now - last_activity < 1.0
                next_mode = sensed if args.mode == "auto" else args.mode
                if active and (next_mode != mode or size != (width, height)):
                    clear_inputs(memory)
                    width, height = size
                    px, py = width / 2, height / 2
                    mode, was_on = next_mode, False
                    with lock:
                        live["on"] = False
                        live["dx"] = live["dy"] = 0.0
                rect = valid_rect(window.top_viewport()) if valid_size and mode == "point" and args.mapping != "relative" else None
                last_sense = now
            if now - last_focus >= 0.02:
                focused, last_focus = window.focused(), now
            viewport_missing = active and mode == "point" and args.mapping != "relative" and rect is None
            with lock:
                wanted = live["wanted"]
                dx, dy = live["dx"], live["dy"]
                live["dx"] = live["dy"] = 0.0
                on = bool(wanted and active and focused and not viewport_missing)
                live["on"] = on
                if wanted and active and not focused and not auto_activate:
                    live["wanted"] = False
            if on != was_on:
                clear_inputs(memory)
                if on:
                    if args.mapping == "relative":
                        px, py = width / 2, height / 2
                    if mode == "look":
                        memory.write(aim.MAILBOX_VA + aim.MB_GATE, struct.pack("<I", 1))
                    else:
                        if args.mapping != "relative":
                            px, py = map_point(rect, width, height, window.cursor_position())
                        memory.write(pin.MAILBOX_VA + pin.MB_POS_X, struct.pack("<ff", px, py))
                        memory.write(pin.MAILBOX_VA + pin.MB_MODE, struct.pack("<I", 2))
                was_on = on
            if on:
                validate_mailboxes(memory)
                if mode == "look" and (dx or dy):
                    pending = struct.unpack("<ff", memory.read(aim.MAILBOX_VA + aim.MB_YAW, 8))
                    if not all(math.isfinite(value) for value in pending):
                        raise RuntimeError("invalid ground mailbox delta; stop and re-anchor")
                    memory.write(aim.MAILBOX_VA + aim.MB_YAW, struct.pack("<ff", pending[0] + dx * sx, pending[1] + dy * sy))
                elif mode == "point":
                    if args.mapping == "relative":
                        px = min(max(px + dx * args.point_sensitivity * (-1 if args.invert_x else 1), 0), width)
                        py = min(max(py + dy * args.point_sensitivity * (-1 if args.invert_y else 1), 0), height)
                    else:
                        px, py = map_point(rect, width, height, window.cursor_position())
                    memory.write(pin.MAILBOX_VA + pin.MB_POS_X, struct.pack("<ff", px, py))
            notify("active" if on else "off" if not wanted else "viewport_missing" if viewport_missing else "inactive" if saw_battle else "waiting")
            if stop.wait(1.0 / args.hz):
                raise _Stopped
    except (KeyboardInterrupt, _Stopped):
        pass
    finally:
        if source:
            source.stop()
        if memory:
            try:
                if mailboxes_validated:
                    clear_inputs(memory)
            except (OSError, RuntimeError, ValueError):
                pass
            finally:
                memory.close()
        if window:
            window.close()
        if gdb:
            try:
                if owner_verified:
                    gdb.cont_all()
            except (OSError, RuntimeError):
                pass
            finally:
                gdb.sock.close()
    notify("complete")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
