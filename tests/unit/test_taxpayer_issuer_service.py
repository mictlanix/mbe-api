"""Deleting a taxpayer issuer that is still referenced must be refused explicitly, rather
than reaching the database and surfacing the FK violation as a 500."""

from unittest.mock import AsyncMock, MagicMock

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


def _db_counting(*counts: int) -> AsyncMock:
    """A db whose successive `execute` calls return the given reference counts in order."""

    def _result(count: int) -> MagicMock:
        result = MagicMock()
        result.scalar_one.return_value = count
        return result

    db = AsyncMock()
    db.execute = AsyncMock(side_effect=[_result(c) for c in counts])
    return db


@pytest.mark.asyncio
async def test_delete_is_refused_when_a_facility_references_the_issuer() -> None:
    db = _db_counting(1)

    with pytest.raises(HTTPException) as exc:
        await taxpayer_issuer_service.delete_taxpayer_issuer(db, _issuer())

    assert exc.value.status_code == 409
    assert 'facilities' in exc.value.detail
    db.delete.assert_not_awaited()


@pytest.mark.asyncio
async def test_delete_checks_every_referencing_table() -> None:
    """Facilities and certificates are clear; the fiscal document reference still blocks it."""
    db = _db_counting(0, 0, 0, 3)

    with pytest.raises(HTTPException) as exc:
        await taxpayer_issuer_service.delete_taxpayer_issuer(db, _issuer())

    assert exc.value.status_code == 409
    assert 'fiscal documents' in exc.value.detail


@pytest.mark.asyncio
async def test_delete_proceeds_when_nothing_references_the_issuer() -> None:
    db = _db_counting(0, 0, 0, 0)
    issuer = _issuer()

    await taxpayer_issuer_service.delete_taxpayer_issuer(db, issuer)

    db.delete.assert_awaited_once_with(issuer)
    db.commit.assert_awaited_once()
