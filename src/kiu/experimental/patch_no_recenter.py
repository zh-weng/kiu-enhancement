"""Opt-in USA-v0 ground battle camera idle-recenter cancellation.

Stop-aim time in FUN_006D75A8 sets P+0x178. FUN_00703268 selects mode 1
for that flag; the mode-1 object at P+0x704 runs FUN_007CAE30. This patch
removes only that object's two calls publishing automatic yaw/pitch through
FUN_006063E8. Input, idle timers, reticle visibility and forced camera modes
are not patched. Battle behavior still requires live validation.

The caller must hold the guest stopped throughout apply/revert. These functions
never resume it; the standalone entry uses the shared guest_session boundary.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import sys

from ..cli import guest_session

TITLE_ID = "0004000000030100"
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"
NOP = bytes.fromhex("00f020e3")


@dataclass(frozen=True)
class PatchSite:
    address: int
    expected: bytes
    replacement: bytes
    purpose: str


PATCH_SITES = (
    PatchSite(0x007CADEC, bytes.fromhex("7dedf8eb"), NOP, "idle automatic yaw follow"),
    PatchSite(0x007CAFFC, bytes.fromhex("f9ecf8eb"), NOP, "idle interpolated yaw/pitch return"),
)

# Immutable semantic/build context, checked even when the two calls are NOPs.
# The constructor binds vtable CF6D94; its +8 getter returns camera mode 1.
GUARDS = (
    (0x005BBB8C, bytes.fromhex("a4109fe5141080e4"), "idle vtable assignment"),
    (0x005BBC38, bytes.fromhex("946dcf00"), "idle vtable literal"),
    (0x00CF6D9C, bytes.fromhex("a4d3b000"), "idle mode getter pointer"),
    (0x00B0D3A4, bytes.fromhex("0100a0e31eff2fe1"), "idle mode 1 getter"),
    (0x00704028, bytes.fromhex("982084e29e1f84e2c10f80e204608de57c1b03eb"),
     "P+0x704 idle update dispatch"),
    (0x007CADE4, bytes.fromhex("0e0ad5ed0500a0e1"), "preserve pitch and select Q"),
    (0x007CAFCC, bytes.fromhex("028ad6ed"), "default pitch from auxiliary state"),
    (0x007CAFF0, bytes.fromhex("180ad4ed0e0a94ed0500a0e1"), "idle angles and Q"),
)


def status(gdb) -> dict[str, object]:
    mismatches = [f"0x{address:08x}: {purpose}"
                  for address, expected, purpose in GUARDS
                  if gdb.read_mem(address, len(expected)) != expected]
    raw = tuple(gdb.read_mem(site.address, len(site.expected)) for site in PATCH_SITES)
    stock = tuple(site.expected for site in PATCH_SITES)
    installed = tuple(site.replacement for site in PATCH_SITES)
    state = ("unknown" if mismatches else "stock" if raw == stock
             else "installed" if raw == installed else "unknown")
    return {"state": state, "title_id": TITLE_ID, "build_sha256": CODE_SHA256,
            "experimental": True, "guard_mismatches": mismatches,
            "sites": [{"address": f"0x{site.address:08x}", "actual_hex": value.hex(),
                       "original_hex": site.expected.hex(),
                       "replacement_hex": site.replacement.hex(),
                       "build_sha256": CODE_SHA256, "purpose": site.purpose}
                      for site, value in zip(PATCH_SITES, raw, strict=True)]}


def _transition(gdb, *, restore: bool) -> dict[str, object]:
    source, target = ("installed", "stock") if restore else ("stock", "installed")
    before = status(gdb)
    if before["state"] != source:
        raise RuntimeError(f"recenter: expected {source}; unknown/mixed or unexpected bytes refused")
    touched = []
    try:
        for site in PATCH_SITES:
            previous = site.replacement if restore else site.expected
            replacement = site.expected if restore else site.replacement
            touched.append((site.address, previous, replacement))
            gdb.write_mem(site.address, replacement)
            if gdb.read_mem(site.address, len(replacement)) != replacement:
                raise RuntimeError(f"recenter: write verification failed at 0x{site.address:08x}")
        after = status(gdb)
        if after["state"] != target:
            raise RuntimeError("recenter: context changed during patch transaction")
    except Exception as exc:
        # A failed two-site transaction must not leave a recognized mixed patch.
        # Never overwrite a foreign value, including on failure recovery.
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
                exc.add_note(f"recenter rollback 0x{address:08x}: {rollback_error}; restart game")
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
