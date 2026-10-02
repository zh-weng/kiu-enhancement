"""KIU USA v0 perspective-projection aspect patch.

A write watchpoint on the ground scene's PICA vertex-uniform command word
(0x14FFBD58 in the captured frame) traced the two projection-copy tails to
0x008DEC80 (vertex shader) and 0x008DECCC (geometry shader).  At either tail,
r0 addresses the command block and the horizontal coefficient is at +0x28;
the exact -1.0 perspective marker is at +0x40.

This build-locked hook scales perspective blocks whose original X/Y ratio is
the top screen's 3/5.  While ``menu_render_pass.py`` marks BottomRenderPass as
temporarily routed to the top framebuffer, it also scales that pass's original
3/4 projection.  The respective scales are (5/3)/target_aspect and
(4/3)/target_aspect, so a 21:9 target uses 5/7 and 4/7.  Ordinary bottom-screen
and orthographic UI uploads remain unchanged.  Apply/revert verify every byte.
"""
from __future__ import annotations

import argparse
import json
import math
import struct
import time

from ..gdbclient import GdbClient

TITLE_ID = "0004000000030100"
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"

PROJECTION_UPLOAD_SITES = (0x008DEC80, 0x008DECCC)
PROJECTION_UPLOAD_ORIGINAL = bytes.fromhex("000095e5")  # ldr r0,[r5]
PROJECTION_CAVES = (0x00C95840, 0x00C958A0)
PROJECTION_CAVE_SIZE = 96
BOTTOM_EXTENSION_CAVE = 0x00C95800
BOTTOM_EXTENSION_CAVE_SIZE = 64
MENU_RENDER_MARKER = 0x00F36820
PERSPECTIVE_MARKER_OFFSET = 64
TOP_RATIO_CUTOFF_OFFSET = 68
SCALE_LITERAL_OFFSET = 72
BOTTOM_SCALE_LITERAL_OFFSET = 36
TOP_RATIO_CUTOFF = -0.675
STOCK_ASPECT = 5 / 3
BOTTOM_STOCK_ASPECT = 4 / 3
DEFAULT_ASPECT = 21 / 9


def encode_branch(source: int, target: int, *, link: bool = False) -> bytes:
    if source & 3 or target & 3:
        raise ValueError("ARM branch endpoints must be word aligned")
    displacement = target - (source + 8)
    if displacement & 3 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError("ARM branch target is out of range")
    opcode = 0xEB000000 if link else 0xEA000000
    return struct.pack("<I", opcode | ((displacement >> 2) & 0x00FFFFFF))


def encode_cond_branch(source: int, target: int, condition: int) -> bytes:
    if not 0 <= condition <= 0xD:
        raise ValueError("invalid ARM condition")
    if source & 3 or target & 3:
        raise ValueError("ARM branch endpoints must be word aligned")
    displacement = target - (source + 8)
    if displacement & 3 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError("ARM branch target is out of range")
    return struct.pack(
        "<I", (condition << 28) | 0x0A000000 | ((displacement >> 2) & 0x00FFFFFF)
    )


def aspect_scale(aspect: float) -> float:
    if not math.isfinite(aspect) or aspect <= 0:
        raise ValueError("aspect must be a finite positive float")
    return STOCK_ASPECT / aspect


def bottom_aspect_scale(aspect: float) -> float:
    if not math.isfinite(aspect) or aspect <= 0:
        raise ValueError("aspect must be a finite positive float")
    return BOTTOM_STOCK_ASPECT / aspect


def build_cave(cave: int, return_address: int, aspect: float = DEFAULT_ASPECT) -> bytes:
    return b"".join(
        (
            PROJECTION_UPLOAD_ORIGINAL,  # ldr r0,[r5]
            bytes.fromhex("402090e5"),  # ldr r2,[r0,#0x40] (perspective marker)
            bytes.fromhex("30309fe5"),  # ldr r3,[pc,#0x30] -> -1.0f
            bytes.fromhex("030052e1"),  # cmp r2,r3
            bytes.fromhex("0900001a"),  # bne passthrough
            bytes.fromhex("0a0a90ed"),  # vldr s0,[r0,#0x28] (horizontal coefficient)
            bytes.fromhex("051a90ed"),  # vldr s2,[r0,#0x14] (vertical coefficient)
            bytes.fromhex("082a9fed"),  # vldr s4,[pc,#0x20] -> -0.675
            bytes.fromhex("022a21ee"),  # vmul.f32 s4,s2,s4
            bytes.fromhex("420ab4ee"),  # vcmp.f32 s0,s4
            bytes.fromhex("10faf1ee"),  # vmrs apsr_nzcv,fpscr
            encode_cond_branch(cave + 44, cave + 76, 0xD),  # ble bottom extension
            bytes.fromhex("041a9fed"),  # vldr s2,[pc,#0x10] -> scale
            bytes.fromhex("010a20ee"),  # vmul.f32 s0,s0,s2
            bytes.fromhex("0a0a80ed"),  # vstr s0,[r0,#0x28]
            encode_branch(cave + 60, return_address),
            struct.pack("<f", -1.0),
            struct.pack("<f", TOP_RATIO_CUTOFF),
            struct.pack("<f", aspect_scale(aspect)),
            bytes.fromhex("00402de9"),  # push {lr}
            encode_branch(cave + 80, BOTTOM_EXTENSION_CAVE, link=True),
            bytes.fromhex("0040bde8"),  # pop {lr}
            encode_branch(cave + 88, return_address),
            bytes(4),
        )
    )


def build_bottom_extension(aspect: float = DEFAULT_ASPECT) -> bytes:
    raw = b"".join(
        (
            bytes.fromhex("18209fe5"),  # ldr r2,[pc,#0x18] -> marker address
            bytes.fromhex("002092e5"),  # ldr r2,[r2]
            bytes.fromhex("010052e3"),  # cmp r2,#1
            bytes.fromhex("0200001a"),  # bne done
            bytes.fromhex("031a9fed"),  # vldr s2,[pc,#0x0c] -> bottom scale
            bytes.fromhex("010a20ee"),  # vmul.f32 s0,s0,s2
            bytes.fromhex("0a0a80ed"),  # vstr s0,[r0,#0x28]
            bytes.fromhex("1eff2fe1"),  # bx lr
            struct.pack("<I", MENU_RENDER_MARKER),
            struct.pack("<f", bottom_aspect_scale(aspect)),
        )
    )
    if len(raw) > BOTTOM_EXTENSION_CAVE_SIZE:
        raise RuntimeError("bottom projection extension exceeds its cave")
    return raw + bytes(BOTTOM_EXTENSION_CAVE_SIZE - len(raw))


def _read_exact(gdb: GdbClient, address: int, size: int, label: str) -> bytes:
    raw = bytes(gdb.read_mem(address, size))
    if len(raw) != size:
        raise RuntimeError(f"short {label} read at 0x{address:08x}: {len(raw)} != {size}")
    return raw


def _expect(gdb: GdbClient, address: int, expected: bytes, label: str) -> None:
    actual = _read_exact(gdb, address, len(expected), label)
    if actual != expected:
        raise RuntimeError(
            f"{label} mismatch at 0x{address:08x}: {actual.hex()} != {expected.hex()}"
        )


def _write_exact(gdb: GdbClient, address: int, data: bytes, label: str) -> None:
    gdb.write_mem(address, data)
    _expect(gdb, address, data, label)


def _installed_cave(raw: bytes, cave: int, return_address: int) -> bool:
    expected = build_cave(cave, return_address)
    return (
        len(raw) == PROJECTION_CAVE_SIZE
        and raw[:SCALE_LITERAL_OFFSET] == expected[:SCALE_LITERAL_OFFSET]
        and raw[SCALE_LITERAL_OFFSET + 4:] == expected[SCALE_LITERAL_OFFSET + 4:]
    )


def _installed_bottom_extension(raw: bytes) -> bool:
    expected = build_bottom_extension()
    return (
        len(raw) == BOTTOM_EXTENSION_CAVE_SIZE
        and raw[:BOTTOM_SCALE_LITERAL_OFFSET] == expected[:BOTTOM_SCALE_LITERAL_OFFSET]
        and raw[BOTTOM_SCALE_LITERAL_OFFSET + 4:] == expected[BOTTOM_SCALE_LITERAL_OFFSET + 4:]
    )


def apply(gdb: GdbClient, aspect: float) -> dict[str, object]:
    replacements = [
        encode_branch(site, cave)
        for site, cave in zip(PROJECTION_UPLOAD_SITES, PROJECTION_CAVES, strict=True)
    ]
    caves = [
        build_cave(cave, site + 4, aspect)
        for site, cave in zip(PROJECTION_UPLOAD_SITES, PROJECTION_CAVES, strict=True)
    ]
    bottom_extension = build_bottom_extension(aspect)
    for site in PROJECTION_UPLOAD_SITES:
        _expect(gdb, site, PROJECTION_UPLOAD_ORIGINAL, "projection upload tail")
    for cave in PROJECTION_CAVES:
        _expect(gdb, cave, bytes(PROJECTION_CAVE_SIZE), "projection cave")
    _expect(
        gdb,
        BOTTOM_EXTENSION_CAVE,
        bytes(BOTTOM_EXTENSION_CAVE_SIZE),
        "bottom projection extension cave",
    )
    try:
        _write_exact(
            gdb,
            BOTTOM_EXTENSION_CAVE,
            bottom_extension,
            "bottom projection extension install",
        )
        for cave, data in zip(PROJECTION_CAVES, caves, strict=True):
            _write_exact(gdb, cave, data, "projection cave install")
        for site, replacement in zip(PROJECTION_UPLOAD_SITES, replacements, strict=True):
            _write_exact(gdb, site, replacement, "projection-upload redirect")
    except BaseException:
        try:
            for site in PROJECTION_UPLOAD_SITES:
                gdb.write_mem(site, PROJECTION_UPLOAD_ORIGINAL)
            for cave in PROJECTION_CAVES:
                gdb.write_mem(cave, bytes(PROJECTION_CAVE_SIZE))
            gdb.write_mem(BOTTOM_EXTENSION_CAVE, bytes(BOTTOM_EXTENSION_CAVE_SIZE))
        except BaseException:
            pass
        raise
    scale = struct.unpack(
        "<f", caves[0][SCALE_LITERAL_OFFSET:SCALE_LITERAL_OFFSET + 4]
    )[0]
    return {
        "patched": True,
        "title_id": TITLE_ID,
        "code_sha256": CODE_SHA256,
        "aspect": aspect,
        "projection_x_scale": scale,
        "menu_bottom_projection_x_scale": bottom_aspect_scale(aspect),
        "patch_sites": [
            {
                "address": f"0x{site:08x}",
                "original_hex": PROJECTION_UPLOAD_ORIGINAL.hex(),
                "replacement_hex": replacement.hex(),
                "build_sha256": CODE_SHA256,
                "purpose": (
                    "Scale top-screen perspective X; the marked native-routed menu "
                    "bottom pass uses its separate 4:3 correction."
                ),
            }
            for site, replacement in zip(PROJECTION_UPLOAD_SITES, replacements, strict=True)
        ],
    }


def set_aspect(gdb: GdbClient, aspect: float) -> dict[str, object]:
    for site, cave in zip(PROJECTION_UPLOAD_SITES, PROJECTION_CAVES, strict=True):
        raw = _read_exact(gdb, cave, PROJECTION_CAVE_SIZE, "installed projection cave")
        if not _installed_cave(raw, cave, site + 4):
            raise RuntimeError("perspective projection patch is not installed")
    literal = struct.pack("<f", aspect_scale(aspect))
    for cave in PROJECTION_CAVES:
        _write_exact(gdb, cave + SCALE_LITERAL_OFFSET, literal, "projection scale literal")
    extension = _read_exact(
        gdb,
        BOTTOM_EXTENSION_CAVE,
        BOTTOM_EXTENSION_CAVE_SIZE,
        "bottom projection extension",
    )
    if not _installed_bottom_extension(extension):
        raise RuntimeError("bottom projection extension is not installed")
    bottom_literal = struct.pack("<f", bottom_aspect_scale(aspect))
    _write_exact(
        gdb,
        BOTTOM_EXTENSION_CAVE + BOTTOM_SCALE_LITERAL_OFFSET,
        bottom_literal,
        "bottom projection scale literal",
    )
    return {
        "aspect": aspect,
        "projection_x_scale": struct.unpack("<f", literal)[0],
        "menu_bottom_projection_x_scale": struct.unpack("<f", bottom_literal)[0],
    }


def revert(gdb: GdbClient) -> dict[str, object]:
    for site, cave in zip(PROJECTION_UPLOAD_SITES, PROJECTION_CAVES, strict=True):
        replacement = encode_branch(site, cave)
        _expect(gdb, site, replacement, "installed projection redirect")
        raw = _read_exact(gdb, cave, PROJECTION_CAVE_SIZE, "installed projection cave")
        if not _installed_cave(raw, cave, site + 4):
            raise RuntimeError("installed projection cave does not match this build")
    extension = _read_exact(
        gdb,
        BOTTOM_EXTENSION_CAVE,
        BOTTOM_EXTENSION_CAVE_SIZE,
        "installed bottom projection extension",
    )
    if not _installed_bottom_extension(extension):
        raise RuntimeError("installed bottom projection extension does not match this build")
    for site in PROJECTION_UPLOAD_SITES:
        _write_exact(gdb, site, PROJECTION_UPLOAD_ORIGINAL, "projection upload restore")
    for cave in PROJECTION_CAVES:
        _write_exact(gdb, cave, bytes(PROJECTION_CAVE_SIZE), "projection cave clear")
    _write_exact(
        gdb,
        BOTTOM_EXTENSION_CAVE,
        bytes(BOTTOM_EXTENSION_CAVE_SIZE),
        "bottom projection extension clear",
    )
    return {
        "restored": True,
        "cleared_bytes": len(PROJECTION_CAVES) * PROJECTION_CAVE_SIZE
        + BOTTOM_EXTENSION_CAVE_SIZE,
    }


def status(gdb: GdbClient) -> dict[str, object]:
    sites = [_read_exact(gdb, site, 4, "projection upload site") for site in PROJECTION_UPLOAD_SITES]
    caves = [_read_exact(gdb, cave, PROJECTION_CAVE_SIZE, "projection cave") for cave in PROJECTION_CAVES]
    extension = _read_exact(
        gdb,
        BOTTOM_EXTENSION_CAVE,
        BOTTOM_EXTENSION_CAVE_SIZE,
        "bottom projection extension",
    )
    installed = all(
        site_raw == encode_branch(site, cave)
        and _installed_cave(cave_raw, cave, site + 4)
        for site, cave, site_raw, cave_raw in zip(
            PROJECTION_UPLOAD_SITES, PROJECTION_CAVES, sites, caves, strict=True
        )
    ) and _installed_bottom_extension(extension)
    stock = all(raw == PROJECTION_UPLOAD_ORIGINAL for raw in sites) and all(
        raw == bytes(PROJECTION_CAVE_SIZE) for raw in caves
    ) and extension == bytes(BOTTOM_EXTENSION_CAVE_SIZE)
    scale = (
        struct.unpack(
            "<f", caves[0][SCALE_LITERAL_OFFSET:SCALE_LITERAL_OFFSET + 4]
        )[0]
        if installed
        else None
    )
    bottom_scale = (
        struct.unpack(
            "<f",
            extension[BOTTOM_SCALE_LITERAL_OFFSET:BOTTOM_SCALE_LITERAL_OFFSET + 4],
        )[0]
        if installed
        else None
    )
    return {
        "state": "installed" if installed else "stock" if stock else "mixed",
        "site_hex": [raw.hex() for raw in sites],
        "cave_hex": [raw.hex() for raw in caves],
        "aspect": STOCK_ASPECT / scale if installed else None,
        "projection_x_scale": scale,
        "menu_bottom_projection_x_scale": bottom_scale,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("apply", "revert", "status", "aspect"))
    parser.add_argument("--aspect", type=float, default=DEFAULT_ASPECT)
    parser.add_argument("--port", type=int, default=24689)
    args = parser.parse_args()

    gdb = GdbClient(port=args.port, timeout=10)
    resumed = False
    try:
        stop = gdb.send("?")
        if not stop.startswith(("S", "T")):
            raise RuntimeError(f"GDB did not report a stopped guest: {stop!r}")
        if args.command == "apply":
            result = apply(gdb, args.aspect)
        elif args.command == "revert":
            result = revert(gdb)
        elif args.command == "aspect":
            result = set_aspect(gdb, args.aspect)
        else:
            result = status(gdb)
        print(json.dumps(result, indent=2, sort_keys=True))
        time.sleep(0.2)
        gdb.cont_all()
        resumed = True
        return 0
    finally:
        if not resumed:
            try:
                gdb.cont_all()
            except BaseException:
                pass
        gdb.sock.close()


if __name__ == "__main__":
    raise SystemExit(main())
