"""AppImage lineage binding rejects unrelated and recycled process IDs."""
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu.backends.linux.launch_process import require_descendant


class LinuxLineageTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.proc = Path(self.directory.name)

    def process(self, pid, parent, start):
        directory = self.proc / str(pid)
        directory.mkdir(exist_ok=True)
        fields = ["S", str(parent), *(["0"] * 17), str(start)]
        (directory / "stat").write_text(f"{pid} (App Image (runtime)) " + " ".join(fields))

    def test_original_process_or_descendant_allowed(self):
        self.process(100, 1, 50)
        self.process(101, 100, 60)
        self.process(102, 101, 70)
        self.assertEqual(require_descendant(100, 100, "50", self.proc), "50")
        self.assertEqual(require_descendant(102, 100, "50", self.proc), "70")

    def test_other_process_rejected_despite_same_executable(self):
        self.process(100, 1, 50)
        self.process(200, 0, 60)
        with self.assertRaisesRegex(RuntimeError, "another process"):
            require_descendant(200, 100, "50", self.proc)

    def test_recycled_launch_pid_rejected(self):
        self.process(100, 1, 999)
        self.process(101, 100, 60)
        with self.assertRaisesRegex(RuntimeError, "lineage"):
            require_descendant(101, 100, "50", self.proc)

    def test_missing_or_malformed_parent_refuses_attach(self):
        self.process(100, 1, 50)
        self.process(101, 999, 60)
        with self.assertRaisesRegex(RuntimeError, "lineage"):
            require_descendant(101, 100, "50", self.proc)
        (self.proc / "100" / "stat").write_text("malformed")
        with self.assertRaisesRegex(RuntimeError, "lineage"):
            require_descendant(100, 100, "50", self.proc)


if __name__ == "__main__":
    unittest.main()
