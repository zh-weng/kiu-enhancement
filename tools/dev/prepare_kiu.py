"""Extract a user-supplied decrypted CCI for local analysis.

Game binaries and generated metadata default to ``local/``.  The metadata
records hashes and portable filenames rather than the user's absolute paths.
"""

import argparse
import hashlib
import json
import struct
from pathlib import Path

from .lz11 import decompress as _lz11_decompress

MEDIA_UNIT = 0x200
ROOT = Path(__file__).resolve().parents[2]
SUPPORTED_TITLE_ID = "0004000000030100"
SUPPORTED_PRODUCT_CODE = "CTR-P-AKDE"
SUPPORTED_CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"

REGION_BITS = ["Japan", "NorthAmerica", "Europe", "Australia", "China", "Korea", "Taiwan"]


def metadata_path(path):
    """Keep metadata shareable even when input/output is outside the repo."""
    path = Path(path)
    try:
        return path.resolve().relative_to(ROOT).as_posix()
    except ValueError:
        return path.name


def parse_codeset(sci, off):
    addr, pages, size = struct.unpack("<III", sci[off:off + 12])
    return {"address": addr, "pages": pages, "size": size}


def parse_sci(sci):
    app_title = sci[0x00:0x08].rstrip(b"\x00").decode("ascii", errors="replace")
    flags_byte = sci[0x0D]
    segments = {
        "text": parse_codeset(sci, 0x10),
        "rodata": parse_codeset(sci, 0x20),
        "data": parse_codeset(sci, 0x30),
        "bss": {"size": struct.unpack("<I", sci[0x3C:0x40])[0]},
    }
    dependencies = [
        f"{v:016X}" for v in struct.unpack("<48Q", sci[0x40:0x40 + 384]) if v != 0
    ]
    return {
        "app_title": app_title,
        "code_compressed": bool(flags_byte & 1),
        "sd_app": bool(flags_byte & 2),
        "remaster_version": struct.unpack("<H", sci[0x0E:0x10])[0],
        "segments": segments,
        "stack_size": struct.unpack("<I", sci[0x1C:0x20])[0],
        "dependencies": dependencies,
    }


def parse_exefs_superblock(sb):
    entries = []
    for i in range(10):
        raw = sb[i * 0x10:(i + 1) * 0x10]
        name_raw = raw[:8]
        if name_raw == b"\x00" * 8:
            continue
        name = name_raw.rstrip(b"\x00").decode("ascii", errors="replace")
        f_off_bytes, f_size_bytes = struct.unpack("<II", raw[8:16])
        if f_size_bytes == 0:
            continue
        sha_expected = None
        if i < 8:
            hpos = 0xC0 + i * 0x20
            sha_expected = sb[hpos:hpos + 0x20].hex()
        entries.append({
            "name": name,
            "offset_in_exefs": 0x200 + f_off_bytes,
            "size": f_size_bytes,
            "sha256_expected": sha_expected,
        })
    return entries


def lz11_decompress(data):
    """Compatibility wrapper around the shared, tested LZ11 decoder."""

    return _lz11_decompress(data)


def parse_smdh(icon):
    if icon is None or icon[:4] != b"SMDH":
        return None
    def utf16(off, count):
        return icon[off:off + count * 2].decode("utf-16-le", errors="replace").rstrip("\x00")
    names = []
    for lang in range(16):
        base = 8 + lang * 0x200
        short = utf16(base, 0x40)
        long_ = utf16(base + 0x80, 0x80)
        pub = utf16(base + 0x180, 0x40)
        if short or long_:
            names.append({"lang_index": lang, "short": short, "long": long_, "publisher": pub})
    region_flags = struct.unpack("<I", icon[0x2010:0x2014])[0]
    regions = [n for i, n in enumerate(REGION_BITS) if region_flags & (1 << i)]
    return {"names": names[:4], "regions": regions}


def parse_ncsd_partitions(table: bytes):
    """Eight interleaved (offset, size) pairs, in 0x200-byte media units."""
    if len(table) != 0x40:
        raise ValueError("truncated NCSD partition table")
    return tuple(struct.iter_unpack("<II", table))


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("cci", type=Path, help="path to your decrypted CCI/3DS dump")
    ap.add_argument("--out-dir", type=Path, default=ROOT / "local/extracted")
    ap.add_argument("--manifest", type=Path, default=ROOT / "local/title.json")
    args = ap.parse_args(argv)

    cci_path = Path(args.cci)
    out_dir = Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    Path(args.manifest).parent.mkdir(parents=True, exist_ok=True)

    with open(cci_path, "rb") as fh:
        fh.seek(0x100)
        if fh.read(4) != b"NCSD":
            raise SystemExit("not a CCI/NCSD container")
        fh.seek(0x104)
        media_size_mu = struct.unpack("<I", fh.read(4))[0]
        fh.seek(0x108)
        ncsd_title_id = struct.unpack("<Q", fh.read(8))[0]
        fh.seek(0x120)
        partitions = parse_ncsd_partitions(fh.read(0x40))
        game_base = partitions[0][0] * MEDIA_UNIT
        print(f"[1/6] NCSD ok, game partition @ {game_base:#x}")

        fh.seek(game_base + 0x100)
        if fh.read(4) != b"NCCH":
            raise SystemExit("partition 0 is not NCCH")
        fh.seek(game_base + 0x104)
        content_size_mu = struct.unpack("<I", fh.read(4))[0]
        fh.seek(game_base + 0x108)
        partition_id = struct.unpack("<Q", fh.read(8))[0]
        fh.seek(game_base + 0x110)
        maker_code, format_version = struct.unpack("<HH", fh.read(4))
        fh.seek(game_base + 0x118)
        program_id = struct.unpack("<Q", fh.read(8))[0]
        fh.seek(game_base + 0x150)
        product_code = fh.read(0x10).rstrip(b"\x00").decode("ascii", errors="replace")
        fh.seek(game_base + 0x160)
        exheader_hash_expected = fh.read(0x20)
        fh.seek(game_base + 0x188)
        flags = fh.read(8)
        crypto_method = flags[3]
        platform = flags[4]
        content_form = flags[5]
        other_flag = flags[7]
        fh.seek(game_base + 0x190)
        (plain_off_mu, plain_size_mu, logo_off_mu, logo_size_mu,
         exefs_off_mu, exefs_size_mu, exefs_hash_mu, _r1,
         romfs_off_mu, romfs_size_mu, romfs_hash_mu, _r2) = struct.unpack("<12I", fh.read(48))
        fh.seek(game_base + 0x1C0)
        exefs_super_hash_expected = fh.read(0x20)

        no_crypto = bool(other_flag & 4)
        print(f"      TitleID={ncsd_title_id:016X}  ProgramID={program_id:016X}")
        print(f"      product={product_code!r} maker={maker_code:#06x} fmtver={format_version}")
        print(f"      crypto_method={crypto_method} no_crypto={no_crypto} content_form={content_form:#04x}")
        if not no_crypto:
            raise SystemExit(
                "\nThis dump is ENCRYPTED (flags[7] NoCrypto bit not set).\n"
                "Provide a decrypted dump (GodMode9 'dump decrypted') or extend this\n"
                "script with proper key handling before static analysis.\n"
            )

        print("[2/6] verifying exheader")
        fh.seek(game_base + 0x180)
        exheader_size = struct.unpack("<I", fh.read(4))[0]
        fh.seek(game_base + 0x200)
        exheader = fh.read(exheader_size)
        got = hashlib.sha256(exheader).digest()
        exhash_ok = got == exheader_hash_expected
        print(f"      exheader hash match: {exhash_ok}")
        if not exhash_ok:
            raise SystemExit("exheader hash mismatch on a supposedly-decrypted dump - aborting")
        (out_dir / "exheader.bin").write_bytes(exheader)

        sci = parse_sci(exheader[:0x200])
        print(f"      remaster_version={sci['remaster_version']} compressed={sci['code_compressed']}")
        for seg in ("text", "rodata", "data"):
            s = sci["segments"][seg]
            print(f"      {seg:<7}: addr={s['address']:#010x} pages={s['pages']:<6} size={s['size']:#010x}")
        print(f"      bss     : size={sci['segments']['bss']['size']:#010x}")
        print(f"      stack   : {sci['stack_size']:#x}")
        print(f"      dependencies ({len(sci['dependencies'])}): {', '.join(sci['dependencies'][:6])}{' ...' if len(sci['dependencies']) > 6 else ''}")

        print("[3/6] verifying ExeFS superblock")
        exefs_abs = game_base + exefs_off_mu * MEDIA_UNIT
        fh.seek(exefs_abs)
        sb_len = max(exefs_hash_mu * MEDIA_UNIT, MEDIA_UNIT)
        superblock = fh.read(sb_len)
        sb_ok = hashlib.sha256(superblock[:MEDIA_UNIT]).digest() == exefs_super_hash_expected
        print(f"      superblock hash match: {sb_ok}")
        if not sb_ok:
            raise SystemExit("ExeFS superblock hash mismatch - aborting")
        entries = parse_exefs_superblock(superblock)

        print("[4/6] extracting ExeFS files")
        extracted = {}
        extracted_paths = {}
        for e in entries:
            fh.seek(exefs_abs + e["offset_in_exefs"])
            blob = fh.read(e["size"])
            if len(blob) != e["size"]:
                raise SystemExit(f"truncated ExeFS file: {e['name']}")
            got_hex = hashlib.sha256(blob).hexdigest()
            ok = (got_hex == e["sha256_expected"]) if e["sha256_expected"] else None
            safe = "".join(c if c.isalnum() or c in "_-" else "_"
                           for c in e["name"].lstrip(".")) or "unnamed"
            dest = out_dir / f"exefs_{safe}.bin"
            dest.write_bytes(blob)
            extracted_paths[e["name"]] = dest
            extracted[e["name"]] = {**e, "file": metadata_path(dest), "sha256_actual": got_hex, "hash_ok": ok}
            print(f"      {e['name']:<8} {e['size']:>12,} bytes  hash_ok={ok}")

    print("[5/6] preparing .code")
    code_bin_path = None
    code_name = ".code" if ".code" in extracted else "code" if "code" in extracted else None
    if code_name:
        raw = extracted_paths[code_name].read_bytes()
        if sci["code_compressed"]:
            raise SystemExit("compressed ExeFS .code is unsupported; the supported USA v0 build is uncompressed")
        else:
            code = raw
            print(f"      already uncompressed ({len(code):,} bytes)")
        code_bin_path = out_dir / "code.bin"
        code_bin_path.write_bytes(code)
        print(f"      .text load address: {sci['segments']['text']['address']:#010x}")

    smdh = parse_smdh(extracted_paths["icon"].read_bytes()) if "icon" in extracted else None
    if smdh:
        print(f"[6/6] SMDH: {smdh['names'][0]['long']} | regions={smdh['regions']}" if smdh["names"] else "[6/6] SMDH present")

    print("[+] hashing source image")
    h = hashlib.sha256()
    with open(cci_path, "rb") as fh:
        while True:
            block = fh.read(1 << 22)
            if not block:
                break
            h.update(block)
    cci_sha = h.hexdigest()
    print(f"      {cci_sha}")

    manifest = {
        "game": "Kid Icarus: Uprising",
        "source_image": {
            "path": cci_path.name,
            "size": cci_path.stat().st_size,
            "sha256": cci_sha,
            "title_id": f"{ncsd_title_id:016X}",
            "media_size_mu": media_size_mu,
        },
        "ncch": {
            "offset": game_base,
            "content_size": content_size_mu * MEDIA_UNIT,
            "partition_id": f"{partition_id:016X}",
            "program_id": f"{program_id:016X}",
            "maker_code": maker_code,
            "format_version": format_version,
            "product_code": product_code,
            "crypto_method": crypto_method,
            "no_crypto": no_crypto,
            "sections_mu": {
                "plain": [plain_off_mu, plain_size_mu],
                "logo": [logo_off_mu, logo_size_mu],
                "exefs": [exefs_off_mu, exefs_size_mu],
                "romfs": [romfs_off_mu, romfs_size_mu],
            },
        },
        "exheader": {
            "sha256_verified": exhash_ok,
            **sci,
        },
        "exefs_superblock_hash_ok": sb_ok,
        "exefs": extracted,
        "code_bin": {
            "file": metadata_path(code_bin_path),
            "size": code_bin_path.stat().st_size,
            "sha256": hashlib.sha256(code_bin_path.read_bytes()).hexdigest(),
            "text_address": sci["segments"]["text"]["address"],
            "compressed_in_exefs": sci["code_compressed"],
        } if code_bin_path else None,
        "smdh": smdh,
    }
    manifest["supported_build"] = bool(
        manifest["source_image"]["title_id"] == SUPPORTED_TITLE_ID
        and manifest["ncch"]["program_id"] == SUPPORTED_TITLE_ID
        and product_code == SUPPORTED_PRODUCT_CODE
        and sci["remaster_version"] == 0
        and manifest["code_bin"]
        and manifest["code_bin"]["sha256"] == SUPPORTED_CODE_SHA256
    )
    Path(args.manifest).write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    print(f"\nDONE. manifest -> {args.manifest}")
    return manifest


if __name__ == "__main__":
    main()
