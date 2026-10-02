"""Walk a function by following its branches, instead of scanning it linearly.

Every wrong call list in this project came from linear scanning, in four
different ways: a fixed window that cut a function short, stopping at the first
``pop {…, pc}`` when the function had several returns, running past the end into
the next function, and -- the one that invalidated a whole conclusion --
disassembling straight through embedded data and resynchronising on garbage.
That last one produced a ``pop {r4, r5, pc}`` for a function whose prologue does
``vpush {d8-d15}``, which cannot be its epilogue, and a ``vstmia`` that turned
out never to execute because it was never an instruction.

Reachability answers all four at once: data is not reachable, the extent is
whatever the branches actually cover, every return is found rather than the
first, and nothing past the last reachable instruction is attributed to the
function.

    python tools/armscan.py 0x0077DCF4
"""
from __future__ import annotations

import argparse
import struct
from dataclasses import dataclass, field
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone.arm import ARM_CC_AL, ARM_OP_IMM, ARM_OP_REG, ARM_REG_LR, ARM_REG_PC

CODE_BASE = 0x00100000
DEFAULT_IMAGE = Path(__file__).resolve().parents[1] / "local/extracted/exefs_code.bin"


@dataclass
class Scan:
    entry: int
    reached: set[int] = field(default_factory=set)
    calls: list[tuple[int, str]] = field(default_factory=list)
    stores: list[tuple[int, str, str]] = field(default_factory=list)
    returns: list[int] = field(default_factory=list)
    indirect: list[int] = field(default_factory=list)   # paths we cannot follow

    @property
    def extent(self) -> tuple[int, int]:
        return (min(self.reached), max(self.reached) + 4) if self.reached else (self.entry, self.entry)

    @property
    def has_vpush(self) -> bool:
        return any(m == "vpush" for _, m, _ in self._all)

    _all: list[tuple[int, str, str]] = field(default_factory=list)


def _writes_pc(insn) -> bool:
    if insn.mnemonic.startswith(("pop", "ldm")) and "pc" in insn.op_str:
        return True
    if insn.mnemonic.startswith("bx"):
        return True
    if insn.mnemonic.startswith("mov") and insn.op_str.startswith("pc,"):
        return True
    if insn.mnemonic.startswith("ldr") and insn.op_str.startswith("pc,"):
        return True
    return False


def scan(code: bytes, entry: int, limit: int = 0x4000) -> Scan:
    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    result = Scan(entry=entry)
    work = [entry]
    while work:
        pc = work.pop()
        while True:
            if pc in result.reached:
                break
            offset = pc - CODE_BASE
            if not (0 <= offset < len(code) - 3) or abs(pc - entry) > limit:
                break
            try:
                insn = next(md.disasm(code[offset:offset + 4], pc))
            except StopIteration:
                break                      # undecodable: treat as the end of a path
            result.reached.add(pc)
            result._all.append((pc, insn.mnemonic, insn.op_str))

            if insn.mnemonic in ("bl", "blx"):
                result.calls.append((pc, insn.op_str))
                pc += 4
                continue
            if insn.mnemonic.startswith(("str", "vstr", "stm", "vstm", "push", "vpush")):
                if insn.mnemonic not in ("push", "vpush"):
                    result.stores.append((pc, insn.mnemonic, insn.op_str))
                pc += 4
                continue
            # A plain branch, conditional or not.
            if insn.mnemonic.startswith("b") and not insn.mnemonic.startswith("bic") \
                    and insn.operands and insn.operands[0].type == ARM_OP_IMM:
                work.append(insn.operands[0].imm)
                if insn.cc == ARM_CC_AL:
                    break                  # unconditional: nothing falls through
                pc += 4
                continue
            if _writes_pc(insn):
                if insn.cc == ARM_CC_AL:
                    (result.returns if not insn.mnemonic.startswith("bx")
                     or insn.operands[0].reg == ARM_REG_LR else result.indirect).append(pc)
                    break
                pc += 4                    # conditional return: the path continues
                continue
            pc += 4
    return result


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("entry", help="function entry, e.g. 0x0077DCF4")
    parser.add_argument("--image", default=str(DEFAULT_IMAGE))
    args = parser.parse_args()

    code = Path(args.image).read_bytes()
    result = scan(code, int(args.entry, 0))
    low, high = result.extent
    print(f"FUN_{result.entry:08X}  reachable 0x{low:08X}..0x{high:08X} "
          f"({high - low} bytes span, {len(result.reached)} instructions)")
    print(f"  returns   {[hex(r) for r in sorted(result.returns)] or 'none'}")
    if result.indirect:
        print(f"  indirect exits (not followed) {[hex(i) for i in sorted(result.indirect)]}")
    if result.has_vpush and not any(m == "vpop" for _, m, _ in result._all):
        print("  WARNING: prologue has vpush but no vpop is reachable -- "
              "the entry or the image is not what it seems")
    print(f"  calls ({len(result.calls)}):")
    for address, operands in sorted(result.calls):
        print(f"     {address:08x}  {operands}")
    non_stack = [(a, m, o) for a, m, o in result.stores if "sp" not in o]
    print(f"  stores off the stack ({len(non_stack)} of {len(result.stores)}):")
    for address, mnemonic, operands in sorted(non_stack):
        print(f"     {address:08x}  {mnemonic:8s} {operands}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
