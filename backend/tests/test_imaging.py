import io
import struct

import pytest
from PIL import Image

from annotation_platform.imaging import encode_gray8_png


def test_encoded_png_round_trips_through_a_real_decoder():
    width, height = 5, 3
    pixels = bytes((row * width + col) % 256 for row in range(height) for col in range(width))
    png = encode_gray8_png(width, height, pixels)

    assert png.startswith(b"\x89PNG\r\n\x1a\n")
    image = Image.open(io.BytesIO(png))
    assert image.mode == "L"
    assert image.size == (width, height)
    assert image.tobytes() == pixels


def test_ihdr_declares_grayscale_8bit_no_interlace():
    png = encode_gray8_png(4, 2, bytes(8))
    ihdr_start = len(b"\x89PNG\r\n\x1a\n") + 8  # signature + length/type of IHDR
    width, height, depth, color_type, compression, filter_method, interlace = struct.unpack(
        ">IIBBBBB", png[ihdr_start : ihdr_start + 13]
    )
    assert (width, height) == (4, 2)
    assert (depth, color_type, compression, filter_method, interlace) == (8, 0, 0, 0, 0)


def test_solid_image_decodes_to_uniform_pixels():
    png = encode_gray8_png(3, 3, bytes([200] * 9))
    image = Image.open(io.BytesIO(png))
    assert set(image.tobytes()) == {200}


@pytest.mark.parametrize(
    ("width", "height", "pixels"),
    [
        (0, 1, b""),
        (1, 0, b""),
        (2, 2, b"\x00\x00\x00"),  # too few bytes
        (2, 2, b"\x00\x00\x00\x00\x00"),  # too many bytes
    ],
)
def test_invalid_dimensions_or_pixel_count_are_rejected(width, height, pixels):
    with pytest.raises(ValueError):
        encode_gray8_png(width, height, pixels)
