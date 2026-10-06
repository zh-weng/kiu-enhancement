"""Small, persistent Azahar display preset; no emulator or platform imports.

Desktop Azahar reads display geometry from qt-config.ini. This adapter either
matches Custom upper width or prepares native SingleScreen fullscreen padding.
It never restores settings on Launcher exit. Call save_preset only with user
authorization and while no Azahar instance is running.
"""
from __future__ import annotations

from dataclasses import dataclass
import math
import os
from pathlib import Path
import re
import tempfile

TITLE_ID = "0004000000030100"
STOCK_ASPECT = 5 / 3
GEOMETRY_DEFAULTS = {
    "custom_top_x": 0, "custom_top_y": 0,
    "custom_top_width": 800, "custom_top_height": 480,
    "custom_bottom_x": 80, "custom_bottom_y": 500,
    "custom_bottom_width": 640, "custom_bottom_height": 480,
    "custom_second_layer_opacity": 100,
}
TITLE_DEFAULTS = {"layout_option": 0, "swap_screen": False, "upright_screen": False}
_SECTION = re.compile(r"\s*\[([^\]\r\n]+)\]\s*(?:[;#].*)?\Z")
_SCALAR = re.compile(r'(\s*)("[^"\r\n]*"|[^\s;#]+)(\s*(?:[;#].*)?)\Z')
_ESCAPE = re.compile(r"%([0-9a-fA-F]{2}|U[0-9a-fA-F]{4})")


@dataclass(frozen=True)
class LayoutSpec:
    """Raw framebuffer rectangles: (x, y, width, height), not logical DIP."""
    top: tuple[int, int, int, int]
    bottom: tuple[int, int, int, int]
    aspect: float
    canvas_size: tuple[int, int] | None = None

    def desktop_top(self, render_rect) -> tuple[float, float, int, int] | None:
        """Translate to physical desktop space, refusing a clipped viewport."""
        try:
            left, upper, width, height = render_rect
            x, y, top_width, top_height = self.top
            if self.canvas_size is not None and (width, height) != self.canvas_size:
                return None
            if (any(type(value) not in (int, float) or not math.isfinite(value)
                    for value in (left, upper, width, height))
                    or any(type(value) is not int or value < 0 for value in self.top)
                    or type(self.aspect) not in (int, float) or not math.isfinite(self.aspect)
                    or self.aspect <= 0
                    or width <= 0 or height <= 0 or top_width <= 0 or top_height <= 0
                    or x + top_width > width or y + top_height > height):
                return None
            return left + x, upper + y, top_width, top_height
        except (TypeError, ValueError, OverflowError):
            return None


def _identity(value: str) -> str:
    # QSettings escapes keys and uses backslashes for nested INI keys.
    decoded = _ESCAPE.sub(lambda match: chr(int(match[1].lstrip("U"), 16)), value)
    return decoded.replace("\\", "/").casefold()


class _Ini:
    """Edit scalar Layout entries without reserializing unrelated settings."""
    def __init__(self, raw: bytes):
        self.bom = raw.startswith(b"\xef\xbb\xbf")
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeError as exc:
            raise ValueError("Azahar display settings must be UTF-8 INI") from exc
        if "\0" in text:
            raise ValueError("NUL byte in Azahar display settings")
        self.lines = text.splitlines(keepends=True)
        self.newline = next((self._parts(line)[1] for line in self.lines
                             if self._parts(line)[1]), "\n")
        self.entries = {}
        self.sections = {}
        section, seen = "", set()
        for index, line in enumerate(self.lines):
            content, _ending = self._parts(line)
            stripped = content.strip()
            if not stripped or stripped.startswith((";", "#")):
                continue
            if stripped.startswith("["):
                match = _SECTION.fullmatch(content)
                if match is None:
                    raise ValueError("Malformed INI section in Azahar settings")
                name = match[1].strip()
                section = _identity(name)
                if section in self.sections:
                    raise ValueError(f"Duplicate INI section: {name}")
                if section == "layout" and name != "Layout":
                    raise ValueError("Noncanonical Layout section is not portable")
                self.sections[section] = index
                continue
            if "=" not in content:
                if section == "layout":
                    raise ValueError("Malformed Layout entry in Azahar settings")
                continue
            key, value = content.split("=", 1)
            identity = _identity(key.strip())
            if not identity or (section, identity) in seen:
                raise ValueError(f"Duplicate or empty INI key: {key.strip()}")
            seen.add((section, identity))
            if section in ("", "general") and identity.startswith("layout/"):
                raise ValueError("Layout entries outside their canonical section are unsupported")
            if section == "layout":
                self.entries[identity] = (index, value, key.strip())

    @staticmethod
    def _parts(line: str) -> tuple[str, str]:
        ending = "\r\n" if line.endswith("\r\n") else line[-1:] if line.endswith(("\n", "\r")) else ""
        return line[:-len(ending)] if ending else line, ending

    def scalar(self, key: str) -> str | None:
        entry = self.entries.get(_identity(key))
        if entry is None:
            return None
        raw_key = _ESCAPE.sub(lambda match: chr(int(match[1].lstrip("U"), 16)), entry[2])
        if raw_key.replace("\\", "/") != key.replace("\\", "/"):
            raise ValueError(f"Noncanonical Layout key is not portable: {key}")
        match = _SCALAR.fullmatch(entry[1])
        if match is None:
            raise ValueError(f"Malformed Layout value: {key}")
        value = match[2]
        return value[1:-1] if value.startswith('"') else value

    def flag(self, key: str, default=False) -> bool:
        value = self.scalar(key)
        if value is None:
            return default
        if value.casefold() not in ("true", "false", "0", "1"):
            raise ValueError(f"Malformed Layout boolean: {key}")
        return value.casefold() in ("true", "1")

    def number(self, key: str, default: int, maximum=65535) -> int:
        value = self.scalar(key)
        if value is not None and (not re.fullmatch(r"[+]?[0-9]+", value)
                                  or not 0 <= int(value) <= maximum):
            raise ValueError(f"Malformed Layout integer: {key}")
        use_default = self.flag(key + "\\default")
        return default if value is None or use_default else int(value)

    def edit(self, updates: dict[str, str]) -> bytes:
        missing = []
        for key, value in updates.items():
            entry = self.entries.get(_identity(key))
            if entry is None:
                missing.append(f"{key}={value}{self.newline}")
                continue
            index, raw_value, _key = entry
            match = _SCALAR.fullmatch(raw_value)
            if match is None:
                raise ValueError(f"Malformed Layout value: {key}")
            content, ending = self._parts(self.lines[index])
            prefix = content[:content.index("=") + 1]
            rendered = f'"{value}"' if match[2].startswith('"') else value
            self.lines[index] = prefix + match[1] + rendered + match[3] + ending
        if missing:
            start = self.sections.get("layout")
            if start is None:
                if self.lines and not self._parts(self.lines[-1])[1]:
                    self.lines[-1] += self.newline
                self.lines.extend([f"[Layout]{self.newline}", *missing])
            else:
                end = min((index for index in self.sections.values() if index > start),
                          default=len(self.lines))
                if end and not self._parts(self.lines[end - 1])[1]:
                    self.lines[end - 1] += self.newline
                self.lines[end:end] = missing
        encoded = "".join(self.lines).encode("utf-8")
        return (b"\xef\xbb\xbf" if self.bom else b"") + encoded


def prepare_preset(global_bytes: bytes, title_bytes: bytes,
                   aspect: float | None) -> tuple[bytes, bytes, LayoutSpec]:
    """Prepare checked replacements without writing files or importing an OS adapter."""
    if aspect is not None and type(aspect) not in (int, float):
        raise ValueError("Display aspect must be a finite positive number")
    try:
        selected = float(STOCK_ASPECT if aspect is None else aspect)
        if not math.isfinite(selected) or selected <= 0:
            raise ValueError("Display aspect must be a finite positive number")
    except (TypeError, OverflowError) as exc:
        raise ValueError("Display aspect must be a finite positive number") from exc
    global_ini, title_ini = _Ini(global_bytes), _Ini(title_bytes)
    # Azahar stores stereo settings in Layout, not Renderer. Its split-eye/VR
    # rendering does not match this preset's single raw-pixel upper rectangle.
    global_stereo = global_ini.number("render_3d", 0, maximum=6)
    title_stereo = title_ini.number("render_3d", 0, maximum=6)
    stereo = global_stereo if title_ini.flag("render_3d\\use_global", default=True) else title_stereo
    if stereo != 0:
        raise ValueError("Display preset supports only 3D Off; disable stereoscopic rendering in Azahar first")
    geometry = {key: global_ini.number(key, default,
                    100 if key == "custom_second_layer_opacity" else 65535)
                for key, default in GEOMETRY_DEFAULTS.items()}
    height = geometry["custom_top_height"]
    scaled_width = height * selected
    if height <= 0 or not math.isfinite(scaled_width) or not 0.5 < scaled_width < 65535.5:
        raise ValueError("Selected aspect produces an invalid upper-screen size")
    width = round(scaled_width)
    # Validate the title values before replacing them, including stale default flags.
    for key, default in TITLE_DEFAULTS.items():
        if type(default) is bool:
            title_ini.flag(key, default)
            title_ini.flag(key + "\\default")
        else:
            title_ini.number(key, default, maximum=6)
        title_ini.flag(key + "\\use_global", default=True)
    updates = {"layout_option": "6", "layout_option\\use_global": "false",
               "swap_screen": "false", "swap_screen\\use_global": "false",
               "upright_screen": "false", "upright_screen\\use_global": "false"}
    for key in TITLE_DEFAULTS:
        if title_ini.scalar(key + "\\default") is not None:
            updates[key + "\\default"] = "false"
    spec = LayoutSpec(
        (geometry["custom_top_x"], geometry["custom_top_y"], width, height),
        tuple(geometry["custom_bottom_" + key] for key in ("x", "y", "width", "height")),
        selected,
    )
    return (global_ini.edit({"custom_top_width": str(width), "custom_top_width\\default": "false"}),
            title_ini.edit(updates), spec)


def prepare_single_preset(global_bytes: bytes, title_bytes: bytes,
                          aspect: float | None, display_size: tuple[int, int]):
    """Native SingleScreen with fullscreen pixel padding; preserve Custom geometry."""
    if (len(display_size) != 2 or any(type(v) is not int or not 0 < v <= 65535
                                    for v in display_size)):
        raise ValueError("Invalid fullscreen display size")
    width, height = display_size
    # Reuse the existing strict identity/format/stereo checks, not its Custom edits.
    _global, title, checked = prepare_preset(global_bytes, title_bytes, aspect)
    ratio = checked.aspect
    horizontal = max(0, round((width - height * ratio) / 2))
    vertical = max(0, round((height - width / ratio) / 2))
    top = (horizontal, vertical, width - 2 * horizontal, height - 2 * vertical)
    updates = {"screen_top_stretch": "true", "screen_top_stretch\\default": "false",
               "screen_top_leftright_padding": str(horizontal),
               "screen_top_leftright_padding\\default": "false",
               "screen_top_topbottom_padding": str(vertical),
               "screen_top_topbottom_padding\\default": "false",
               "screen_bottom_stretch": "false", "screen_bottom_stretch\\default": "false"}
    original = _Ini(global_bytes)
    # Validate targeted values before replacing them.
    original.flag("screen_top_stretch")
    original.flag("screen_bottom_stretch")
    for key in ("screen_top_leftright_padding", "screen_top_topbottom_padding"):
        original.number(key, 0)
    single_title = _Ini(title).edit({"layout_option": "1"})
    return original.edit(updates), single_title, LayoutSpec(top, (0, 0, 0, 0), ratio, display_size)


def _read_optional(path: Path) -> bytes | None:
    if path.is_symlink():
        raise ValueError("Symlinked emulator configuration is not supported")
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _atomic_replace(path: Path, content: bytes) -> None:
    pending = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=".kiu-display-", delete=False) as output:
            pending = Path(output.name)
            output.write(content)
        os.replace(pending, path)
    finally:
        if pending is not None:
            pending.unlink(missing_ok=True)


def save_preset(config_dir: Path, aspect: float | None, *, display_size=None) -> LayoutSpec:
    """Persist only this preset's fields; compare bytes and safely roll back write failures.

    The caller must ensure no Azahar process is running. A successfully saved
    preset is persistent, not a Launcher-exit transaction. No backup copies or
    emulator data are created. Detected concurrent edits are not rolled back.
    """
    root = Path(config_dir).resolve()
    paths = (root / "qt-config.ini", root / "custom" / f"{TITLE_ID}.ini")
    if paths[1].parent.is_symlink() or paths[1].parent.resolve() != paths[1].parent:
        raise ValueError("Symlinked emulator configuration is not supported")
    original = tuple(_read_optional(path) for path in paths)
    if display_size is None:
        global_bytes, title_bytes, spec = prepare_preset(original[0] or b"", original[1] or b"", aspect)
    else:
        global_bytes, title_bytes, spec = prepare_single_preset(
            original[0] or b"", original[1] or b"", aspect, display_size)
    replacements = (global_bytes, title_bytes)
    expected = list(original)
    attempted = []
    try:
        for index, path in enumerate(paths):
            if any(_read_optional(target) != previous for target, previous in zip(paths, expected)):
                raise RuntimeError("Azahar display settings changed while preparing the preset")
            if original[index] == replacements[index]:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            attempted.append(index)
            _atomic_replace(path, replacements[index])
            expected[index] = replacements[index]
        if any(_read_optional(path) != content for path, content in zip(paths, replacements)):
            raise RuntimeError("Azahar display settings changed while saving the preset")
    except BaseException as error:
        rollback_errors = []
        for index in reversed(attempted):
            path = paths[index]
            try:
                if _read_optional(path) == replacements[index]:
                    if original[index] is None:
                        path.unlink()
                    else:
                        _atomic_replace(path, original[index])
            except Exception as rollback_error:
                rollback_errors.append(str(rollback_error))
        if rollback_errors:
            raise RuntimeError("Display preset save failed; rollback was incomplete: "
                               + "; ".join(rollback_errors)) from error
        raise
    return spec
