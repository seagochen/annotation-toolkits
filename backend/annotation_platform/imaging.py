"""Minimal stdlib PNG encoding for single-channel 8-bit rasters.

Segmentation masks and depth-map edits are stored as real, viewable PNG
files without pulling Pillow/numpy into the platform's runtime dependencies
(the web app is pure standard library on purpose -- see requirements.txt).
The platform never needs to decode an arbitrary PNG: callers that produced
the raster already hold the raw pixel bytes, and the frontend decodes the
served file natively via <img>/canvas. So only an encoder lives here.
"""

from __future__ import annotations

import struct
import zlib

_SIGNATURE = b"\x89PNG\r\n\x1a\n"
_COLOR_TYPE_GRAYSCALE = 0
_BIT_DEPTH = 8
_FILTER_NONE = 0


def encode_gray8_png(width: int, height: int, pixels: bytes) -> bytes:
    """Encode a row-major 8-bit grayscale raster as PNG bytes.

    `pixels` must hold exactly `width * height` bytes, one per pixel, with
    no row padding.
    """
    if width <= 0 or height <= 0:
        raise ValueError("width and height must be positive")
    expected = width * height
    if len(pixels) != expected:
        raise ValueError(f"expected {expected} pixel bytes, got {len(pixels)}")

    ihdr = struct.pack(
        ">IIBBBBB", width, height, _BIT_DEPTH, _COLOR_TYPE_GRAYSCALE, 0, 0, 0
    )
    raw = bytearray()
    for row in range(height):
        start = row * width
        raw.append(_FILTER_NONE)
        raw.extend(pixels[start : start + width])
    idat = zlib.compress(bytes(raw), 9)

    return b"".join(
        (
            _SIGNATURE,
            _chunk(b"IHDR", ihdr),
            _chunk(b"IDAT", idat),
            _chunk(b"IEND", b""),
        )
    )


def _chunk(chunk_type: bytes, data: bytes) -> bytes:
    body = chunk_type + data
    return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))
