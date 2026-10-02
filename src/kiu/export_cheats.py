"""Export build-locked Azahar/Gateway cheats for the verified KIU USA build.

The generated file deliberately has separate Enable and Restore entries.
Azahar does not undo a write when a cheat is merely unchecked, so a restore
entry is required to put the guest back to stock bytes.

Every fixed code write is derived from the production patch modules, guarded
by its expected current words, and recorded with the authoritative code hash in
``manifests/gateway_cheats.json``.  Generated artifacts live under ``local/``.
"""
from __future__ import annotations

import hashlib
import argparse
import json
import math
import re
import struct
from dataclasses import dataclass
from pathlib import Path

from .patches import patch_60fps as fps
from .patches import patch_aim_request as aim
from .experimental import menu_render_pass as menu
from .experimental import patch_pitch_limit as pitch
from .patches import patch_widescreen as wide
from .patches import pin_reticle as reticle

ROOT = Path(__file__).resolve().parents[2]

TITLE_ID = "0004000000030100"
PRODUCT_CODE = "CTR-P-AKDE"
CODE_BASE = 0x00100000
CODE_SHA256 = "2718aa2077a29560208c4e499288e5fecc4446d7adec95efb91ebf1b19eda679"
CODE_PATH = ROOT / "local/extracted/exefs_code.bin"
# Write beside the user's working directory, never into installed site-packages.
CHEAT_PATH = Path(f"local/cheats/{TITLE_ID}.txt")
MANIFEST_PATH = Path("local/cheats/gateway_cheats.json")


@dataclass(frozen=True)
class StaticPatch:
    name: str
    address: int
    original: bytes
    replacement: bytes

    def __post_init__(self) -> None:
        if len(self.original) != len(self.replacement) or len(self.original) % 4:
            raise ValueError(f"{self.name}: patches must be equal, word-sized blobs")
        if not 0 <= self.address <= 0x0FFFFFFF:
            raise ValueError(f"{self.name}: Gateway address is out of range")

    def record(self) -> dict[str, object]:
        return {
            "name": self.name,
            "address": f"0x{self.address:08x}",
            "size": len(self.original),
            "original_hex": self.original.hex(),
            "replacement_hex": self.replacement.hex(),
            "build_sha256": CODE_SHA256,
        }


def _words(blob: bytes) -> list[int]:
    return [struct.unpack_from("<I", blob, offset)[0] for offset in range(0, len(blob), 4)]


def _equal_words(address: int, blob: bytes) -> list[str]:
    return [f"5{address + i * 4:07X} {word:08X}" for i, word in enumerate(_words(blob))]


def _write_blob(address: int, blob: bytes) -> list[str]:
    """Emit one word write or an E-code block for an arbitrary word blob."""
    if len(blob) == 4:
        return [f"0{address:07X} {_words(blob)[0]:08X}"]
    words = _words(blob)
    lines = [f"E{address:07X} {len(blob):08X}"]
    for index in range(0, len(words), 2):
        first = words[index]
        second = words[index + 1] if index + 1 < len(words) else 0
        lines.append(f"{first:08X} {second:08X}")
    return lines


def _guarded(expected: list[tuple[int, bytes]], writes: list[tuple[int, bytes]]) -> list[str]:
    """Apply all writes only when every expected word matches exactly."""
    lines: list[str] = []
    for address, blob in expected:
        lines.extend(_equal_words(address, blob))
    for address, blob in writes:
        lines.extend(_write_blob(address, blob))
    lines.append("D2000000 00000000")
    return lines


def _section(name: str, comments: list[str], lines: list[str]) -> str:
    build = f"Exact build only: code SHA-256 {CODE_SHA256}"
    return "\n".join([f"[{name}]", f"*{build}", *(f"*{item}" for item in comments), *lines, ""])


def fps_patches() -> tuple[list[StaticPatch], list[StaticPatch]]:
    caves = [
        StaticPatch(name, address, bytes(len(blob)), blob)
        for address, blob, name in fps.CAVES
    ]
    entries = [
        StaticPatch(site.purpose, site.address, site.expected, site.replacement)
        for site in fps.PATCH_SITES
    ]
    return caves, entries


def fps_enable(caves: list[StaticPatch], entries: list[StaticPatch]) -> list[str]:
    all_static = caves + entries
    lines: list[str] = []
    for patch in all_static:
        lines.extend(_equal_words(patch.address, patch.original))
    # Resolve *(SCHED_SLOT), then verify the live scheduler is the stock 30/2 object.
    lines.extend(
        [
            f"B{fps.SCHED_SLOT:07X} 00000000",
            f"5{fps.SCHED_NOMINAL_OFF:07X} {fps.SCHED_NOMINAL_ORIG:08X}",
            f"5{fps.SCHED_INTERVAL_OFF:07X} {fps.SCHED_INTERVAL_ORIG:08X}",
            "D3000000 00000000",
        ]
    )
    for patch in all_static:  # caves first, redirects second
        lines.extend(_write_blob(patch.address, patch.replacement))
    lines.extend(
        [
            f"B{fps.SCHED_SLOT:07X} 00000000",
            f"0{fps.SCHED_INTERVAL_OFF:07X} {fps.SCHED_INTERVAL_60:08X}",
            "D2000000 00000000",
        ]
    )
    # Keep the dynamic interval at zero while our two exact redirects are live.
    for patch in all_static:
        lines.extend(_equal_words(patch.address, patch.replacement))
    lines.extend(
        [
            f"B{fps.SCHED_SLOT:07X} 00000000",
            f"5{fps.SCHED_NOMINAL_OFF:07X} {fps.SCHED_NOMINAL_ORIG:08X}",
            f"0{fps.SCHED_INTERVAL_OFF:07X} {fps.SCHED_INTERVAL_60:08X}",
            "D2000000 00000000",
        ]
    )
    return lines


def fps_restore(caves: list[StaticPatch], entries: list[StaticPatch]) -> list[str]:
    all_static = caves + entries
    lines: list[str] = []
    for patch in all_static:
        lines.extend(_equal_words(patch.address, patch.replacement))
    lines.extend(
        [
            f"B{fps.SCHED_SLOT:07X} 00000000",
            f"5{fps.SCHED_NOMINAL_OFF:07X} {fps.SCHED_NOMINAL_ORIG:08X}",
            f"0{fps.SCHED_INTERVAL_OFF:07X} {fps.SCHED_INTERVAL_ORIG:08X}",
            "D3000000 00000000",
        ]
    )
    for patch in entries:  # disconnect first, then clear caves
        lines.extend(_write_blob(patch.address, patch.original))
    for patch in caves:
        lines.extend(_write_blob(patch.address, patch.original))
    lines.append("D2000000 00000000")
    return lines


def widescreen_patches(aspect: float) -> list[StaticPatch]:
    caves = [
        StaticPatch(
            f"routed-bottom projection extension {aspect:g}",
            wide.BOTTOM_EXTENSION_CAVE,
            bytes(wide.BOTTOM_EXTENSION_CAVE_SIZE),
            wide.build_bottom_extension(aspect),
        ),
    ] + [
        StaticPatch(
            f"projection cave {aspect:g}",
            cave,
            bytes(wide.PROJECTION_CAVE_SIZE),
            wide.build_cave(cave, site + 4, aspect),
        )
        for site, cave in zip(wide.PROJECTION_UPLOAD_SITES, wide.PROJECTION_CAVES, strict=True)
    ]
    entries = [
        StaticPatch(
            f"projection redirect {aspect:g}",
            site,
            wide.PROJECTION_UPLOAD_ORIGINAL,
            wide.encode_branch(site, cave),
        )
        for site, cave in zip(wide.PROJECTION_UPLOAD_SITES, wide.PROJECTION_CAVES, strict=True)
    ]
    return caves + entries


def menu_render_patches() -> list[StaticPatch]:
    return [
        StaticPatch(
            "menu bottom-to-top pre-pass cave",
            menu.PRE_CAVE,
            bytes(menu.CAVE_SIZE),
            menu.PRE_CAVE_BYTES,
        ),
        StaticPatch(
            "menu bottom-to-top post-pass cave",
            menu.POST_CAVE,
            bytes(menu.CAVE_SIZE),
            menu.POST_CAVE_BYTES,
        ),
        StaticPatch(
            "menu bottom-to-top pre-pass redirect",
            menu.PRE_SITE,
            menu.THUNK_ORIGINAL[:4],
            menu.PRE_REPLACEMENT,
        ),
        StaticPatch(
            "menu bottom-to-top post-pass redirect",
            menu.POST_SITE,
            menu.THUNK_ORIGINAL[:4],
            menu.POST_REPLACEMENT,
        ),
    ]


def menu_render_enable(patches: list[StaticPatch]) -> list[str]:
    caves, redirects = patches[:2], patches[2:]
    writes = [(item.address, item.replacement) for item in caves]
    writes.append((menu.ACTIVE_MARKER, struct.pack("<I", menu.MARKER_INACTIVE)))
    writes.extend((item.address, item.replacement) for item in redirects)
    return _guarded([(item.address, item.original) for item in patches], writes)


def menu_render_restore(patches: list[StaticPatch]) -> list[str]:
    caves, redirects = patches[:2], patches[2:]
    writes = [(item.address, item.original) for item in redirects]
    writes.append((menu.ACTIVE_MARKER, struct.pack("<I", menu.MARKER_INACTIVE)))
    writes.extend((item.address, item.original) for item in caves)
    return _guarded([(item.address, item.replacement) for item in patches], writes)


def native_mouse_patches() -> list[StaticPatch]:
    return [
        StaticPatch("ground rotation-request cave", aim.HOOK_VA, bytes(len(aim.assemble_hook())), aim.assemble_hook()),
        StaticPatch("air reticle late cave", reticle.LATE_HOOK_VA, bytes(len(reticle.assemble_late_hook())), reticle.assemble_late_hook()),
        StaticPatch("air reticle clamp cave", reticle.HOOK_VA, bytes(len(reticle.assemble_hook())), reticle.assemble_hook()),
        StaticPatch("ground rotation-request redirect", aim.CALL_SITE, aim.CALL_ORIGINAL, aim.encode_branch(aim.CALL_SITE, aim.HOOK_VA)),
        StaticPatch("air reticle clamp redirect", reticle.CALL_SITE, reticle.CALL_ORIGINAL, reticle.encode_branch(reticle.CALL_SITE, reticle.HOOK_VA)),
        StaticPatch("air reticle late redirect", reticle.LATE_SITE, reticle.late_original(), reticle.encode_branch(reticle.LATE_SITE, reticle.LATE_HOOK_VA)),
    ]


def _native_mailbox_repair(patches: list[StaticPatch]) -> list[str]:
    redirects = patches[-3:]
    lines: list[str] = []
    for address, image, magic, version in (
        (aim.MAILBOX_VA, aim.mailbox_image(), aim.MAGIC, aim.VERSION),
        (reticle.MAILBOX_VA, reticle.mailbox_image(), reticle.MAGIC, reticle.VERSION),
    ):
        # A fresh process or restored state may have lost the mailbox while the
        # code hooks remain. Reinitialize only when magic or version disagrees,
        # never on every cheat tick (which would erase live mouse input).
        for field_address, field_value in ((address, magic), (address + 4, version)):
            for patch in redirects:
                lines.extend(_equal_words(patch.address, patch.replacement))
            lines.append(f"6{field_address:07X} {field_value:08X}")
            lines.extend(_write_blob(address, image))
            lines.append("D2000000 00000000")
    return lines


def native_enable(patches: list[StaticPatch]) -> list[str]:
    expected = [(patch.address, patch.original) for patch in patches]
    caves, redirects = patches[:-3], patches[-3:]
    # Caves and inert mailboxes must exist before any live call site points at
    # them. This matches the GDB patchers' installation dependency order.
    writes = [(patch.address, patch.replacement) for patch in caves]
    writes.extend([(aim.MAILBOX_VA, aim.mailbox_image()),
                   (reticle.MAILBOX_VA, reticle.mailbox_image())])
    writes.extend((patch.address, patch.replacement) for patch in redirects)
    return _guarded(expected, writes) + _native_mailbox_repair(patches)


def native_restore(patches: list[StaticPatch]) -> list[str]:
    expected = [(patch.address, patch.replacement) for patch in patches]
    # Disconnect all redirects before clearing their executable caves.
    redirects = patches[-3:]
    caves = patches[:-3]
    writes = [(patch.address, patch.original) for patch in redirects + caves]
    writes.extend(
        [
            (aim.MAILBOX_VA, bytes(aim.MAILBOX_SIZE)),
            (reticle.MAILBOX_VA, bytes(reticle.MAILBOX_SIZE)),
        ]
    )
    return _guarded(expected, writes)


def pitch_patches(degrees: float = 60.0) -> list[StaticPatch]:
    radians = math.radians(degrees)
    return [
        StaticPatch("ground pitch upper constant", pitch.UPPER_VA, pitch.STOCK_UPPER, struct.pack("<f", radians)),
        StaticPatch("ground pitch lower constant", pitch.LOWER_VA, pitch.STOCK_LOWER, struct.pack("<f", -radians)),
        StaticPatch("force upper pitch bound", pitch.UPPER_MOVE_VA, pitch.STOCK_UPPER_MOVE, pitch.FORCED_UPPER_MOVE),
        StaticPatch("force lower pitch bound", pitch.LOWER_MOVE_VA, pitch.STOCK_LOWER_MOVE, pitch.FORCED_LOWER_MOVE),
    ]


def verify_image(groups: list[list[StaticPatch]], code_path: Path = CODE_PATH) -> bytes:
    blob = code_path.read_bytes()
    digest = hashlib.sha256(blob).hexdigest()
    if digest != CODE_SHA256:
        raise RuntimeError(f"unexpected code SHA-256: {digest}")
    seen: dict[tuple[int, int], bytes] = {}
    for patches in groups:
        for patch in patches:
            key = (patch.address, len(patch.original))
            prior = seen.setdefault(key, patch.original)
            if prior != patch.original:
                raise RuntimeError(f"inconsistent stock bytes for 0x{patch.address:08x}")
            start = patch.address - CODE_BASE
            actual = blob[start : start + len(patch.original)]
            if actual != patch.original:
                raise RuntimeError(
                    f"{patch.name} stock mismatch at 0x{patch.address:08x}: "
                    f"{actual.hex()} != {patch.original.hex()}"
                )
    return blob


def validate_cheat_text(text: str) -> None:
    sections = 0
    for number, line in enumerate(text.splitlines(), 1):
        if not line or line.startswith("*"):
            continue
        if line.startswith("[") and line.endswith("]"):
            sections += 1
            continue
        if not re.fullmatch(r"[0-9A-F]{8} [0-9A-F]{8}", line):
            raise RuntimeError(f"invalid Gateway line {number}: {line!r}")
    if sections != 12:
        raise RuntimeError(f"expected 12 cheat sections, got {sections}")


def build(code_path: Path | None = None) -> tuple[str, dict[str, object]]:
    fps_caves, fps_entries = fps_patches()
    ws_16 = widescreen_patches(16 / 9)
    ws_21 = widescreen_patches(21 / 9)
    menu_render = menu_render_patches()
    native = native_mouse_patches()
    pitch_60 = pitch_patches(60.0)
    if code_path is not None:
        verify_image(
            [fps_caves + fps_entries, ws_16, ws_21, menu_render, native, pitch_60],
            code_path,
        )

    sections = [
        _section(
            "60 FPS - Enable",
            ["Production patch. Keeps scheduler interval at 0 through its live pointer."],
            fps_enable(fps_caves, fps_entries),
        ),
        _section(
            "60 FPS - Restore 30 FPS",
            ["Disable the Enable entry first, pulse this Restore entry, then disable Restore."],
            fps_restore(fps_caves, fps_entries),
        ),
        _section(
            "Widescreen 16-9 - Enable",
            [
                "Top perspective plus native-routed menu bottom pass; ordinary bottom and orthographic HUD pass through."
            ],
            _guarded(
                [(item.address, item.original) for item in ws_16],
                [(item.address, item.replacement) for item in ws_16],
            ),
        ),
        _section(
            "Widescreen 21-9 - Enable",
            ["Do not enable together with the 16-9 entry; restore before switching."],
            _guarded(
                [(item.address, item.original) for item in ws_21],
                [(item.address, item.replacement) for item in ws_21],
            ),
        ),
        _section(
            "Widescreen - Restore 5-3",
            ["Handles either generated 16-9 or 21-9 variant."],
            _guarded(
                [(item.address, item.replacement) for item in ws_16],
                [(item.address, item.original) for item in reversed(ws_16)],
            )
            + _guarded(
                [(item.address, item.replacement) for item in ws_21],
                [(item.address, item.original) for item in reversed(ws_21)],
            ),
        ),
        _section(
            "Menu Bottom-to-Top Native Route - Enable",
            [
                "Guest render-pass route; no F9 and no framebuffer copy.",
                "EXPERIMENTAL: original 320-wide menu layouts remain and the physical bottom may retain an old frame.",
                "A matching Widescreen entry corrects routed perspective content only.",
            ],
            menu_render_enable(menu_render),
        ),
        _section(
            "Menu Bottom-to-Top Native Route - Restore",
            ["Disable Enable first, pulse this Restore entry, then disable Restore."],
            menu_render_restore(menu_render),
        ),
        _section(
            "Native Mouse Guest Hooks - Enable",
            [
                "Helper only: still run kiu-enhance aim for Raw Input and mailbox updates.",
                "Installs ground rotation and air reticle hooks; it is not mouse input by itself.",
            ],
            native_enable(native),
        ),
        _section(
            "Native Mouse Guest Hooks - Restore",
            ["Stop native_fps_aim.py and disable Enable before pulsing this entry."],
            native_restore(native),
        ),
        _section(
            "Ground Pitch Limit 60deg - Enable EXPERIMENTAL",
            ["Measured useful, but another downstream limiter remains; this is not a final 60-degree guarantee."],
            _guarded(
                [(item.address, item.original) for item in pitch_60],
                [(item.address, item.replacement) for item in pitch_60],
            ),
        ),
        _section(
            "Ground Pitch Limit - Restore",
            ["Restores the four stock words used by the experimental pitch patch."],
            _guarded(
                [(item.address, item.replacement) for item in pitch_60],
                [(item.address, item.original) for item in pitch_60],
            ),
        ),
        _section(
            "All Core Patches - Restore",
            [
                "Emergency stock restore for 60 FPS, either widescreen variant, and native guest hooks.",
                "Also restores the native menu route. Experimental pitch has its own restore entry.",
            ],
            fps_restore(fps_caves, fps_entries)
            + _guarded(
                [(item.address, item.replacement) for item in ws_16],
                [(item.address, item.original) for item in reversed(ws_16)],
            )
            + _guarded(
                [(item.address, item.replacement) for item in ws_21],
                [(item.address, item.original) for item in reversed(ws_21)],
            )
            + menu_render_restore(menu_render)
            + native_restore(native),
        ),
    ]
    text = "\n".join(sections)
    validate_cheat_text(text)

    manifest: dict[str, object] = {
        "schema_version": 1,
        "title_id": TITLE_ID,
        "product_code": PRODUCT_CODE,
        "code_sha256": CODE_SHA256,
        "generated_cheat": f"local/cheats/{TITLE_ID}.txt",
        "features": {
            "60_fps": {
                "status": "production_runtime_user_accepted",
                "standalone_cheat": True,
                "enable": "60 FPS - Enable",
                "restore": "60 FPS - Restore 30 FPS",
                "sites": [item.record() for item in fps_caves + fps_entries]
                + [
                    {
                        "name": "live scheduler interval",
                        "address_expression": "*(0x00d63824)+0x54",
                        "original_hex": "02000000",
                        "replacement_hex": "00000000",
                        "build_sha256": CODE_SHA256,
                    }
                ],
            },
            "widescreen": {
                "status": "production_verified",
                "standalone_cheat": True,
                "variants": {
                    "16:9": [item.record() for item in ws_16],
                    "21:9": [item.record() for item in ws_21],
                },
                "restore": "Widescreen - Restore 5-3",
            },
            "menu_bottom_to_top": {
                "status": "experimental_route_verified_2d_fill_pending",
                "standalone_cheat": True,
                "enable": "Menu Bottom-to-Top Native Route - Enable",
                "restore": "Menu Bottom-to-Top Native Route - Restore",
                "sites": [item.record() for item in menu_render],
                "mailboxes": [f"0x{menu.ACTIVE_MARKER:08x}"],
                "notes": (
                    "Target/viewport routing and battle restore are runtime verified. "
                    "Perspective content can use the matching widescreen variant; original "
                    "320-wide menu layouts remain, and the physical bottom may retain an old frame."
                ),
            },
            "native_mouse_guest_hooks": {
                "status": "preview_helper_full_regression_pending",
                "standalone_cheat": False,
                "requires": "kiu-enhance aim",
                "enable": "Native Mouse Guest Hooks - Enable",
                "restore": "Native Mouse Guest Hooks - Restore",
                "sites": [item.record() for item in native],
                "mailboxes": [f"0x{aim.MAILBOX_VA:08x}", f"0x{reticle.MAILBOX_VA:08x}"],
            },
            "ground_pitch_limit_60": {
                "status": "experimental_downstream_limiter_remains",
                "standalone_cheat": True,
                "enable": "Ground Pitch Limit 60deg - Enable EXPERIMENTAL",
                "restore": "Ground Pitch Limit - Restore",
                "sites": [item.record() for item in pitch_60],
            },
        },
        "excluded": {
            "native_mouse_input": "Raw Input and host process-memory updates cannot be expressed as Gateway codes.",
        },
    }
    return text, manifest


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify-code", type=Path,
                        help="optionally verify against your own extracted code.bin")
    parser.add_argument("--output", type=Path, default=CHEAT_PATH)
    parser.add_argument("--manifest", type=Path, default=MANIFEST_PATH)
    args = parser.parse_args(argv)
    text, manifest = build(args.verify_code)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.manifest.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(text, encoding="utf-8", newline="\n")
    args.manifest.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8", newline="\n")
    print(f"build locked to {CODE_SHA256}")
    if args.verify_code is not None:
        print(f"verified {args.verify_code}")
    print(f"wrote {args.output}")
    print(f"wrote {args.manifest}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
