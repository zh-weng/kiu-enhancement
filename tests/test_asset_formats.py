import struct
import unittest

from tools.dev.kiu_assets import decode_pica_texture, iter_cgfx_textures, iter_darc
from tools.dev.lz11 import decompress


def _literal_lz11(payload: bytes) -> bytes:
    encoded = bytearray((0x11, len(payload) & 0xFF, (len(payload) >> 8) & 0xFF, (len(payload) >> 16) & 0xFF))
    for offset in range(0, len(payload), 8):
        chunk = payload[offset:offset + 8]
        encoded.append(0)
        encoded.extend(chunk)
    return bytes(encoded)


def _make_darc() -> bytes:
    table_offset = 0x1C
    node_count = 3
    node_bytes = bytearray()
    # Root folder, child folder, and one file.  Folder values are exclusive
    # end indices in the DARC pre-order node table.
    node_bytes.extend(struct.pack("<3I", 0x01000000, 0, 3))
    node_bytes.extend(struct.pack("<3I", 0x01000002, 0, 3))
    node_bytes.extend(struct.pack("<3I", 0x00000010, 0x80, 5))
    names = "\0folder\0hello.bin\0".encode("utf-16le")
    table_length = node_count * 12 + len(names)
    data = bytearray(0x80 + 5)
    data[0x80:0x85] = b"hello"
    header = struct.pack(
        "<4sHHIIIII",
        b"darc",
        0xFEFF,
        0x1C,
        0x01000000,
        len(data),
        table_offset,
        table_length,
        0x80,
    )
    data[:0x1C] = header
    data[table_offset:table_offset + len(node_bytes)] = node_bytes
    data[table_offset + len(node_bytes):table_offset + len(node_bytes) + len(names)] = names
    return bytes(data)


def _make_cgfx_l8() -> bytes:
    # One 8x8 L8 texture.  The bytes are already in the 3DS tile order and
    # contain the pixel value at each logical coordinate.
    tile_order = (
        0, 1, 8, 9, 2, 3, 10, 11,
        16, 17, 24, 25, 18, 19, 26, 27,
        4, 5, 12, 13, 6, 7, 14, 15,
        20, 21, 28, 29, 22, 23, 30, 31,
        32, 33, 40, 41, 34, 35, 42, 43,
        48, 49, 56, 57, 50, 51, 58, 59,
        36, 37, 44, 45, 38, 39, 46, 47,
        52, 53, 60, 61, 54, 55, 62, 63,
    )
    pixels = bytes((x + y * 8 for y in range(8) for x in range(8)))
    image = bytes(pixels[index] for index in tile_order)

    data_base = 0x14
    dict_base = 0x9C
    entry_base = dict_base + 0x1C
    txob_base = 0xC8
    image_base = 0x180
    output = bytearray(image_base + len(image))
    output[:0x14] = struct.pack("<4sHHIII", b"CGFX", 0xFEFF, 0x14, 0x05000000, len(output), 2)
    output[data_base:data_base + 8] = b"DATA" + struct.pack("<I", 0x164)
    # DATA dictionary 1: count 1 and a self-relative pointer at 0x28.
    struct.pack_into("<II", output, data_base + 0x10, 1, dict_base - (data_base + 0x14))
    output[dict_base:dict_base + 0x1C] = b"DICT" + struct.pack("<I", 0x2C) + struct.pack("<I", 1) + b"\0" * 0x10
    struct.pack_into("<4I", output, entry_base, 0x75, 0x10000, 0x64, 0x04)
    output[0x124:0x124 + 8] = b"test_l8\0"
    struct.pack_into("<I", output, txob_base, 0x20000011)
    output[txob_base + 4:txob_base + 8] = b"TXOB"
    struct.pack_into("<I", output, txob_base + 0x18, 8)
    struct.pack_into("<I", output, txob_base + 0x1C, 8)
    struct.pack_into("<I", output, txob_base + 0x34, 7)
    struct.pack_into("<I", output, txob_base + 0x44, len(image))
    struct.pack_into("<I", output, txob_base + 0x48, image_base - (txob_base + 0x48))
    output[image_base:image_base + len(image)] = image
    return bytes(output)


class AssetFormatTests(unittest.TestCase):
    def test_lz11_literal_stream_round_trips(self):
        payload = bytes(range(32))
        self.assertEqual(decompress(_literal_lz11(payload)), payload)


    def test_darc_file_offsets_are_absolute(self):
        entries = list(iter_darc(_make_darc()))
        self.assertEqual(
            [(entry.path, entry.offset, entry.data) for entry in entries],
            [("folder/hello.bin", 0x80, b"hello")],
        )


    def test_cgfx_texture_dictionary_and_l8_decode(self):
        cgfx = _make_cgfx_l8()
        textures = list(iter_cgfx_textures(cgfx))
        self.assertEqual(len(textures), 1)
        texture = textures[0]
        self.assertEqual(
            (texture.name, texture.width, texture.height, texture.format_name),
            ("test_l8", 8, 8, "L8"),
        )
        rgba = decode_pica_texture(texture.data, texture.width, texture.height, texture.format)
        self.assertEqual(rgba[0:4], bytes((0, 0, 0, 255)))
        self.assertEqual(rgba[(7 * 8 + 3) * 4:(7 * 8 + 4) * 4], bytes((59, 59, 59, 255)))


    def test_darc_bad_folder_range_is_rejected(self):
        data = bytearray(_make_darc())
        struct.pack_into("<I", data, 0x1C + 12 + 8, 99)
        with self.assertRaisesRegex(ValueError, "folder range"):
            list(iter_darc(bytes(data)))


if __name__ == "__main__":
    unittest.main()
