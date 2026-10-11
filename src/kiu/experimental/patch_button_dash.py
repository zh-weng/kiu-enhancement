"""Opt-in USA-v0 ground dash on the emulator's HID Debug button.

The raw HID bit 0x1000 replaces flick detection at the existing logical-input
merge. The game still calculates pressed/released edges and owns dash direction,
stamina, state restrictions and termination. Both ground queue modes now take
the same logical pressed 0x800 request. No speed or analog movement is changed.

Bind a host key to Azahar's button_debug, then bind the Deck paddle to that key.
This requires no host mailbox and does not change the existing mouse ABI.
Apply/revert must run while the guest is stopped.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import sys

from ..cli import guest_session

TITLE_ID = "0004000000030100"
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"
HID_BUTTON = 0x1000
DASH_ACTION = 0x800


@dataclass(frozen=True)
class PatchSite:
    address: int
    expected: bytes
    replacement: bytes
    purpose: str


PATCH_SITES = (
    PatchSite(
        0x0060FEEC, bytes.fromhex("96feffeb005085e100f020e3010000ea"),
        bytes.fromhex("000097e5010a10e3025bc5e3025b8513"),
        "replace stick flick with raw HID Debug held; clear all other dash sources",
    ),
    PatchSite(
        0x006104DC, bytes.fromhex("041091e5011401e2211ca0e1"),
        bytes.fromhex("081091e5021b01e2a115a0e1"),
        "ground mode 0 takes logical dash pressed instead of its old held action",
    ),
)
GUARDS = (
    (0x0060FDA0, bytes.fromhex("f0472de90040a0e10170a0e1"), "r4 input object and r7 raw HID frame"),
    (0x0060FEE0, bytes.fromhex("480ab0ee641084e20400a0e1"), "flick call context"),
    (0x0060FEFC, bytes.fromhex("000056e32600001a020815e3"), "resume normal input processing"),
    (0x0060FFA8, bytes.fromhex("181094e5050021e0052000e0010000e0200084e51c2084e5"),
     "preserve native pressed/released edge calculation"),
    (0x006104A8, bytes.fromhex("082091e5022b02e2a225a0e11220c0e5"),
     "ground mode 1 still takes logical dash pressed"),
    (0x006104E8, bytes.fromhex("1210c0e51eff2fe1"), "mode 0 publishes dash request to queue +0x12"),
    (0x004E92EC, bytes.fromhex("082090e5d211d0e1"), "native dash consumes queued direction and request"),
    (0x004E98B0, bytes.fromhex("8cf5ffeb"), "preserve native StDashStart dispatch"),
)


def status(gdb) -> dict[str, object]:
    mismatches = [f"0x{address:08x}: {purpose}"
                  for address, expected, purpose in GUARDS
                  if gdb.read_mem(address, len(expected)) != expected]
    values = tuple(gdb.read_mem(site.address, len(site.expected)) for site in PATCH_SITES)
    state = ("unknown" if mismatches else
             "stock" if values == tuple(site.expected for site in PATCH_SITES) else
             "installed" if values == tuple(site.replacement for site in PATCH_SITES) else "unknown")
    return {"state": state, "title_id": TITLE_ID, "build_sha256": CODE_SHA256,
            "experimental": True, "hid_button": HID_BUTTON, "guard_mismatches": mismatches,
            "sites": [{"address": f"0x{site.address:08x}", "actual_hex": value.hex(),
                       "original_hex": site.expected.hex(), "replacement_hex": site.replacement.hex(),
                       "build_sha256": CODE_SHA256, "purpose": site.purpose}
                      for site, value in zip(PATCH_SITES, values, strict=True)]}


def _transition(gdb, *, restore: bool) -> dict[str, object]:
    source, target = ("installed", "stock") if restore else ("stock", "installed")
    if status(gdb)["state"] != source:
        raise RuntimeError(f"button dash: expected {source}; unknown/mixed bytes refused")
    touched = []
    try:
        for site in PATCH_SITES:
            old, new = ((site.replacement, site.expected) if restore else
                        (site.expected, site.replacement))
            touched.append((site.address, old, new))
            gdb.write_mem(site.address, new)
            if gdb.read_mem(site.address, len(new)) != new:
                raise RuntimeError(f"button dash: write verification failed at 0x{site.address:08x}")
        after = status(gdb)
        if after["state"] != target:
            raise RuntimeError("button dash: context changed during transaction")
    except Exception as exc:
        for address, old, new in reversed(touched):
            try:
                current = gdb.read_mem(address, len(old))
                if current == new:
                    gdb.write_mem(address, old)
                    if gdb.read_mem(address, len(old)) != old:
                        raise RuntimeError("rollback readback mismatch")
                elif current != old:
                    raise RuntimeError("foreign/partial bytes; rollback refused")
            except Exception as rollback_error:
                exc.add_note(f"button dash rollback 0x{address:08x}: {rollback_error}; restart game")
        raise
    after["restored" if restore else "patched"] = True
    return after


def apply(gdb) -> dict[str, object]:
    return _transition(gdb, restore=False)


def revert(gdb) -> dict[str, object]:
    return _transition(gdb, restore=True)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("status", "apply", "revert"))
    parser.add_argument("--port", type=int, default=24689)
    args = parser.parse_args(argv)
    try:
        with guest_session(args.port) as gdb:
            result = {"status": status, "apply": apply, "revert": revert}[args.command](gdb)
        print(json.dumps(result, indent=2))
        return 0
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"kiu: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
