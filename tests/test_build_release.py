"""Source-pack tests use a temporary tree and need neither Git nor a ROM."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest import mock
import zipfile

from tools import build_release as release


def fixture(root):
    for name in release.REQUIRED_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"fixture: {name}\n", encoding="utf-8")
    (root / "tests").mkdir()
    (root / "tests/test_example.py").write_text("# test fixture\n", encoding="utf-8")


def cheats(_root):
    return b"[Fixture patch]\n00100000 00000000\n"


class ReleaseTests(unittest.TestCase):
    def test_allowlist_excludes_local_archives_binary_and_unlisted_tools(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            for name in ("local/private-config.json", "local/game.cci", "docs/old.md",
                         "tools/obsolete.py", "tools/rogue.dll", "tests/game.bin",
                         "tools/__pycache__/cache.pyc", "archive/private.txt"):
                path = root / name
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_bytes(b"private\0binary")
            files = release.release_files(root, cheat_generator=cheats)
            self.assertTrue(all(not name.startswith(("local/", "archive/")) for name in files))
            self.assertNotIn("tools/obsolete.py", files)
            self.assertNotIn("tools/rogue.dll", files)
            self.assertNotIn("tests/game.bin", files)
            self.assertIn(f"cheats/{release.TITLE_ID}.txt", files)
            self.assertEqual(set(files), set(files["FILES.txt"].decode().splitlines()))
            for line in files["SHA256SUMS"].decode().splitlines():
                digest, name = line.split("  ", 1)
                self.assertEqual(digest, hashlib.sha256(files[name]).hexdigest())

    def test_missing_required_source_reports_each_filename(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            for name in ("README.md", "manifests/title.json"):
                (root / name).unlink()
            with self.assertRaises(release.ReleaseError) as caught:
                release.release_files(root, cheat_generator=cheats)
            self.assertIn("README.md", str(caught.exception))
            self.assertIn("manifests/title.json", str(caught.exception))

    def test_binary_in_allowlisted_source_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            (root / "README.md").write_bytes(b"secret\0binary")
            with self.assertRaisesRegex(release.ReleaseError, "README.md"):
                release.release_files(root, cheat_generator=cheats)

    def test_zip_is_byte_deterministic_despite_file_mtime(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            fixture(root)
            first = root / "local/first.zip"
            second = root / "local/second.zip"
            release.write_release(release.release_files(root, cheat_generator=cheats), first, root=root)
            os.utime(root / "README.md", (1234567890, 1234567890))
            release.write_release(release.release_files(root, cheat_generator=cheats), second, root=root)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with zipfile.ZipFile(first) as archive:
                self.assertIsNone(archive.testzip())
                self.assertEqual(archive.namelist(), sorted(archive.namelist()))
                self.assertTrue(all(info.date_time == (1980, 1, 1, 0, 0, 0)
                                    for info in archive.infolist()))

    def test_check_does_not_write_a_release(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "release.zip"
            with mock.patch.object(release, "release_files", return_value={"README.md": b"source"}):
                self.assertEqual(release.main(["--check", "--output", str(output)]), 0)
            self.assertFalse(output.exists())

    def test_output_inside_source_tree_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with self.assertRaisesRegex(release.ReleaseError, "under local"):
                release.write_release({"README.md": b"source"}, root / "tools/release.zip", root=root)


if __name__ == "__main__":
    unittest.main()
