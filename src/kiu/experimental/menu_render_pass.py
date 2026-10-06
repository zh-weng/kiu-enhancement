"""Route KI:U's bottom menu render pass into the top framebuffer.

This is an in-game render-target patch, not an Azahar screen-layout shortcut.
For ``BottomRenderPass`` only, the pre-pass hook checks the game's existing
menu gate (``uiMgr+0x140 == NULL``).  While the gate is active it temporarily
uses screen target 0 and a 400-pixel viewport.  The post-pass hook resolves the
same top target and immediately restores the callback's stock 320-pixel width.

The bottom pass therefore replaces the top image in menus without a CPU
framebuffer copy.  Width 400 here is only the pass viewport: menus still use
their original 320-wide authored layouts, so some leave black space.  The
physical bottom surface can retain an older frame while its pass is redirected.
Battle rendering remains target 1 / width 320.  Apply and revert are locked
to the exact USA-v0 code bytes and zero-filled caves.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

from ..gdbclient import GdbClient


TITLE_ID = "0004000000030100"
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"
CODE_BASE = 0x00100000
CODE_PATH = Path(__file__).resolve().parents[3] / "local/extracted/exefs_code.bin"

PRE_SITE = 0x009167C0
POST_SITE = 0x0091688C
THUNK_ORIGINAL = bytes.fromhex("541080e2060091e80000a0e1")

# Reuse the zero window formerly occupied by the superseded final-framebuffer
# CPU-copy experiment.  The stable 60-FPS caves begin at 0x00C957C0.
PRE_CAVE = 0x00C95640
POST_CAVE = 0x00C95700
CAVE_SIZE = 0xC0

UI_MANAGER_SLOT = 0x00D636BC
BOTTOM_NAME_0 = 0x74746F42  # b"Bott"
BOTTOM_NAME_1 = 0x65526D6F  # b"omRe"
STOCK_WIDTH = 320
TOP_WIDTH = 400
ACTIVE_MARKER = 0x00F36820
MARKER_INACTIVE = 0
MARKER_ACTIVE = 1


def encode_branch(source: int, target: int) -> bytes:
    if source & 3 or target & 3:
        raise ValueError("ARM branch endpoints must be word aligned")
    displacement = target - (source + 8)
    if displacement & 3 or not -(1 << 25) <= displacement < (1 << 25):
        raise ValueError("ARM branch target is out of range")
    return struct.pack("<I", 0xEA000000 | ((displacement >> 2) & 0x00FFFFFF))


def _bottom_and_menu_source(*, restore_width: bool, return_address: int) -> str:
    marker_value = MARKER_INACTIVE if restore_width else MARKER_ACTIVE
    width_action = (
        f"movw  r3, #{STOCK_WIDTH}\n"
        "        strne r3, [r1, #0x4c]"
        if restore_width
        else
        f"movw  r3, #{TOP_WIDTH}\n"
        "        strne r3, [r1, #0x4c]"
    )
    return f"""
        mov   r12, #0
        ldr   r1, [r0, #0x18]
        movw  r2, #{BOTTOM_NAME_0 & 0xffff}
        movt  r2, #{BOTTOM_NAME_0 >> 16}
        cmp   r1, r2
        bne   replay
        ldr   r1, [r0, #0x1c]
        movw  r2, #{BOTTOM_NAME_1 & 0xffff}
        movt  r2, #{BOTTOM_NAME_1 >> 16}
        cmp   r1, r2
        bne   replay

        movw  r1, #{UI_MANAGER_SLOT & 0xffff}
        movt  r1, #{UI_MANAGER_SLOT >> 16}
        ldr   r1, [r1]
        cmp   r1, #0
        beq   menu
        ldr   r1, [r1, #0x140]
        cmp   r1, #0
        bne   stock

    menu:
        mov   r12, #1
        movw  r2, #{ACTIVE_MARKER & 0xffff}
        movt  r2, #{ACTIVE_MARKER >> 16}
        mov   r3, #{marker_value}
        str   r3, [r2]
        ldr   r1, [r0, #0x40]
        cmp   r1, #0
        {width_action}
        b     replay

    stock:
        movw  r2, #{ACTIVE_MARKER & 0xffff}
        movt  r2, #{ACTIVE_MARKER >> 16}
        mov   r3, #0
        str   r3, [r2]
        ldr   r1, [r0, #0x40]
        cmp   r1, #0
        movw  r3, #{STOCK_WIDTH}
        strne r3, [r1, #0x4c]

    replay:
        add   r1, r0, #0x54
        ldmia r1, {{r1, r2}}
        cmp   r12, #1
        moveq r2, #0
        b     #{return_address}
    """


def build_cave(cave: int, *, restore_width: bool, return_address: int) -> bytes:
    if getattr(sys, "frozen", False):
        import kiu_launcher_payloads as payloads
        if payloads.BUILD_SHA256 != CODE_SHA256:
            raise RuntimeError("frozen menu hook targets an unsupported build")
        raw = payloads.MENU_POST_HOOK if restore_width else payloads.MENU_PRE_HOOK
        if type(raw) is not bytes or len(raw) != CAVE_SIZE:
            raise RuntimeError("invalid frozen menu hook payload")
        return raw
    from keystone import KS_ARCH_ARM, KS_MODE_ARM, Ks
    ks = Ks(KS_ARCH_ARM, KS_MODE_ARM)
    raw = bytes(
        ks.asm(
            _bottom_and_menu_source(
                restore_width=restore_width,
                return_address=return_address,
            ),
            cave,
        )[0]
    )
    if len(raw) > CAVE_SIZE:
        raise RuntimeError(f"render-pass cave is {len(raw)} bytes, window is {CAVE_SIZE}")
    return raw + bytes(CAVE_SIZE - len(raw))


PRE_CAVE_BYTES = build_cave(PRE_CAVE, restore_width=False, return_address=PRE_SITE + 12)
POST_CAVE_BYTES = build_cave(POST_CAVE, restore_width=True, return_address=POST_SITE + 12)
PRE_REPLACEMENT = encode_branch(PRE_SITE, PRE_CAVE)
POST_REPLACEMENT = encode_branch(POST_SITE, POST_CAVE)


def _load_image() -> bytes:
    """Optional developer check against a locally prepared USA-v0 code image.

    Runtime patching uses the declared expected bytes below and does not need
    this file. Full-image identity is verified by the prepare workflow.
    """
    image = CODE_PATH.read_bytes()
    digest = hashlib.sha256(image).hexdigest()
    if digest != CODE_SHA256:
        raise RuntimeError(f"wrong code.bin: sha256 {digest}, expected {CODE_SHA256}")
    for site in (PRE_SITE, POST_SITE):
        offset = site - CODE_BASE
        actual = image[offset : offset + len(THUNK_ORIGINAL)]
        if actual != THUNK_ORIGINAL:
            raise RuntimeError(
                f"USA-v0 thunk mismatch at 0x{site:08x}: "
                f"{actual.hex()} != {THUNK_ORIGINAL.hex()}"
            )
    for cave in (PRE_CAVE, POST_CAVE):
        offset = cave - CODE_BASE
        if image[offset : offset + CAVE_SIZE] != bytes(CAVE_SIZE):
            raise RuntimeError(f"declared cave at 0x{cave:08x} is not zero in code.bin")
    return image


def _read_exact(gdb: GdbClient, address: int, size: int, label: str) -> bytes:
    raw = bytes(gdb.read_mem(address, size))
    if len(raw) != size:
        raise RuntimeError(f"short {label} read at 0x{address:08x}")
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


def apply(gdb: GdbClient) -> dict[str, object]:
    _expect(gdb, PRE_SITE, THUNK_ORIGINAL, "stock pre-pass thunk")
    _expect(gdb, POST_SITE, THUNK_ORIGINAL, "stock post-pass thunk")
    _expect(gdb, PRE_CAVE, bytes(CAVE_SIZE), "empty pre-pass cave")
    _expect(gdb, POST_CAVE, bytes(CAVE_SIZE), "empty post-pass cave")
    _expect(gdb, ACTIVE_MARKER, struct.pack("<I", MARKER_INACTIVE), "inactive pass marker")
    try:
        _write_exact(
            gdb,
            ACTIVE_MARKER,
            struct.pack("<I", MARKER_INACTIVE),
            "pass marker reset",
        )
        _write_exact(gdb, PRE_CAVE, PRE_CAVE_BYTES, "pre-pass cave install")
        _write_exact(gdb, POST_CAVE, POST_CAVE_BYTES, "post-pass cave install")
        _write_exact(gdb, PRE_SITE, PRE_REPLACEMENT, "pre-pass redirect")
        _write_exact(gdb, POST_SITE, POST_REPLACEMENT, "post-pass redirect")
    except BaseException:
        try:
            gdb.write_mem(PRE_SITE, THUNK_ORIGINAL[:4])
            gdb.write_mem(POST_SITE, THUNK_ORIGINAL[:4])
            gdb.write_mem(PRE_CAVE, bytes(CAVE_SIZE))
            gdb.write_mem(POST_CAVE, bytes(CAVE_SIZE))
        except BaseException:
            pass
        raise
    return {
        "installed": True,
        "behavior": "menu: BottomRenderPass target=0 width=400; battle: target=1 width=320",
        "build_sha256": CODE_SHA256,
        "sites": [
            {
                "address": f"0x{site:08x}",
                "original_hex": THUNK_ORIGINAL[:4].hex(),
                "replacement_hex": replacement.hex(),
                "build_sha256": CODE_SHA256,
            }
            for site, replacement in (
                (PRE_SITE, PRE_REPLACEMENT),
                (POST_SITE, POST_REPLACEMENT),
            )
        ],
    }


def status(gdb: GdbClient) -> dict[str, object]:
    site_values = (
        _read_exact(gdb, PRE_SITE, len(THUNK_ORIGINAL), "pre-pass site"),
        _read_exact(gdb, POST_SITE, len(THUNK_ORIGINAL), "post-pass site"),
    )
    cave_values = (
        _read_exact(gdb, PRE_CAVE, CAVE_SIZE, "pre-pass cave"),
        _read_exact(gdb, POST_CAVE, CAVE_SIZE, "post-pass cave"),
    )
    marker = struct.unpack("<I", _read_exact(gdb, ACTIVE_MARKER, 4, "pass marker"))[0]
    installed = site_values == (
        PRE_REPLACEMENT + THUNK_ORIGINAL[4:], POST_REPLACEMENT + THUNK_ORIGINAL[4:]
    ) and cave_values == (
        PRE_CAVE_BYTES,
        POST_CAVE_BYTES,
    ) and marker in (MARKER_INACTIVE, MARKER_ACTIVE)
    stock = site_values == (THUNK_ORIGINAL, THUNK_ORIGINAL) and cave_values == (
        bytes(CAVE_SIZE),
        bytes(CAVE_SIZE),
    ) and marker == MARKER_INACTIVE
    return {
        "state": "installed" if installed else "stock" if stock else "mixed",
        "site_hex": [value.hex() for value in site_values],
        "active_marker": marker,
        "build_sha256": CODE_SHA256,
    }


def can_revert(gdb: GdbClient) -> bool:
    """Never erase a cave in execution or split a redirected pre/post pair."""
    marker = _read_exact(gdb, ACTIVE_MARKER, 4, "pass marker")
    if marker == struct.pack("<I", MARKER_ACTIVE):
        return False
    if marker != struct.pack("<I", MARKER_INACTIVE):
        raise RuntimeError("unknown render-pass marker; restoration refused")
    for thread_id in gdb.list_threads():
        gdb.select_general_thread(thread_id)
        registers = gdb.read_registers()
        pc = registers[15]
        if any(start <= pc < start + CAVE_SIZE for start in (PRE_CAVE, POST_CAVE)):
            return False
    return True


def revert(gdb: GdbClient) -> dict[str, object]:
    _expect(gdb, PRE_SITE, PRE_REPLACEMENT + THUNK_ORIGINAL[4:], "installed pre-pass thunk")
    _expect(gdb, POST_SITE, POST_REPLACEMENT + THUNK_ORIGINAL[4:], "installed post-pass thunk")
    _expect(gdb, PRE_CAVE, PRE_CAVE_BYTES, "installed pre-pass cave")
    _expect(gdb, POST_CAVE, POST_CAVE_BYTES, "installed post-pass cave")
    if not can_revert(gdb):
        raise RuntimeError("menu render pass is busy; resume and retry restoration")
    _write_exact(gdb, PRE_SITE, THUNK_ORIGINAL[:4], "pre-pass restore")
    _write_exact(gdb, POST_SITE, THUNK_ORIGINAL[:4], "post-pass restore")
    _write_exact(
        gdb,
        ACTIVE_MARKER,
        struct.pack("<I", MARKER_INACTIVE),
        "pass marker clear",
    )
    _write_exact(gdb, PRE_CAVE, bytes(CAVE_SIZE), "pre-pass cave clear")
    _write_exact(gdb, POST_CAVE, bytes(CAVE_SIZE), "post-pass cave clear")
    return {"restored": True, "cleared_bytes": CAVE_SIZE * 2}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("command", choices=("apply", "revert", "status"))
    parser.add_argument("--port", type=int, default=24689)
    args = parser.parse_args()

    gdb = GdbClient(port=args.port, timeout=15)
    resumed = False
    try:
        stop = gdb.send("?")
        if not stop.startswith(("S", "T")):
            raise RuntimeError(f"GDB did not report a stopped guest: {stop!r}")
        result = {
            "apply": apply,
            "revert": revert,
            "status": status,
        }[args.command](gdb)
        print(json.dumps(result, indent=2, sort_keys=True))
        time.sleep(0.15)
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
