"""Read-only exact-build checks using a synthetic, non-game CCI fixture."""
import hashlib
from pathlib import Path
import struct
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from kiu import game_build as build


def container(*, code=b"test-code", code_offset=0x34, code_index=9,
              encrypted=False, compressed=False, remaster=0, duplicate=False):
    """Use the last of ten table slots and a byte, not media-unit, offset."""
    game, exefs = 0x800, 0x1000
    image = bytearray(0x2000)
    image[0x100:0x104] = b"NCSD"
    struct.pack_into("<I", image, 0x104, len(image) // 0x200)
    struct.pack_into("<Q", image, 0x108, int(build.TITLE_ID, 16))
    struct.pack_into("<II", image, 0x120, game // 0x200, 8)
    image[game + 0x100:game + 0x104] = b"NCCH"
    struct.pack_into("<I", image, game + 0x104, 8)
    struct.pack_into("<Q", image, game + 0x118, int(build.TITLE_ID, 16))
    image[game + 0x150:game + 0x15A] = build.PRODUCT_CODE.encode("ascii")
    image[game + 0x18F] = 0 if encrypted else 4
    struct.pack_into("<I", image, game + 0x180, 0x400)
    struct.pack_into("<III", image, game + 0x1A0, 4, 4, 1)
    exheader = bytearray(0x400)
    exheader[0x0D] = int(compressed)
    struct.pack_into("<H", exheader, 0x0E, remaster)
    image[game + 0x200:game + 0x600] = exheader
    image[game + 0x160:game + 0x180] = hashlib.sha256(exheader).digest()
    superblock = bytearray(0x200)
    for index in (code_index, 0) if duplicate else (code_index,):
        superblock[index * 0x10:index * 0x10 + 5] = b".code"
        struct.pack_into("<II", superblock, index * 0x10 + 8, code_offset, len(code))
    # Deliberately unrelated per-file hash: it is not plaintext proof.
    superblock[0xC0:0xE0] = b"\xA5" * 32
    image[exefs:exefs + 0x200] = superblock
    if 0 <= code_offset <= 0x600 - len(code):
        image[exefs + 0x200 + code_offset:exefs + 0x200 + code_offset + len(code)] = code
    image[game + 0x1C0:game + 0x1E0] = hashlib.sha256(superblock).digest()
    return bytes(image)


class GameBuildTests(unittest.TestCase):
    def verify(self, data, **options):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "owned.cci"
            path.write_bytes(data)
            with mock.patch.object(build, "SOURCE_SHA256", hashlib.sha256(data).hexdigest()), \
                    mock.patch.object(build, "CODE_SHA256", hashlib.sha256(b"test-code").hexdigest()):
                result = build.verify_game(path, **options)
            self.assertEqual(list(root.iterdir()), [path])
            self.assertEqual(path.read_bytes(), data)
            self.assertNotIn(directory, str(result))
            return result

    def test_exact_identity_hashes_without_extracting_files(self):
        data = container()
        updates = []
        result = self.verify(data, progress=updates.append)
        self.assertEqual(result, {
            "title_id": build.TITLE_ID, "product_code": build.PRODUCT_CODE,
            "remaster_version": 0, "container": "CCI", "no_crypto": True,
            "source_sha256": hashlib.sha256(data).hexdigest(),
            "code_sha256": hashlib.sha256(b"test-code").hexdigest(),
            "source_size": len(data), "code_size": len(b"test-code"),
        })
        self.assertTrue(any("game code: 100%" in item for item in updates))
        self.assertTrue(any("source image: 100%" in item for item in updates))

    def test_renamed_3ds_uses_container_not_extension(self):
        data = container()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owned.3ds"
            path.write_bytes(data)
            with mock.patch.object(build, "SOURCE_SHA256", hashlib.sha256(data).hexdigest()), \
                    mock.patch.object(build, "CODE_SHA256", hashlib.sha256(b"test-code").hexdigest()):
                self.assertEqual(build.verify_game(path)["container"], "CCI")

    def test_non_cci_container_is_refused(self):
        data = bytearray(container())
        data[0x100:0x104] = b"NCCH"
        with self.assertRaisesRegex(build.GameVerificationError, "Unsupported container"):
            self.verify(data)

    def test_short_header_is_refused_without_struct_error(self):
        with self.assertRaisesRegex(build.GameVerificationError, "CCI header"):
            self.verify(b"short fixture")

    def test_other_region_identifiers_are_refused(self):
        data = bytearray(container())
        data[0x953] = ord("J")
        with self.assertRaisesRegex(build.GameVerificationError, "Unsupported region/build"):
            self.verify(data)

    def test_encrypted_build_is_refused_before_hashing(self):
        with self.assertRaisesRegex(build.GameVerificationError, "Encrypted"):
            self.verify(container(encrypted=True))

    def test_update_is_refused_even_with_matching_source_hash(self):
        with self.assertRaisesRegex(build.GameVerificationError, "update/remaster"):
            self.verify(container(remaster=1))

    def test_compressed_code_is_refused_not_decoded(self):
        with self.assertRaisesRegex(build.GameVerificationError, "Compressed"):
            self.verify(container(compressed=True))

    def test_matching_identifiers_do_not_accept_other_code(self):
        with self.assertRaisesRegex(build.GameVerificationError, "code SHA256"):
            self.verify(container(code=b"othercode"))

    def test_matching_code_does_not_accept_other_source(self):
        data = container()
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "owned.cci"
            path.write_bytes(data)
            with mock.patch.object(build, "SOURCE_SHA256", "0" * 64), \
                    mock.patch.object(build, "CODE_SHA256", hashlib.sha256(b"test-code").hexdigest()):
                with self.assertRaisesRegex(build.GameVerificationError, "source image SHA256"):
                    build.verify_game(path)

    def test_exheader_hash_is_verified(self):
        data = bytearray(container())
        data[0xA20] ^= 1
        with self.assertRaisesRegex(build.GameVerificationError, "Exheader hash"):
            self.verify(data)

    def test_superblock_hash_is_verified(self):
        data = bytearray(container())
        data[0x11FF] ^= 1
        with self.assertRaisesRegex(build.GameVerificationError, "superblock hash"):
            self.verify(data)

    def test_code_offset_and_size_must_stay_within_exefs(self):
        with self.assertRaisesRegex(build.GameVerificationError, "out-of-bounds ExeFS entry"):
            self.verify(container(code_offset=0x1000))

    def test_duplicate_code_entries_are_refused(self):
        with self.assertRaisesRegex(build.GameVerificationError, "Expected one"):
            self.verify(container(duplicate=True))

    def test_missing_code_entry_is_refused(self):
        data = bytearray(container())
        data[0x1090:0x10A0] = bytes(0x10)
        data[0x9C0:0x9E0] = hashlib.sha256(data[0x1000:0x1200]).digest()
        with self.assertRaisesRegex(build.GameVerificationError, "Expected one"):
            self.verify(data)

    def test_truncated_partition_is_refused(self):
        with self.assertRaisesRegex(build.GameVerificationError, "game partition"):
            self.verify(container()[:0x1400])

    def test_partition_offset_cannot_leave_file(self):
        data = bytearray(container())
        struct.pack_into("<I", data, 0x120, 0xFFFFFFFF)
        with self.assertRaisesRegex(build.GameVerificationError, "out-of-bounds game partition"):
            self.verify(data)

    def test_ncch_content_cannot_leave_declared_partition(self):
        data = bytearray(container())
        struct.pack_into("<I", data, 0x904, 0x100)
        with self.assertRaisesRegex(build.GameVerificationError, "out-of-bounds NCCH content"):
            self.verify(data)

    def test_exefs_cannot_leave_ncch_content(self):
        data = bytearray(container())
        struct.pack_into("<I", data, 0x9A0, 0xFFFFFFFF)
        with self.assertRaisesRegex(build.GameVerificationError, "out-of-bounds ExeFS"):
            self.verify(data)

    def test_cancellation_before_file_access(self):
        with self.assertRaises(build.VerificationCancelled):
            build.verify_game(Path("nonexistent.cci"), cancelled=lambda: True)

    def test_cancellation_during_streaming_hash(self):
        cancelled = False

        def update(message):
            nonlocal cancelled
            if message.startswith("Verifying source image"):
                cancelled = True

        with mock.patch.object(build, "HASH_CHUNK", 128):
            with self.assertRaises(build.VerificationCancelled):
                self.verify(container(), progress=update, cancelled=lambda: cancelled)

    def test_missing_file_is_an_io_error(self):
        with self.assertRaises(FileNotFoundError):
            build.verify_game(Path("nonexistent.cci"))


if __name__ == "__main__":
    unittest.main()
