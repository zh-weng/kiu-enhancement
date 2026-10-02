"""Nintendo LZ11 decompression.

The distance field in an LZ11 back-reference is stored as ``distance - 1``.
It is tempting to treat it as a negative two's-complement offset, but doing so
breaks on real 3DS archives (including Kid Icarus: Uprising's ``.zrc`` files).
"""


def decompress(data):
    """Decompress an LZ11 stream and return its exact uncompressed payload."""

    if len(data) < 4 or data[0] != 0x11:
        raise ValueError("missing LZ11 type byte")

    out_size = int.from_bytes(data[1:4], "little")
    pos = 4
    # Nintendo's extended form uses a zero 24-bit size followed by a 32-bit
    # size.  The game data currently uses the short form, but accepting both
    # makes this utility safe to reuse for other title resources.
    if out_size == 0:
        if len(data) < 8:
            raise ValueError("truncated LZ11 extended size")
        out_size = int.from_bytes(data[4:8], "little")
        pos = 8

    out = bytearray()
    while len(out) < out_size:
        if pos >= len(data):
            raise ValueError("truncated LZ11 flag byte")
        flags = data[pos]
        pos += 1

        for bit in range(8):
            if len(out) >= out_size:
                break

            if not (flags & (0x80 >> bit)):
                if pos >= len(data):
                    raise ValueError("truncated LZ11 literal")
                out.append(data[pos])
                pos += 1
                continue

            if pos + 1 >= len(data):
                raise ValueError("truncated LZ11 back-reference")
            first = data[pos]
            second = data[pos + 1]
            pos += 2
            length_code = first >> 4

            if length_code == 0:
                if pos >= len(data):
                    raise ValueError("truncated LZ11 long back-reference")
                third = data[pos]
                pos += 1
                length = (((first & 0x0F) << 4) | (second >> 4)) + 0x11
                displacement = ((second & 0x0F) << 8) | third
            elif length_code == 1:
                if pos + 1 >= len(data):
                    raise ValueError("truncated LZ11 very-long back-reference")
                third = data[pos]
                fourth = data[pos + 1]
                pos += 2
                length = (
                    ((first & 0x0F) << 12)
                    | (second << 4)
                    | (third >> 4)
                ) + 0x111
                displacement = ((third & 0x0F) << 8) | fourth
            else:
                length = length_code + 1
                displacement = ((first & 0x0F) << 8) | second

            distance = displacement + 1
            if distance > len(out):
                raise ValueError(
                    "LZ11 displacement before start of output "
                    f"(distance={distance}, output={len(out)})"
                )
            if len(out) + length > out_size:
                raise ValueError("LZ11 back-reference exceeds declared output size")

            # Index from the growing output rather than copying a fixed slice:
            # overlapping matches are part of the LZ11 format.
            for _ in range(length):
                out.append(out[-distance])

    return bytes(out)
