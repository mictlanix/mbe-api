"""Deleting a taxpayer issuer that is still referenced must be refused explicitly, rather
than reaching the database and surfacing the FK violation as a 500.

The reference counting itself lives in `app.services.references` and is covered by
`test_references.py`; these tests pin that the issuer delete is wired to it."""

from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.enums import FiscalCertificationProvider
from app.models.fiscal import TaxpayerIssuer
from app.services import taxpayer_issuer_service


def _issuer() -> TaxpayerIssuer:
    return TaxpayerIssuer(
        taxpayer_issuer_id='RFC123456789A',
        name='Acme SA de CV',
        regime='601',
        provider=FiscalCertificationProvider.NONE,
        comment=None,
        postal_code='06000',
    )


@pytest.mark.asyncio
async def test_delete_is_refused_while_referenced() -> None:
    db = AsyncMock()
    issuer = _issuer()
    blocked = HTTPException(status_code=409, detail='Still referenced by facility (2)')

    with patch.object(
        taxpayer_issuer_service, 'assert_not_referenced', new=AsyncMock(side_effect=blocked)
    ):
        with pytest.raises(HTTPException) as exc:
            await taxpayer_issuer_service.delete_taxpayer_issuer(db, issuer)

    assert exc.value.status_code == 409
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_proceeds_when_nothing_references_the_issuer() -> None:
    db = AsyncMock()
    issuer = _issuer()

    with patch.object(taxpayer_issuer_service, 'assert_not_referenced', new=AsyncMock()) as guard:
        await taxpayer_issuer_service.delete_taxpayer_issuer(db, issuer)

    guard.assert_awaited_once()
    db.delete.assert_awaited_once_with(issuer)
    db.commit.assert_awaited_once()
