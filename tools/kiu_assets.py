"""Parsers and decoders for Kid Icarus: Uprising asset containers.

This module intentionally has no image-library dependency.  It handles the
Nintendo LZ11/DARC/CGFX layers and returns ordinary RGBA bytes; the command
line extractor is responsible for writing PNG files.
"""

from __future__ import annotations

from dataclasses import dataclass
import struct

try:  # Running as ``python -m tools...``.
    from .lz11 import decompress as lz11_decompress
except ImportError:  # Running the script directly from the tools directory.
    from lz11 import decompress as lz11_decompress


class AssetFormatError(ValueError):
    """Raised when an asset container is structurally invalid."""


TEXTURE_FORMAT_NAMES = {
    0: "RGBA8",
    1: "RGB8",
    2: "RGBA5551",
    3: "RGB565",
    4: "RGBA4",
    5: "LA8",
    6: "HILO8",
    7: "L8",
    8: "A8",
    9: "LA4",
    10: "L4",
    11: "A4",
    12: "ETC1",
    13: "ETC1A4",
}


@dataclass(frozen=True)
class DarcEntry:
    """One file entry from a DARC archive."""

    path: str
    offset: int
    size: int
    data: bytes


@dataclass(frozen=True)
class CgfxTexture:
    """A TXOB texture and its metadata inside a CGFX container."""

    index: int
    name: str
    width: int
    height: int
    format: int
    mipmap_levels: int
    data_offset: int
    data_size: int
    data: bytes

    @property
    def format_name(self) -> str:
        return TEXTURE_FORMAT_NAMES.get(self.format, f"UNKNOWN_{self.format}")


def _u16(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 2 > len(data):
        raise AssetFormatError(f"u16 outside buffer at 0x{offset:x}")
    return struct.unpack_from("<H", data, offset)[0]


def _u32(data: bytes, offset: int) -> int:
    if offset < 0 or offset + 4 > len(data):
        raise AssetFormatError(f"u32 outside buffer at 0x{offset:x}")
    return struct.unpack_from("<I", data, offset)[0]


def _read_ascii(data: bytes, offset: int) -> str:
    if offset < 0 or offset >= len(data):
        raise AssetFormatError(f"string outside buffer at 0x{offset:x}")
    end = data.find(b"\0", offset)
    if end < 0:
        raise AssetFormatError(f"unterminated string at 0x{offset:x}")
    return data[offset:end].decode("ascii", errors="replace")


def _read_utf16le(data: bytes, offset: int, limit: int) -> str:
    if offset < 0 or offset >= limit or limit > len(data):
        raise AssetFormatError(f"UTF-16 string outside table at 0x{offset:x}")
    end = offset
    while end + 1 < limit:
        if data[end] == 0 and data[end + 1] == 0:
            return data[offset:end].decode("utf-16le", errors="replace")
        end += 2
    raise AssetFormatError(f"unterminated UTF-16 string at 0x{offset:x}")


def iter_darc(data: bytes):
    """Yield entries from a little-endian DARC archive.

    DARC folder nodes store the exclusive end index of their pre-order
    subtree.  That makes recursive traversal possible without relying on
    filenames or a separate directory table.
    """

    if len(data) < 0x1C or data[:4] != b"darc":
        raise AssetFormatError("missing DARC signature")
    if _u16(data, 4) != 0xFEFF:
        raise AssetFormatError("unsupported DARC byte order")

    header_size = _u16(data, 6)
    file_length = _u32(data, 0x0C)
    table_offset = _u32(data, 0x10)
    table_length = _u32(data, 0x14)
    data_offset = _u32(data, 0x18)
    if header_size < 0x1C or table_offset < header_size:
        raise AssetFormatError("invalid DARC header")
    table_end = table_offset + table_length
    if table_end > len(data):
        raise AssetFormatError("DARC table outside buffer")
    if file_length and file_length > len(data):
        raise AssetFormatError("DARC declared length exceeds buffer")

    node_count_pos = table_offset + 8
    node_count = _u32(data, node_count_pos)
    if node_count == 0:
        return
    nodes_end = table_offset + node_count * 12
    if nodes_end > table_end:
        raise AssetFormatError("DARC node table exceeds table length")
    if node_count > 1_000_000:
        raise AssetFormatError("unreasonable DARC node count")

    nodes = [
        (
            _u32(data, table_offset + index * 12),
            _u32(data, table_offset + index * 12 + 4),
            _u32(data, table_offset + index * 12 + 8),
        )
        for index in range(node_count)
    ]
    name_base = nodes_end

    def node_name(node):
        name_offset = node[0] & 0x00FFFFFF
        return _read_utf16le(data, name_base + name_offset, table_end)

    def walk(start: int, end: int, prefix: tuple[str, ...]):
        index = start
        while index < end:
            name_field, offset_or_zero, size_or_end = nodes[index]
            name = node_name(nodes[index])
            is_folder = bool(name_field & 0x01000000)
            if is_folder:
                subtree_end = size_or_end
                if subtree_end <= index or subtree_end > end or subtree_end > node_count:
                    raise AssetFormatError(
                        f"invalid DARC folder range {index}:{subtree_end}"
                    )
                child_prefix = prefix if not name else prefix + (name,)
                yield from walk(index + 1, subtree_end, child_prefix)
                index = subtree_end
                continue

            path_parts = prefix + (name,)
            # DARC's file-entry offset is an absolute offset from the start
            # of the archive.  ``data_offset`` marks the beginning of the
            # payload region but is not an additional base to add (the first
            # file normally has offset == data_offset).
            payload_start = offset_or_zero
            payload_end = payload_start + size_or_end
            # NintendoWare emits empty archives whose aligned data_offset is
            # just beyond the truncated file.  That is valid when the entry
            # has no payload; non-empty files still need to lie in the data
            # region and inside the buffer.
            if size_or_end and (payload_start < data_offset or payload_end > len(data)):
                raise AssetFormatError(
                    f"DARC file outside data region: {'/'.join(path_parts)}"
                )
            if not size_or_end:
                payload_start = min(payload_start, len(data))
                payload_end = payload_start
            yield DarcEntry(
                path="/".join(path_parts),
                offset=offset_or_zero,
                size=size_or_end,
                data=data[payload_start:payload_end],
            )
            index += 1

    root_name_field, _, root_end = nodes[0]
    if not (root_name_field & 0x01000000):
        raise AssetFormatError("DARC root node is not a folder")
    if root_end == 0:
        root_end = node_count
    if root_end > node_count:
        raise AssetFormatError("DARC root folder exceeds node table")
    yield from walk(1, root_end, ())


def unwrap_lz11_darc(data: bytes) -> bytes:
    """Return a DARC payload from either raw DARC or LZ11-wrapped DARC."""

    if data[:4] == b"darc":
        return data
    if data[:1] == b"\x11":
        unpacked = lz11_decompress(data)
        if unpacked[:4] == b"darc":
            return unpacked
    raise AssetFormatError("resource is neither DARC nor LZ11-wrapped DARC")


def iter_cgfx_textures(data: bytes):
    """Yield TXOB textures from a CGFX/BCRES/BCTEX container."""

    if len(data) < 0x14 or data[:4] != b"CGFX":
        raise AssetFormatError("missing CGFX signature")
    if _u16(data, 4) != 0xFEFF:
        raise AssetFormatError("unsupported CGFX byte order")

    header_size = _u16(data, 6)
    data_base = header_size
    if data_base + 4 > len(data) or data[data_base:data_base + 4] != b"DATA":
        # Some tools place padding before the first block.  Search only a
        # small header-adjacent region so a random marker in image data cannot
        # be mistaken for the DATA block.
        data_base = data.find(b"DATA", header_size, min(len(data), header_size + 0x100))
        if data_base < 0:
            raise AssetFormatError("CGFX DATA block not found")

    texture_count_pos = data_base + 0x10  # dictionary 1 entry count
    texture_offset_pos = data_base + 0x14
    texture_count = _u32(data, texture_count_pos)
    texture_offset = _u32(data, texture_offset_pos)
    if texture_count == 0:
        return
    dict_base = texture_offset_pos + texture_offset
    if data[dict_base:dict_base + 4] != b"DICT":
        raise AssetFormatError("CGFX texture DICT not found")
    dict_count = _u32(data, dict_base + 8)
    if dict_count > texture_count:
        # The count in DATA is authoritative for the type, while DICT is the
        # actual number of entries in a few NintendoWare revisions.
        dict_count = texture_count
    entries_base = dict_base + 0x1C
    if entries_base + dict_count * 0x10 > len(data):
        raise AssetFormatError("CGFX texture DICT exceeds buffer")

    for index in range(dict_count):
        entry = entries_base + index * 0x10
        name_field = entry + 8
        object_field = entry + 0x0C
        name_offset = name_field + _u32(data, name_field)
        object_base = object_field + _u32(data, object_field)
        if object_base + 0x4C > len(data):
            raise AssetFormatError(f"CGFX TXOB {index} exceeds buffer")
        if data[object_base + 4:object_base + 8] != b"TXOB":
            # Dictionary 1 is normally textures, but malformed or mixed
            # resources exist.  Leave those entries for the inventory to
            # report rather than interpreting unrelated fields as pixels.
            continue

        name = _read_ascii(data, name_offset)
        if not name:
            name = f"texture_{index:04d}"
        height = _u32(data, object_base + 0x18)
        width = _u32(data, object_base + 0x1C)
        mipmap_levels = _u32(data, object_base + 0x28)
        texture_format = _u32(data, object_base + 0x34)
        texture_size = _u32(data, object_base + 0x44)
        texture_field = object_base + 0x48
        texture_offset = texture_field + _u32(data, texture_field)
        texture_end = texture_offset + texture_size
        if width == 0 or height == 0:
            raise AssetFormatError(f"CGFX TXOB {index} has zero dimensions")
        if texture_end > len(data):
            raise AssetFormatError(f"CGFX TXOB {index} pixel data exceeds buffer")
        yield CgfxTexture(
            index=index,
            name=name,
            width=width,
            height=height,
            format=texture_format,
            mipmap_levels=mipmap_levels,
            data_offset=texture_offset,
            data_size=texture_size,
            data=data[texture_offset:texture_end],
        )


TILE_ORDER = (
    0, 1, 8, 9, 2, 3, 10, 11,
    16, 17, 24, 25, 18, 19, 26, 27,
    4, 5, 12, 13, 6, 7, 14, 15,
    20, 21, 28, 29, 22, 23, 30, 31,
    32, 33, 40, 41, 34, 35, 42, 43,
    48, 49, 56, 57, 50, 51, 58, 59,
    36, 37, 44, 45, 38, 39, 46, 47,
    52, 53, 60, 61, 54, 55, 62, 63,
)

ETC1_MODULATION_TABLE = (
    (2, 8, -2, -8),
    (5, 17, -5, -17),
    (9, 29, -9, -29),
    (13, 42, -13, -42),
    (18, 60, -18, -60),
    (24, 80, -24, -80),
    (33, 106, -33, -106),
    (47, 183, -47, -183),
)


def _clip8(value: int) -> int:
    return max(0, min(255, value))


def _sign_extend(value: int, bits: int) -> int:
    sign = 1 << (bits - 1)
    return value - (1 << bits) if value & sign else value


def _etc1_block(block: bytes):
    if len(block) != 8:
        raise AssetFormatError("ETC1 block must be 8 bytes")
    reversed_block = block[::-1]
    top = int.from_bytes(reversed_block[:4], "little")
    bottom = int.from_bytes(reversed_block[4:], "little")
    flip = bool(top & 0x01000000)
    difference = bool(top & 0x02000000)

    if difference:
        r1 = top & 0xF8
        g1 = (top & 0xF800) >> 8
        b1 = (top & 0xF80000) >> 16
        r2 = (r1 >> 3) + _sign_extend(top & 0x07, 3)
        g2 = (g1 >> 3) + _sign_extend((top >> 8) & 0x07, 3)
        b2 = (b1 >> 3) + _sign_extend((top >> 16) & 0x07, 3)
        r1 += r1 >> 5
        g1 += g1 >> 5
        b1 += b1 >> 5
        r2 = (r2 << 3) + (r2 >> 2)
        g2 = (g2 << 3) + (g2 >> 2)
        b2 = (b2 << 3) + (b2 >> 2)
    else:
        r1 = (top & 0xF0) + ((top & 0xF0) >> 4)
        g1 = ((top & 0xF000) >> 8) + ((top & 0xF000) >> 12)
        b1 = ((top & 0xF00000) >> 16) + ((top & 0xF00000) >> 20)
        r2 = ((top & 0x0F) << 4) + (top & 0x0F)
        g2 = ((top & 0x0F00) >> 4) + ((top & 0x0F00) >> 8)
        b2 = ((top & 0x0F0000) >> 12) + ((top & 0x0F0000) >> 16)

    table_1 = (top >> 29) & 7
    table_2 = (top >> 26) & 7

    def pixel(red: int, green: int, blue: int, x: int, y: int, table: int):
        index = x * 4 + y
        msb = bottom << 1
        if index < 8:
            modulation_index = ((bottom >> (index + 24)) & 1) + (
                (msb >> (index + 8)) & 2
            )
        else:
            modulation_index = ((bottom >> (index + 8)) & 1) + (
                (msb >> (index - 8)) & 2
            )
        modulation = ETC1_MODULATION_TABLE[table][modulation_index]
        return (
            _clip8(red + modulation),
            _clip8(green + modulation),
            _clip8(blue + modulation),
        )

    pixels = [(0, 0, 0)] * 16
    if not flip:
        for y in range(4):
            for x in range(2):
                pixels[y * 4 + x] = pixel(r1, g1, b1, x, y, table_1)
                pixels[y * 4 + x + 2] = pixel(r2, g2, b2, x + 2, y, table_2)
    else:
        for y in range(2):
            for x in range(4):
                pixels[y * 4 + x] = pixel(r1, g1, b1, x, y, table_1)
                pixels[(y + 2) * 4 + x] = pixel(r2, g2, b2, x, y + 2, table_2)
    return pixels


def _etc1_scramble(width: int, height: int):
    columns = (width + 3) // 4
    rows = (height + 3) // 4
    scramble = []
    base_accumulator = 0
    line_accumulator = 0
    base_number = 0
    line_number = 0
    for tile in range(columns * rows):
        if tile % columns == 0 and tile > 0:
            if line_accumulator < 1:
                line_accumulator += 1
                line_number += 2
                base_number = line_number
            else:
                line_accumulator = 0
                base_number -= 2
                line_number = base_number
        scramble.append(base_number)
        if base_accumulator < 1:
            base_accumulator += 1
            base_number += 1
        else:
            base_accumulator = 0
            base_number += 3
    return scramble


def _decode_etc1(data: bytes, width: int, height: int, with_alpha: bool, flip_y: bool):
    columns = (width + 3) // 4
    rows = (height + 3) // 4
    block_size = 16 if with_alpha else 8
    expected = columns * rows * block_size
    if len(data) < expected:
        raise AssetFormatError(
            f"ETC1 data too short: {len(data)} < {expected} for {width}x{height}"
        )

    # First decode blocks in their stored order.  The 3DS then applies a
    # separate tile permutation, handled below.
    physical = [(0, 0, 0, 255)] * (width * height)
    cursor = 0
    for block_y in range(rows):
        for block_x in range(columns):
            if with_alpha:
                alpha_data = data[cursor:cursor + 8]
                color_data = data[cursor + 8:cursor + 16]
            else:
                alpha_data = b""
                color_data = data[cursor:cursor + 8]
            cursor += block_size
            colors = _etc1_block(color_data)
            for x in range(4):
                for y in range(4):
                    out_x = block_x * 4 + x
                    out_y = block_y * 4 + y
                    if out_x >= width or out_y >= height:
                        continue
                    index = x * 4 + y
                    alpha = 255
                    if with_alpha:
                        alpha_nibble = alpha_data[index // 2]
                        alpha_nibble = (
                            alpha_nibble & 0x0F
                            if index % 2 == 0
                            else alpha_nibble >> 4
                        )
                        alpha = alpha_nibble * 0x11
                    red, green, blue = colors[y * 4 + x]
                    physical[out_y * width + out_x] = (red, green, blue, alpha)

    output = bytearray(width * height * 4)
    for output_tile, source_tile in enumerate(_etc1_scramble(width, height)):
        output_tile_x = output_tile % columns
        output_tile_y = output_tile // columns
        source_tile_x = source_tile % columns
        source_tile_y = source_tile // columns
        for x in range(4):
            for y in range(4):
                output_x = output_tile_x * 4 + x
                output_y = output_tile_y * 4 + y
                source_x = source_tile_x * 4 + x
                source_y = source_tile_y * 4 + y
                if output_x >= width or output_y >= height:
                    continue
                if source_x >= width or source_y >= height:
                    continue
                if flip_y:
                    output_y = height - 1 - output_y
                pixel_offset = (output_y * width + output_x) * 4
                output[pixel_offset:pixel_offset + 4] = bytes(
                    physical[source_y * width + source_x]
                )
    return bytes(output)


def _put_pixel(output: bytearray, width: int, height: int, x: int, y: int,
               pixel: tuple[int, int, int, int], flip_y: bool) -> None:
    if x >= width or y >= height:
        return
    if flip_y:
        y = height - 1 - y
    offset = (y * width + x) * 4
    output[offset:offset + 4] = bytes(pixel)


def decode_pica_texture(
    data: bytes,
    width: int,
    height: int,
    texture_format: int,
    *,
    flip_y: bool = False,
) -> bytes:
    """Decode one PICA200 texture into tightly packed logical RGBA bytes.

    The default orientation is the human-readable orientation used by the
    texture export path.  ``flip_y=True`` is available when matching a GPU
    upload/debug buffer is more useful.
    """

    if width <= 0 or height <= 0:
        raise AssetFormatError("texture dimensions must be positive")
    if texture_format in (12, 13):
        return _decode_etc1(data, width, height, texture_format == 13, flip_y)

    bytes_per_pixel = {
        0: 4,
        1: 3,
        2: 2,
        3: 2,
        4: 2,
        5: 2,
        6: 2,
        7: 1,
        8: 1,
        9: 1,
        10: 0,
        11: 0,
    }.get(texture_format)
    if bytes_per_pixel is None:
        raise AssetFormatError(f"unsupported PICA texture format {texture_format}")

    tile_columns = (width + 7) // 8
    tile_rows = (height + 7) // 8
    output = bytearray(width * height * 4)
    cursor = 0
    nibble_cursor = 0

    def read_bytes(length: int) -> bytes:
        nonlocal cursor
        end = cursor + length
        if end > len(data):
            raise AssetFormatError(
                f"texture data too short at {cursor}: need {length}, have {len(data)}"
            )
        chunk = data[cursor:end]
        cursor = end
        return chunk

    def read_nibble() -> int:
        nonlocal nibble_cursor
        byte_offset = nibble_cursor // 2
        if byte_offset >= len(data):
            raise AssetFormatError("texture nibble data too short")
        value = data[byte_offset]
        nibble_cursor += 1
        return (value >> 4) & 0x0F if nibble_cursor % 2 == 1 else value & 0x0F

    for tile_y in range(tile_rows):
        for tile_x in range(tile_columns):
            for order in TILE_ORDER:
                x = order % 8
                y = order // 8
                if texture_format in (10, 11):
                    nibble = read_nibble()
                    if texture_format == 10:
                        pixel = (nibble * 0x11, nibble * 0x11, nibble * 0x11, 255)
                    else:
                        pixel = (255, 255, 255, nibble * 0x11)
                else:
                    raw = read_bytes(bytes_per_pixel)
                    if texture_format == 0:  # A8B8G8R8 in memory order.
                        pixel = (raw[3], raw[2], raw[1], raw[0])
                    elif texture_format == 1:  # B8G8R8.
                        pixel = (raw[2], raw[1], raw[0], 255)
                    elif texture_format == 2:  # R5G5B5A1.
                        value = int.from_bytes(raw, "little")
                        pixel = (
                            ((value >> 11) & 0x1F) * 8,
                            ((value >> 6) & 0x1F) * 8,
                            ((value >> 1) & 0x1F) * 8,
                            (value & 1) * 255,
                        )
                    elif texture_format == 3:  # R5G6B5.
                        value = int.from_bytes(raw, "little")
                        pixel = (
                            ((value >> 11) & 0x1F) * 8,
                            ((value >> 5) & 0x3F) * 4,
                            (value & 0x1F) * 8,
                            255,
                        )
                    elif texture_format == 4:  # R4G4B4A4.
                        value = int.from_bytes(raw, "little")
                        pixel = (
                            ((value >> 12) & 0x0F) * 0x11,
                            ((value >> 8) & 0x0F) * 0x11,
                            ((value >> 4) & 0x0F) * 0x11,
                            (value & 0x0F) * 0x11,
                        )
                    elif texture_format == 5:  # A8L8.
                        pixel = (raw[1], raw[1], raw[1], raw[0])
                    elif texture_format == 6:  # HILO8; retain both channels.
                        pixel = (raw[1], raw[0], 255, 255)
                    elif texture_format == 7:  # L8.
                        pixel = (raw[0], raw[0], raw[0], 255)
                    elif texture_format == 8:  # A8.
                        pixel = (255, 255, 255, raw[0])
                    elif texture_format == 9:  # L4A4 (low nibble luminance).
                        pixel = (
                            (raw[0] & 0x0F) * 0x11,
                            (raw[0] & 0x0F) * 0x11,
                            (raw[0] & 0x0F) * 0x11,
                            (raw[0] >> 4) * 0x11,
                        )
                    else:
                        raise AssetFormatError(
                            f"unsupported PICA texture format {texture_format}"
                        )
                _put_pixel(output, width, height, tile_x * 8 + x, tile_y * 8 + y, pixel, flip_y)

    return bytes(output)
