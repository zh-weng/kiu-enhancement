"""Reproducible, read-only inspection of ARM code.bin ranges.

The script verifies the authoritative Kid Icarus: Uprising code hash, prints
raw bytes plus disassembly for requested virtual-address ranges, resolves
simple PC-relative ADR/LDR operands, and enumerates direct ARM BL callers of
requested targets.  It deliberately does not assign semantic names.
"""

from __future__ import annotations

import argparse
import hashlib
import struct
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone.arm import ARM_OP_IMM, ARM_OP_MEM, ARM_REG_PC


ROOT = Path(__file__).resolve().parents[2]
CODE_PATH = ROOT / "local" / "extracted" / "exefs_code.bin"
TEXT_BASE = 0x00100000
TEXT_SIZE = 0x00B950F0
EXPECTED_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"


def parse_int(text: str) -> int:
    return int(text, 0)


def direct_bl_target(address: int, word: int) -> int | None:
    if word >> 24 != 0xEB:
        return None
    displacement = word & 0x00FFFFFF
    if displacement & 0x00800000:
        displacement -= 0x01000000
    return (address + 8 + (displacement << 2)) & 0xFFFFFFFF


def resolve_pc_relative(insn, blob: bytes) -> str:
    operands = insn.operands
    if insn.mnemonic == "ldr" and len(operands) >= 2 and operands[1].type == ARM_OP_MEM:
        mem = operands[1].mem
        if mem.base == ARM_REG_PC and not mem.index:
            pool = (insn.address + 8 + mem.disp) & 0xFFFFFFFF
            off = pool - TEXT_BASE
            if 0 <= off <= len(blob) - 4:
                value = struct.unpack_from("<I", blob, off)[0]
                return f" ; pool={pool:#010x} value={value:#010x}"
            return f" ; pool={pool:#010x}"
    if insn.mnemonic in {"add", "sub"} and len(operands) == 3:
        if operands[1].type == 1 and insn.reg_name(operands[1].reg) == "pc" and operands[2].type == ARM_OP_IMM:
            sign = 1 if insn.mnemonic == "add" else -1
            target = (insn.address + 8 + sign * operands[2].imm) & 0xFFFFFFFF
            return f" ; pc-target={target:#010x}"
    return ""


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, default=CODE_PATH,
                        help="local uncompressed code image (the build hash is verified)")
    parser.add_argument("--range", dest="ranges", action="append", nargs=2, metavar=("START", "END"), default=[])
    parser.add_argument("--callers-of", action="append", type=parse_int, default=[])
    args = parser.parse_args()

    blob = args.image.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"unexpected code SHA-256: {digest}")
    print(f"code_sha256={digest}")

    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    for start_text, end_text in args.ranges:
        start, end = parse_int(start_text), parse_int(end_text)
        if start < TEXT_BASE or end <= start or end > TEXT_BASE + len(blob):
            raise SystemExit(f"invalid range {start:#x}..{end:#x}")
        data = blob[start - TEXT_BASE : end - TEXT_BASE]
        print(f"\nrange={start:#010x}..{end:#010x} bytes={data.hex()}")
        for insn in md.disasm(data, start):
            suffix = resolve_pc_relative(insn, blob)
            print(f"{insn.address:#010x}: {bytes(insn.bytes).hex():8}  {insn.mnemonic:<8} {insn.op_str}{suffix}")

    wanted = set(args.callers_of)
    if wanted:
        hits = {target: [] for target in wanted}
        text = blob[:TEXT_SIZE]
        for off in range(0, len(text) - 3, 4):
            word = struct.unpack_from("<I", text, off)[0]
            address = TEXT_BASE + off
            target = direct_bl_target(address, word)
            if target in hits:
                hits[target].append((address, text[off : off + 4].hex()))
        for target in sorted(hits):
            rendered = ", ".join(f"{address:#010x}:{raw}" for address, raw in hits[target]) or "none"
            print(f"direct_bl_callers[{target:#010x}]={rendered}")


if __name__ == "__main__":
    main()
