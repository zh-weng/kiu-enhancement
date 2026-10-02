"""KIU USA v0 60 FPS patch with complete game-time scaling.

Unlocking the scheduler raises the full frame pipeline from 30 to 60 Hz but
leaves both engine time sources at their stock 30 Hz values:

* FUN_0060DFF8 receives s0=1/30 under both scheduler intervals 2 and 0.
* The global time singleton at 0x00E174E4 derives its four public delta channels
  from a master value of 2.0 VBlanks; overwriting a public channel is ineffective
  because FUN_00607A4C recomputes it every update.

This build-locked patch scales both sources by 0.5 while every logic, render,
post-render, and present phase continues to run every cycle. No task phase is
skipped, so both framebuffer banks traverse the complete frame pipeline.
Apply/revert verify every original/replacement byte.
"""
from __future__ import annotations

import argparse
import json
import struct
import time
from dataclasses import dataclass

from gdbclient import GdbClient

TITLE_ID = "0004000000030100"
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"

SCHED_SLOT = 0x00D63824
SCHED_NOMINAL_OFF = 0x50
SCHED_INTERVAL_OFF = 0x54
SCHED_NOMINAL_ORIG = 30
SCHED_INTERVAL_ORIG = 2
SCHED_INTERVAL_60 = 0

DISPATCH_DT_SITE = 0x0060E00C
DISPATCH_DT_ORIGINAL = bytes.fromhex("408ab0ee")  # vmov.f32 s16, s0
DISPATCH_DT_CAVE = 0x00C957C0
DISPATCH_DT_CAVE_BYTES = bytes.fromhex(
    "028a9fed"  # vldr s16, [pc, #8] -> 0.5f literal
    "088a20ee"  # vmul.f32 s16, s0, s16
    "1eff2fe1"  # bx lr
    "00f020e3"  # nop
    "0000003f"  # 0.5f
)

GLOBAL_DT_SITE = 0x00607A74
GLOBAL_DT_ORIGINAL = bytes.fromhex("080a90ed")  # vldr s0, [r0, #0x20]
GLOBAL_DT_CAVE = 0x00C957E0
GLOBAL_DT_CAVE_BYTES = bytes.fromhex(
    "080a90ed"  # vldr s0, [r0, #0x20]
    "022adfed"  # vldr s5, [pc, #8] -> 0.5f literal
    "220a20ee"  # vmul.f32 s0, s0, s5
    "a1c8e5ea"  # b 0x00607a78 (do not clobber this leaf function's LR)
    "00f020e3"  # nop
    "0000003f"  # 0.5f
)
GLOBAL_TIME_OBJECT = 0x00E174E4


def encode_branch(source: int, target: int, *, link: bool) -> bytes:
    if source & 3 or target & 3:
        raise ValueError("ARM branch endpoints must be word aligned")
    displacement = target - (source + 8)
    if displacement & 3 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError("ARM branch target is out of range")
    opcode = 0xEB000000 if link else 0xEA000000
    return struct.pack("<I", opcode | ((displacement >> 2) & 0x00FFFFFF))


@dataclass(frozen=True)
class PatchSite:
    address: int
    expected: bytes
    replacement: bytes
    cave: int
    purpose: str

    def record(self) -> dict[str, object]:
        return {
            "address": f"0x{self.address:08x}",
            "original_hex": self.expected.hex(),
            "replacement_hex": self.replacement.hex(),
            "build_sha256": CODE_SHA256,
            "purpose": self.purpose,
        }


PATCH_SITES = (
    PatchSite(
        DISPATCH_DT_SITE,
        DISPATCH_DT_ORIGINAL,
        encode_branch(DISPATCH_DT_SITE, DISPATCH_DT_CAVE, link=True),
        DISPATCH_DT_CAVE,
        "Scale FUN_0060DFF8's incoming 1/30 s delta to 1/60 s.",
    ),
    PatchSite(
        GLOBAL_DT_SITE,
        GLOBAL_DT_ORIGINAL,
        encode_branch(GLOBAL_DT_SITE, GLOBAL_DT_CAVE, link=False),
        GLOBAL_DT_CAVE,
        "Scale the global time singleton's 2-VBlank master delta to 1 VBlank.",
    ),
)
CAVES = (
    (DISPATCH_DT_CAVE, DISPATCH_DT_CAVE_BYTES, "dispatcher delta cave"),
    (GLOBAL_DT_CAVE, GLOBAL_DT_CAVE_BYTES, "global delta cave"),
)


def _read_exact(gdb: GdbClient, address: int, size: int, label: str) -> bytes:
    raw = bytes(gdb.read_mem(address, size))
    if len(raw) != size:
        raise RuntimeError(f"short {label} read at 0x{address:08x}: {len(raw)} != {size}")
    return raw


def _u32(gdb: GdbClient, address: int) -> int:
    return struct.unpack("<I", _read_exact(gdb, address, 4, "u32"))[0]


def _f32(gdb: GdbClient, address: int) -> float:
    return struct.unpack("<f", _read_exact(gdb, address, 4, "f32"))[0]


def _expect(gdb: GdbClient, address: int, expected: bytes, label: str) -> None:
    actual = _read_exact(gdb, address, len(expected), label)
    if actual != expected:
        raise RuntimeError(
            f"{label} mismatch at 0x{address:08x}: {actual.hex()} != {expected.hex()}"
        )


def _write_exact(gdb: GdbClient, address: int, data: bytes, label: str) -> None:
    gdb.write_mem(address, data)
    _expect(gdb, address, data, label)


def _scheduler(gdb: GdbClient) -> int:
    scheduler = _u32(gdb, SCHED_SLOT)
    if scheduler == 0:
        raise RuntimeError(f"scheduler slot 0x{SCHED_SLOT:08x} is null")
    nominal = _u32(gdb, scheduler + SCHED_NOMINAL_OFF)
    if nominal != SCHED_NOMINAL_ORIG:
        raise RuntimeError(f"scheduler nominal={nominal}, expected {SCHED_NOMINAL_ORIG}")
    return scheduler


def apply(gdb: GdbClient) -> dict[str, object]:
    for site in PATCH_SITES:
        _expect(gdb, site.address, site.expected, "original time instruction")
    for address, cave, label in CAVES:
        _expect(gdb, address, bytes(len(cave)), label)
    scheduler = _scheduler(gdb)
    interval_address = scheduler + SCHED_INTERVAL_OFF
    if _u32(gdb, interval_address) != SCHED_INTERVAL_ORIG:
        raise RuntimeError("scheduler interval is not the stock value 2")

    try:
        for address, cave, label in CAVES:
            _write_exact(gdb, address, cave, f"{label} install")
        for site in PATCH_SITES:
            _write_exact(gdb, site.address, site.replacement, "time redirect install")
        _write_exact(
            gdb,
            interval_address,
            struct.pack("<I", SCHED_INTERVAL_60),
            "60 Hz scheduler interval",
        )
    except BaseException:
        try:
            gdb.write_mem(interval_address, struct.pack("<I", SCHED_INTERVAL_ORIG))
            for site in PATCH_SITES:
                gdb.write_mem(site.address, site.expected)
            for address, cave, _label in CAVES:
                gdb.write_mem(address, bytes(len(cave)))
        except BaseException:
            pass
        raise

    return {
        "patched": True,
        "title_id": TITLE_ID,
        "code_sha256": CODE_SHA256,
        "scheduler": f"0x{scheduler:08x}",
        "interval": SCHED_INTERVAL_60,
        "dispatcher_dt": "1/60 s",
        "global_delta": "1 VBlank",
        "frame_phase_gates": 0,
        "patch_sites": [site.record() for site in PATCH_SITES],
    }


def revert(gdb: GdbClient) -> dict[str, object]:
    for site in PATCH_SITES:
        _expect(gdb, site.address, site.replacement, "installed time redirect")
    for address, cave, label in CAVES:
        _expect(gdb, address, cave, f"installed {label}")
    scheduler = _scheduler(gdb)
    interval_address = scheduler + SCHED_INTERVAL_OFF
    if _u32(gdb, interval_address) != SCHED_INTERVAL_60:
        raise RuntimeError("scheduler interval is not the installed value 0")

    _write_exact(
        gdb,
        interval_address,
        struct.pack("<I", SCHED_INTERVAL_ORIG),
        "stock scheduler interval",
    )
    for site in PATCH_SITES:
        _write_exact(gdb, site.address, site.expected, "time instruction restore")
    for address, cave, label in CAVES:
        _write_exact(gdb, address, bytes(len(cave)), f"{label} clear")
    return {
        "restored": True,
        "scheduler": f"0x{scheduler:08x}",
        "interval": SCHED_INTERVAL_ORIG,
        "patch_sites_restored": len(PATCH_SITES),
    }


def status(gdb: GdbClient) -> dict[str, object]:
    scheduler = _scheduler(gdb)
    site_values = [
        _read_exact(gdb, site.address, len(site.expected), "time instruction")
        for site in PATCH_SITES
    ]
    cave_values = [
        _read_exact(gdb, address, len(cave), label) for address, cave, label in CAVES
    ]
    installed = all(
        actual == site.replacement for actual, site in zip(site_values, PATCH_SITES)
    ) and all(
        actual == cave for actual, (_address, cave, _label) in zip(cave_values, CAVES)
    )
    stock = all(
        actual == site.expected for actual, site in zip(site_values, PATCH_SITES)
    ) and all(
        actual == bytes(len(cave))
        for actual, (_address, cave, _label) in zip(cave_values, CAVES)
    )
    return {
        "state": "installed" if installed else "stock" if stock else "mixed",
        "scheduler": f"0x{scheduler:08x}",
        "interval": _u32(gdb, scheduler + SCHED_INTERVAL_OFF),
        "site_hex": [value.hex() for value in site_values],
        "cave_hex": [value.hex() for value in cave_values],
        "global_delta_channels": [
            _f32(gdb, GLOBAL_TIME_OBJECT + offset) for offset in (0, 4, 8, 12)
        ],
        "global_master_delta": _f32(gdb, GLOBAL_TIME_OBJECT + 0x20),
        "frame_phase_gates": 0,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "revert", "status"))
    parser.add_argument("--port", type=int, default=24689)
    args = parser.parse_args()

    gdb = GdbClient(port=args.port, timeout=10)
    resumed = False
    try:
        stop = gdb.send("?")
        if not stop.startswith(("S", "T")):
            raise RuntimeError(f"GDB did not report a stopped guest: {stop!r}")
        result = (
            status(gdb)
            if args.command == "status"
            else apply(gdb)
            if args.command == "apply"
            else revert(gdb)
        )
        print(f"{args.command}: {json.dumps(result, separators=(',', ':'))}")
        gdb.cont_all()
        resumed = True
        time.sleep(0.2)
        return 0
    finally:
        if not resumed:
            try:
                gdb.cont_all()
            except BaseException:
                pass
        try:
            gdb.sock.close()
        except BaseException:
            pass


if __name__ == "__main__":
    raise SystemExit(main())
