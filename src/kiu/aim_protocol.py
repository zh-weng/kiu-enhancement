"""Platform-neutral host interfaces and the existing guest mailbox ABI.

ABI 1 describes today's hooks, not an atomic/acknowledged input protocol.
Changing field meaning or layout requires a new version and hook fingerprint.
Backends must never translate native pointers into public guest addresses.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Callable, Protocol, runtime_checkable

ABI_VERSION = 1
DeltaCallback = Callable[[int, int], None]
ActionCallback = Callable[[], None]


@runtime_checkable
class MemoryTransport(Protocol):
    """Exact guest-VA data I/O. Raise on short I/O or stale session mapping.

    This interface is for mailbox data, NOT executable code installation.
    The install transport must stop/resume the guest and invalidate JIT code.
    """
    def read(self, address: int, size: int) -> bytes: ...
    def write(self, address: int, data: bytes) -> None: ...
    def close(self) -> None: ...


@runtime_checkable
class InputSource(Protocol):
    """Relative device counts, no OS pointer acceleration, short callbacks.

    Adapters accept on_delta, on_toggle and on_quit callbacks at creation.
    A start failure must leave no hotkey/device registration behind.
    stop is idempotent. Callback threads must not perform transport I/O.
    """
    def start(self, timeout: float = 5.0) -> InputSource: ...
    def stop(self) -> None: ...


@runtime_checkable
class WindowContext(Protocol):
    """Focus and physical-pixel geometry for the selected emulator session.

    All coordinates use the same desktop space, including negative origins.
    Return None when the top viewport is not known; do not guess a layout.
    Linux/macOS adapters may require user-approved input/desktop permissions.
    """
    def focused(self) -> bool: ...
    def cursor_position(self) -> tuple[int, int]: ...
    def top_viewport(self) -> tuple[float, float, float, float] | None: ...


@dataclass(frozen=True)
class Mailbox:
    name: str
    address: int
    size: int
    magic: int
    version: int


# Little-endian u32 headers; floats are IEEE-754 binary32, radians for ground
# yaw/pitch and guest viewport pixels for air x/y. No host-sized types here.
GROUND = Mailbox("ground", 0x00F36100, 0x20, 0x4D494158, 1)
RETICLE = Mailbox("reticle", 0x00F360C0, 0x40, 0x54455249, 1)
MAILBOXES = (GROUND, RETICLE)


def validate_mailboxes(memory: MemoryTransport) -> None:
    """Fail closed on a missing/unsupported header; not a savestate detector.

    A restored mapping can carry the same bytes. Backends still need a fresh
    session anchor after load/restart. Header checks alone cannot prove that.
    """
    for mailbox in MAILBOXES:
        actual = memory.read(mailbox.address, 8)
        expected = struct.pack("<II", mailbox.magic, mailbox.version)
        if actual != expected:
            raise RuntimeError(f"{mailbox.name}: mailbox ABI mismatch; stop and reinstall/re-anchor")
