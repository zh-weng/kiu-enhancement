"""Platform-neutral aiming state and cursor mapping for mailbox ABI 1.

Platform adapters supply guest-memory I/O and physical desktop coordinates.
This module has no emulator, game-image, assembler, or operating-system imports.
"""
from __future__ import annotations

import struct

from .aim_protocol import MemoryTransport, RETICLE

# Public little-endian mailbox ABI 1 offsets, not native process pointers.
RETICLE_CONTROLLER_OFFSET = 0x18
RETICLE_CALLS_OFFSET = 0x24


def as_float(bits: int) -> float:
    """Interpret a guest u32 as an IEEE-754 binary32 value."""
    return struct.unpack("<f", struct.pack("<I", bits))[0]


def sense(memory: MemoryTransport) -> tuple[int, str]:
    """Return reticle calls and the battle mode from the guest's controller.

    The reticle clamp records a camera-controller pointer for ground battles,
    and zero for air battles, where the camera follows rails. The call counter
    lets the caller distinguish a current battle from an idle/loading state;
    this function alone does not infer liveness from a zero controller pointer.
    """
    calls, _applied = struct.unpack(
        "<II", memory.read(RETICLE.address + RETICLE_CALLS_OFFSET, 8))
    controller = struct.unpack(
        "<I", memory.read(RETICLE.address + RETICLE_CONTROLLER_OFFSET, 4))[0]
    return calls, "look" if controller else "point"


def map_point(
    rect: tuple[float, float, float, float],
    width: float,
    height: float,
    cursor: tuple[float, float],
) -> tuple[float, float]:
    """Map a physical desktop point into a clamped guest reticle position.

    rect is (left, top, width, height), with positive extents. Negative desktop
    origins are valid. The guest viewport and desktop rectangle may differ in
    size; callers choose their aspect and scale before invoking this mapping.
    """
    left, top, rect_width, rect_height = rect
    x, y = cursor
    return (
        min(max((x - left) / rect_width * width, 0.0), width),
        min(max((y - top) / rect_height * height, 0.0), height),
    )
