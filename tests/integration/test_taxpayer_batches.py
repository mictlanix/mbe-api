"""Taxpayer batch rules against real tables (#44, spec 027, US3)."""

import json

import pytest
from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.fiscal import TaxpayerBatch, TaxpayerIssuer
from app.schemas.fiscal import TaxpayerBatchCreate, TaxpayerBatchUpdate
from app.services import taxpayer_batch_service as svc
from app.services.cfdi import batch_settings
from tests.integration.seed import ISSUER_RFC, LEGACY_BATCH_TEMPLATE, seed_fiscal_document

pytestmark = pytest.mark.usefixtures('seeded')
OTHER = 'EKU9003173C9'


def _body(batch: str = 'F', type_: int = 0, **template: object) -> dict:
    return {'batch': batch, 'type': type_, 'template': {'name': 'T02Blue', **template}}


async def _create(db: AsyncSession, rfc: str = ISSUER_RFC, **body: object) -> TaxpayerBatch:
    created = await svc.create_batch(db, rfc, TaxpayerBatchCreate.model_validate(_body(**body)))
    return await svc.get_batch(db, rfc, created.taxpayer_batch_id)


async def _status(code: int, call) -> HTTPException:  # noqa: ANN001
    with pytest.raises(HTTPException) as caught:
        await call
    assert caught.value.status_code == code, caught.value.detail
    return caught.value


async def test_list_is_by_series_with_the_in_use_flag(db: AsyncSession) -> None:
    await _create(db, batch='Z')
    await _create(db, batch='B', type_=200)
    await seed_fiscal_document(db, 'invoice', issuer=ISSUER_RFC, batch='Z')

    listed = await svc.list_batches(db, ISSUER_RFC)

    assert [(b.batch, b.type, b.in_use) for b in listed] == [('B', 200, False), ('Z', 0, True)]


async def test_create_writes_legacy_json_that_printing_reads(db: AsyncSession) -> None:
    accounts = [
        {'bank': 'BBVA', 'account': '0123', 'clabe': '012180001234567891', 'currency': 'MXN'}
    ]
    batch = await _create(db, logo='~/Content/images/x.png', footer_height=20, extra_info=accounts)

    stored = json.loads(batch.template)
    assert stored == {
        'Name': 'T02Blue',
        'Logo': '~/Content/images/x.png',
        'HeaderHeight': 0,
        'FooterHeight': 20,
        'ExtraInfo': [
            {'Bank': 'BBVA', 'Account': '0123', 'CLABE': '012180001234567891', 'Currency': 'MXN'}
        ],
    }
    settings = batch_settings(batch.template)
    assert (settings.logo_name, settings.footer_height_mm) == ('x.png', 20)
    assert settings.accounts[0]['CLABE'] == '012180001234567891'
    [listed] = await svc.list_batches(db, ISSUER_RFC)
    assert listed.template.extra_info[0].clabe == '012180001234567891'


async def test_a_legacy_single_quoted_template_reads_back(db: AsyncSession) -> None:
    db.add(TaxpayerBatch(taxpayer=ISSUER_RFC, batch='L', type=0, template=LEGACY_BATCH_TEMPLATE))
    await db.commit()

    [listed] = await svc.list_batches(db, ISSUER_RFC)

    assert listed.template.model_dump() == {
        'name': 'T02Blue',
        'logo': '~/Content/images/casamaestra.png',
        'header_height': 0,
        'footer_height': 15,
        'extra_info': [],
    }


async def test_a_series_is_unique_per_issuer(db: AsyncSession) -> None:
    await _create(db, batch='F')

    await _status(409, _create(db, batch='F', type_=200))
    db.add(TaxpayerIssuer(taxpayer_issuer_id=OTHER, name='Otro', regime='601', provider=0))
    await db.commit()
    assert (await _create(db, rfc=OTHER, batch='F')).taxpayer == OTHER


async def test_create_for_an_unknown_issuer_is_404(db: AsyncSession) -> None:
    await _status(404, _create(db, rfc='NOP010101NOP'))


async def test_a_batch_in_use_keeps_its_series_and_type(db: AsyncSession) -> None:
    batch = await _create(db, batch='F')
    await seed_fiscal_document(db, 'invoice', issuer=ISSUER_RFC, batch='F')

    for change in (_body(batch='G'), _body(type_=100)):
        await _status(409, svc.update_batch(db, batch, TaxpayerBatchUpdate.model_validate(change)))
    updated = await svc.update_batch(
        db, batch, TaxpayerBatchUpdate.model_validate(_body(footer_height=30))
    )
    assert (updated.batch, updated.type, updated.template.footer_height) == ('F', 0, 30)
    await _status(409, svc.delete_batch(db, batch))


async def test_a_batch_not_in_use_changes_freely(db: AsyncSession) -> None:
    batch = await _create(db, batch='F')
    await _create(db, batch='H')
    # Another issuer's documents in series F do not count.
    await seed_fiscal_document(db, 'invoice', batch='F')

    updated = await svc.update_batch(
        db, batch, TaxpayerBatchUpdate.model_validate(_body(batch='G', type_=100))
    )
    assert (updated.batch, updated.type) == ('G', 100)
    await _status(
        409, svc.update_batch(db, batch, TaxpayerBatchUpdate.model_validate(_body(batch='H')))
    )
    await svc.delete_batch(db, batch)
    assert [b.batch for b in await svc.list_batches(db, ISSUER_RFC)] == ['H']


async def test_get_batch_belongs_to_its_issuer(db: AsyncSession) -> None:
    batch = await _create(db, batch='F')

    assert (await svc.get_batch(db, ISSUER_RFC, batch.taxpayer_batch_id)) is not None
    assert await svc.get_batch(db, OTHER, batch.taxpayer_batch_id) is None
    assert await svc.get_batch(db, ISSUER_RFC, 999999) is None
