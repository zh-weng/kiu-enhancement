"""Frozen launcher consumes the same build-locked hooks, without Keystone."""
from __future__ import annotations

import json
from pathlib import Path
import subprocess
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu.patches import patch_aim_request as ground, pin_reticle as reticle
from kiu.experimental import menu_render_pass as menu


class FrozenPayloadTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.reference = {
            "GROUND_HOOK": ground.assemble_hook(),
            "RETICLE_HOOK": reticle.assemble_hook(),
            "RETICLE_LATE_HOOK": reticle.assemble_late_hook(),
            "MENU_PRE_HOOK": menu.PRE_CAVE_BYTES,
            "MENU_POST_HOOK": menu.POST_CAVE_BYTES,
        }

    def payload_module(self, **overrides):
        values = dict(self.reference, BUILD_SHA256=ground.CODE_SHA256)
        values.update(overrides)
        return types.SimpleNamespace(**values)

    def test_frozen_bytes_match_source_assembler_exactly(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.dict(sys.modules, {"kiu_launcher_payloads": self.payload_module()}):
            self.assertEqual(ground.assemble_hook(), self.reference["GROUND_HOOK"])
            self.assertEqual(reticle.assemble_hook(), self.reference["RETICLE_HOOK"])
            self.assertEqual(reticle.assemble_late_hook(), self.reference["RETICLE_LATE_HOOK"])
            for restore, cave, site, name in ((False, menu.PRE_CAVE, menu.PRE_SITE, "MENU_PRE_HOOK"),
                                               (True, menu.POST_CAVE, menu.POST_SITE, "MENU_POST_HOOK")):
                self.assertEqual(menu.build_cave(cave, restore_width=restore, return_address=site + 12),
                                 self.reference[name])

    def test_wrong_build_hash_is_rejected_by_all_three_builders(self):
        with mock.patch.object(sys, "frozen", True, create=True), \
                mock.patch.dict(sys.modules, {"kiu_launcher_payloads": self.payload_module(BUILD_SHA256="wrong")}):
            for build in (ground.assemble_hook, reticle.assemble_hook, reticle.assemble_late_hook):
                with self.subTest(build=build.__name__), self.assertRaisesRegex(RuntimeError, "unsupported build"):
                    build()

    def test_existing_pool_checks_still_reject_corrupt_frozen_bytes(self):
        for field, build in (("GROUND_HOOK", ground.assemble_hook),
                             ("RETICLE_HOOK", reticle.assemble_hook),
                             ("RETICLE_LATE_HOOK", reticle.assemble_late_hook)):
            corrupt = self.reference[field][:-4] + bytes(4)
            with self.subTest(field=field), mock.patch.object(sys, "frozen", True, create=True), \
                    mock.patch.dict(sys.modules, {"kiu_launcher_payloads": self.payload_module(**{field: corrupt})}):
                with self.assertRaisesRegex(RuntimeError, "pool words"):
                    build()

    def test_fresh_frozen_process_never_imports_keystone(self):
        script = """
import importlib.abc,json,sys,types
sys.path.insert(0,sys.argv[1])
class BlockKeystone(importlib.abc.MetaPathFinder):
    def find_spec(self,fullname,path=None,target=None):
        if fullname == 'keystone' or fullname.startswith('keystone.'):
            raise AssertionError('Keystone must not be loaded by frozen runtime')
sys.meta_path.insert(0,BlockKeystone())
sys.frozen=True
values=json.loads(sys.argv[2]); p=types.ModuleType('kiu_launcher_payloads')
p.BUILD_SHA256=values.pop('BUILD_SHA256')
for name,hexbytes in values.items(): setattr(p,name,bytes.fromhex(hexbytes))
sys.modules[p.__name__]=p
from kiu.patches import patch_aim_request,pin_reticle
from kiu.experimental import menu_render_pass
patch_aim_request.assemble_hook(); pin_reticle.assemble_hook(); pin_reticle.assemble_late_hook()
assert len(menu_render_pass.PRE_CAVE_BYTES) == menu_render_pass.CAVE_SIZE
assert 'keystone' not in sys.modules
"""
        values = {name: blob.hex() for name, blob in self.reference.items()}
        values["BUILD_SHA256"] = ground.CODE_SHA256
        result = subprocess.run([sys.executable, "-c", script,
                                 str(Path(__file__).resolve().parents[1] / "src"), json.dumps(values)],
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)


if __name__ == "__main__":
    unittest.main()
