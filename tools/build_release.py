"""Build a deterministic preview source ZIP from an explicit source allowlist.

This includes working-tree changes without requiring a Git commit.  ROMs,
extracted game files, saves, personal configuration and research archives are
never traversed.  Cheat code is generated from the current patch definitions;
it contains patch instructions, not a game image.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import zipfile


ROOT = Path(__file__).resolve().parents[1]
TITLE_ID = "0004000000030100"
DEFAULT_OUTPUT = ROOT / "local/releases/kiu-enhancements-preview-source.zip"
ROOT_FILES = (
    "README.md", "STATUS.md", "AGENTS.md", "LICENSE", "pyproject.toml", ".gitignore", ".gitattributes", "run_kiu.py",
)
DOC_FILES = (
    "docs/ARCHITECTURE.md", "docs/GATEWAY_CHEATS.md",
    "docs/DEVELOPMENT.md", "docs/KNOWLEDGE.md",
)
MANIFEST_FILES = (
    "manifests/title.json", "manifests/gateway_cheats.json", "manifests/aim_protocol.json",
)
PACKAGE_FILES = (
    "src/kiu/__init__.py", "src/kiu/__main__.py", "src/kiu/cli.py",
    "src/kiu/aim_core.py", "src/kiu/aim_protocol.py", "src/kiu/export_cheats.py",
    "src/kiu/gdbclient.py",
    "src/kiu/patches/__init__.py", "src/kiu/patches/patch_60fps.py",
    "src/kiu/patches/patch_widescreen.py", "src/kiu/patches/patch_aim_request.py",
    "src/kiu/patches/pin_reticle.py",
    "src/kiu/backends/__init__.py", "src/kiu/backends/windows/__init__.py",
    "src/kiu/backends/windows/native_fps_aim.py", "src/kiu/backends/windows/guest_memory.py",
    "src/kiu/backends/windows/win_raw_mouse.py", "src/kiu/backends/windows/win_window.py",
    "src/kiu/experimental/__init__.py", "src/kiu/experimental/menu_render_pass.py",
    "src/kiu/experimental/patch_pitch_limit.py",
)
DEV_NAMES = (
    "__init__", "armscan", "extract_kiu_textures", "find_arm_mem_refs",
    "inspect_arm_static", "kiu_assets", "lz11", "prepare_kiu",
)
DEV_FILES = (
    "tools/__init__.py", "tools/build_release.py",
    *(f"tools/dev/{name}.py" for name in DEV_NAMES),
)
REQUIRED_FILES = (
    *ROOT_FILES, *DOC_FILES, *MANIFEST_FILES, *PACKAGE_FILES, *DEV_FILES,
    ".github/workflows/ci.yml",
)


class ReleaseError(ValueError):
    """The source tree does not satisfy the release allowlist."""


def generate_cheats(root: Path) -> bytes:
    """Run the exporter without a ROM and without changing source metadata."""
    with tempfile.TemporaryDirectory(prefix="kiu-release-cheats-") as directory:
        output = Path(directory) / f"{TITLE_ID}.txt"
        result = subprocess.run(
            [sys.executable, str(root / "run_kiu.py"), "export-cheats", "--",
             "--output", str(output), "--manifest", str(Path(directory) / "manifest.json")],
            cwd=root, capture_output=True, text=True,
        )
        if result.returncode:
            raise ReleaseError(
                "cheat generation failed; install the project dependencies first:\n"
                + (result.stderr.strip() or result.stdout.strip())
            )
        if not output.is_file():
            raise ReleaseError("cheat exporter did not produce the expected title file")
        generated = json.loads((Path(directory) / "manifest.json").read_text(encoding="utf-8"))
        canonical = json.loads((root / "manifests/gateway_cheats.json").read_text(encoding="utf-8"))
        if generated != canonical:
            raise ReleaseError("manifests/gateway_cheats.json is stale; regenerate it explicitly before release")
        return output.read_bytes()


def _read_source(root: Path, relative: str) -> bytes:
    path = root / relative
    try:
        path.resolve().relative_to(root)
    except ValueError as exc:
        raise ReleaseError(f"source path leaves the repository: {relative}") from exc
    if path.is_symlink():
        raise ReleaseError(f"source symlink is not allowed: {relative}")
    data = path.read_bytes()
    try:
        data.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ReleaseError(f"source is not UTF-8 text: {relative}") from exc
    if b"\0" in data:
        raise ReleaseError(f"source contains binary data: {relative}")
    return data


def release_files(root: Path = ROOT, *, cheat_generator=None) -> dict[str, bytes]:
    root = Path(root).resolve()
    missing = [name for name in REQUIRED_FILES if not (root / name).is_file()]
    if missing:
        raise ReleaseError("missing required source files:\n" + "\n".join(missing))
    tests = sorted(path for path in (root / "tests").glob("test_*.py") if path.is_file())
    if not tests:
        raise ReleaseError("missing required tests: tests/test_*.py")
    names = [*REQUIRED_FILES, *(path.relative_to(root).as_posix() for path in tests)]
    files = {name: _read_source(root, name) for name in sorted(names)}
    cheats = (cheat_generator or generate_cheats)(root)
    if not cheats.strip() or b"\0" in cheats:
        raise ReleaseError("generated cheats must be nonempty text")
    try:
        cheats.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ReleaseError("generated cheats are not UTF-8 text") from exc
    files[f"cheats/{TITLE_ID}.txt"] = cheats
    files["FILES.txt"] = (
        "\n".join(sorted([*files, "FILES.txt", "SHA256SUMS"])) + "\n"
    ).encode("utf-8")
    files["SHA256SUMS"] = "".join(
        f"{hashlib.sha256(data).hexdigest()}  {name}\n"
        for name, data in sorted(files.items())
    ).encode("utf-8")
    return files


def write_release(files: dict[str, bytes], output: Path, *, root: Path = ROOT) -> None:
    output = Path(output).resolve()
    root = Path(root).resolve()
    if output.suffix.lower() != ".zip":
        raise ReleaseError("release output must be a .zip file")
    try:
        relative = output.relative_to(root)
    except ValueError:
        pass  # An explicit output outside the repository is supported.
    else:
        if not relative.parts or relative.parts[0] != "local":
            raise ReleaseError("generated output inside the repository must be under local/")
    output.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(prefix=".kiu-release-", suffix=".zip",
                                     dir=output.parent, delete=False) as handle:
        pending = Path(handle.name)
    try:
        # Stored entries avoid differences between platform zlib versions.
        with zipfile.ZipFile(pending, "w", compression=zipfile.ZIP_STORED) as archive:
            for name, data in sorted(files.items()):
                info = zipfile.ZipInfo(name, date_time=(1980, 1, 1, 0, 0, 0))
                info.create_system = 3
                info.external_attr = 0o100644 << 16
                archive.writestr(info, data)
        pending.replace(output)
    finally:
        if pending.exists():
            pending.unlink()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--check", action="store_true",
                        help="validate the allowlist and generated cheats without writing a ZIP")
    args = parser.parse_args(argv)
    try:
        files = release_files()
        if args.check:
            print(f"Release check passed: {len(files)} text files, {sum(map(len, files.values()))} bytes")
        else:
            write_release(files, args.output)
            digest = hashlib.sha256(args.output.read_bytes()).hexdigest()
            print(f"Wrote {args.output}\nSHA-256: {digest}\nFiles: {len(files)}")
    except (ReleaseError, OSError) as exc:
        print(f"Release build failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
