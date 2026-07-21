"""Guard rails applied after a CSD parses cleanly but before it is stored."""

from datetime import datetime
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.fiscal import TaxpayerCertificate, TaxpayerIssuer
from app.services.csd_service import ParsedCsd
from app.services.taxpayer_certificate_service import create_taxpayer_certificate


def _parsed(taxpayer: str | None = 'AAA010101AAA') -> ParsedCsd:
    return ParsedCsd(
        certificate_id='00001000000500003416',
        taxpayer=taxpayer,
        valid_from=datetime(2024, 1, 1),
        valid_to=datetime(2028, 1, 1),
    )


def _db(*gets: object) -> AsyncMock:
    """A db whose successive `get` calls return the given rows (issuer, then certificate)."""
    db = AsyncMock()
    db.get = AsyncMock(side_effect=list(gets))
    db.add = MagicMock()  # Session.add is synchronous
    return db


async def _create(db: AsyncMock, parsed: ParsedCsd, taxpayer: str = 'AAA010101AAA'):
    return await create_taxpayer_certificate(
        db,
        parsed,
        taxpayer=taxpayer,
        certificate_data=b'CERT',
        key_data=b'KEY',
        key_password=b'hunter2',
    )


@pytest.mark.asyncio
async def test_certificate_for_another_rfc_is_rejected() -> None:
    db = _db()

    with pytest.raises(HTTPException) as exc:
        await _create(db, _parsed('BBB020202BBB'))

    assert exc.value.status_code == 422
    assert 'BBB020202BBB' in exc.value.detail
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_unknown_issuer_is_rejected() -> None:
    db = _db(None)

    with pytest.raises(HTTPException) as exc:
        await _create(db, _parsed())

    assert exc.value.status_code == 422
    assert exc.value.detail == 'Taxpayer issuer not found'


@pytest.mark.asyncio
async def test_already_registered_certificate_is_rejected() -> None:
    db = _db(TaxpayerIssuer(taxpayer_issuer_id='AAA010101AAA'), TaxpayerCertificate())

    with pytest.raises(HTTPException) as exc:
        await _create(db, _parsed())

    assert exc.value.status_code == 409
    db.add.assert_not_called()


@pytest.mark.asyncio
async def test_certificate_without_an_rfc_is_attached_to_the_named_issuer() -> None:
    """An unreadable holder is not fatal — the form's issuer stands in."""
    db = _db(TaxpayerIssuer(taxpayer_issuer_id='AAA010101AAA'), None)

    stored = await _create(db, _parsed(None))

    assert stored.taxpayer == 'AAA010101AAA'
    assert stored.key_password == b'hunter2'
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_taxpayer_is_normalised_to_upper_case() -> None:
    db = _db(TaxpayerIssuer(taxpayer_issuer_id='AAA010101AAA'), None)

    stored = await _create(db, _parsed(), taxpayer='aaa010101aaa')

    assert stored.taxpayer == 'AAA010101AAA'
