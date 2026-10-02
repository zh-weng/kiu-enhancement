"""CCI extraction regressions using an artificial container, without a ROM."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

from tools import prepare_kiu as prepare


def container(code=b"\x1e\xff\x2f\xe1", *, compressed=False, bad_superblock=False):
    game = 0x800
    exefs = game + 0x800
    image = bytearray(exefs + 0x200 + len(code))
    image[0x100:0x104] = b"NCSD"
    struct.pack_into("<I", image, 0x104, len(image) // 0x200 + 1)
    struct.pack_into("<Q", image, 0x108, int(prepare.SUPPORTED_TITLE_ID, 16))
    struct.pack_into("<II", image, 0x120, game // 0x200, 6)
    image[game + 0x100:game + 0x104] = b"NCCH"
    struct.pack_into("<I", image, game + 0x104, 6)
    for off in (0x108, 0x118):
        struct.pack_into("<Q", image, game + off, int(prepare.SUPPORTED_TITLE_ID, 16))
    image[game + 0x150:game + 0x15A] = prepare.SUPPORTED_PRODUCT_CODE.encode("ascii")
    struct.pack_into("<I", image, game + 0x180, 0x400)
    image[game + 0x18F] = 4  # NoCrypto
    struct.pack_into("<III", image, game + 0x1A0, 4, 2, 1)

    exheader = bytearray(0x400)
    exheader[:6] = b"apollo"
    exheader[0xD] = int(compressed)
    struct.pack_into("<III", exheader, 0x10, 0x00100000, 1, len(code))
    image[game + 0x200:game + 0x600] = exheader
    image[game + 0x160:game + 0x180] = hashlib.sha256(exheader).digest()

    superblock = bytearray(0x200)
    superblock[:5] = b".code"
    struct.pack_into("<II", superblock, 8, 0, len(code))
    superblock[0xC0:0xE0] = hashlib.sha256(code).digest()
    image[exefs:exefs + 0x200] = superblock
    image[exefs + 0x200:] = code
    image[game + 0x1C0:game + 0x1E0] = (
        bytes(32) if bad_superblock else hashlib.sha256(superblock).digest()
    )
    return bytes(image)


class PrepareTests(unittest.TestCase):
    def extract(self, root, data):
        source = root / "private" / "my-dump.cci"
        source.parent.mkdir()
        source.write_bytes(data)
        output = root / "output"
        manifest = root / "metadata.json"
        with redirect_stdout(io.StringIO()):
            result = prepare.main([
                str(source), "--out-dir", str(output), "--manifest", str(manifest),
            ])
        return result, output, manifest

    def test_dot_code_is_extracted_and_hashed(self):
        code = bytes.fromhex("1eff2fe100000000")
        data = container(code)
        with tempfile.TemporaryDirectory() as directory:
            result, output, manifest = self.extract(Path(directory), data)
            self.assertEqual((output / "code.bin").read_bytes(), code)
            self.assertEqual((output / "exefs_code.bin").read_bytes(), code)
            self.assertEqual(result["code_bin"]["sha256"], hashlib.sha256(code).hexdigest())
            self.assertEqual(result["source_image"]["sha256"], hashlib.sha256(data).hexdigest())
            self.assertTrue(result["exheader"]["sha256_verified"])
            self.assertTrue(result["exefs_superblock_hash_ok"])
            self.assertFalse(result["supported_build"])
            self.assertEqual(json.loads(manifest.read_text()), result)

    def test_metadata_does_not_record_private_absolute_paths(self):
        with tempfile.TemporaryDirectory() as directory:
            result, _, manifest = self.extract(Path(directory), container())
            self.assertEqual(result["source_image"]["path"], "my-dump.cci")
            self.assertEqual(result["exefs"][".code"]["file"], "exefs_code.bin")
            self.assertEqual(result["code_bin"]["file"], "code.bin")
            self.assertNotIn(directory, manifest.read_text())

    def test_repo_output_paths_are_relative_posix(self):
        self.assertEqual(
            prepare.metadata_path(prepare.ROOT / "local/extracted/code.bin"),
            "local/extracted/code.bin",
        )

    def test_invalid_superblock_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SystemExit, "superblock hash mismatch"):
                self.extract(Path(directory), container(bad_superblock=True))

    def test_compressed_code_is_not_misdecoded_as_lz11(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(SystemExit, "compressed ExeFS .code is unsupported"):
                self.extract(Path(directory), container(compressed=True))


if __name__ == "__main__":
    unittest.main()
