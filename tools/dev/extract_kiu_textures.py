"""Extract nested Kid Icarus: Uprising resources and export CGFX textures.

The ROM itself is never modified.  By default all files found inside DARC
archives are written below ``local/texture_work/asset_pack/unpacked`` and all
decoded textures are written as PNGs below ``.../original_png``.
"""

from __future__ import annotations

import argparse
from collections import Counter
import fnmatch
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[2]

try:
    from PIL import Image
except ImportError as exc:  # pragma: no cover - exercised only on bad setup.
    raise SystemExit("Pillow is required for PNG export: python -m pip install pillow") from exc

from .kiu_assets import (
    AssetFormatError,
    decode_pica_texture,
    iter_cgfx_textures,
    iter_darc,
    unwrap_lz11_darc,
)


TITLE_ID = "0004000000030100"
ROM_SHA256 = "98a5fd7708d4d31364953f05183c919093c9a1bac9a0a1b7a97fbb33c063cbc8"


def _safe_component(value: str) -> str:
    invalid = '<>:"/\\|?*'
    cleaned = "".join("_" if char in invalid or ord(char) < 32 else char for char in value)
    cleaned = cleaned.rstrip(" .") or "_"
    if cleaned.upper() in {
        "CON", "PRN", "AUX", "NUL",
        *(f"COM{index}" for index in range(1, 10)),
        *(f"LPT{index}" for index in range(1, 10)),
    }:
        cleaned = f"_{cleaned}"
    return cleaned


def _safe_parts(parts):
    return tuple(_safe_component(str(part)) for part in parts if str(part))


def _format_size(value: int) -> str:
    units = ("B", "KiB", "MiB", "GiB")
    amount = float(value)
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{value} B"


class Extractor:
    def __init__(self, romfs: Path, output: Path, *, write_raw: bool, write_png: bool, flip_y: bool):
        self.romfs = romfs
        self.output = output
        self.raw_root = output / "unpacked"
        self.png_root = output / "original_png"
        self.write_raw = write_raw
        self.write_png = write_png
        self.flip_y = flip_y
        self.stats = Counter()
        self.format_counts = Counter()
        self.errors = []
        self.textures = []

    def _record_error(self, origin: str, error: Exception) -> None:
        self.stats["errors"] += 1
        if len(self.errors) < 500:
            self.errors.append({"origin": origin, "error": str(error)})

    def _write_raw(self, storage_parts, data: bytes) -> None:
        target = self.raw_root.joinpath(*_safe_parts(storage_parts))
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        self.stats["raw_files_written"] += 1
        self.stats["raw_bytes_written"] += len(data)

    def _png_path(self, storage_parts, texture_index: int, name: str) -> Path:
        parts = _safe_parts(storage_parts)
        if not parts:
            parent = self.png_root
            stem = "resource"
        else:
            parent = self.png_root.joinpath(*parts[:-1])
            stem = parts[-1]
        filename = _safe_component(f"{stem}__tex{texture_index:03d}_{name}.png")
        return parent / filename

    def _export_texture(self, container: bytes, origin: str, storage_parts, texture) -> None:
        self.stats["textures_seen"] += 1
        self.format_counts[texture.format_name] += 1
        try:
            rgba = decode_pica_texture(
                texture.data,
                texture.width,
                texture.height,
                texture.format,
                flip_y=self.flip_y,
            )
        except (AssetFormatError, ValueError) as exc:
            self.stats["texture_decode_errors"] += 1
            self._record_error(f"{origin}::{texture.name}", exc)
            return

        output_path = None
        if self.write_png:
            output_path = self._png_path(storage_parts, texture.index, texture.name)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            Image.frombytes("RGBA", (texture.width, texture.height), rgba).save(
                output_path, format="PNG"
            )
            self.stats["png_files_written"] += 1
            self.stats["png_bytes_written"] += output_path.stat().st_size

        self.textures.append(
            {
                "origin": origin,
                "output_png": output_path.relative_to(self.output).as_posix()
                if output_path is not None else None,
                "name": texture.name,
                "index": texture.index,
                "width": texture.width,
                "height": texture.height,
                "format": texture.format,
                "format_name": texture.format_name,
                "mipmap_levels": texture.mipmap_levels,
                "data_offset": texture.data_offset,
                "data_size": texture.data_size,
                "texture_sha256": hashlib.sha256(texture.data).hexdigest(),
                "container_sha256": hashlib.sha256(container).hexdigest(),
            }
        )

    def _process_resource(self, data: bytes, origin: str, storage_parts, suffix: str, depth: int = 0) -> None:
        if depth > 12:
            self._record_error(origin, AssetFormatError("archive nesting exceeds depth limit"))
            return

        is_darc = data[:4] == b"darc"
        is_lz11_archive = data[:1] == b"\x11" and suffix.lower() in {".zrc", ".arc"}
        if is_darc or is_lz11_archive:
            try:
                archive = unwrap_lz11_darc(data) if not is_darc else data
                entries = list(iter_darc(archive))
            except (AssetFormatError, ValueError) as exc:
                self.stats["archive_errors"] += 1
                self._record_error(origin, exc)
                return

            self.stats["archives_parsed"] += 1
            self.stats["archive_entries_seen"] += len(entries)
            for entry in entries:
                entry_parts = tuple(part for part in entry.path.split("/") if part)
                child_origin = f"{origin}::{entry.path}"
                child_storage = tuple(storage_parts) + entry_parts
                if self.write_raw:
                    self._write_raw(child_storage, entry.data)
                # Keep the archive file itself and its extracted children
                # separate; Windows cannot represent both ``bind.arc`` as a
                # file and ``bind.arc/...`` as a directory.
                child_suffix = Path(entry.path).suffix
                child_is_archive = (
                    entry.data[:4] == b"darc"
                    or entry.data[:1] == b"\x11"
                    and child_suffix.lower() in {".zrc", ".arc"}
                )
                child_content_storage = (
                    child_storage[:-1] + (child_storage[-1] + "__contents",)
                    if child_is_archive else child_storage
                )
                self._process_resource(
                    entry.data,
                    child_origin,
                    child_content_storage,
                    child_suffix,
                    depth + 1,
                )
            return

        if data[:4] != b"CGFX":
            return

        self.stats["cgfx_containers_seen"] += 1
        try:
            textures = list(iter_cgfx_textures(data))
        except (AssetFormatError, ValueError) as exc:
            self.stats["cgfx_errors"] += 1
            self._record_error(origin, exc)
            return
        self.stats["cgfx_containers_with_textures"] += bool(textures)
        for texture in textures:
            self._export_texture(data, origin, storage_parts, texture)

    def run(self, patterns) -> dict:
        if not self.romfs.is_dir():
            raise SystemExit(f"RomFS directory not found: {self.romfs}")
        files = sorted(path for path in self.romfs.rglob("*") if path.is_file())
        selected = []
        for path in files:
            relative = path.relative_to(self.romfs).as_posix()
            if patterns and not any(fnmatch.fnmatch(relative, pattern) for pattern in patterns):
                continue
            selected.append((path, relative))

        self.stats["romfs_files_seen"] = len(files)
        self.stats["resources_selected"] = len(selected)
        for index, (path, relative) in enumerate(selected, start=1):
            data = path.read_bytes()
            self._process_resource(data, relative, tuple(relative.split("/")), path.suffix)
            if index == 1 or index % 250 == 0 or index == len(selected):
                print(
                    f"[{index}/{len(selected)}] {relative} | "
                    f"archives={self.stats['archives_parsed']} "
                    f"textures={self.stats['textures_seen']}"
                )

        manifest = {
            "title_id": TITLE_ID,
            "rom_sha256": ROM_SHA256,
            "romfs": self.romfs.name,
            "orientation": "top-left PNG orientation" if not self.flip_y else "vertical-flipped PNG orientation",
            "stats": dict(self.stats),
            "format_counts": dict(sorted(self.format_counts.items())),
            "textures": self.textures,
            "errors": self.errors,
            "errors_truncated": self.stats["errors"] > len(self.errors),
        }
        self.output.mkdir(parents=True, exist_ok=True)
        manifest_path = self.output / "asset_manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
        print(f"manifest: {manifest_path}")
        print(f"textures: {self.stats['textures_seen']} ({dict(self.format_counts)})")
        print(f"PNG output: {self.stats['png_files_written']} files, {_format_size(self.stats['png_bytes_written'])}")
        if self.write_raw:
            print(f"raw output: {self.stats['raw_files_written']} files, {_format_size(self.stats['raw_bytes_written'])}")
        if self.errors:
            print(f"errors: {self.stats['errors']} (see asset_manifest.json)", file=sys.stderr)
        return manifest


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--romfs",
        type=Path,
        default=ROOT / "local/texture_work/romfs",
        help="extracted RomFS directory",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=ROOT / "local/texture_work/asset_pack",
        help="generated output directory",
    )
    parser.add_argument(
        "--path",
        action="append",
        default=[],
        help="limit direct RomFS inputs using fnmatch; may be repeated",
    )
    parser.add_argument(
        "--no-raw",
        action="store_true",
        help="do not write files extracted from nested archives",
    )
    parser.add_argument(
        "--no-png",
        action="store_true",
        help="only unpack nested files and write metadata",
    )
    parser.add_argument(
        "--flip-y",
        action="store_true",
        help="vertically flip exported PNGs for GPU/debug-buffer orientation",
    )
    args = parser.parse_args()
    Extractor(
        args.romfs,
        args.output,
        write_raw=not args.no_raw,
        write_png=not args.no_png,
        flip_y=args.flip_y,
    ).run(args.path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
