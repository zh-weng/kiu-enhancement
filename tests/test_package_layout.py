"""The public runtime is a package, not a collection of tools-path imports."""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class PackageLayoutTests(unittest.TestCase):
    def test_cli_clean_import_does_not_load_host_or_patch_backends(self):
        script = """
import sys
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0, sys.argv[1])
with patch.object(Path, 'read_bytes', side_effect=AssertionError('no game reads')):
    from kiu import cli
assert cli.parser().parse_args(['status']).command == 'status'
assert not any(name.startswith(('kiu.backends.windows.', 'kiu.patches.',
                                'kiu.experimental.')) for name in sys.modules)
assert not {'capstone', 'keystone', 'gdbclient', 'kiu_cli'}.intersection(sys.modules)
"""
        with tempfile.TemporaryDirectory() as directory:
            clean_src = Path(directory) / "src"
            shutil.copytree(ROOT / "src", clean_src,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            result = subprocess.run(
                [sys.executable, "-c", script, str(clean_src)], cwd=directory,
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_module_entrypoint_works_without_tools_or_a_game_image(self):
        script = """
import runpy
import sys
sys.path.insert(0, sys.argv[1])
sys.argv = ['kiu', '--help']
try:
    runpy.run_module('kiu', run_name='__main__')
except SystemExit as error:
    assert error.code == 0, error.code
else:
    raise AssertionError('--help must exit successfully')
assert not any(name.startswith('kiu.backends.windows.') for name in sys.modules)
"""
        with tempfile.TemporaryDirectory() as directory:
            clean_src = Path(directory) / "src"
            shutil.copytree(ROOT / "src", clean_src,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            result = subprocess.run(
                [sys.executable, "-c", script, str(clean_src)], cwd=directory,
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("export-cheats", result.stdout)

    def test_checkout_entrypoint_is_independent_of_the_working_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            result = subprocess.run(
                [sys.executable, str(ROOT / "run_kiu.py"), "--help"], cwd=directory,
                capture_output=True, text=True,
            )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("restore", result.stdout)

    def test_checkout_launcher_does_not_shadow_the_installed_package(self):
        with tempfile.TemporaryDirectory() as directory:
            clean_root = Path(directory)
            clean_src = clean_root / "src"
            shutil.copytree(ROOT / "src", clean_src,
                            ignore=shutil.ignore_patterns("__pycache__", "*.pyc"))
            for name in ("run_kiu.py", "kiu.py"):
                if (ROOT / name).is_file():
                    shutil.copy2(ROOT / name, clean_root / name)
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(clean_src)
            for arguments in (
                ["-c", "import kiu.aim_protocol; assert kiu.aim_protocol.ABI_VERSION == 1"],
                ["-m", "kiu.patches.patch_60fps", "--help"],
            ):
                with self.subTest(arguments=arguments):
                    result = subprocess.run(
                        [sys.executable, *arguments], cwd=clean_root, env=environment,
                        capture_output=True, text=True,
                    )
                    self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue((clean_root / "run_kiu.py").is_file())
            self.assertFalse((clean_root / "kiu.py").exists())


if __name__ == "__main__":
    unittest.main()
