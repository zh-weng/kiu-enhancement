"""List ARM instructions whose memory operand uses selected displacements.

This is a build-locked, read-only helper for locating all static accesses to
known structure members without assigning names to the surrounding objects.
"""

from __future__ import annotations

import argparse
import hashlib
from pathlib import Path

from capstone import CS_ARCH_ARM, CS_MODE_ARM, Cs
from capstone.arm import ARM_OP_MEM


ROOT = Path(__file__).resolve().parents[1]
CODE_PATH = ROOT / "local" / "extracted" / "exefs_code.bin"
TEXT_BASE = 0x00100000
TEXT_SIZE = 0x00B950F0
EXPECTED_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--image", type=Path, default=CODE_PATH,
                        help="local uncompressed code image (the build hash is verified)")
    parser.add_argument("displacements", nargs="+", type=lambda value: int(value, 0))
    args = parser.parse_args()

    blob = args.image.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != EXPECTED_SHA256:
        raise SystemExit(f"unexpected code SHA-256: {digest}")
    wanted = set(args.displacements)
    print(f"code_sha256={digest}")

    md = Cs(CS_ARCH_ARM, CS_MODE_ARM)
    md.detail = True
    count = 0
    for off in range(0, TEXT_SIZE - 3, 4):
        decoded = list(md.disasm(blob[off : off + 4], TEXT_BASE + off, count=1))
        if not decoded:
            continue
        insn = decoded[0]
        for operand in insn.operands:
            if operand.type == ARM_OP_MEM and operand.mem.disp in wanted:
                base = insn.reg_name(operand.mem.base)
                print(
                    f"{insn.address:#010x}: {bytes(insn.bytes).hex():8}  "
                    f"{insn.mnemonic:<8} {insn.op_str} ; base={base} disp={operand.mem.disp:#x}"
                )
                count += 1
                break
    print(f"matches={count}")


if __name__ == "__main__":
    main()
