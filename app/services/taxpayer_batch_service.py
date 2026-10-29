"""Taxpayer batches: an issuer's series, with document type and print template (#44, spec 027).

A batch is "in use" once any fiscal document of its issuer carries its series. From then on its
series and type are fixed, because existing documents and their serials are tied to them; the
print template can still change (FR-001, FR-002).
"""

import json

from fastapi import HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.models.fiscal import FiscalDocument, TaxpayerBatch, TaxpayerIssuer
from app.schemas.fiscal import (
    BatchAccount,
    BatchTemplate,
    TaxpayerBatchCreate,
    TaxpayerBatchResponse,
    TaxpayerBatchUpdate,
)
from app.services.cfdi import template_values

_ACCOUNT_KEYS = {'bank': 'Bank', 'account': 'Account', 'clabe': 'CLABE', 'currency': 'Currency'}


def read_template(template: str | None) -> BatchTemplate:
    """Legacy's single-quoted text or this API's JSON, field by field with defaults."""
    values = template_values(template)

    def number(key: str, default: int) -> int:
        value = values.get(key)
        ok = isinstance(value, int | float) and not isinstance(value, bool) and value >= 0
        return int(value) if ok else default

    extra = values.get('ExtraInfo')
    return BatchTemplate(
        name=str(values.get('Name') or 'T02Blue'),
        logo=values.get('Logo') if isinstance(values.get('Logo'), str) else None,
        header_height=number('HeaderHeight', 0),
        footer_height=number('FooterHeight', 15),
        extra_info=[
            BatchAccount(
                **{field: str(item.get(key) or '') for field, key in _ACCOUNT_KEYS.items()}
            )
            for item in (extra if isinstance(extra, list) else [])
            if isinstance(item, dict)
        ],
    )


def write_template(template: BatchTemplate) -> str:
    """Legacy's keys, as JSON both Newtonsoft and `ast.literal_eval` read: no `null` is written."""
    values: dict[str, object] = {'Name': template.name}
    if template.logo:
        values['Logo'] = template.logo
    values['HeaderHeight'] = template.header_height
    values['FooterHeight'] = template.footer_height
    values['ExtraInfo'] = [
        {key: getattr(account, field) for field, key in _ACCOUNT_KEYS.items()}
        for account in template.extra_info
    ]
    return json.dumps(values, ensure_ascii=False, indent=4)


async def _in_use(db: AsyncSession, taxpayer: str, batch: str) -> bool:
    query = select(FiscalDocument.fiscal_document_id).where(
        FiscalDocument.issuer == taxpayer, FiscalDocument.batch == batch
    )
    return (await db.execute(query.limit(1))).first() is not None


async def _response(db: AsyncSession, batch: TaxpayerBatch) -> TaxpayerBatchResponse:
    return TaxpayerBatchResponse(
        taxpayer_batch_id=batch.taxpayer_batch_id,
        taxpayer=batch.taxpayer,
        batch=batch.batch,
        type=batch.type,
        template=read_template(batch.template),
        in_use=await _in_use(db, batch.taxpayer, batch.batch),
    )


async def _assert_series_free(
    db: AsyncSession, taxpayer: str, series: str, *, exclude: int | None = None
) -> None:
    query = select(TaxpayerBatch.taxpayer_batch_id).where(
        TaxpayerBatch.taxpayer == taxpayer, TaxpayerBatch.batch == series
    )
    if exclude is not None:
        query = query.where(TaxpayerBatch.taxpayer_batch_id != exclude)
    if (await db.execute(query)).first() is not None:
        raise DomainError(
            ErrorCode.RESOURCE_ALREADY_EXISTS,
            'The issuer already has this series',
            {'resource': 'taxpayer_batch', 'field': 'batch'},
        )


async def list_batches(db: AsyncSession, taxpayer: str) -> list[TaxpayerBatchResponse]:
    query = (
        select(TaxpayerBatch)
        .where(TaxpayerBatch.taxpayer == taxpayer)
        .order_by(TaxpayerBatch.batch, TaxpayerBatch.taxpayer_batch_id)
    )
    return [await _response(db, b) for b in (await db.execute(query)).scalars()]


async def get_batch(db: AsyncSession, taxpayer: str, batch_id: int) -> TaxpayerBatch | None:
    batch = await db.get(TaxpayerBatch, batch_id)
    return batch if batch is not None and batch.taxpayer == taxpayer else None


async def create_batch(
    db: AsyncSession, taxpayer: str, data: TaxpayerBatchCreate
) -> TaxpayerBatchResponse:
    if await db.get(TaxpayerIssuer, taxpayer) is None:
        raise not_found(TaxpayerIssuer, taxpayer)
    await _assert_series_free(db, taxpayer, data.batch)
    batch = TaxpayerBatch(
        taxpayer=taxpayer, batch=data.batch, type=data.type, template=write_template(data.template)
    )
    db.add(batch)
    await db.commit()
    return await _response(db, batch)


async def update_batch(
    db: AsyncSession, batch: TaxpayerBatch, data: TaxpayerBatchUpdate
) -> TaxpayerBatchResponse:
    renamed = data.batch != batch.batch
    if (renamed or data.type != batch.type) and await _in_use(db, batch.taxpayer, batch.batch):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='The series is in use: only its template can change',
        )
    if renamed:
        await _assert_series_free(db, batch.taxpayer, data.batch, exclude=batch.taxpayer_batch_id)
    batch.batch = data.batch
    batch.type = data.type
    batch.template = write_template(data.template)
    await db.commit()
    return await _response(db, batch)


async def delete_batch(db: AsyncSession, batch: TaxpayerBatch) -> None:
    if await _in_use(db, batch.taxpayer, batch.batch):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='The series is in use and cannot be deleted',
        )
    await db.delete(batch)
    await db.commit()
