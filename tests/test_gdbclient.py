from __future__ import annotations

import socket
import sys
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from kiu.gdbclient import GdbClient  # noqa: E402


class _FakeSocket:
    def __init__(self) -> None:
        self.options: list[tuple[int, int, int]] = []
        self.timeout = None

    def setsockopt(self, level: int, option: int, value: int) -> None:
        self.options.append((level, option, value))

    def settimeout(self, timeout: float) -> None:
        self.timeout = timeout


class _ScriptedGdbClient(GdbClient):
    def __init__(self, script: list[tuple[str, str]]) -> None:
        self.script = list(script)
        self.commands: list[str] = []

    def send(self, cmd: str, expect_reply: bool = True) -> str:
        self.commands.append(cmd)
        if not self.script:
            raise AssertionError(f"unexpected GDB command: {cmd}")
        expected_cmd, reply = self.script.pop(0)
        if cmd != expected_cmd:
            raise AssertionError(f"expected GDB command {expected_cmd!r}, got {cmd!r}")
        return reply


class GdbClientSocketTests(unittest.TestCase):
    def test_local_rsp_connection_disables_nagle(self) -> None:
        fake = _FakeSocket()
        with mock.patch("kiu.gdbclient.socket.create_connection", return_value=fake) as connect:
            client = GdbClient(host="127.0.0.1", port=24689, timeout=3.5)

        connect.assert_called_once_with(("127.0.0.1", 24689), timeout=3.5)
        self.assertIn((socket.IPPROTO_TCP, socket.TCP_NODELAY, 1), fake.options)
        self.assertEqual(fake.timeout, 3.5)
        self.assertIs(client.sock, fake)


class GdbClientThreadTests(unittest.TestCase):
    def test_list_threads_parses_multiple_chunks_until_terminal_reply(self) -> None:
        client = _ScriptedGdbClient(
            [
                ("qfThreadInfo", "m1,a"),
                ("qsThreadInfo", "m10,2f"),
                ("qsThreadInfo", "l"),
            ]
        )

        self.assertEqual(client.list_threads(), (0x1, 0xA, 0x10, 0x2F))
        self.assertEqual(
            client.commands,
            ["qfThreadInfo", "qsThreadInfo", "qsThreadInfo"],
        )
        self.assertEqual(client.script, [])

    def test_list_threads_rejects_unexpected_reply_type(self) -> None:
        client = _ScriptedGdbClient([("qfThreadInfo", "OK")])

        with self.assertRaisesRegex(RuntimeError, "unexpected thread-list reply"):
            client.list_threads()

    def test_list_threads_rejects_empty_non_terminal_chunk(self) -> None:
        client = _ScriptedGdbClient([("qfThreadInfo", "m")])

        with self.assertRaisesRegex(RuntimeError, "empty non-terminal"):
            client.list_threads()

    def test_list_threads_rejects_malformed_hex_id(self) -> None:
        client = _ScriptedGdbClient([("qfThreadInfo", "m1,not-hex")])

        with self.assertRaisesRegex(RuntimeError, "invalid thread id"):
            client.list_threads()

    def test_list_threads_rejects_duplicate_across_chunks(self) -> None:
        client = _ScriptedGdbClient(
            [
                ("qfThreadInfo", "m1,a"),
                ("qsThreadInfo", "ma"),
            ]
        )

        with self.assertRaisesRegex(RuntimeError, "duplicate thread id"):
            client.list_threads()

    def test_list_threads_rejects_zero_id(self) -> None:
        client = _ScriptedGdbClient([("qfThreadInfo", "m0")])

        with self.assertRaisesRegex(RuntimeError, "invalid or duplicate thread id"):
            client.list_threads()

    def test_list_threads_rejects_terminal_reply_without_threads(self) -> None:
        client = _ScriptedGdbClient([("qfThreadInfo", "l")])

        with self.assertRaisesRegex(RuntimeError, "reported no guest threads"):
            client.list_threads()

    def test_select_general_thread_sends_hex_hg_request(self) -> None:
        client = _ScriptedGdbClient([("Hg2f", "OK")])

        self.assertIsNone(client.select_general_thread(0x2F))
        self.assertEqual(client.commands, ["Hg2f"])
        self.assertEqual(client.script, [])

    def test_select_general_thread_rejects_stub_failure(self) -> None:
        client = _ScriptedGdbClient([("Hg2f", "E01")])

        with self.assertRaisesRegex(RuntimeError, "failed to select thread 2f: E01"):
            client.select_general_thread(0x2F)

    def test_select_general_thread_rejects_non_positive_ids_without_transport(self) -> None:
        for thread_id in (0, -1):
            with self.subTest(thread_id=thread_id):
                client = _ScriptedGdbClient([])
                with self.assertRaisesRegex(ValueError, "invalid thread id"):
                    client.select_general_thread(thread_id)
                self.assertEqual(client.commands, [])


if __name__ == "__main__":
    unittest.main()
