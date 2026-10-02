"""Pure aiming semantics shared by Windows, Linux, and macOS adapters."""
from __future__ import annotations

import shutil
import struct
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))

import aim_core as core  # noqa: E402
from aim_protocol import RETICLE  # noqa: E402


class FakeTransport:
    def __init__(self, calls: int, controller: int) -> None:
        self.reads: list[tuple[int, int]] = []
        self.regions = {
            (RETICLE.address + 0x24, 8): struct.pack("<II", calls, 0),
            (RETICLE.address + 0x18, 4): struct.pack("<I", controller),
        }

    def read(self, address: int, size: int) -> bytes:
        self.reads.append((address, size))
        return self.regions[(address, size)]


class AimCoreTests(unittest.TestCase):
    def test_ground_mode_uses_the_controller_even_if_no_new_frame(self) -> None:
        memory = FakeTransport(calls=0, controller=0x14000000)
        self.assertEqual(core.sense(memory), (0, "look"))
        self.assertEqual(memory.reads, [(RETICLE.address + 0x24, 8),
                                        (RETICLE.address + 0x18, 4)])

    def test_air_mode_and_idle_keep_the_call_counter_visible(self) -> None:
        self.assertEqual(core.sense(FakeTransport(42, 0)), (42, "point"))
        self.assertEqual(core.sense(FakeTransport(0, 0)), (0, "point"))

    def test_transport_failure_is_not_misreported_as_an_air_battle(self) -> None:
        class StaleTransport:
            def read(self, _address, _size):
                raise OSError("mapping changed")

        with self.assertRaisesRegex(OSError, "mapping changed"):
            core.sense(StaleTransport())

    def test_short_guest_read_is_rejected(self) -> None:
        memory = FakeTransport(42, 0)
        memory.regions[(RETICLE.address + 0x24, 8)] = b"\0" * 4
        with self.assertRaises(struct.error):
            core.sense(memory)

    def test_guest_float_bits_preserve_sign_and_fraction(self) -> None:
        for value in (0.0, -1.5, 400.0, 0.25):
            bits = struct.unpack("<I", struct.pack("<f", value))[0]
            self.assertEqual(core.as_float(bits), value)

    def test_mapping_preserves_center_and_corners(self) -> None:
        rect = (100.0, 50.0, 800.0, 480.0)
        self.assertEqual(core.map_point(rect, 400, 240, (100, 50)), (0, 0))
        self.assertEqual(core.map_point(rect, 400, 240, (500, 290)), (200, 120))
        self.assertEqual(core.map_point(rect, 400, 240, (900, 530)), (400, 240))

    def test_mapping_clamps_each_axis_outside_the_rectangle(self) -> None:
        rect = (100.0, 50.0, 800.0, 480.0)
        self.assertEqual(core.map_point(rect, 400, 240, (-5, 600)), (0, 240))
        self.assertEqual(core.map_point(rect, 400, 240, (1000, -5)), (400, 0))

    def test_mapping_accepts_a_negative_monitor_origin(self) -> None:
        rect = (-1920.0, -240.0, 1920.0, 1080.0)
        self.assertEqual(core.map_point(rect, 400, 240, (-960, 300)), (200, 120))

    def test_mapping_is_absolute_and_independent_of_previous_motion(self) -> None:
        rect = (0.0, 0.0, 800.0, 480.0)
        first = core.map_point(rect, 400, 240, (250, 300))
        core.map_point(rect, 400, 240, (10000, -10000))
        self.assertEqual(core.map_point(rect, 400, 240, (250, 300)), first)

    def test_clean_import_needs_only_the_protocol_and_standard_library(self) -> None:
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
with patch.object(Path, 'read_bytes', side_effect=AssertionError('no game reads')):
    import aim_core
assert not any(name in sys.modules for name in
               ('guest_memory', 'win_window', 'native_fps_aim', 'capstone', 'keystone'))
assert aim_core.map_point((0, 0, 800, 480), 400, 240, (400, 240)) == (200, 120)
"""
        with tempfile.TemporaryDirectory() as directory:
            for name in ("aim_core", "aim_protocol"):
                shutil.copy2(ROOT / "tools" / f"{name}.py", directory)
            result = subprocess.run([sys.executable, "-c", script, directory],
                                    cwd=directory, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
