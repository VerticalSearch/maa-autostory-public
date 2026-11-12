"""Strict decoder for raw ``adb exec-out screencap`` buffers.

AOSP ``screencap`` writes a little-endian header followed immediately by
row-major pixel data with no stride padding:

* legacy header (12 bytes): ``uint32 width, height, format``
* modern header (16 bytes): ``uint32 width, height, format, colorspace``

Only ``RGBA_8888`` (format 1) and ``RGBX_8888`` (format 2) with four bytes per
pixel are supported, and the payload length must match the header *exactly*.
The returned BGR array is a new, writable, C-contiguous ``uint8`` array; it
never aliases the input buffer.

Header autodetection is intentionally conservative.  A 16-byte-header capture
truncated by four bytes has the same total length as a 12-byte-header capture
with the same dimensions, so length alone cannot distinguish them.  For the
autodetect path we apply a strict legacy-RGBA alpha check to reject the common
truncated-16 signature; this is a heuristic, not proof.  Callers that know the
device's header size should pin ``header_bytes=12`` or ``header_bytes=16``;
pinned parsing trusts the caller's header-size assertion and never falls back.
See REPORT.md for the full ambiguity discussion.
"""

from __future__ import annotations

import struct
from typing import Any, Optional

import cv2
import numpy as np

__all__ = ["RawCaptureError", "decode_raw_screencap"]

_HEADER_12 = 12
_HEADER_16 = 16
_HEADER_SIZES = (_HEADER_12, _HEADER_16)

_BYTES_PER_PIXEL = 4

_MAX_DIMENSION = 8192
_MAX_PIXELS = 16_777_216  # 4096 * 4096

_FORMAT_RGBA_8888 = 1
_FORMAT_RGBX_8888 = 2
_SUPPORTED_FORMATS = (_FORMAT_RGBA_8888, _FORMAT_RGBX_8888)

_COLORSPACE_SRGB = 1


class RawCaptureError(ValueError):
    """The raw screencap buffer is unsupported, malformed or ambiguous."""


class _LegacyAlphaAmbiguity(RawCaptureError):
    """Internal: a 12-byte interpretation failed the opaque-alpha heuristic."""


def _as_byte_view(data: Any) -> memoryview:
    """Return a 1-D, C-contiguous, byte-oriented memoryview.

    ``bytes``, ``bytearray``, ``memoryview`` and C-contiguous array-like
    objects are accepted.  Strings and non-contiguous buffers are rejected.
    """

    if isinstance(data, str):
        raise RawCaptureError("raw screencap data must be bytes-like, not str")
    try:
        view = memoryview(data)
    except TypeError as exc:
        raise RawCaptureError(
            f"raw screencap data must be bytes-like, got {type(data).__name__}"
        ) from exc

    try:
        view = view.cast("B")
    except TypeError as exc:
        raise RawCaptureError("raw screencap data must be a C-contiguous bytes-like buffer") from exc

    if not view.c_contiguous:
        raise RawCaptureError("raw screencap data must be a C-contiguous bytes-like buffer")
    return view


def _parse_header(view: memoryview, header_bytes: int) -> tuple[int, int, int, Optional[int]]:
    """Validate header, dimensions, format/length and return metadata.

    All checks that can prevent a large allocation happen before any ndarray is
    created for the pixel payload.
    """

    actual_length = len(view)
    if actual_length < header_bytes:
        raise RawCaptureError(
            f"truncated {header_bytes}-byte header: got {actual_length} bytes"
        )

    width, height, image_format = struct.unpack_from("<III", view, 0)
    colorspace = None
    if header_bytes == _HEADER_16:
        (colorspace,) = struct.unpack_from("<I", view, _HEADER_12)

    if width == 0:
        raise RawCaptureError("invalid width 0 in screencap header")
    if height == 0:
        raise RawCaptureError("invalid height 0 in screencap header")
    if width > _MAX_DIMENSION:
        raise RawCaptureError(
            f"width {width} exceeds the {_MAX_DIMENSION}-pixel dimension limit"
        )
    if height > _MAX_DIMENSION:
        raise RawCaptureError(
            f"height {height} exceeds the {_MAX_DIMENSION}-pixel dimension limit"
        )

    pixel_count = width * height
    if pixel_count > _MAX_PIXELS:
        raise RawCaptureError(
            f"pixel count {pixel_count} exceeds the {_MAX_PIXELS}-pixel allocation limit"
        )

    if image_format not in _SUPPORTED_FORMATS:
        raise RawCaptureError(
            f"unsupported screencap format {image_format}; "
            "only RGBA_8888 (1) and RGBX_8888 (2) are supported"
        )

    if header_bytes == _HEADER_16 and colorspace != _COLORSPACE_SRGB:
        raise RawCaptureError(
            f"unsupported screencap colorspace {colorspace}; "
            f"only sRGB ({_COLORSPACE_SRGB}) is accepted to avoid an implicit "
            "wide-gamut to sRGB conversion"
        )

    expected_length = header_bytes + pixel_count * _BYTES_PER_PIXEL
    if actual_length < expected_length:
        raise RawCaptureError(
            f"truncated payload for {width}x{height} {header_bytes}-byte-header capture: "
            f"got {actual_length} bytes, need exactly {expected_length}"
        )
    if actual_length > expected_length:
        raise RawCaptureError(
            f"overlong buffer for {width}x{height} {header_bytes}-byte-header capture: "
            f"got {actual_length} bytes, need exactly {expected_length}"
        )

    return width, height, image_format, colorspace


def _all_alpha_opaque(pixels: np.ndarray) -> bool:
    """Return True when every RGBA alpha byte is 0xFF.

    ``pixels`` is a zero-copy ``(h, w, 4)`` uint8 view.  The reduction does not
    copy the full payload.
    """

    return bool(pixels[:, :, 3].min() == 255)


def _decode(view: memoryview, header_bytes: int, *, legacy_alpha_check: bool) -> np.ndarray:
    width, height, image_format, _colorspace = _parse_header(view, header_bytes)

    pixel_bytes = width * height * _BYTES_PER_PIXEL
    # np.frombuffer is a zero-copy view; the input is never modified.
    raw_pixels = np.frombuffer(
        view,
        dtype=np.uint8,
        count=pixel_bytes,
        offset=header_bytes,
    )
    pixels = raw_pixels.reshape(height, width, _BYTES_PER_PIXEL)

    # The 16-byte-header buffer truncated by 4 bytes is length-identical to a
    # legacy capture.  For RGBA_8888 the shifted interpretation puts the
    # colorspace word in the first pixel and its alpha byte is 0x00.  Requiring
    # every legacy RGBA alpha to be 0xFF rejects that corruption signature.
    # This cannot prove the header size and does not cover RGBX_8888.
    if (
        legacy_alpha_check
        and header_bytes == _HEADER_12
        and image_format == _FORMAT_RGBA_8888
        and not _all_alpha_opaque(pixels)
    ):
        raise _LegacyAlphaAmbiguity(
            "12-byte header is ambiguous with a 16-byte-header capture truncated "
            "by 4 bytes, and its RGBA alpha bytes are not all 0xFF"
        )

    try:
        bgr = cv2.cvtColor(pixels, cv2.COLOR_RGBA2BGR)
    except cv2.error as exc:  # pragma: no cover - defensive; shape is validated
        raise RawCaptureError("OpenCV failed to convert RGBA pixels to BGR") from exc

    if (
        not bgr.flags.owndata
        or not bgr.flags.c_contiguous
        or not bgr.flags.writeable
    ):
        bgr = np.array(bgr, dtype=np.uint8, copy=True, order="C")
    return bgr


def decode_raw_screencap(data: Any, *, header_bytes: Optional[int] = None) -> np.ndarray:
    """Decode one raw ``screencap`` buffer into a writable BGR ``uint8`` array.

    Parameters
    ----------
    data:
        Raw bytes (or a C-contiguous bytes-like object) from
        ``adb exec-out screencap``.
    header_bytes:
        ``None`` to autodetect a 12- or 16-byte header, or exactly ``12`` /
        ``16`` to pin the known device format.  Pinned parsing never falls
        back to the other size.

    Returns
    -------
    numpy.ndarray
        ``(height, width, 3)`` uint8 BGR, C-contiguous, writable and not
        backed by ``data``.

    Raises
    ------
    RawCaptureError
        For any unsupported format/colorspace, bad dimensions, length
        mismatch, or ambiguous autodetection.
    """

    view = _as_byte_view(data)

    if header_bytes is not None:
        if (
            isinstance(header_bytes, bool)
            or not isinstance(header_bytes, int)
            or header_bytes not in _HEADER_SIZES
        ):
            raise RawCaptureError("header_bytes must be None, 12 or 16")
        return _decode(view, header_bytes, legacy_alpha_check=False)

    # Try the modern layout first.  A valid 16-byte capture cannot also have an
    # exact legacy length with the same width/height/format (the expected
    # lengths differ by exactly four bytes).
    try:
        return _decode(view, _HEADER_16, legacy_alpha_check=False)
    except RawCaptureError as modern_error:
        modern_message = str(modern_error)

    try:
        return _decode(view, _HEADER_12, legacy_alpha_check=True)
    except _LegacyAlphaAmbiguity as ambiguity:
        raise RawCaptureError(
            "ambiguous raw screencap: the length matches a 12-byte legacy header, "
            "but the legacy RGBA alpha bytes are not all 0xFF, which is also the "
            "signature of a 16-byte-header capture truncated by 4 bytes. "
            "Refusing to guess; specify header_bytes=12/16 or recapture. "
            f"({ambiguity})"
        ) from None
    except RawCaptureError as legacy_error:
        raise RawCaptureError(
            "unsupported raw screencap (autodetect): "
            f"16-byte header: {modern_message}; "
            f"12-byte header: {legacy_error}"
        ) from None
