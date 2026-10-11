"""Widen the ground camera's vertical limit.

``FUN_007462A4`` clamps the pitch it is about to publish against two constants
in its own literal pool:

    007464f8  vldr s0, [pc, #0x150]   ; 0x00746650 = +1.39626336
    007464fc  vcmpe.f32 s18, s0
    00746504  vmovgt.f32 s18, s0      ; upper bound
    00746508  vldr s0, [pc, #0x144]   ; 0x00746654 = -1.39626336
    0074650c  vcmpe.f32 s19, s0
    00746518  vmovle.f32 s19, s0      ; lower bound

1.39626336 rad is 80.0000 degrees to six figures.  Changing those two words on
their own, however, does nothing at all, which is worth stating because it was
the obvious move and it was wrong: measured in a ground battle, the pitch stops
at +5.95 and -0.69 degrees, and widening the constants from 80 to 88 left both
stops exactly where they were.

They are not the limit.  ``s18`` and ``s19`` are the limit -- per-call bounds
loaded from ``Q+0x08`` and ``Q+0x14``, adjusted each frame, and *then* capped at
80 degrees, a cap they never come near.  When the pitch reaches one of them the
code zeroes the pitch request outright:

    00746524  vmov.f32 s0, s18
    00746528  vldr s1, [r5, #0x38]     ; the pitch, Q+0x38 = P+0x2B0
    0074652c  vcmpe.f32 s0, s1
    00746534  bhi #0x7465c8            ; still inside the bound, carry on
    00746538  vmov.f32 s17, s24        ; at the bound: request := 0

So this makes the two caps unconditional.  ``vmovgt.f32 s18, s0`` becomes
``vmov.f32 s18, s0`` and ``vmovle.f32 s19, s0`` becomes ``vmov.f32 s19, s0``,
each a single condition-field nibble, and the bounds then *are* the pool
constants rather than being merely limited by them.  Nothing else reads s18 or
s19: across the whole function they are loaded, adjusted, capped, compared
against the pitch, and used to clamp the result -- that is all.

The flag scheduling around the second move is why it can be forced safely.  The
``vmovle`` at 00746518 acts on the ``vmrs`` at 00746510, which carries the
s19-versus-limit comparison; the ``vcmpe`` at 00746514 belongs to the ``ble``
after it and has not reached the flags yet.  Forcing the move therefore drops
one comparison and disturbs no other.

Whether the *rendered* camera follows all the way out is a separate question
from whether the stored angle does, and this checks the stored angle only.  A
value near 90 degrees points the camera at the poles, where a look-at built from
an up vector degenerates; ``--degrees 89`` is as far as this will go without
``--force`` for that reason.

    python -m kiu.experimental.patch_pitch_limit status
    python -m kiu.experimental.patch_pitch_limit set --degrees 88
    python -m kiu.experimental.patch_pitch_limit revert
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import sys

from ..gdbclient import GdbClient

CODE_BASE = 0x00100000
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"

UPPER_VA = 0x00746650        # loaded by 007464f8, the upper bound
LOWER_VA = 0x00746654        # loaded by 00746508, the lower bound
LOADERS = (0x007464F8, 0x00746508)

# The conditional moves that apply the caps, and the same instructions with the
# condition field forced to AL.
UPPER_MOVE_VA = 0x00746504   # vmovgt.f32 s18, s0
LOWER_MOVE_VA = 0x00746518   # vmovle.f32 s19, s0

# Verified against the USA-v0 image with CODE_SHA256; no game binary is needed
# to import or run this tool. Every mutation checks the live instruction sites.
STOCK_UPPER = bytes.fromhex("c2b8b23f")
STOCK_LOWER = bytes.fromhex("c2b8b2bf")
STOCK_UPPER_MOVE = bytes.fromhex("409ab0ce")
STOCK_LOWER_MOVE = bytes.fromhex("409af0de")
LOADER_ORIGINALS = (bytes.fromhex("540a9fed"), bytes.fromhex("510a9fed"))


def unconditional(word: bytes) -> bytes:
    """The same instruction with its condition field set to AL."""
    return word[:3] + bytes([(word[3] & 0x0F) | 0xE0])


FORCED_UPPER_MOVE = unconditional(STOCK_UPPER_MOVE)
FORCED_LOWER_MOVE = unconditional(STOCK_LOWER_MOVE)

SAFE_MAX_DEGREES = 89.0


def as_float(raw: bytes) -> float:
    return struct.unpack("<f", raw)[0]


def _check_live_sites(gdb: GdbClient) -> None:
    for va, original in zip(LOADERS, LOADER_ORIGINALS):
        if gdb.read_mem(va, 4) != original:
            raise RuntimeError(f"pitch loader 0x{va:08x} does not match USA-v0")
    moves = tuple(gdb.read_mem(va, 4) for va in (UPPER_MOVE_VA, LOWER_MOVE_VA))
    stock = (STOCK_UPPER_MOVE, STOCK_LOWER_MOVE)
    forced = (FORCED_UPPER_MOVE, FORCED_LOWER_MOVE)
    if moves not in (stock, forced):
        raise RuntimeError("pitch instructions are neither stock nor this patch")
    bounds = tuple(gdb.read_mem(va, 4) for va in (UPPER_VA, LOWER_VA))
    if moves == stock and bounds != (STOCK_UPPER, STOCK_LOWER):
        raise RuntimeError("stock pitch instructions have foreign bound constants")
    if moves == forced:
        upper, lower = map(as_float, bounds)
        if not math.isfinite(upper) or upper <= 0 or lower != -upper:
            raise RuntimeError("installed pitch bounds are not a finite symmetric pair")


def status(gdb: GdbClient) -> dict[str, object]:
    upper = as_float(gdb.read_mem(UPPER_VA, 4))
    lower = as_float(gdb.read_mem(LOWER_VA, 4))
    forced = (gdb.read_mem(UPPER_MOVE_VA, 4) == FORCED_UPPER_MOVE
              and gdb.read_mem(LOWER_MOVE_VA, 4) == FORCED_LOWER_MOVE)
    return {
        "limit_deg": math.degrees(upper),
        "bounds_forced": forced,
        "effective": (f"+/-{math.degrees(upper):.2f} deg" if forced
                      else "whatever the game computes (this patch is inactive)"),
        "stock": (gdb.read_mem(UPPER_VA, 4) == STOCK_UPPER
                  and gdb.read_mem(LOWER_VA, 4) == STOCK_LOWER
                  and not forced),
        "stock_cap_deg": math.degrees(as_float(STOCK_UPPER)),
    }


def set_limit(gdb: GdbClient, degrees: float, force: bool = False, *, resume: bool = True) -> dict[str, object]:
    if not math.isfinite(degrees) or degrees <= 0:
        raise ValueError("the limit has to be positive")
    if degrees > SAFE_MAX_DEGREES and not force:
        raise RuntimeError(
            f"{degrees:g} degrees is past {SAFE_MAX_DEGREES:g}; at the poles the "
            "look-at basis degenerates and the view can spin or flip. Pass "
            "--force if you want it anyway")
    _check_live_sites(gdb)
    radians = math.radians(degrees)
    gdb.write_mem(UPPER_VA, struct.pack("<f", radians))
    gdb.write_mem(LOWER_VA, struct.pack("<f", -radians))
    gdb.write_mem(UPPER_MOVE_VA, FORCED_UPPER_MOVE)
    gdb.write_mem(LOWER_MOVE_VA, FORCED_LOWER_MOVE)
    if resume:
        gdb.cont_all()
    written = status(gdb)
    if abs(written["limit_deg"] - degrees) > 0.01 or not written["bounds_forced"]:
        raise RuntimeError(f"write did not take: {written}")
    written["build_sha256"] = CODE_SHA256
    written["sites"] = [
        {"address": f"0x{address:08x}", "original_hex": original.hex(),
         "replacement_hex": replacement.hex(), "build_sha256": CODE_SHA256}
        for address, original, replacement in (
            (UPPER_VA, STOCK_UPPER, struct.pack("<f", radians)),
            (LOWER_VA, STOCK_LOWER, struct.pack("<f", -radians)),
            (UPPER_MOVE_VA, STOCK_UPPER_MOVE, FORCED_UPPER_MOVE),
            (LOWER_MOVE_VA, STOCK_LOWER_MOVE, FORCED_LOWER_MOVE),
        )]
    return written


def revert(gdb: GdbClient, *, resume: bool = True) -> dict[str, object]:
    _check_live_sites(gdb)
    gdb.write_mem(UPPER_VA, STOCK_UPPER)
    gdb.write_mem(LOWER_VA, STOCK_LOWER)
    gdb.write_mem(UPPER_MOVE_VA, STOCK_UPPER_MOVE)
    gdb.write_mem(LOWER_MOVE_VA, STOCK_LOWER_MOVE)
    if resume:
        gdb.cont_all()
    return status(gdb)


class SessionPatch:
    """Opt-in launch adapter: never adopt or restore a different pitch setting."""

    def __init__(self, degrees: float):
        if not math.isfinite(degrees) or not 0 < degrees <= SAFE_MAX_DEGREES:
            raise ValueError(f"Experimental pitch must be in (0, {SAFE_MAX_DEGREES:g}] degrees")
        self.degrees = degrees
        radians = math.radians(degrees)
        self.sites = (UPPER_VA, LOWER_VA, UPPER_MOVE_VA, LOWER_MOVE_VA)
        self.stock = (STOCK_UPPER, STOCK_LOWER, STOCK_UPPER_MOVE, STOCK_LOWER_MOVE)
        self.installed = (struct.pack("<f", radians), struct.pack("<f", -radians),
                          FORCED_UPPER_MOVE, FORCED_LOWER_MOVE)

    def status(self, gdb):
        _check_live_sites(gdb)
        raw = tuple(gdb.read_mem(address, 4) for address in self.sites)
        state = "stock" if raw == self.stock else "installed" if raw == self.installed else "unknown"
        return {"state": state, "limit_deg": self.degrees, "build_sha256": CODE_SHA256}

    def apply(self, gdb):
        return self._transition(gdb, restore=False)

    def revert(self, gdb):
        return self._transition(gdb, restore=True)

    def _transition(self, gdb, *, restore: bool):
        source, target = ("installed", "stock") if restore else ("stock", "installed")
        if self.status(gdb)["state"] != source:
            raise RuntimeError(f"pitch: expected {source}; refusing unknown/mixed session bytes")
        old, new = (self.installed, self.stock) if restore else (self.stock, self.installed)
        touched = []
        try:
            for address, previous, replacement in zip(self.sites, old, new, strict=True):
                touched.append((address, previous, replacement))
                gdb.write_mem(address, replacement)
                if gdb.read_mem(address, len(replacement)) != replacement:
                    raise RuntimeError(f"pitch: write verification failed at 0x{address:08x}")
            after = self.status(gdb)
            if after["state"] != target:
                raise RuntimeError("pitch: context changed during session transaction")
        except Exception as exc:
            for address, previous, replacement in reversed(touched):
                try:
                    current = gdb.read_mem(address, len(previous))
                    if current == replacement:
                        gdb.write_mem(address, previous)
                        if gdb.read_mem(address, len(previous)) != previous:
                            raise RuntimeError("rollback readback mismatch")
                    elif current != previous:
                        raise RuntimeError("foreign/partial bytes; rollback refused")
                except Exception as rollback_error:
                    exc.add_note(f"pitch rollback 0x{address:08x}: {rollback_error}; restart game")
            raise
        after["restored" if restore else "patched"] = True
        after["sites"] = [
            {"address": f"0x{address:08x}", "original_hex": original.hex(),
             "replacement_hex": replacement.hex(), "build_sha256": CODE_SHA256}
            for address, original, replacement in zip(self.sites, self.stock, self.installed, strict=True)]
        return after


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "set", "revert"))
    parser.add_argument("--degrees", type=float, default=88.0)
    parser.add_argument("--force", action="store_true")
    parser.add_argument("--port", type=int, default=24689)
    args = parser.parse_args()

    gdb = GdbClient(port=args.port, timeout=15)
    try:
        gdb.send("?")
        if args.command == "set":
            result = set_limit(gdb, args.degrees, args.force)
        elif args.command == "revert":
            result = revert(gdb)
        else:
            result = status(gdb)
        print(json.dumps(result, indent=2))
    finally:
        try:
            gdb.cont_all()
        except BaseException:
            pass
        gdb.sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
