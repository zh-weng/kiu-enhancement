"""Reach guest memory through the emulator's process instead of the GDB stub.

Measured on this machine: writing eight bytes through the stub costs the
emulator roughly a tenth of a second of runtime.  Driving a burst of 25 packets
took 1.38 s of wall clock and let the game advance three camera updates -- about
8% speed.  That is fine for setting a patch up once and fatal for anything that
has to arrive every frame, which is why every mouse driver in this project so
far has been capped at 25 Hz and has felt like it.

``WriteProcessMemory`` has none of that cost: the emulator is not stopped, not
signalled, and does not know it happened.

Finding where a guest address lives in the host is done by planting a nonce
rather than by assuming anything about how Azahar lays out guest RAM.  The
caller writes a unique 16 bytes at a guest address through the stub -- once --
and this scans the emulator's committed read/write pages for it.  Two hits mean
the region is mirrored and the caller is told, rather than one being picked
silently.

    gdb.write_mem(va, nonce)
    memory = GuestMemory.attach()
    memory.anchor(va, nonce)
    memory.write(va, struct.pack("<ff", yaw, pitch))     # no stall
"""
from __future__ import annotations

import ctypes
import ctypes.wintypes as wintypes
import struct
from dataclasses import dataclass

PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
PROCESS_QUERY_INFORMATION = 0x0400

MEM_COMMIT = 0x1000
PAGE_READWRITE = 0x04
PAGE_EXECUTE_READWRITE = 0x40
PAGE_WRITECOPY = 0x08
PAGE_EXECUTE_WRITECOPY = 0x80
WRITABLE = (PAGE_READWRITE | PAGE_EXECUTE_READWRITE
            | PAGE_WRITECOPY | PAGE_EXECUTE_WRITECOPY)
PAGE_GUARD = 0x100
PAGE_NOACCESS = 0x01

kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
kernel32.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
kernel32.OpenProcess.restype = wintypes.HANDLE
kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
kernel32.CloseHandle.restype = wintypes.BOOL
kernel32.ReadProcessMemory.argtypes = (
    wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_size_t,
    ctypes.POINTER(ctypes.c_size_t),
)
kernel32.ReadProcessMemory.restype = wintypes.BOOL
kernel32.WriteProcessMemory.argtypes = kernel32.ReadProcessMemory.argtypes
kernel32.WriteProcessMemory.restype = wintypes.BOOL


class MEMORY_BASIC_INFORMATION64(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_ulonglong),
                ("AllocationBase", ctypes.c_ulonglong),
                ("AllocationProtect", wintypes.DWORD),
                ("__alignment1", wintypes.DWORD),
                ("RegionSize", ctypes.c_ulonglong),
                ("State", wintypes.DWORD),
                ("Protect", wintypes.DWORD),
                ("Type", wintypes.DWORD),
                ("__alignment2", wintypes.DWORD)]


kernel32.VirtualQueryEx.argtypes = (
    wintypes.HANDLE, ctypes.c_void_p, ctypes.POINTER(MEMORY_BASIC_INFORMATION64),
    ctypes.c_size_t,
)
kernel32.VirtualQueryEx.restype = ctypes.c_size_t


def listening_pid(port: int = 24689) -> int | None:
    """Whoever owns the GDB port -- the only emulator worth talking to.

    An ESTABLISHED row counts, not just LISTENING: Azahar's stub serves one
    client and drops its listening socket while that client is attached, so
    looking only for LISTENING finds nothing exactly when a caller is connected
    and needs the answer.  The server side is the row whose *local* address is
    the port.
    """
    import subprocess
    out = subprocess.run(["netstat", "-ano"], capture_output=True, text=True,
                         creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    for line in out.splitlines():
        parts = line.split()
        if (len(parts) >= 5 and parts[0].upper() == "TCP"
                and parts[1].endswith(f":{port}")
                and parts[3].upper() in ("LISTENING", "ESTABLISHED")):
            try:
                return int(parts[4])
            except ValueError:
                return None
    return None


def find_process(image: str = "azahar.exe", port: int = 24689) -> int:
    """The emulator to attach to, preferring the one holding the GDB port.

    A hung emulator keeps its entry in tasklist without serving its stub, so a
    name-only lookup either picks the dead one or refuses on ambiguity.  The
    process listening on the port is the one that is actually running the game.
    """
    import subprocess
    out = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {image}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)).stdout
    pids = [int(line.split('","')[1]) for line in out.splitlines()
            if line.startswith('"') and '","' in line]
    if not pids:
        raise RuntimeError(f"no {image} process is running")
    if len(pids) == 1:
        return pids[0]
    owner = listening_pid(port)
    if owner in pids:
        return owner
    raise RuntimeError(
        f"{len(pids)} {image} processes are running ({pids}) and none of them "
        f"holds port {port}; close the stale ones")


@dataclass
class Region:
    base: int
    size: int


class GuestMemory:
    """Guest addresses, resolved once against the host and then written directly."""

    def __init__(self, pid: int) -> None:
        self.pid = pid
        self.handle = kernel32.OpenProcess(
            PROCESS_VM_READ | PROCESS_VM_WRITE | PROCESS_VM_OPERATION
            | PROCESS_QUERY_INFORMATION, False, pid)
        if not self.handle:
            raise OSError(ctypes.get_last_error(),
                          f"OpenProcess({pid}) failed; the emulator may be elevated")
        self._offset: int | None = None      # host = guest + offset

    @classmethod
    def attach(cls, image: str = "azahar.exe", port: int = 24689) -> "GuestMemory":
        return cls(find_process(image, port))

    def close(self) -> None:
        if self.handle:
            kernel32.CloseHandle(self.handle)
            self.handle = None

    # -- raw host access -------------------------------------------------

    def _read_host(self, address: int, size: int) -> bytes | None:
        buffer = (ctypes.c_char * size)()
        got = ctypes.c_size_t(0)
        ok = kernel32.ReadProcessMemory(self.handle, ctypes.c_void_p(address),
                                        buffer, size, ctypes.byref(got))
        return buffer.raw[:got.value] if ok else None

    def _write_host(self, address: int, data: bytes) -> None:
        put = ctypes.c_size_t(0)
        ok = kernel32.WriteProcessMemory(self.handle, ctypes.c_void_p(address),
                                         data, len(data), ctypes.byref(put))
        if not ok or put.value != len(data):
            raise OSError(ctypes.get_last_error(),
                          f"WriteProcessMemory(0x{address:x}, {len(data)}) failed")

    def regions(self, limit: int = 1 << 47) -> list[Region]:
        found: list[Region] = []
        info = MEMORY_BASIC_INFORMATION64()
        address = 0
        while address < limit:
            got = kernel32.VirtualQueryEx(self.handle, ctypes.c_void_p(address),
                                          ctypes.byref(info), ctypes.sizeof(info))
            if not got:
                break
            if (info.State == MEM_COMMIT and (info.Protect & WRITABLE)
                    and not (info.Protect & (PAGE_GUARD | PAGE_NOACCESS))):
                found.append(Region(info.BaseAddress, info.RegionSize))
            address = info.BaseAddress + info.RegionSize
            if info.RegionSize == 0:
                break
        return found

    # -- anchoring -------------------------------------------------------

    def anchor(self, guest_va: int, nonce: bytes, chunk: int = 1 << 22) -> int:
        """Locate ``nonce`` (already written at ``guest_va``) and fix the mapping.

        The offset it fixes is only good for the guest region the anchor lands
        in.  Azahar maps guest regions to separate host allocations, so an
        anchor planted in the linear heap around 0x00F00000 does not resolve an
        object at 0x14000000; reading one through the other's offset fails
        outright rather than returning something plausible, but plant a second
        anchor rather than assuming one covers the address space.
        """
        if len(nonce) < 12:
            raise ValueError("a nonce shorter than 12 bytes invites a false match")
        hits: list[int] = []
        for region in self.regions():
            offset = 0
            while offset < region.size:
                size = min(chunk, region.size - offset)
                data = self._read_host(region.base + offset, size)
                if data:
                    start = 0
                    while True:
                        found = data.find(nonce, start)
                        if found < 0:
                            break
                        hits.append(region.base + offset + found)
                        if len(hits) > 4:
                            raise RuntimeError(
                                "the nonce appears more than four times; it is not unique")
                        start = found + 1
                # Overlap by the nonce length so a match on a chunk seam is not missed.
                offset += size - (len(nonce) - 1) if size == chunk else size
        if not hits:
            raise RuntimeError(
                f"nonce not found in the emulator; was it written at 0x{guest_va:08x}?")
        if len(hits) > 1:
            raise RuntimeError(
                f"nonce found at {len(hits)} host addresses "
                f"({', '.join(hex(h) for h in hits)}); guest RAM is mirrored, so "
                "writing one copy may not be the one the game reads")
        self._offset = hits[0] - guest_va
        return hits[0]

    @property
    def anchored(self) -> bool:
        return self._offset is not None

    def host_address(self, guest_va: int) -> int:
        if self._offset is None:
            raise RuntimeError("anchor() has not been called")
        return guest_va + self._offset

    def read(self, guest_va: int, size: int) -> bytes:
        data = self._read_host(self.host_address(guest_va), size)
        if data is None or len(data) != size:
            raise OSError(f"failed to read {size} bytes at guest 0x{guest_va:08x}")
        return data

    def write(self, guest_va: int, data: bytes) -> None:
        self._write_host(self.host_address(guest_va), data)

    def verify(self, guest_va: int, expected: bytes) -> bool:
        return self.read(guest_va, len(expected)) == expected


def nonce_bytes(seed: int) -> bytes:
    """16 bytes that will not occur by accident."""
    return struct.pack("<QQ", 0xA51CE5A11ADE0001 ^ seed, 0x0BADC0FFEE0DDF00 ^ seed)
