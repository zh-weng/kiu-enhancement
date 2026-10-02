"""Own the reticle position at the one place the game reads it.

``FUN_006D2ABC`` is the clamp: it is handed the reticle object ``A`` in r0, the
camera controller ``P`` in r1, and the four bounds plus a vertical carry in
s0-s4.  It reads the accumulated position from ``A+0xA4/+0xA8``, clamps it, and
turns whatever fell outside the bounds into a camera rotation request at
``P+0xD38/+0xD3C``.

Five load-bearing instructions were re-verified byte for byte against this
project's own image before any of this was written:

    006d2ed4  bl #0x6d2abc                  the call site hooked here
    006d2ad4  vldr s16, [r4, #0xa4]         A+0xA4/+0xA8 is the position
    006d2be4  vstr s0, [r0, #0x138]         r0 = P+0xC00, so P+0xD38/+0xD3C
    00703fec  vldmia r0, {s0, s1}           the consumer of that request
    0074664c  0.5 / +1.39626336 / -1.39626336   a pitch clamped to +/-80.00 deg

Two jobs, one hook, because they share a call site:

*   Observe. Record ``A``, ``P``, viewport ``A+0xE0/+0xE8``, centre
    ``A+0xE4/+0xEC`` and call count. The current driver uses the recorded
    controller and call activity to distinguish ground/air from an idle state.
*   Write.  Replace the position the clamp is about to read: with the centre
    (mode 1), so the overflow is zero and the game asks for no rotation of its
    own, or with a host-supplied point (mode 2), which is direct absolute
    reticle control.

The cave touches r4-r6 only, so r0-r3 and every VFP register survive -- s0-s4
are live arguments here, and a cave that clobbered them would corrupt the
bounds rather than the position.  The position is copied as a word, not a
float, so no VFP register is needed to move it either.

    python -m kiu.patches.pin_reticle apply
    python -m kiu.patches.pin_reticle watch
    python -m kiu.patches.pin_reticle mode 1
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone.arm import ARM_OP_MEM, ARM_REG_PC
from keystone import KS_ARCH_ARM, KS_MODE_ARM, Ks

from ..gdbclient import GdbClient

CODE_BASE = 0x00100000
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"

CLAMP = 0x006D2ABC           # FUN_006D2ABC(A, P, s0..s3 bounds, s4 carry)
CALL_SITE = 0x006D2ED4       # the bl to it inside FUN_006D2D8C

# Writing at the clamp is not the last word.  FUN_006D2D8C then runs
# FUN_006D2820 and FUN_0081739C, and the latter is handed the position by
# pointer -- 006d2ef8 add r1, r4, #0xa4 -- along with the viewport and the
# bounds.  It smooths.  Measured against a real mouse, the position the game
# ended up with trailed the commanded one by 8 px at 2656 counts/s and by
# 196 px at 14049 counts/s, which is exactly the complaint that the same
# physical mouse travel moves the reticle less the faster it is moved.
#
# So the position is asserted a second time, on the instruction after that
# call, where r4 is still the reticle object and nothing else runs before
# FUN_006CF458 reads A+0xA4 to build the aim ray.
LATE_SITE = 0x006D2F10       # strb r7, [r4, #0xd4] -- displaced into the cave
LATE_ORIGINAL_ASM = "strb r7, [r4, #0xd4]"

# Byte-for-byte verified against the USA-v0 image with CODE_SHA256. The runtime
# compares them before patching, without requiring a distributed game binary.
CALL_ORIGINAL = bytes.fromhex("f8feffeb")
LATE_ORIGINAL = bytes.fromhex("d470c4e5")

# The far end of the alignment gap, separate from current aim, menu, 60 FPS
# and projection caves. Every install verifies this range is empty first.
HOOK_VA = 0x00C95F00
HOOK_LIMIT = 0x00C96000
LATE_HOOK_VA = 0x00C95200    # clear of patch_aim_request at 0xC95100
LATE_HOOK_LIMIT = 0x00C95500
MAILBOX_VA = 0x00F360C0

MB_MAGIC = 0x00
MB_VERSION = 0x04
MB_MODE = 0x08               # host: 0 observe, 1 pin to centre, 2 host position
MB_POS_X = 0x0C              # host: float bits, mode 2
MB_POS_Y = 0x10
MB_A = 0x14                  # hook: the reticle object
MB_P = 0x18                  # hook: the camera controller
MB_W = 0x1C                  # hook: A+0xE0, viewport width as a float
MB_H = 0x20                  # hook: A+0xE8
MB_CALLS = 0x24              # hook: times the clamp was called
MB_APPLIED = 0x28            # hook: times the position was replaced
MB_CX = 0x2C                 # hook: A+0xE4, the centre the game itself computed
MB_CY = 0x30
MB_ACT_X = 0x34              # late hook: the position the game's own pipeline
MB_ACT_Y = 0x38              # produced, sampled after its smoothing pass
MB_LATE = 0x3C               # late hook: times it re-asserted the position
MAILBOX_SIZE = 0x40

MAGIC = 0x54455249           # 'IRET'
VERSION = 1

A_POS_X, A_POS_Y = 0xA4, 0xA8
A_W, A_H = 0xE0, 0xE8
A_CX, A_CY = 0xE4, 0xEC


def hook_source(pool_a: int = 0, pool_b: int = 0) -> str:
    return f"""
        push  {{r4, r5, r6, lr}}
        ldr   r5, [pc, #{pool_a}]
        ldr   r6, [r5]
        ldr   r4, [pc, #{pool_b}]
        cmp   r6, r4
        bne   out

        str   r0, [r5, #{MB_A}]
        str   r1, [r5, #{MB_P}]
        ldr   r6, [r0, #{A_W}]
        str   r6, [r5, #{MB_W}]
        ldr   r6, [r0, #{A_H}]
        str   r6, [r5, #{MB_H}]
        ldr   r6, [r0, #{A_CX}]
        str   r6, [r5, #{MB_CX}]
        ldr   r6, [r0, #{A_CY}]
        str   r6, [r5, #{MB_CY}]
        ldr   r6, [r5, #{MB_CALLS}]
        add   r6, r6, #1
        str   r6, [r5, #{MB_CALLS}]

        ldr   r6, [r5, #{MB_MODE}]
        cmp   r6, #1
        beq   centre
        cmp   r6, #2
        beq   host
        b     out

    centre:
        ldr   r6, [r0, #{A_CX}]
        str   r6, [r0, #{A_POS_X}]
        ldr   r6, [r0, #{A_CY}]
        str   r6, [r0, #{A_POS_Y}]
        b     applied

    host:
        ldr   r6, [r5, #{MB_POS_X}]
        str   r6, [r0, #{A_POS_X}]
        ldr   r6, [r5, #{MB_POS_Y}]
        str   r6, [r0, #{A_POS_Y}]

    applied:
        ldr   r6, [r5, #{MB_APPLIED}]
        add   r6, r6, #1
        str   r6, [r5, #{MB_APPLIED}]

    out:
        pop   {{r4, r5, r6, lr}}
        b     #0x{CLAMP:x}
    """


LDR_POOL_INDEX = (1, 3)      # the two `ldr rX, [pc, ...]`, in order


def assemble_hook() -> bytes:
    ks = Ks(KS_ARCH_ARM, KS_MODE_ARM)
    a = b = 0
    for _ in range(5):
        body = bytes(ks.asm(hook_source(a, b), HOOK_VA)[0])
        want_a = len(body) - (LDR_POOL_INDEX[0] * 4 + 8)
        want_b = len(body) + 4 - (LDR_POOL_INDEX[1] * 4 + 8)
        if (a, b) == (want_a, want_b):
            break
        a, b = want_a, want_b
    else:
        raise RuntimeError("literal pool offsets did not settle")
    blob = body + struct.pack("<II", MAILBOX_VA, MAGIC)
    _check_pool(blob)
    return blob


def _check_pool(blob: bytes) -> None:
    """A wrong pc-relative offset assembles and installs silently.

    Four separate times in this project a hand-written ``[pc, #N]`` landed
    inside the cave's own code, and the hook then wrote through whatever the
    instruction there happened to encode.  This is the assertion that caught it
    every time after.
    """
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    targets = [
        insn.address + 8 + op.mem.disp
        for insn in md.disasm(blob, HOOK_VA)
        for op in insn.operands
        if op.type == ARM_OP_MEM and op.mem.base == ARM_REG_PC
    ]
    pool = HOOK_VA + len(blob) - 8
    if targets != [pool, pool + 4]:
        raise RuntimeError(
            f"literal loads reach {[hex(t) for t in targets]}, pool at {hex(pool)}"
        )
    if struct.unpack_from("<II", blob, len(blob) - 8) != (MAILBOX_VA, MAGIC):
        raise RuntimeError("pool words are not the mailbox address and magic")
    tail = next(i for i in md.disasm(blob, HOOK_VA)
                if i.address == HOOK_VA + len(blob) - 12)
    if (tail.mnemonic, tail.op_str) != ("b", f"#0x{CLAMP:x}"):
        raise RuntimeError(
            f"cave does not tail into the clamp: {tail.mnemonic} {tail.op_str}")


def late_hook_source(pool_a: int = 0, pool_b: int = 0) -> str:
    """Re-assert the position after the game's own smoothing pass.

    r4 is the reticle object here and r7 holds the value the displaced
    instruction stores, so that store goes first and only r5-r7 are borrowed
    afterwards.  Nothing is called, so the stack alignment of a three-register
    push does not matter.
    """
    return f"""
        {LATE_ORIGINAL_ASM}
        push  {{r5, r6, r7}}
        ldr   r5, [pc, #{pool_a}]
        ldr   r6, [r5]
        ldr   r7, [pc, #{pool_b}]
        cmp   r6, r7
        bne   lateout
        ldr   r6, [r5, #{MB_MODE}]
        cmp   r6, #2
        bne   lateout
        ldr   r6, [r4, #{A_POS_X}]
        str   r6, [r5, #{MB_ACT_X}]
        ldr   r6, [r4, #{A_POS_Y}]
        str   r6, [r5, #{MB_ACT_Y}]
        ldr   r6, [r5, #{MB_POS_X}]
        str   r6, [r4, #{A_POS_X}]
        ldr   r6, [r5, #{MB_POS_Y}]
        str   r6, [r4, #{A_POS_Y}]
        ldr   r6, [r5, #{MB_LATE}]
        add   r6, r6, #1
        str   r6, [r5, #{MB_LATE}]
    lateout:
        pop   {{r5, r6, r7}}
        bx    lr
    """


LATE_POOL_INDEX = (2, 4)     # the displaced store and the push come first


def late_original() -> bytes:
    return LATE_ORIGINAL


def assemble_late_hook() -> bytes:
    ks = Ks(KS_ARCH_ARM, KS_MODE_ARM)
    a = b = 0
    for _ in range(5):
        body = bytes(ks.asm(late_hook_source(a, b), LATE_HOOK_VA)[0])
        want_a = len(body) - (LATE_POOL_INDEX[0] * 4 + 8)
        want_b = len(body) + 4 - (LATE_POOL_INDEX[1] * 4 + 8)
        if (a, b) == (want_a, want_b):
            break
        a, b = want_a, want_b
    else:
        raise RuntimeError("late literal pool offsets did not settle")
    blob = body + struct.pack("<II", MAILBOX_VA, MAGIC)
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    targets = [insn.address + 8 + op.mem.disp
               for insn in md.disasm(blob, LATE_HOOK_VA)
               for op in insn.operands
               if op.type == ARM_OP_MEM and op.mem.base == ARM_REG_PC]
    pool = LATE_HOOK_VA + len(blob) - 8
    if targets != [pool, pool + 4]:
        raise RuntimeError(
            f"late literal loads reach {[hex(t) for t in targets]}, pool {hex(pool)}")
    first = next(md.disasm(blob, LATE_HOOK_VA))
    if first.bytes != late_original():
        raise RuntimeError("the displaced instruction is not first in the cave")
    return blob


def encode_branch(site: int, target: int, link: bool = True) -> bytes:
    offset = (target - (site + 8)) >> 2
    if not -(1 << 23) <= offset < (1 << 23):
        raise ValueError(f"branch 0x{site:08x} -> 0x{target:08x} out of range")
    return struct.pack("<I", (0xEB000000 if link else 0xEA000000) | (offset & 0xFFFFFF))


def mailbox_image(mode: int = 0) -> bytes:
    return struct.pack("<III", MAGIC, VERSION, mode) + bytes(MAILBOX_SIZE - 0x0C)


def _as_float(bits: int) -> float:
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def read_mailbox(gdb: GdbClient) -> dict[str, object]:
    raw = gdb.read_mem(MAILBOX_VA, MAILBOX_SIZE)
    magic, version, mode, px, py = struct.unpack_from("<IIIII", raw, 0)
    a, p, w, h, calls, applied, cx, cy = struct.unpack_from("<IIIIIIII", raw, MB_A)
    ax, ay = struct.unpack_from("<ff", raw, MB_ACT_X)
    late = struct.unpack_from("<I", raw, MB_LATE)[0]
    return {
        "magic_ok": magic == MAGIC,
        "version": version,
        "mode": mode,
        "host_pos": (_as_float(px), _as_float(py)),
        "A": f"0x{a:08x}",
        "P": f"0x{p:08x}",
        "viewport": (_as_float(w), _as_float(h)),
        "centre": (_as_float(cx), _as_float(cy)),
        "calls": calls,
        "applied": applied,
        "actual": (ax, ay),
        "late_writes": late,
    }


def status(gdb: GdbClient) -> dict[str, object]:
    blob = assemble_hook()
    late = assemble_late_hook()
    site = gdb.read_mem(CALL_SITE, 4)
    late_site = gdb.read_mem(LATE_SITE, 4)
    cave = gdb.read_mem(HOOK_VA, len(blob))
    late_cave = gdb.read_mem(LATE_HOOK_VA, len(late))
    installed = (site == encode_branch(CALL_SITE, HOOK_VA)
                 and late_site == encode_branch(LATE_SITE, LATE_HOOK_VA)
                 and cave == blob and late_cave == late)
    stock = (site == CALL_ORIGINAL and late_site == LATE_ORIGINAL
             and cave == bytes(len(blob)) and late_cave == bytes(len(late)))
    return {
        "state": "installed" if installed else "stock" if stock else "unknown",
        "hook_matches": installed,
        "mailbox": read_mailbox(gdb),
    }


def apply(gdb: GdbClient, *, resume: bool = True) -> dict[str, object]:
    blob = assemble_hook()
    if HOOK_VA + len(blob) > HOOK_LIMIT:
        raise RuntimeError(f"hook is {len(blob)} bytes and does not fit its cave")
    if gdb.read_mem(CALL_SITE, 4) != CALL_ORIGINAL:
        raise RuntimeError("the clamp call site is not stock; another patch owns it")
    if gdb.read_mem(HOOK_VA, len(blob)) != bytes(len(blob)):
        raise RuntimeError(f"hook cave 0x{HOOK_VA:08x} is not empty")
    late = assemble_late_hook()
    if LATE_HOOK_VA + len(late) > LATE_HOOK_LIMIT:
        raise RuntimeError(f"late hook is {len(late)} bytes and does not fit")
    if gdb.read_mem(LATE_SITE, 4) != late_original():
        raise RuntimeError("the late site is not stock; another patch owns it")
    if gdb.read_mem(LATE_HOOK_VA, len(late)) != bytes(len(late)):
        raise RuntimeError(f"late cave 0x{LATE_HOOK_VA:08x} is not empty")
    gdb.write_mem(HOOK_VA, blob)
    gdb.write_mem(LATE_HOOK_VA, late)
    gdb.write_mem(MAILBOX_VA, mailbox_image(mode=0))
    gdb.write_mem(CALL_SITE, encode_branch(CALL_SITE, HOOK_VA))
    gdb.write_mem(LATE_SITE, encode_branch(LATE_SITE, LATE_HOOK_VA))
    if resume:
        gdb.cont_all()
    return {"patched": True, "bytes": len(blob), "cave": f"0x{HOOK_VA:08x}",
            "late_bytes": len(late), "late_cave": f"0x{LATE_HOOK_VA:08x}",
            "build_sha256": CODE_SHA256,
            "sites": [
                {"address": f"0x{address:08x}", "original_hex": original.hex(),
                 "replacement_hex": replacement.hex(), "build_sha256": CODE_SHA256}
                for address, original, replacement in (
                    (CALL_SITE, CALL_ORIGINAL, encode_branch(CALL_SITE, HOOK_VA)),
                    (LATE_SITE, LATE_ORIGINAL, encode_branch(LATE_SITE, LATE_HOOK_VA)),
                    (HOOK_VA, bytes(len(blob)), blob),
                    (LATE_HOOK_VA, bytes(len(late)), late),
                )]}


def revert(gdb: GdbClient, *, resume: bool = True) -> dict[str, object]:
    blob = assemble_hook()
    late = assemble_late_hook()
    for address, expected in (
        (CALL_SITE, encode_branch(CALL_SITE, HOOK_VA)),
        (LATE_SITE, encode_branch(LATE_SITE, LATE_HOOK_VA)),
        (HOOK_VA, blob),
        (LATE_HOOK_VA, late),
    ):
        if gdb.read_mem(address, len(expected)) != expected:
            raise RuntimeError(f"0x{address:08x} is not this reticle hook; refusing to restore")
    gdb.write_mem(CALL_SITE, CALL_ORIGINAL)
    gdb.write_mem(LATE_SITE, late_original())
    gdb.write_mem(HOOK_VA, bytes(len(blob)))
    gdb.write_mem(LATE_HOOK_VA, bytes(len(late)))
    gdb.write_mem(MAILBOX_VA, bytes(MAILBOX_SIZE))
    if resume:
        gdb.cont_all()
    return {"restored": gdb.read_mem(CALL_SITE, 4) == CALL_ORIGINAL
                        and gdb.read_mem(LATE_SITE, 4) == late_original()}


def set_mode(gdb: GdbClient, mode: int) -> None:
    gdb.write_mem(MAILBOX_VA + MB_MODE, struct.pack("<I", int(mode)))


def push_position(gdb: GdbClient, x: float, y: float) -> None:
    gdb.write_mem(MAILBOX_VA + MB_POS_X, struct.pack("<ff", float(x), float(y)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=("apply", "revert", "status", "asm", "mode", "watch", "pos"))
    parser.add_argument("value", nargs="*")
    parser.add_argument("--port", type=int, default=24689)
    parser.add_argument("--seconds", type=float, default=20.0)
    args = parser.parse_args()

    if args.command == "asm":
        blob = assemble_hook()
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        for i in md.disasm(blob, HOOK_VA):
            print(f"  {i.address:08x}  {i.bytes.hex():8s}  {i.mnemonic:8s} {i.op_str}")
        print(f"{len(blob)} bytes at 0x{HOOK_VA:08x}, "
              f"call site 0x{CALL_SITE:08x} original {CALL_ORIGINAL.hex()}")
        print()
        late = assemble_late_hook()
        for i in md.disasm(late, LATE_HOOK_VA):
            print(f"  {i.address:08x}  {i.bytes.hex():8s}  "
                  f"{i.mnemonic:8s} {i.op_str}")
        print(f"{len(late)} bytes at 0x{LATE_HOOK_VA:08x}, "
              f"late site 0x{LATE_SITE:08x} original {late_original().hex()}")
        return 0

    gdb = GdbClient(port=args.port, timeout=15)
    try:
        gdb.send("?")
        if args.command == "apply":
            print(json.dumps(apply(gdb), indent=2))
        elif args.command == "revert":
            print(json.dumps(revert(gdb), indent=2))
        elif args.command == "mode":
            set_mode(gdb, int(args.value[0]))
            gdb.cont_all()
            print(json.dumps(read_mailbox(gdb), indent=2))
        elif args.command == "pos":
            push_position(gdb, float(args.value[0]), float(args.value[1]))
            gdb.cont_all()
            print(json.dumps(read_mailbox(gdb), indent=2))
        elif args.command == "watch":
            gdb.cont_all()
            deadline = time.time() + args.seconds
            previous = None
            while time.time() < deadline:
                box = read_mailbox(gdb)
                rate = "" if previous is None else f"  +{box['calls'] - previous:d}/s"
                previous = box["calls"]
                print(f"  A={box['A']} P={box['P']} viewport={box['viewport']} "
                      f"centre={box['centre']} calls={box['calls']} "
                      f"applied={box['applied']}{rate}")
                gdb.cont_all()
                time.sleep(1.0)
        else:
            print(json.dumps(status(gdb), indent=2))
    finally:
        try:
            gdb.cont_all()
        except BaseException:
            pass
        gdb.sock.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
