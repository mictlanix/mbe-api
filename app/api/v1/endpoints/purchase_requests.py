from datetime import date

from fastapi import APIRouter, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.core.errors import not_found
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.models.purchases import PurchaseRequest
from app.schemas import ListResponse
from app.schemas.purchases import (
    BulkApprove,
    BulkApproveResponse,
    PurchaseRequestCreate,
    PurchaseRequestResponse,
    PurchaseRequestSummary,
    PurchaseRequestUpdate,
    RejectRequest,
    RequestLineAdd,
    RequestLineUpdate,
    ToPurchaseSet,
)
from app.services import purchase_request_service as service

router = APIRouter()

_READ = require_privilege(SystemObject.PURCHASE_REQUEST, AccessRight.READ)
_CREATE = require_privilege(SystemObject.PURCHASE_REQUEST, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.PURCHASE_REQUEST, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.PURCHASE_REQUEST, AccessRight.DELETE)
_QUEUE = require_privilege(SystemObject.PURCHASE_REQUEST_APPROVAL, AccessRight.READ)
_DECIDE = require_privilege(SystemObject.PURCHASE_REQUEST_APPROVAL, AccessRight.UPDATE)
# Marking lines to purchase is the purchasing agent's job (FR-011).
_MARK = require_privilege(SystemObject.PURCHASES_ORDERS, AccessRight.UPDATE)


async def _request_or_404(db: AsyncSession, request_id: int):  # noqa: ANN202
    """Header only: the state-changing services read lines after they lock (research R3)."""
    request = await service.get_request(db, request_id)
    if request is None:
        raise not_found(PurchaseRequest, request_id)
    return request


async def _response(db: AsyncSession, request) -> PurchaseRequestResponse:  # noqa: ANN001
    return PurchaseRequestResponse.model_validate(await service.attach(db, request))


@router.get('', response_model=ListResponse[PurchaseRequestSummary])
async def list_purchase_requests(
    everyone: bool = Query(False, alias='all'),
    request_status: str | None = Query(None, alias='status'),
    warehouse: int | None = Query(None),
    facility: int | None = Query(None),
    creator: int | None = Query(None),
    serial: int | None = Query(None),
    request_id: int | None = Query(None, alias='id'),
    date_from: date | None = Query(None, alias='from'),
    date_to: date | None = Query(None, alias='to'),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[PurchaseRequestSummary]:
    items, total = await service.list_requests(
        db,
        current=current,
        everyone=everyone,
        state=request_status,
        warehouse=warehouse,
        facility=facility,
        creator=creator,
        serial=serial,
        request_id=request_id,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[PurchaseRequestSummary.model_validate(item) for item in items], total=total
    )


@router.post('', response_model=PurchaseRequestResponse, status_code=status.HTTP_201_CREATED)
async def create_purchase_request(
    data: PurchaseRequestCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    return await _response(db, await service.create(db, data, current=current))


@router.get('/approval-queue', response_model=ListResponse[PurchaseRequestSummary])
async def purchase_request_approval_queue(
    facility: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_QUEUE),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[PurchaseRequestSummary]:
    """Submitted requests of every facility, oldest first (FR-007)."""
    items, total = await service.approval_queue(db, facility=facility, skip=skip, limit=limit)
    return ListResponse(
        items=[PurchaseRequestSummary.model_validate(item) for item in items], total=total
    )


@router.post('/approve', response_model=BulkApproveResponse)
async def approve_purchase_requests(
    data: BulkApprove,
    current: CurrentUser = Depends(_DECIDE),
    db: AsyncSession = Depends(get_db),
) -> BulkApproveResponse:
    """Each request approved or refused on its own, with the reason (FR-005)."""
    results = await service.approve_many(db, data.ids, current=current)
    return BulkApproveResponse.model_validate({'results': results})


@router.get('/{request_id}', response_model=PurchaseRequestResponse)
async def get_purchase_request(
    request_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    return await _response(db, await _request_or_404(db, request_id))


@router.put('/{request_id}', response_model=PurchaseRequestResponse)
async def update_purchase_request(
    request_id: int,
    data: PurchaseRequestUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    await _request_or_404(db, request_id)
    return await _response(db, await service.update(db, request_id, data, current=current))


@router.post('/{request_id}/lines', response_model=PurchaseRequestResponse)
async def add_purchase_request_line(
    request_id: int,
    data: RequestLineAdd,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    await _request_or_404(db, request_id)
    return await _response(db, await service.add_line(db, request_id, data, current=current))


@router.put('/{request_id}/lines/{line_id}', response_model=PurchaseRequestResponse)
async def update_purchase_request_line(
    request_id: int,
    line_id: int,
    data: RequestLineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    await _request_or_404(db, request_id)
    request = await service.update_line(db, request_id, line_id, data, current=current)
    return await _response(db, request)


@router.delete('/{request_id}/lines/{line_id}', response_model=PurchaseRequestResponse)
async def remove_purchase_request_line(
    request_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    await _request_or_404(db, request_id)
    request = await service.remove_line(db, request_id, line_id, current=current)
    return await _response(db, request)


@router.post('/{request_id}/submit', response_model=PurchaseRequestResponse)
async def submit_purchase_request(
    request_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    await _request_or_404(db, request_id)
    return await _response(db, await service.submit(db, request_id, current=current))


@router.post('/{request_id}/cancel', response_model=PurchaseRequestResponse)
async def cancel_purchase_request(
    request_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    await _request_or_404(db, request_id)
    return await _response(db, await service.cancel(db, request_id, current=current))


@router.put('/{request_id}/lines/{line_id}/to-purchase', response_model=PurchaseRequestResponse)
async def mark_purchase_request_line(
    request_id: int,
    line_id: int,
    data: ToPurchaseSet,
    current: CurrentUser = Depends(_MARK),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    await _request_or_404(db, request_id)
    request = await service.mark(
        db, request_id, line_id, to_purchase=data.to_purchase, current=current
    )
    return await _response(db, request)


@router.post('/{request_id}/approve', response_model=PurchaseRequestResponse)
async def approve_purchase_request(
    request_id: int,
    current: CurrentUser = Depends(_DECIDE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    await _request_or_404(db, request_id)
    return await _response(db, await service.approve(db, request_id, current=current))


@router.post('/{request_id}/reject', response_model=PurchaseRequestResponse)
async def reject_purchase_request(
    request_id: int,
    data: RejectRequest,
    current: CurrentUser = Depends(_DECIDE),
    db: AsyncSession = Depends(get_db),
) -> PurchaseRequestResponse:
    await _request_or_404(db, request_id)
    return await _response(db, await service.reject(db, request_id, data, current=current))
