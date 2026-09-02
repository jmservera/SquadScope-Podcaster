"""Bounded, dependency-free validation of downloaded image bytes.

Why this module exists
----------------------
Remote assets (currently the DOG watermark) used to be accepted or rejected on
the ``Content-Type`` response header alone.  That is both too strict and too
lax:

* Too strict — object stores and raw-file endpoints routinely serve a perfectly
  valid PNG/JPEG as ``application/octet-stream``, which the header check
  rejected.
* Too lax — a response with *no* ``Content-Type`` was accepted unconditionally,
  and any server can simply *claim* ``image/png`` while returning an HTML error
  page, a script, or a multi-gigapixel decompression bomb.  Those bytes then
  reached ffmpeg, which failed opaquely much later.

The header is attacker/misconfiguration controlled; the bytes are the ground
truth.  :func:`sniff_image` therefore decides on the bytes, parsing **only the
container header** of a handful of formats ffmpeg can overlay.  It never
decodes pixel data, so validation is O(1) in memory and time regardless of the
declared image dimensions — a 60000x60000 PNG is rejected from its 24-byte
IHDR, without ever allocating a pixel buffer.

Guarantees:

* Accepts real JPEG / PNG / GIF / WebP / BMP bytes whatever the declared
  ``Content-Type`` (including ``application/octet-stream`` and no header).
* Rejects HTML, text, truncated headers and forged ``Content-Type`` values whose
  bytes are not one of the recognised formats.
* Rejects implausible geometry (zero/absurd dimensions, or a pixel count above
  :data:`MAX_IMAGE_PIXELS`) — the decompression-bomb guard.
* Never raises anything other than :class:`InvalidImageError` for untrusted
  input.
"""

from __future__ import annotations

import struct
from dataclasses import dataclass

#: Largest total pixel count accepted for a fetched image.  A logo/watermark is
#: a small corner graphic; 40 megapixels is already far beyond any legitimate
#: value and keeps the decoder ffmpeg eventually runs well bounded.
MAX_IMAGE_PIXELS = 40_000_000

#: Largest accepted single dimension.  Catches "1 x 4 billion" strips that stay
#: under the total-pixel cap but still break downstream scaling.
MAX_IMAGE_DIMENSION = 20_000

#: Bytes each supported container needs before its dimension fields can be read.
#: These are the *real* per-format minima the parsers below enforce, and each is
#: the offset just past the last dimension byte of the smallest header for that
#: format (WebP lists its smallest variant, lossless ``VP8L``; the ``VP8`` and
#: ``VP8X`` variants need 30 and enforce that themselves).
FORMAT_HEADER_MIN_BYTES: dict[str, int] = {
    "gif": 10,  # "GIF8?a"(6) + 16-bit width + 16-bit height
    "jpeg": 11,  # SOI(2) + SOF marker(2) + length(2) + precision(1) + 16-bit h/w
    "png": 24,  # signature(8) + length(4) + "IHDR"(4) + 32-bit width + height
    "webp": 25,  # RIFF(12) + "VP8L"(4) + signature byte + packed 14-bit w/h
    "bmp": 26,  # "BM"(2) + file header(12) + DIB size(4) + 32-bit width + height
}

#: Cheap length floor applied *before* signature matching.  It is **not** the
#: point at which a container signature becomes identifiable (PNG's is 8 bytes,
#: GIF's 6, JPEG's 3), and it is not a per-format header minimum either — those
#: are :data:`FORMAT_HEADER_MIN_BYTES` and are enforced individually once the
#: signature is known.  It only discards bodies far too short to be a *complete*
#: image in any supported format (even a 1x1 GIF — the smallest renderable file
#: any of them can produce — is several times this size), so an obviously
#: truncated or non-image response is rejected without any further parsing.
MIN_IMAGE_BYTES = 16

#: Formats we are willing to hand to ffmpeg as an overlay input.
SUPPORTED_FORMATS = ("jpeg", "png", "gif", "webp", "bmp")


class InvalidImageError(ValueError):
    """Raised when a byte string is not a usable image.

    Carries a short, stable :attr:`reason` token suitable for logs and operator
    dashboards (never the raw bytes, which are untrusted).
    """

    def __init__(self, reason: str, detail: str = "") -> None:
        super().__init__(f"{reason}: {detail}" if detail else reason)
        self.reason = reason
        self.detail = detail


@dataclass(frozen=True)
class ImageInfo:
    """Container-level facts about validated image bytes."""

    format: str
    width: int
    height: int

    @property
    def pixels(self) -> int:
        return self.width * self.height


def _check_geometry(fmt: str, width: int, height: int) -> ImageInfo:
    """Validate parsed dimensions, rejecting bombs and nonsense geometry."""
    if width <= 0 or height <= 0:
        raise InvalidImageError("invalid_dimensions", f"{fmt} {width}x{height}")
    if width > MAX_IMAGE_DIMENSION or height > MAX_IMAGE_DIMENSION:
        raise InvalidImageError("image_too_large", f"{fmt} {width}x{height}")
    if width * height > MAX_IMAGE_PIXELS:
        raise InvalidImageError("image_too_large", f"{fmt} {width}x{height}")
    return ImageInfo(format=fmt, width=width, height=height)


def _sniff_png(data: bytes) -> ImageInfo:
    # Signature(8) + length(4) + "IHDR"(4) + width(4) + height(4)
    if len(data) < FORMAT_HEADER_MIN_BYTES["png"] or data[12:16] != b"IHDR":
        raise InvalidImageError("malformed_image", "png without IHDR")
    width, height = struct.unpack(">II", data[16:24])
    return _check_geometry("png", width, height)


def _sniff_gif(data: bytes) -> ImageInfo:
    if len(data) < FORMAT_HEADER_MIN_BYTES["gif"]:
        raise InvalidImageError("malformed_image", "gif header truncated")
    width, height = struct.unpack("<HH", data[6:10])
    return _check_geometry("gif", width, height)


def _sniff_bmp(data: bytes) -> ImageInfo:
    # BITMAPINFOHEADER width/height are signed; a negative height just means a
    # top-down bitmap, so compare on the absolute value.
    if len(data) < FORMAT_HEADER_MIN_BYTES["bmp"]:
        raise InvalidImageError("malformed_image", "bmp header truncated")
    width, height = struct.unpack("<ii", data[18:26])
    return _check_geometry("bmp", abs(width), abs(height))


def _sniff_webp(data: bytes) -> ImageInfo:
    chunk = data[12:16]
    if chunk == b"VP8 ":
        # Lossy: 3-byte frame tag, 3-byte start code, then 14-bit dimensions.
        if len(data) < 30 or data[23:26] != b"\x9d\x01\x2a":
            raise InvalidImageError("malformed_image", "webp vp8 header")
        width = struct.unpack("<H", data[26:28])[0] & 0x3FFF
        height = struct.unpack("<H", data[28:30])[0] & 0x3FFF
    elif chunk == b"VP8L":
        # Lossless: signature byte 0x2f then 14 bits width-1, 14 bits height-1.
        if len(data) < FORMAT_HEADER_MIN_BYTES["webp"] or data[20] != 0x2F:
            raise InvalidImageError("malformed_image", "webp vp8l header")
        bits = struct.unpack("<I", data[21:25])[0]
        width = (bits & 0x3FFF) + 1
        height = ((bits >> 14) & 0x3FFF) + 1
    elif chunk == b"VP8X":
        # Extended: 24-bit little-endian canvas width-1 / height-1.
        if len(data) < 30:
            raise InvalidImageError("malformed_image", "webp vp8x header")
        width = int.from_bytes(data[24:27], "little") + 1
        height = int.from_bytes(data[27:30], "little") + 1
    else:
        raise InvalidImageError("malformed_image", "unknown webp chunk")
    return _check_geometry("webp", width, height)


def _sniff_jpeg(data: bytes) -> ImageInfo:
    """Walk JPEG marker segments to the first Start-Of-Frame.

    The walk is strictly forward and bounded by ``len(data)``, and every segment
    length is validated, so a hostile/truncated file cannot loop or over-read.
    """
    # Start-Of-Frame markers carrying dimensions.  DHP/DAC/RST/SOS are excluded.
    sof_markers = {
        0xC0, 0xC1, 0xC2, 0xC3,
        0xC5, 0xC6, 0xC7,
        0xC9, 0xCA, 0xCB,
        0xCD, 0xCE, 0xCF,
    }  # fmt: skip
    index = 2
    length = len(data)
    while index + 3 < length:
        if data[index] != 0xFF:
            raise InvalidImageError("malformed_image", "jpeg marker desync")
        # Fill bytes (0xFF padding) are legal between segments.
        marker = data[index + 1]
        index += 2
        if marker == 0xFF:
            index -= 1
            continue
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
            continue  # standalone markers carry no payload
        if index + 1 >= length:
            break
        segment_length = struct.unpack(">H", data[index : index + 2])[0]
        if segment_length < 2:
            raise InvalidImageError("malformed_image", "jpeg segment length")
        if marker in sof_markers:
            if index + 7 > length:
                break
            height, width = struct.unpack(">HH", data[index + 3 : index + 7])
            return _check_geometry("jpeg", width, height)
        if marker == 0xDA:  # SOS — entropy-coded data starts, no SOF found
            break
        index += segment_length
    raise InvalidImageError("malformed_image", "jpeg without frame header")


def sniff_image(data: bytes) -> ImageInfo:
    """Identify and sanity-check *data* as an image, from its bytes alone.

    Args:
        data: Untrusted bytes, already length-capped by the caller.

    Returns:
        :class:`ImageInfo` describing the detected container format and canvas
        size.  Pixel data is never decoded.

    Raises:
        InvalidImageError: If the bytes are too short, are not one of
            :data:`SUPPORTED_FORMATS`, have a malformed header, or declare
            implausible / bomb-sized dimensions.
    """
    if not isinstance(data, (bytes, bytearray, memoryview)):
        raise InvalidImageError("not_an_image", "non-bytes payload")
    data = bytes(data)
    if len(data) < MIN_IMAGE_BYTES:
        raise InvalidImageError("not_an_image", f"only {len(data)} bytes")

    if data.startswith(b"\x89PNG\r\n\x1a\n"):
        return _sniff_png(data)
    if data.startswith((b"GIF87a", b"GIF89a")):
        return _sniff_gif(data)
    if data.startswith(b"RIFF") and data[8:12] == b"WEBP":
        return _sniff_webp(data)
    if data.startswith(b"BM"):
        return _sniff_bmp(data)
    if data.startswith(b"\xff\xd8\xff"):
        return _sniff_jpeg(data)
    raise InvalidImageError("not_an_image", "unrecognised container signature")


def is_valid_image(data: bytes) -> bool:
    """Boolean convenience wrapper around :func:`sniff_image`."""
    try:
        sniff_image(data)
    except InvalidImageError:
        return False
    return True
