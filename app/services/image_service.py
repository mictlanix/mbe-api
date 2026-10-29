"""Image intake: normalise to PNG, then name and store.

Two callers with different needs, so those two steps are separate. Product photos are
**content-addressed** — the same picture uploaded twice is one file, which is what you want for a
catalogue. Proof-of-delivery captures must never be, because deduplicating them would mean
deleting one order's evidence removed another's (FR-044b); signatures are low-entropy images and a
hurried squiggle colliding across two customers is entirely plausible.
"""

import asyncio
import hashlib
import io
import os
import uuid

from PIL import Image, UnidentifiedImageError

from app.core.config import settings
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError

_MAX_BYTES = 2 * 1024 * 1024  # 2 MB

PNG_RESPONSE = {200: {'content': {'image/png': {'schema': {'type': 'string', 'format': 'binary'}}}}}
_MAX_WIDTH = 150
_MAX_POD_WIDTH = 1024


def normalise_to_png(content: bytes, *, max_width: int = _MAX_WIDTH) -> bytes:
    """Decode, flatten, bound the width, and re-encode as PNG.

    Shared by both callers so the size guard and the format handling cannot drift apart.
    """
    if len(content) > _MAX_BYTES:
        raise DomainError(
            ErrorCode.IMAGE_TOO_LARGE,
            'Image exceeds maximum allowed size of 2 MB',
            {'max_bytes': _MAX_BYTES},
        )

    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except UnidentifiedImageError as e:
        raise DomainError(
            ErrorCode.IMAGE_UNSUPPORTED_FORMAT, 'Unsupported or unrecognizable image format'
        ) from e

    # Use first frame for animated formats (GIF, WEBP)
    if hasattr(img, 'n_frames') and img.n_frames > 1:
        img.seek(0)

    img = img.convert('RGBA')

    if img.width > max_width:
        new_height = round(img.height * max_width / img.width)
        img = img.resize((max_width, new_height), Image.LANCZOS)

    buf = io.BytesIO()
    img.save(buf, format='PNG')
    return buf.getvalue()


def _write(png_bytes: bytes, directory: str, filename: str) -> str:
    path = os.path.join(directory, filename)
    if not os.path.exists(path):
        os.makedirs(directory, exist_ok=True)
        with open(path, 'wb') as f:
            f.write(png_bytes)
    return filename


def _process_and_save_sync(content: bytes, images_dir: str) -> str:
    png_bytes = normalise_to_png(content)
    return _write(png_bytes, images_dir, f'{hashlib.sha256(png_bytes).hexdigest()}.png')


def _save_pod_sync(content: bytes, pod_dir: str) -> str:
    # A UUID, never a digest: two identical captures must remain two files (FR-044b).
    png_bytes = normalise_to_png(content, max_width=_MAX_POD_WIDTH)
    return _write(png_bytes, pod_dir, f'{uuid.uuid4().hex}.png')


async def process_and_save_image(content: bytes, images_dir: str) -> str:
    return await asyncio.to_thread(_process_and_save_sync, content, images_dir)


async def save_proof_image(content: bytes, pod_dir: str) -> str:
    """Store a signature or photo under `pod_dir`, outside the public `/images` mount.

    Retrieval goes through an authenticated route: a customer's signature is personal data and
    must not sit behind a URL that works for anyone who has it (FR-044a).
    """
    return await asyncio.to_thread(_save_pod_sync, content, pod_dir)


async def delete_image(directory: str, filename: str) -> None:
    """Remove a stored file; one that is already gone is not an error.

    A name that could point outside `directory` is refused, so a bad value in a column can never
    become a delete of some other file.
    """
    if '/' in filename or '..' in filename:
        raise ValueError('Invalid image filename')
    try:
        await asyncio.to_thread(os.remove, os.path.join(directory, filename))
    except FileNotFoundError:
        pass


def image_url(filename: str | None) -> str | None:
    """Resolve a stored image filename to a URL a non-same-origin client can render."""
    if not filename:
        return None
    if '/' in filename:
        return filename
    base = settings.images_base_url.rstrip('/')
    return f'{base}/images/{filename}' if base else f'/images/{filename}'
