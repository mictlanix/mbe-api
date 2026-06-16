import asyncio
import hashlib
import io
import os

from PIL import Image, UnidentifiedImageError

_MAX_BYTES = 2 * 1024 * 1024  # 2 MB
_MAX_WIDTH = 150


def _process_and_save_sync(content: bytes, images_dir: str) -> str:
    if len(content) > _MAX_BYTES:
        raise ValueError("Image exceeds maximum allowed size of 2 MB")

    try:
        img = Image.open(io.BytesIO(content))
        img.load()
    except UnidentifiedImageError as e:
        raise ValueError("Unsupported or unrecognizable image format") from e

    # Use first frame for animated formats (GIF, WEBP)
    if hasattr(img, "n_frames") and img.n_frames > 1:
        img.seek(0)

    img = img.convert("RGBA")

    if img.width > _MAX_WIDTH:
        new_height = round(img.height * _MAX_WIDTH / img.width)
        img = img.resize((_MAX_WIDTH, new_height), Image.LANCZOS)

    # Convert to PNG bytes
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    png_bytes = buf.getvalue()

    digest = hashlib.sha256(png_bytes).hexdigest()
    filename = f"{digest}.png"
    path = os.path.join(images_dir, filename)

    if not os.path.exists(path):
        os.makedirs(images_dir, exist_ok=True)
        with open(path, "wb") as f:
            f.write(png_bytes)

    return filename


async def process_and_save_image(content: bytes, images_dir: str) -> str:
    return await asyncio.to_thread(_process_and_save_sync, content, images_dir)
