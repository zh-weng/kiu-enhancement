import socket
import struct
import time


class GdbClient:
    def __init__(self, host="127.0.0.1", port=24689, timeout=10):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        # RSP alternates very small command, ACK, and reply packets.  Leaving
        # Nagle enabled can combine the one-byte client ACK with Windows'
        # delayed-ACK policy and add tens of milliseconds to every mailbox
        # word.  Native mouse fast-lane motion is one small aligned write and
        # still needs its reply promptly, so force latency-oriented delivery
        # on this local TCP link.
        self.sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        self.sock.settimeout(timeout)
        self.buf = b""

    def _send_packet(self, data: bytes):
        cksum = sum(data) & 0xFF
        pkt = b"$" + data + b"#" + f"{cksum:02x}".encode()
        self.sock.sendall(pkt)

    def _recv_exact(self, n):
        while len(self.buf) < n:
            chunk = self.sock.recv(4096)
            if not chunk:
                raise ConnectionError("gdb stub closed connection")
            self.buf += chunk
        out, self.buf = self.buf[:n], self.buf[n:]
        return out

    def _recv_packet(self, timeout=None) -> str:
        """Receive and acknowledge one RSP packet.

        Continue commands are acknowledged immediately, while their eventual
        stop reply arrives asynchronously.  Keeping the parser in one place
        avoids the off-by-one checksum reads in the older one-off tools.
        """
        old_timeout = self.sock.gettimeout()
        if timeout is not None:
            self.sock.settimeout(timeout)
        try:
            while True:
                c = self._recv_exact(1)
                if c == b"+":
                    continue
                if c == b"-":
                    raise RuntimeError("stub NAK")
                if c == b"$":
                    break

            payload = bytearray()
            while True:
                c = self._recv_exact(1)
                if c == b"#":
                    break
                payload += c

            checksum_raw = self._recv_exact(2)
            try:
                expected = int(checksum_raw, 16)
            except ValueError as exc:
                self.sock.sendall(b"-")
                raise RuntimeError(f"invalid RSP checksum {checksum_raw!r}") from exc
            actual = sum(payload) & 0xFF
            if actual != expected:
                self.sock.sendall(b"-")
                raise RuntimeError(
                    f"RSP checksum mismatch: got {expected:02x}, calculated {actual:02x}"
                )
            self.sock.sendall(b"+")
            return payload.decode(errors="replace")
        finally:
            if timeout is not None:
                self.sock.settimeout(old_timeout)

    def send(self, cmd: str, expect_reply=True) -> str:
        self._send_packet(cmd.encode())
        if not expect_reply:
            return ""
        return self._recv_packet()

    def interrupt(self):
        self.sock.sendall(b"\x03")
        deadline = time.time() + 5
        while time.time() < deadline:
            try:
                c = self._recv_exact(1)
            except socket.timeout:
                break
            if c == b"$":
                payload = b""
                while True:
                    ch = self._recv_exact(1)
                    if ch == b"#":
                        break
                    payload += ch
                self._recv_exact(2)
                self.sock.sendall(b"+")
                return payload.decode(errors="replace")
        return ""

    def read_mem(self, addr: int, length: int) -> bytes:
        reply = self.send(f"m{addr:x},{length:x}")
        if reply.startswith("E"):
            raise RuntimeError(f"read error at {addr:#x}: {reply}")
        return bytes.fromhex(reply)

    def write_mem(self, addr: int, data: bytes):
        reply = self.send(f"M{addr:x},{len(data):x}:" + data.hex())
        if not reply.startswith("OK"):
            raise RuntimeError(f"write error at {addr:#x}: {reply}")

    def get_stop_reply(self):
        return self.interrupt()

    def cont(self):
        # Azahar's Continue() builds its runnable-thread list from Hc.  A plain
        # `c` leaves that list empty in this build and can keep emulation frozen.
        reply = self.send("Hc0")
        if reply != "OK":
            raise RuntimeError(f"failed to select continue thread: {reply}")
        self.send("c", expect_reply=False)

    def cont_all(self):
        """Resume every thread in the attached process.

        Azahar interprets Hc0 as only the lowest thread ID.  Hc-1 leaves the
        continue list empty, and ClearUnscheduleMode then applies to all
        threads.  Touch polling commonly runs outside the lowest thread.
        """
        reply = self.send("Hc-1")
        if reply != "OK":
            raise RuntimeError(f"failed to select all continue threads: {reply}")
        self.send("c", expect_reply=False)

    def cont_thread(self, thread_id: int):
        """Resume only one guest thread, identified by its GDB thread ID."""
        if thread_id < 1:
            raise ValueError(f"invalid thread id: {thread_id}")
        reply = self.send(f"Hc{thread_id:x}")
        if reply != "OK":
            raise RuntimeError(f"failed to select continue thread {thread_id:x}: {reply}")
        self.send("c", expect_reply=False)

    def list_threads(self) -> tuple[int, ...]:
        """Return every thread id reported for the selected guest process."""

        reply = self.send("qfThreadInfo")
        result = []
        while True:
            if reply == "l":
                break
            if not reply.startswith("m"):
                raise RuntimeError(f"unexpected thread-list reply: {reply!r}")
            payload = reply[1:]
            if not payload:
                raise RuntimeError("empty non-terminal thread-list reply")
            for item in payload.split(","):
                try:
                    thread_id = int(item, 16)
                except ValueError as exc:
                    raise RuntimeError(
                        f"invalid thread id in thread-list reply: {item!r}"
                    ) from exc
                if thread_id < 1 or thread_id in result:
                    raise RuntimeError(
                        f"invalid or duplicate thread id in thread list: {thread_id}"
                    )
                result.append(thread_id)
            reply = self.send("qsThreadInfo")
        if not result:
            raise RuntimeError("GDB stub reported no guest threads")
        return tuple(result)

    def select_general_thread(self, thread_id: int) -> None:
        """Select the stopped thread used by register and memory-register packets."""

        if thread_id < 1:
            raise ValueError(f"invalid thread id: {thread_id}")
        reply = self.send(f"Hg{thread_id:x}")
        if reply != "OK":
            raise RuntimeError(f"failed to select thread {thread_id:x}: {reply}")

    def wait_for_stop(self, timeout=10.0):
        """Wait for an asynchronous T/S/W stop packet after continue."""
        reply = self._recv_packet(timeout=timeout)
        if not reply.startswith(("T", "S", "W")):
            raise RuntimeError(f"unexpected asynchronous GDB reply: {reply}")
        return reply

    def read_registers(self):
        """Return the ARM r0-r15 and CPSR register block as integers."""
        raw = self.send("g")
        if raw.startswith("E"):
            raise RuntimeError(f"register read failed: {raw}")
        data = bytes.fromhex(raw)
        if len(data) < 17 * 4:
            raise RuntimeError(f"short ARM register block: {len(data)} bytes")
        return tuple(struct.unpack_from("<I", data, index * 4)[0] for index in range(17))

    def set_breakpoint(self, addr: int, length: int = 4):
        # Azahar accepts only the architectural instruction widths here:
        # four bytes for ARM and two bytes for Thumb.  The old hard-coded
        # length of one is rejected by CommitBreakpoint with E02.
        r = self.send(f"Z0,{addr:x},{length:x}")
        if not r.startswith("OK"):
            raise RuntimeError(f"breakpoint at {addr:#x} failed: {r}")

    def clear_breakpoint(self, addr: int, length: int = 4):
        r = self.send(f"z0,{addr:x},{length:x}")
        if not r.startswith("OK"):
            raise RuntimeError(f"clear breakpoint at {addr:#x} failed: {r}")

    def set_write_watchpoint(self, addr: int, length=4):
        r = self.send(f"Z2,{addr:x},{length}")
        return r

    def clear_watchpoint(self, addr: int, length=4):
        return self.send(f"z2,{addr:x},{length}")

    def read_reg(self, name_idx: int):
        return self.send(f"p{name_idx:x}")


if __name__ == "__main__":
    import sys

    g = GdbClient()
    stop = g.get_stop_reply()
    print("stop reply:", stop)
