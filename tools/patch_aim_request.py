"""Feed the camera rotation request directly, one value per consumption.

``FUN_006D2ABC`` turns reticle overflow into a rotation request at
``P+0xD38/+0xD3C``; ``FUN_00703B68`` loads that pair and hands it to
``FUN_007462A4``, which merges it into the controller's angles and tail-calls
``FUN_006063E8`` to write yaw/pitch at ``P+0x2AC/+0x2B0`` and rebuild the
direction and eye.  Measured: 0.05 held in ``P+0xD38`` for two seconds swung
yaw by 20 degrees, and the rendered view orbited with it.

This hooks the consumption itself:

    00703fe0  add    r0, r4, #0xc00
    00703fe4  add    r0, r0, #0x138      ; r0 = P+0xD38
    00703fe8  vmov.f32 s2, s16           ; dt
    00703fec  vldmia r0, {s0, s1}        ; the request
    00703ffc  add    r1, r4, #0x278      ; so P = r1 - 0x278
    00704004  bl     #0x7462a4           <- replaced

The hook executes at the game's request-consumption point, replacing the
game's request so the reticle's edge push is suppressed as well. The cave
clears its input after reading, limiting persistent stale requests. This does
NOT guarantee exactly-once host delivery: the current host read-modify-write
can race with guest consume-and-clear. See docs/KNOWLEDGE.md for ABI limits.

Downstream camera logic is untouched, including the +/-1.39626336 rad
literal limit. Dynamic bounds can be narrower; this is not a guarantee of an
actual +/-80-degree rendered view.

The cave writes s0 and s1, which is the point, and nothing else: s2 is dt and
the callee reads three more arguments off the stack at [sp, #0x60], [sp, #0x64]
and [sp, #0x68], so it has to be entered with sp exactly as the call site left
it.  That is why this tails into the callee with ``b`` after restoring the
stack rather than calling it.

    python tools/patch_aim_request.py apply
    python tools/patch_aim_request.py turn 0.05 0
"""
from __future__ import annotations

import argparse
import json
import struct
import sys
import time
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone.arm import ARM_OP_MEM, ARM_REG_PC
from keystone import KS_ARCH_ARM, KS_MODE_ARM, Ks

sys.path.insert(0, str(Path(__file__).resolve().parent))
from gdbclient import GdbClient  # noqa: E402

CODE_BASE = 0x00100000
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"

MERGE = 0x007462A4           # FUN_007462A4(state, Q, A, ..., s0 yaw, s1 pitch, s2 dt)
CALL_SITE = 0x00704004
P_FROM_R1 = 0x278            # r1 = P + 0x278 at the call site

# Verified against the USA-v0 image with CODE_SHA256. Runtime installs still
# compare these bytes before writing; end users do not need extracted code.bin.
CALL_ORIGINAL = bytes.fromhex("a60801eb")
P_SETUP_ORIGINAL = bytes.fromhex("9e1f84e2")  # add r1, r4, #0x278

# The head of the alignment gap, well clear of every other cave in this project.
HOOK_VA = 0x00C95100
HOOK_LIMIT = 0x00C95500
MAILBOX_VA = 0x00F36100

MB_MAGIC = 0x00
MB_VERSION = 0x04
MB_GATE = 0x08               # host: 0 stock, 1 the host owns the request
MB_YAW = 0x0C                # host: float, consumed and zeroed by the cave
MB_PITCH = 0x10
MB_FRAMES = 0x14             # hook: consumptions seen
MB_APPLIED = 0x18            # hook: consumptions the host supplied
MB_P = 0x1C                  # hook: the camera controller, recovered from r1
MAILBOX_SIZE = 0x20

MAGIC = 0x4D494158           # 'XAIM'
VERSION = 1


def hook_source(pool_a: int = 0, pool_b: int = 0) -> str:
    return f"""
        push  {{r4, r5, r6, lr}}
        ldr   r5, [pc, #{pool_a}]
        ldr   r6, [r5]
        ldr   r4, [pc, #{pool_b}]
        cmp   r6, r4
        bne   out

        sub   r6, r1, #{P_FROM_R1}
        str   r6, [r5, #{MB_P}]
        ldr   r6, [r5, #{MB_FRAMES}]
        add   r6, r6, #1
        str   r6, [r5, #{MB_FRAMES}]

        ldr   r6, [r5, #{MB_GATE}]
        cmp   r6, #1
        bne   out

        vldr  s0, [r5, #{MB_YAW}]
        vldr  s1, [r5, #{MB_PITCH}]
        mov   r6, #0
        str   r6, [r5, #{MB_YAW}]
        str   r6, [r5, #{MB_PITCH}]

        ldr   r6, [r5, #{MB_APPLIED}]
        add   r6, r6, #1
        str   r6, [r5, #{MB_APPLIED}]

    out:
        pop   {{r4, r5, r6, lr}}
        b     #0x{MERGE:x}
    """


LDR_POOL_INDEX = (1, 3)


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
    """A wrong pc-relative offset assembles and installs silently."""
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
            f"literal loads reach {[hex(t) for t in targets]}, pool at {hex(pool)}")
    if struct.unpack_from("<II", blob, len(blob) - 8) != (MAILBOX_VA, MAGIC):
        raise RuntimeError("pool words are not the mailbox address and magic")
    tail = next(i for i in md.disasm(blob, HOOK_VA)
                if i.address == HOOK_VA + len(blob) - 12)
    if (tail.mnemonic, tail.op_str) != ("b", f"#0x{MERGE:x}"):
        raise RuntimeError(f"cave does not tail into the merge: "
                           f"{tail.mnemonic} {tail.op_str}")


def encode_branch(site: int, target: int, link: bool = True) -> bytes:
    offset = (target - (site + 8)) >> 2
    if not -(1 << 23) <= offset < (1 << 23):
        raise ValueError(f"branch 0x{site:08x} -> 0x{target:08x} out of range")
    return struct.pack("<I", (0xEB000000 if link else 0xEA000000) | (offset & 0xFFFFFF))


def mailbox_image(gate: int = 0) -> bytes:
    return struct.pack("<III", MAGIC, VERSION, gate) + bytes(MAILBOX_SIZE - 0x0C)


def read_mailbox(gdb: GdbClient) -> dict[str, object]:
    raw = gdb.read_mem(MAILBOX_VA, MAILBOX_SIZE)
    magic, version, gate = struct.unpack_from("<III", raw, 0)
    yaw, pitch = struct.unpack_from("<ff", raw, MB_YAW)
    frames, applied, p = struct.unpack_from("<III", raw, MB_FRAMES)
    return {"magic_ok": magic == MAGIC, "version": version, "gate": gate,
            "pending": (yaw, pitch), "frames": frames, "applied": applied,
            "P": f"0x{p:08x}"}


def status(gdb: GdbClient) -> dict[str, object]:
    blob = assemble_hook()
    site = gdb.read_mem(CALL_SITE, 4)
    cave = gdb.read_mem(HOOK_VA, len(blob))
    setup_matches = gdb.read_mem(CALL_SITE - 8, 4) == P_SETUP_ORIGINAL
    installed = setup_matches and site == encode_branch(CALL_SITE, HOOK_VA) and cave == blob
    stock = setup_matches and site == CALL_ORIGINAL and cave == bytes(len(blob))
    state = "installed" if installed else "stock" if stock else "unknown"
    return {"state": state,
            "hook_matches": installed,
            "mailbox": read_mailbox(gdb)}


def apply(gdb: GdbClient, *, resume: bool = True) -> dict[str, object]:
    blob = assemble_hook()
    if HOOK_VA + len(blob) > HOOK_LIMIT:
        raise RuntimeError(f"hook is {len(blob)} bytes and does not fit its cave")
    if gdb.read_mem(CALL_SITE, 4) != CALL_ORIGINAL:
        raise RuntimeError("the merge call site is not stock; another patch owns it")
    if gdb.read_mem(CALL_SITE - 8, 4) != P_SETUP_ORIGINAL:
        raise RuntimeError("the merge argument setup does not match USA-v0")
    if gdb.read_mem(HOOK_VA, len(blob)) != bytes(len(blob)):
        raise RuntimeError(f"hook cave 0x{HOOK_VA:08x} is not empty")
    gdb.write_mem(HOOK_VA, blob)
    gdb.write_mem(MAILBOX_VA, mailbox_image(gate=0))
    gdb.write_mem(CALL_SITE, encode_branch(CALL_SITE, HOOK_VA))
    if resume:
        gdb.cont_all()
    return {"patched": True, "bytes": len(blob), "cave": f"0x{HOOK_VA:08x}",
            "build_sha256": CODE_SHA256,
            "sites": [
                {"address": f"0x{address:08x}", "original_hex": original.hex(),
                 "replacement_hex": replacement.hex(), "build_sha256": CODE_SHA256}
                for address, original, replacement in (
                    (CALL_SITE, CALL_ORIGINAL, encode_branch(CALL_SITE, HOOK_VA)),
                    (HOOK_VA, bytes(len(blob)), blob),
                )]}


def revert(gdb: GdbClient, *, resume: bool = True) -> dict[str, object]:
    blob = assemble_hook()
    if gdb.read_mem(CALL_SITE - 8, 4) != P_SETUP_ORIGINAL:
        raise RuntimeError("the merge argument setup does not match USA-v0")
    if gdb.read_mem(CALL_SITE, 4) != encode_branch(CALL_SITE, HOOK_VA):
        raise RuntimeError("the merge call site is not this hook; refusing to restore")
    if gdb.read_mem(HOOK_VA, len(blob)) != blob:
        raise RuntimeError("the rotation-request cave is not this hook")
    gdb.write_mem(CALL_SITE, CALL_ORIGINAL)
    gdb.write_mem(HOOK_VA, bytes(len(blob)))
    gdb.write_mem(MAILBOX_VA, bytes(MAILBOX_SIZE))
    if resume:
        gdb.cont_all()
    return {"restored": gdb.read_mem(CALL_SITE, 4) == CALL_ORIGINAL}


def set_gate(gdb: GdbClient, gate: int) -> None:
    gdb.write_mem(MAILBOX_VA + MB_GATE, struct.pack("<I", 1 if gate else 0))


def push_request(gdb: GdbClient, yaw: float, pitch: float) -> None:
    """One packet: the two floats are adjacent, so this is a single write."""
    gdb.write_mem(MAILBOX_VA + MB_YAW, struct.pack("<ff", float(yaw), float(pitch)))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command",
                        choices=("apply", "revert", "status", "asm", "gate", "turn"))
    parser.add_argument("value", nargs="*")
    parser.add_argument("--port", type=int, default=24689)
    parser.add_argument("--packets", type=int, default=30)
    args = parser.parse_args()

    if args.command == "asm":
        blob = assemble_hook()
        md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
        for i in md.disasm(blob, HOOK_VA):
            print(f"  {i.address:08x}  {i.bytes.hex():8s}  {i.mnemonic:8s} {i.op_str}")
        print(f"{len(blob)} bytes at 0x{HOOK_VA:08x}, "
              f"call site 0x{CALL_SITE:08x} original {CALL_ORIGINAL.hex()}")
        return 0

    gdb = GdbClient(port=args.port, timeout=15)
    try:
        gdb.send("?")
        if args.command == "apply":
            print(json.dumps(apply(gdb), indent=2))
        elif args.command == "revert":
            print(json.dumps(revert(gdb), indent=2))
        elif args.command == "gate":
            set_gate(gdb, int(args.value[0]))
            gdb.cont_all()
            print(json.dumps(read_mailbox(gdb), indent=2))
        elif args.command == "turn":
            yaw, pitch = float(args.value[0]), float(args.value[1])
            set_gate(gdb, 1)
            for _ in range(args.packets):
                push_request(gdb, yaw, pitch)
                gdb.cont_all()
                time.sleep(0.04)
            set_gate(gdb, 0)
            gdb.cont_all()
            print(json.dumps(read_mailbox(gdb), indent=2))
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
