"""Read-only verification of the exact supported, decrypted CCI build.

No game data is extracted or written.  Container identifiers alone are not
trusted: both the full source image and its uncompressed ExeFS code must match
the supported SHA256 values.  This module has no host-platform dependencies.
"""
from __future__ import annotations

from collections.abc import Callable
import hashlib
from pathlib import Path
import struct
from typing import BinaryIO

TITLE_ID = "0004000000030100"
PRODUCT_CODE = "CTR-P-AKDE"
SOURCE_SHA256 = "98a5fd7708d4d31364953f05183c919093c9a1bac9a0a1b7a97fbb33c063cbc8"
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"
MEDIA_UNIT = 0x200
HASH_CHUNK = 4 * 1024 * 1024


class GameVerificationError(ValueError):
    """The selected image is unsupported, encrypted, or malformed."""


class VerificationCancelled(RuntimeError):
    """The caller cancelled verification before it completed."""


def _cancel(cancelled: Callable[[], bool] | None) -> None:
    if cancelled is not None and cancelled():
        raise VerificationCancelled("Game verification cancelled")


def _bounds(offset: int, length: int, limit: int, label: str) -> None:
    if offset < 0 or length <= 0 or offset > limit or length > limit - offset:
        raise GameVerificationError(f"Truncated or out-of-bounds {label}")


def _read(
    source: BinaryIO, offset: int, length: int, limit: int, label: str,
    cancelled: Callable[[], bool] | None,
) -> bytes:
    _cancel(cancelled)
    _bounds(offset, length, limit, label)
    source.seek(offset)
    data = source.read(length)
    if len(data) != length:
        raise GameVerificationError(f"Truncated {label}")
    return data


def _hash(
    source: BinaryIO, offset: int, length: int, label: str,
    cancelled: Callable[[], bool] | None, progress: Callable[[str], None] | None,
) -> str:
    source.seek(offset)
    digest = hashlib.sha256()
    remaining = length
    previous = -1
    while remaining:
        _cancel(cancelled)
        block = source.read(min(HASH_CHUNK, remaining))
        if not block:
            raise GameVerificationError(f"Truncated {label}")
        digest.update(block)
        remaining -= len(block)
        percent = (length - remaining) * 100 // length
        if progress is not None and percent != previous:
            progress(f"Verifying {label}: {percent}%")
            previous = percent
    _cancel(cancelled)
    return digest.hexdigest()


def verify_game(
    path: Path, *, cancelled: Callable[[], bool] | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict[str, object]:
    """Verify a user-owned decrypted CCI/3DS without creating game files.

    Returns portable identity metadata, with no filenames or private paths.
    Cancellation raises ``VerificationCancelled``; invalid images raise
    ``GameVerificationError``. File-access errors remain ``OSError``.
    Standalone NCCH/CXI, encrypted dumps, updates, and compressed code are not
    supported by this exact-build workflow.
    """
    _cancel(cancelled)
    if progress is not None:
        progress("Checking decrypted CCI container")
    with Path(path).open("rb") as source:
        source.seek(0, 2)
        source_size = source.tell()
        header = _read(source, 0, MEDIA_UNIT, source_size, "CCI header", cancelled)
        if header[0x100:0x104] != b"NCSD":
            raise GameVerificationError("Unsupported container: select a decrypted CCI/3DS, not CIA/CXI")
        media_size = struct.unpack_from("<I", header, 0x104)[0] * MEDIA_UNIT
        title_id = f"{struct.unpack_from('<Q', header, 0x108)[0]:016X}"
        if title_id != TITLE_ID:
            raise GameVerificationError("Unsupported title: Kid Icarus Uprising USA v0 is required")
        if not media_size or source_size > media_size:
            raise GameVerificationError("Invalid CCI media size")
        # NCSD has eight interleaved (offset, size) pairs, not two arrays.
        game_offset, game_units = struct.unpack_from("<II", header, 0x120)
        game_offset *= MEDIA_UNIT
        game_size = game_units * MEDIA_UNIT
        _bounds(game_offset, game_size, source_size, "game partition")
        ncch = _read(source, game_offset, MEDIA_UNIT, source_size, "NCCH header", cancelled)
        if ncch[0x100:0x104] != b"NCCH":
            raise GameVerificationError("Unsupported game partition: missing NCCH header")
        content_size = struct.unpack_from("<I", ncch, 0x104)[0] * MEDIA_UNIT
        _bounds(0, content_size, game_size, "NCCH content")
        program_id = f"{struct.unpack_from('<Q', ncch, 0x118)[0]:016X}"
        product_code = ncch[0x150:0x160].rstrip(b"\0").decode("ascii", errors="replace")
        if program_id != TITLE_ID or product_code != PRODUCT_CODE:
            raise GameVerificationError("Unsupported region/build or update: USA v0 is required")
        if not ncch[0x18F] & 4:
            raise GameVerificationError("Encrypted image: a decrypted NoCrypto CCI is required")
        exheader_size = struct.unpack_from("<I", ncch, 0x180)[0]
        if exheader_size != 0x400:
            raise GameVerificationError("Unsupported exheader size for USA v0")
        _bounds(0x200, exheader_size, content_size, "exheader")
        exheader = _read(source, game_offset + 0x200, exheader_size, source_size, "exheader", cancelled)
        if hashlib.sha256(exheader).digest() != ncch[0x160:0x180]:
            raise GameVerificationError("Exheader hash mismatch")
        remaster_version = struct.unpack_from("<H", exheader, 0x0E)[0]
        if remaster_version != 0:
            raise GameVerificationError("Unsupported update/remaster version: USA v0 is required")
        if exheader[0x0D] & 1:
            raise GameVerificationError("Compressed ExeFS code is unsupported")
        exefs_offset, exefs_units, hash_units = struct.unpack_from("<III", ncch, 0x1A0)
        exefs_offset *= MEDIA_UNIT
        exefs_size = exefs_units * MEDIA_UNIT
        _bounds(exefs_offset, exefs_size, content_size, "ExeFS")
        if exefs_offset < 0x600 or hash_units != 1 or exefs_size < MEDIA_UNIT:
            raise GameVerificationError("Unsupported ExeFS superblock layout")
        exefs_start = game_offset + exefs_offset
        superblock = _read(source, exefs_start, MEDIA_UNIT, source_size, "ExeFS superblock", cancelled)
        if hashlib.sha256(superblock).digest() != ncch[0x1C0:0x1E0]:
            raise GameVerificationError("ExeFS superblock hash mismatch")
        code_entries = []
        for index in range(10):
            name = superblock[index * 0x10:index * 0x10 + 8].rstrip(b"\0")
            if not name:
                continue
            offset, size = struct.unpack_from("<II", superblock, index * 0x10 + 8)
            _bounds(MEDIA_UNIT + offset, size, exefs_size, "ExeFS entry")
            if name == b".code":
                code_entries.append((exefs_start + MEDIA_UNIT + offset, size))
        if len(code_entries) != 1:
            raise GameVerificationError("Expected one uncompressed ExeFS .code entry")
        code_start, code_size = code_entries[0]
        # Per-file ExeFS hashes in this decrypted dump reflect ciphertext.
        # Compatibility is proven by the full known source/code hashes, not
        # by those stored per-file hashes.
        code_hash = _hash(source, code_start, code_size, "game code", cancelled, progress)
        if code_hash != CODE_SHA256:
            raise GameVerificationError("Unsupported game code SHA256: exact USA v0 is required")
        source_hash = _hash(source, 0, source_size, "source image", cancelled, progress)
        if source_hash != SOURCE_SHA256:
            raise GameVerificationError("Unsupported source image SHA256: exact decrypted USA v0 CCI is required")
    if progress is not None:
        progress("Game build verified: USA v0")
    return {
        "title_id": title_id, "product_code": product_code,
        "remaster_version": remaster_version, "container": "CCI", "no_crypto": True,
        "source_sha256": source_hash, "code_sha256": code_hash,
        "source_size": source_size, "code_size": code_size,
    }
