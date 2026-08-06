"""
==============================================================
AI Maintenance Voice Assistant
Damage Photo Service
--------------------------------------------------------------

Purpose
-------
Turn whatever a technician's phone or laptop hands us into a
clean, bounded, safe-to-store image, so the rest of the app never
has to think about a 12-megapixel HEIC-ish upload again.

Why process at all rather than storing the upload verbatim
----------------------------------------------------------
• Size. A modern phone camera produces 3-8 MB per shot. Several
  of those per finding, fetched into a PDF and streamed to a
  supervisor's browser, is a lot of HANA Cloud traffic for an
  image that is displayed a few hundred pixels wide.
• Privacy. Phone photos carry EXIF, which routinely includes GPS
  coordinates. A maintenance record should not silently capture
  where the technician was standing. Re-encoding through Pillow
  drops every EXIF block, including that.
• Trust. "Whatever bytes the client posted" should never be handed
  back out with an image content type. Decoding and re-encoding
  means what we store is an image we produced ourselves, so a
  file that merely claims to be a JPEG cannot survive the trip.
• Orientation. Phones store the sensor image plus an EXIF rotation
  flag. Strip EXIF naively and the photo appears sideways, so the
  rotation is applied to the pixels before it is discarded.

IMPORTANT
---------
This module never reads environment variables directly.
All settings come from backend.config.

Example
-------
    from backend.photo_service import process_upload

    photo = process_upload(raw_bytes, "damage.jpg")
    photo.data, photo.mime_type, photo.width, photo.height
==============================================================
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from backend.config import (
    PHOTO_MAX_DIMENSION,
    PHOTO_JPEG_QUALITY,
    PHOTO_MAX_UPLOAD_BYTES,
    SUPPORTED_PHOTO_FORMATS,
    LOG_LEVEL,
)

logger = logging.getLogger("mro_copilot.photo_service")
logger.setLevel(LOG_LEVEL)


class UnsupportedImageError(ValueError):
    """Raised when the upload is not a decodable image we accept."""


class ImageTooLargeError(ValueError):
    """Raised when the raw upload exceeds PHOTO_MAX_UPLOAD_BYTES."""


@dataclass
class ProcessedPhoto:
    """A validated, re-encoded image ready to be stored."""

    data: bytes
    mime_type: str
    width: int
    height: int
    original_name: Optional[str] = None


def _ext_is_plausible(file_name: Optional[str]) -> bool:
    """
    Cheap sanity check on the extension.

    Deliberately advisory only - the real gate is whether Pillow can
    decode the bytes. A phone that names a capture "blob" should not be
    rejected for it, and a ".png" that is really a JPEG is fine because
    the content is what gets trusted.
    """
    if not file_name:
        return True
    suffix = Path(file_name).suffix.lower().lstrip(".")
    return not suffix or suffix in SUPPORTED_PHOTO_FORMATS


def process_upload(
    raw: bytes,
    file_name: Optional[str] = None,
) -> ProcessedPhoto:
    """
    Validate, normalise and re-encode one uploaded image.

    Returns a ProcessedPhoto whose bytes are safe to store and serve.
    Raises UnsupportedImageError / ImageTooLargeError for bad input.
    """
    if not raw:
        raise UnsupportedImageError("That photo came through empty.")

    if len(raw) > PHOTO_MAX_UPLOAD_BYTES:
        limit_mb = PHOTO_MAX_UPLOAD_BYTES / (1024 * 1024)
        raise ImageTooLargeError(
            f"That photo is {len(raw) / (1024 * 1024):.1f} MB, over the "
            f"{limit_mb:.0f} MB limit. Try a lower camera resolution."
        )

    if not _ext_is_plausible(file_name):
        raise UnsupportedImageError(
            f"'{file_name}' is not a supported image type. "
            f"Supported: {', '.join(sorted(SUPPORTED_PHOTO_FORMATS))}."
        )

    try:
        from PIL import Image, ImageOps
    except ImportError as exc:  # pragma: no cover - dependency is pinned
        raise UnsupportedImageError(
            "Pillow is not installed, so photos cannot be processed. "
            "Run: pip install Pillow"
        ) from exc

    try:
        image = Image.open(io.BytesIO(raw))
        # Force a decode now: Image.open is lazy, so a truncated or
        # bogus file would otherwise only blow up later during save().
        image.load()
    except Exception as exc:  # noqa: BLE001 - Pillow raises many types
        raise UnsupportedImageError(
            "That file could not be read as an image. JPEG or PNG works best."
        ) from exc

    # Apply the EXIF rotation to the pixels *before* the metadata is
    # dropped, otherwise portrait phone shots come out sideways.
    image = ImageOps.exif_transpose(image)

    had_alpha = image.mode in ("RGBA", "LA", "P")

    # Bound the long edge. thumbnail() preserves aspect ratio and is a
    # no-op for images already smaller than the box.
    before = image.size
    image.thumbnail((PHOTO_MAX_DIMENSION, PHOTO_MAX_DIMENSION), Image.LANCZOS)

    buffer = io.BytesIO()

    if had_alpha:
        # Keep transparency (a marked-up screenshot or annotated diagram)
        # by staying with PNG - flattening onto white would destroy the
        # annotation on anything but a white background.
        image.convert("RGBA").save(buffer, format="PNG", optimize=True)
        mime_type = "image/png"
    else:
        image.convert("RGB").save(
            buffer,
            format="JPEG",
            quality=PHOTO_JPEG_QUALITY,
            optimize=True,
            progressive=True,
        )
        mime_type = "image/jpeg"

    data = buffer.getvalue()

    logger.info(
        "Processed photo %s: %sx%s -> %sx%s, %.0f KB -> %.0f KB (%s)",
        file_name or "(unnamed)",
        before[0], before[1], image.size[0], image.size[1],
        len(raw) / 1024, len(data) / 1024, mime_type,
    )

    return ProcessedPhoto(
        data=data,
        mime_type=mime_type,
        width=image.size[0],
        height=image.size[1],
        original_name=file_name,
    )
