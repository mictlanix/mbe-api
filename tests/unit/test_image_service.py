"""Product images deduplicate; proof-of-delivery captures must not.

Content-addressing is right for a catalogue and wrong for evidence: if two signatures hashed the
same, deleting one order's proof would remove another order's (FR-044b).
"""

import io

import pytest
from PIL import Image

from app.services.image_service import (
    normalise_to_png,
    process_and_save_image,
    save_proof_image,
)


def _png(colour: str = 'red', size: tuple[int, int] = (10, 10)) -> bytes:
    buf = io.BytesIO()
    Image.new('RGB', size, colour).save(buf, format='PNG')
    return buf.getvalue()


class TestNormalisation:
    def test_returns_png_bytes(self) -> None:
        out = normalise_to_png(_png())

        assert Image.open(io.BytesIO(out)).format == 'PNG'

    def test_bounds_the_width(self) -> None:
        out = normalise_to_png(_png(size=(4000, 2000)), max_width=100)

        assert Image.open(io.BytesIO(out)).width == 100

    def test_refuses_something_that_is_not_an_image(self) -> None:
        with pytest.raises(ValueError, match='Unsupported'):
            normalise_to_png(b'definitely not an image')

    def test_refuses_an_oversized_upload(self) -> None:
        with pytest.raises(ValueError, match='2 MB'):
            normalise_to_png(b'x' * (2 * 1024 * 1024 + 1))


class TestStorageStrategies:
    @pytest.mark.asyncio
    async def test_product_images_deduplicate(self, tmp_path) -> None:
        content = _png()

        first = await process_and_save_image(content, str(tmp_path))
        second = await process_and_save_image(content, str(tmp_path))

        assert first == second
        assert len(list(tmp_path.iterdir())) == 1

    @pytest.mark.asyncio
    async def test_identical_proof_captures_stay_two_files(self, tmp_path) -> None:
        """The aliasing hazard FR-044b exists for."""
        content = _png()

        first = await save_proof_image(content, str(tmp_path))
        second = await save_proof_image(content, str(tmp_path))

        assert first != second
        assert len(list(tmp_path.iterdir())) == 2

    @pytest.mark.asyncio
    async def test_proof_filename_is_not_derived_from_content(self, tmp_path) -> None:
        import hashlib

        content = _png()
        name = await save_proof_image(content, str(tmp_path))
        digest = hashlib.sha256(normalise_to_png(content, max_width=1024)).hexdigest()

        assert digest not in name

    @pytest.mark.asyncio
    async def test_proof_is_written_where_it_was_asked_to_be(self, tmp_path) -> None:
        name = await save_proof_image(_png(), str(tmp_path))

        assert (tmp_path / name).exists()
