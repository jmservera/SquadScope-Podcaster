"""Tests for :mod:`podcaster.image_validation`.

The remote watermark fetch used to trust the ``Content-Type`` response header,
which rejected valid images served as ``application/octet-stream`` while
accepting anything at all when the header was absent or forged. These tests pin
the replacement contract: **the bytes decide**, validation is header-only (no
pixel decode), and hostile input is rejected with a stable reason token.

All fixtures are synthesised in-process — no third-party artwork is vendored.
"""

from __future__ import annotations

import struct
import zlib

import pytest

from podcaster.image_validation import (
    MAX_IMAGE_DIMENSION,
    MAX_IMAGE_PIXELS,
    ImageInfo,
    InvalidImageError,
    is_valid_image,
    sniff_image,
)


def _png(width: int, height: int) -> bytes:
    """PNG signature + a well-formed IHDR declaring *width* x *height*."""
    ihdr = b"IHDR" + struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    return (
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + ihdr
        + struct.pack(">I", zlib.crc32(ihdr) & 0xFFFFFFFF)
    )


def _gif(width: int, height: int) -> bytes:
    return b"GIF89a" + struct.pack("<HH", width, height) + b"\x00" * 16


def _bmp(width: int, height: int) -> bytes:
    return b"BM" + b"\x00" * 16 + struct.pack("<ii", width, height) + b"\x00" * 8


def _jpeg(width: int, height: int, *, extra_segment: bool = True) -> bytes:
    out = bytearray(b"\xff\xd8")
    if extra_segment:
        # A JFIF APP0 segment the walker must skip over to reach the SOF0.
        payload = b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
        out += b"\xff\xe0" + struct.pack(">H", len(payload) + 2) + payload
    sof = struct.pack(">BHHB", 8, height, width, 3) + b"\x01\x11\x00\x02\x11\x01\x03\x11\x01"
    out += b"\xff\xc0" + struct.pack(">H", len(sof) + 2) + sof
    out += b"\xff\xd9"
    return bytes(out)


def _webp_vp8x(width: int, height: int) -> bytes:
    body = b"WEBP" + b"VP8X" + struct.pack("<I", 10) + b"\x00\x00\x00\x00"
    body += (width - 1).to_bytes(3, "little") + (height - 1).to_bytes(3, "little")
    return b"RIFF" + struct.pack("<I", len(body)) + body


class TestValidFormats:
    """Every format ffmpeg can overlay is recognised from its own bytes."""

    def test_png(self):
        assert sniff_image(_png(120, 80)) == ImageInfo("png", 120, 80)

    def test_gif(self):
        assert sniff_image(_gif(64, 32)) == ImageInfo("gif", 64, 32)

    def test_bmp(self):
        assert sniff_image(_bmp(10, 20)) == ImageInfo("bmp", 10, 20)

    def test_bmp_top_down_negative_height(self):
        """A negative BMP height means top-down rows, not an invalid image."""
        assert sniff_image(_bmp(10, -20)) == ImageInfo("bmp", 10, 20)

    def test_jpeg_with_leading_app_segment(self):
        assert sniff_image(_jpeg(300, 150)) == ImageInfo("jpeg", 300, 150)

    def test_jpeg_without_app_segment(self):
        assert sniff_image(_jpeg(8, 8, extra_segment=False)) == ImageInfo("jpeg", 8, 8)

    def test_webp_extended(self):
        assert sniff_image(_webp_vp8x(200, 100)) == ImageInfo("webp", 200, 100)

    def test_bundled_claracle_asset_is_a_valid_jpeg(self):
        from podcaster import watermark

        info = sniff_image(watermark.LOGO_PATH.read_bytes())
        assert info.format == "jpeg"
        assert info.pixels > 0

    def test_pixels_property(self):
        assert sniff_image(_png(4, 5)).pixels == 20


class TestRejectsNonImages:
    """HTML soft-404s and other non-image bodies never pass as images."""

    @pytest.mark.parametrize(
        "body",
        [
            b"<!DOCTYPE html><html><body>404 Not Found</body></html>",
            b"<html><head><title>Error</title></head><body>nope</body></html>",
            b'{"error": "not found", "status": 404, "detail": "x"}',
            b"#!/bin/sh\nrm -rf / # definitely not a logo\n",
            b"\x00" * 64,
        ],
    )
    def test_non_image_bodies_rejected(self, body):
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(body)
        assert excinfo.value.reason in ("not_an_image", "malformed_image")
        assert is_valid_image(body) is False

    def test_empty_body_rejected(self):
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(b"")
        assert excinfo.value.reason == "not_an_image"

    def test_truncated_png_rejected(self):
        with pytest.raises(InvalidImageError):
            sniff_image(_png(10, 10)[:12])

    def test_png_signature_without_ihdr_rejected(self):
        forged = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(forged)
        assert excinfo.value.reason == "malformed_image"

    def test_jpeg_signature_without_frame_header_rejected(self):
        # SOI then straight to SOS: entropy data with no dimensions anywhere.
        forged = b"\xff\xd8\xff\xda" + struct.pack(">H", 12) + b"\x00" * 32
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(forged)
        assert excinfo.value.reason == "malformed_image"

    def test_jpeg_marker_desync_rejected(self):
        forged = b"\xff\xd8\xff" + b"\x41" * 64
        with pytest.raises(InvalidImageError):
            sniff_image(forged)

    def test_webp_with_unknown_chunk_rejected(self):
        forged = b"RIFF" + struct.pack("<I", 20) + b"WEBP" + b"XXXX" + b"\x00" * 16
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(forged)
        assert excinfo.value.reason == "malformed_image"

    def test_non_bytes_payload_rejected(self):
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image("not bytes")  # type: ignore[arg-type]
        assert excinfo.value.reason == "not_an_image"


class TestDecompressionBombs:
    """Header-declared geometry is capped so nothing gigapixel is ever decoded."""

    def test_gigapixel_png_rejected_from_header(self):
        bomb = _png(60_000, 60_000)
        assert len(bomb) < 64, "the bomb is tiny on the wire — that is the point"
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(bomb)
        assert excinfo.value.reason == "image_too_large"

    def test_long_thin_strip_rejected(self):
        """Stays under the pixel cap but blows the per-dimension cap."""
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(_png(1, MAX_IMAGE_DIMENSION + 1))
        assert excinfo.value.reason == "image_too_large"

    def test_pixel_cap_enforced(self):
        side = int(MAX_IMAGE_PIXELS**0.5) + 500
        assert side <= MAX_IMAGE_DIMENSION, "this case must hit the pixel cap, not the dim cap"
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(_png(side, side))
        assert excinfo.value.reason == "image_too_large"

    def test_gigapixel_gif_rejected(self):
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(_gif(65_535, 65_535))
        assert excinfo.value.reason == "image_too_large"

    def test_zero_dimension_rejected(self):
        with pytest.raises(InvalidImageError) as excinfo:
            sniff_image(_png(0, 100))
        assert excinfo.value.reason == "invalid_dimensions"

    def test_validation_is_bounded_for_hostile_input(self):
        """A large hostile body must not cause unbounded work.

        The walker is strictly forward and length-checked, so even a megabyte of
        JPEG-shaped garbage terminates immediately rather than scanning forever.
        """
        hostile = b"\xff\xd8" + b"\xff\xe0\x00\x02" * 200_000
        with pytest.raises(InvalidImageError):
            sniff_image(hostile)


class TestContentTypeIndependence:
    """The validator never sees a header — that is the whole point."""

    def test_same_bytes_valid_regardless_of_any_declared_type(self):
        data = _png(32, 32)
        # sniff_image takes no content type at all, so an octet-stream body and
        # an image/png body are literally the same call.
        assert sniff_image(data).format == "png"
        assert is_valid_image(data) is True
