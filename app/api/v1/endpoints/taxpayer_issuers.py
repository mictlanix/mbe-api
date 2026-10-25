from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.models.fiscal import TaxpayerBatch
from app.schemas import ListResponse
from app.schemas.fiscal import (
    TaxpayerBatchCreate,
    TaxpayerBatchResponse,
    TaxpayerBatchUpdate,
    TaxpayerIssuerCreate,
    TaxpayerIssuerResponse,
    TaxpayerIssuerUpdate,
)
from app.services import taxpayer_batch_service, taxpayer_issuer_service

router = APIRouter()


@router.get('', response_model=ListResponse[TaxpayerIssuerResponse])
async def list_taxpayer_issuers(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[TaxpayerIssuerResponse]:
    items, total = await taxpayer_issuer_service.list_taxpayer_issuers(
        db, search=search, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=TaxpayerIssuerResponse, status_code=status.HTTP_201_CREATED)
async def create_taxpayer_issuer(
    data: TaxpayerIssuerCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerIssuerResponse:
    issuer = await taxpayer_issuer_service.create_taxpayer_issuer(db, data)
    return TaxpayerIssuerResponse.model_validate(issuer)


@router.get('/{rfc}', response_model=TaxpayerIssuerResponse)
async def get_taxpayer_issuer(
    rfc: str,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerIssuerResponse:
    issuer = await taxpayer_issuer_service.get_taxpayer_issuer(db, rfc)
    if issuer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer issuer not found'
        )
    return TaxpayerIssuerResponse.model_validate(issuer)


@router.put('/{rfc}', response_model=TaxpayerIssuerResponse)
async def update_taxpayer_issuer(
    rfc: str,
    data: TaxpayerIssuerUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerIssuerResponse:
    issuer = await taxpayer_issuer_service.get_taxpayer_issuer(db, rfc)
    if issuer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer issuer not found'
        )
    issuer = await taxpayer_issuer_service.update_taxpayer_issuer(db, issuer, data)
    return TaxpayerIssuerResponse.model_validate(issuer)


@router.delete('/{rfc}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_taxpayer_issuer(
    rfc: str,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    issuer = await taxpayer_issuer_service.get_taxpayer_issuer(db, rfc)
    if issuer is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer issuer not found'
        )
    await taxpayer_issuer_service.delete_taxpayer_issuer(db, issuer)


# ── Batches (#44, spec 027) ───────────────────────────────────────────────────


async def _issuer_or_404(db: AsyncSession, rfc: str) -> None:
    if await taxpayer_issuer_service.get_taxpayer_issuer(db, rfc) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Taxpayer issuer not found'
        )


async def _batch_or_404(db: AsyncSession, rfc: str, batch_id: int) -> TaxpayerBatch:
    batch = await taxpayer_batch_service.get_batch(db, rfc, batch_id)
    if batch is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Batch not found')
    return batch


@router.get('/{rfc}/batches', response_model=list[TaxpayerBatchResponse])
async def list_taxpayer_batches(
    rfc: str,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> list[TaxpayerBatchResponse]:
    """The issuer's series, by series. `in_use` says whether any of its documents carries it."""
    await _issuer_or_404(db, rfc)
    return await taxpayer_batch_service.list_batches(db, rfc)


@router.post(
    '/{rfc}/batches', response_model=TaxpayerBatchResponse, status_code=status.HTTP_201_CREATED
)
async def create_taxpayer_batch(
    rfc: str,
    data: TaxpayerBatchCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerBatchResponse:
    return await taxpayer_batch_service.create_batch(db, rfc, data)


@router.put('/{rfc}/batches/{taxpayer_batch_id}', response_model=TaxpayerBatchResponse)
async def update_taxpayer_batch(
    rfc: str,
    taxpayer_batch_id: int,
    data: TaxpayerBatchUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> TaxpayerBatchResponse:
    """A series in use keeps its series text and type; its template can still change."""
    batch = await _batch_or_404(db, rfc, taxpayer_batch_id)
    return await taxpayer_batch_service.update_batch(db, batch, data)


@router.delete('/{rfc}/batches/{taxpayer_batch_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_taxpayer_batch(
    rfc: str,
    taxpayer_batch_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.TAXPAYERS, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    batch = await _batch_or_404(db, rfc, taxpayer_batch_id)
    await taxpayer_batch_service.delete_batch(db, batch)
