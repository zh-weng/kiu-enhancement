"""PID-bound Linux mailbox transport using process_vm_readv/writev.

The Launcher must be the emulator's parent under the normal Yama policy.
No privilege elevation or ptrace-policy changes are attempted. A fresh GDB
nonce proves the mapping; only the two ABI 1 mailboxes are exposed thereafter.
Transfers are not atomic, just like the existing Windows preview adapter.
"""
from __future__ import annotations

import ctypes
import errno
import os
import secrets
from dataclasses import dataclass
from pathlib import Path

from ...aim_protocol import MAILBOXES

PROC = Path("/proc")


def socket_inodes(port: int, proc: Path = PROC) -> set[str]:
    """Local server sockets, including established sockets after GDB attaches."""
    found = set()
    for name in ("tcp", "tcp6"):
        try:
            lines = (proc / "net" / name).read_text().splitlines()[1:]
        except OSError:
            continue
        for line in lines:
            parts = line.split()
            if len(parts) > 9 and parts[3] in ("01", "0A"):
                try:
                    if int(parts[1].rsplit(":", 1)[1], 16) == port:
                        found.add(parts[9])
                except ValueError:
                    continue
    return found


def listening_pid(port: int = 24689, *, proc: Path = PROC) -> int | None:
    if not 1 <= port <= 65535:
        raise ValueError("invalid TCP port")
    inodes = socket_inodes(port, proc)
    owners = set()
    if not inodes:
        return None
    for process in proc.iterdir():
        if not process.name.isdecimal():
            continue
        try:
            for descriptor in (process / "fd").iterdir():
                target = os.readlink(descriptor)
                if target.startswith("socket:[") and target[8:-1] in inodes:
                    owners.add(int(process.name))
                    break
        except (OSError, PermissionError):
            continue
    # Multiple processes/servers are ambiguous, not a reason to pick the first.
    return next(iter(owners)) if len(owners) == 1 else None


def process_identity(pid: int, proc: Path = PROC) -> str:
    text = (proc / str(pid) / "stat").read_text()
    # comm can contain spaces and parentheses. Field 22 is starttime.
    return text.rsplit(")", 1)[1].split()[19]


@dataclass(frozen=True)
class Region:
    base: int
    size: int


def writable_regions(text: str) -> list[Region]:
    result = []
    for line in text.splitlines():
        parts = line.split()
        if len(parts) < 2 or not parts[1].startswith("rw"):
            continue
        start, end = (int(value, 16) for value in parts[0].split("-"))
        result.append(Region(start, end - start))
    return result


class IOVec(ctypes.Structure):
    _fields_ = [("base", ctypes.c_void_p), ("length", ctypes.c_size_t)]


class GuestMemory:
    def __init__(self, pid: int) -> None:
        if type(pid) is not int or pid <= 0:
            raise ValueError("PID must be positive")
        self.pid = pid
        self._identity = process_identity(pid)
        self._offset: int | None = None
        self._region: Region | None = None
        self._closed = False
        self._libc = ctypes.CDLL(None, use_errno=True)
        for name in ("process_vm_readv", "process_vm_writev"):
            function = getattr(self._libc, name)
            function.argtypes = (ctypes.c_int, ctypes.POINTER(IOVec), ctypes.c_ulong,
                                 ctypes.POINTER(IOVec), ctypes.c_ulong, ctypes.c_ulong)
            function.restype = ctypes.c_ssize_t

    @classmethod
    def attach(cls, port: int = 24689, expected_pid: int | None = None) -> GuestMemory:
        owner = listening_pid(port)
        if owner is None or expected_pid is not None and owner != expected_pid:
            raise RuntimeError(f"GDB port {port} belongs to PID {owner}, expected {expected_pid}")
        return cls(owner)

    def close(self) -> None:
        self._closed = True
        self._offset = None
        self._region = None

    def _check_identity(self) -> None:
        if self._closed or process_identity(self.pid) != self._identity:
            raise RuntimeError("emulator exited or PID was reused; refusing memory access")

    def _transfer(self, address: int, size: int, data: bytes | None = None) -> bytes:
        self._check_identity()
        if size < 0 or size > 1 << 24:
            raise ValueError("invalid transfer size")
        buffer = ctypes.create_string_buffer(data, size) if data is not None else ctypes.create_string_buffer(size)
        local = IOVec(ctypes.cast(buffer, ctypes.c_void_p), size)
        remote = IOVec(address, size)
        function = self._libc.process_vm_writev if data is not None else self._libc.process_vm_readv
        count = function(self.pid, ctypes.byref(local), 1, ctypes.byref(remote), 1, 0)
        if count != size:
            code = ctypes.get_errno() if count < 0 else errno.EIO
            message = "short process-memory transfer"
            if code == errno.EPERM:
                message = "Linux denied emulator memory access; launch Azahar from this Launcher (no security policy is changed)"
            raise OSError(code, message)
        return buffer.raw

    def _read_host(self, address: int, size: int) -> bytes:
        return self._transfer(address, size)

    def regions(self) -> list[Region]:
        self._check_identity()
        return writable_regions((PROC / str(self.pid) / "maps").read_text())

    def anchor(self, guest_va: int, nonce: bytes, chunk: int = 1 << 22) -> int:
        if len(nonce) < 12 or chunk <= len(nonce):
            raise ValueError("nonce/chunk too small")
        hits: set[int] = set()
        matched_region = None
        for region in self.regions():
            offset = 0
            while offset < region.size:
                size = min(chunk, region.size - offset)
                try:
                    data = self._read_host(region.base + offset, size)
                except OSError as exc:
                    if exc.errno in (errno.EPERM, errno.ESRCH):
                        raise
                    offset += size
                    continue
                cursor = 0
                while (found := data.find(nonce, cursor)) >= 0:
                    hits.add(region.base + offset + found)
                    matched_region = region
                    if len(hits) > 1:
                        raise RuntimeError("nonce has multiple host mappings; refusing ambiguous guest RAM")
                    cursor = found + 1
                offset += size - len(nonce) + 1 if size == chunk else size
        if not hits:
            raise RuntimeError("fresh guest nonce was not found in emulator memory")
        host = next(iter(hits))
        self._offset = host - guest_va
        self._region = matched_region
        if not self.verify(guest_va, nonce, scratch=True):
            self.close()
            raise RuntimeError("guest anchor changed during scan")
        return host

    def host_address(self, guest_va: int, size: int, *, scratch: bool = False) -> int:
        if self._offset is None or self._region is None:
            raise RuntimeError("memory is not anchored")
        allowed = any(box.address <= guest_va and guest_va + size <= box.address + box.size
                      for box in MAILBOXES)
        # A nonce is installed immediately after the ground mailbox, never code.
        if scratch:
            allowed = guest_va == 0x00F36140 and size == 16
        if not allowed or size <= 0:
            raise ValueError("Linux realtime transport only exposes ABI 1 mailbox data")
        host = guest_va + self._offset
        if not self._region.base <= host < host + size <= self._region.base + self._region.size:
            raise RuntimeError("guest access lies outside the verified host region")
        return host

    def read(self, guest_va: int, size: int) -> bytes:
        return self._read_host(self.host_address(guest_va, size), size)

    def write(self, guest_va: int, data: bytes) -> None:
        self._transfer(self.host_address(guest_va, len(data)), len(data), data)

    def verify(self, guest_va: int, expected: bytes, *, scratch: bool = False) -> bool:
        return self._read_host(self.host_address(guest_va, len(expected), scratch=scratch), len(expected)) == expected


def nonce_bytes(seed: int = 0) -> bytes:
    # Ignore the old Windows seed argument; every Linux anchor is fresh.
    return secrets.token_bytes(16)
