import os
from datetime import datetime

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, DeliveryOrderStatus, FulfillmentType, SystemObject
from app.models.logistics import ProofOfDelivery
from app.schemas import ListResponse
from app.schemas.delivery_order import (
    DeliveryOrderCreate,
    DeliveryOrderEventResponse,
    DeliveryOrderLineResponse,
    DeliveryOrderLineUpdate,
    DeliveryOrderResponse,
    DeliveryOrderSummary,
    DeliveryOrderUpdate,
    ProofOfDeliveryResponse,
    ReasonRequest,
)
from app.services import delivery_order_service, image_service

router = APIRouter()

_READ = require_privilege(SystemObject.DELIVERY_ORDERS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.DELIVERY_ORDERS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.DELIVERY_ORDERS, AccessRight.UPDATE)
_APPROVAL_READ = require_privilege(SystemObject.DELIVERY_ORDER_APPROVAL, AccessRight.READ)
_APPROVAL_UPDATE = require_privilege(SystemObject.DELIVERY_ORDER_APPROVAL, AccessRight.UPDATE)


async def _order_or_404(db: AsyncSession, delivery_order_id: int):  # noqa: ANN202
    order = await delivery_order_service.get_order(db, delivery_order_id)
    if order is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Delivery order not found'
        )
    return order


async def _with_lines(db: AsyncSession, order) -> DeliveryOrderResponse:  # noqa: ANN001
    body = DeliveryOrderResponse.model_validate(order)
    body.lines = [
        DeliveryOrderLineResponse.model_validate(line)
        for line in await delivery_order_service.lines_of(db, order.delivery_order_id)
    ]
    return body


# ── Approval queue ────────────────────────────────────────────────────────────
#
# Registered before `/{delivery_order_id}` so FastAPI does not match "approval" as an id — the
# same trap the existing `/sales-orders/product-lookup` route avoids.


@router.get('/approval', response_model=ListResponse[DeliveryOrderSummary])
async def list_approval_queue(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_APPROVAL_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[DeliveryOrderSummary]:
    """Exactly the orders awaiting a decision (FR-021)."""
    items, total = await delivery_order_service.list_orders(
        db,
        current=current,
        order_status=DeliveryOrderStatus.PENDING_APPROVAL,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[DeliveryOrderSummary.model_validate(o) for o in items], total=total
    )


@router.post('/approval/{delivery_order_id}/approve', response_model=DeliveryOrderResponse)
async def approve_delivery_order(
    delivery_order_id: int,
    current: CurrentUser = Depends(_APPROVAL_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await _order_or_404(db, delivery_order_id)
    order = await delivery_order_service.approve(db, order, current=current)
    return await _with_lines(db, order)


@router.post('/approval/{delivery_order_id}/reject', response_model=DeliveryOrderResponse)
async def reject_delivery_order(
    delivery_order_id: int,
    data: ReasonRequest,
    current: CurrentUser = Depends(_APPROVAL_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await _order_or_404(db, delivery_order_id)
    order = await delivery_order_service.reject(db, order, data.reason, current=current)
    return await _with_lines(db, order)


# ── Delivery orders ───────────────────────────────────────────────────────────


@router.get('', response_model=ListResponse[DeliveryOrderSummary])
async def list_delivery_orders(
    order_status: DeliveryOrderStatus | None = Query(None, alias='status'),
    customer: int | None = Query(None),
    facility: int | None = Query(None),
    fulfillment_type: FulfillmentType | None = Query(None),
    date_from: datetime | None = Query(None),
    date_to: datetime | None = Query(None),
    mine: bool = Query(False),
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[DeliveryOrderSummary]:
    """`mine` is how an author finds a rejected draft — no notification is sent (FR-067)."""
    items, total = await delivery_order_service.list_orders(
        db,
        current=current,
        order_status=order_status,
        customer=customer,
        facility=facility,
        fulfillment_type=fulfillment_type,
        date_from=date_from,
        date_to=date_to,
        mine=mine,
        search=search,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[DeliveryOrderSummary.model_validate(o) for o in items], total=total
    )


@router.post('', response_model=DeliveryOrderResponse, status_code=status.HTTP_201_CREATED)
async def create_delivery_order(
    data: DeliveryOrderCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await delivery_order_service.create_from_sales_order(
        db, data.sales_order, current=current, fulfillment_type=data.fulfillment_type
    )
    return await _with_lines(db, order)


@router.get('/{delivery_order_id}', response_model=DeliveryOrderResponse)
async def get_delivery_order(
    delivery_order_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    return await _with_lines(db, await _order_or_404(db, delivery_order_id))


@router.put('/{delivery_order_id}', response_model=DeliveryOrderResponse)
async def update_delivery_order(
    delivery_order_id: int,
    data: DeliveryOrderUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await _order_or_404(db, delivery_order_id)
    order = await delivery_order_service.update_order(db, order, data, current=current)
    return await _with_lines(db, order)


@router.put('/{delivery_order_id}/lines/{line_id}', response_model=DeliveryOrderResponse)
async def update_delivery_order_line(
    delivery_order_id: int,
    line_id: int,
    data: DeliveryOrderLineUpdate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await _order_or_404(db, delivery_order_id)
    await delivery_order_service.update_line(db, order, line_id, data.quantity)
    return await _with_lines(db, order)


@router.delete('/{delivery_order_id}/lines/{line_id}', response_model=DeliveryOrderResponse)
async def delete_delivery_order_line(
    delivery_order_id: int,
    line_id: int,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await _order_or_404(db, delivery_order_id)
    await delivery_order_service.delete_line(db, order, line_id)
    return await _with_lines(db, order)


@router.post('/{delivery_order_id}/confirm', response_model=DeliveryOrderResponse)
async def confirm_delivery_order(
    delivery_order_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await _order_or_404(db, delivery_order_id)
    order = await delivery_order_service.confirm(db, order, current=current)
    return await _with_lines(db, order)


@router.post('/{delivery_order_id}/cancel', response_model=DeliveryOrderResponse)
async def cancel_delivery_order(
    delivery_order_id: int,
    data: ReasonRequest,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await _order_or_404(db, delivery_order_id)
    order = await delivery_order_service.cancel(db, order, data.reason, current=current)
    return await _with_lines(db, order)


@router.post('/{delivery_order_id}/requeue', response_model=DeliveryOrderResponse)
async def requeue_delivery_order(
    delivery_order_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await _order_or_404(db, delivery_order_id)
    order = await delivery_order_service.requeue(db, order, current=current)
    return await _with_lines(db, order)


@router.get('/{delivery_order_id}/events', response_model=list[DeliveryOrderEventResponse])
async def list_delivery_order_events(
    delivery_order_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[DeliveryOrderEventResponse]:
    await _order_or_404(db, delivery_order_id)
    events = await delivery_order_service.events_of(db, delivery_order_id)
    return [DeliveryOrderEventResponse.model_validate(e) for e in events]


# ── Counter pickup ────────────────────────────────────────────────────────────


@router.post('/{delivery_order_id}/ready-for-pickup', response_model=DeliveryOrderResponse)
async def mark_ready_for_pickup(
    delivery_order_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    order = await _order_or_404(db, delivery_order_id)
    order = await delivery_order_service.mark_ready_for_pickup(db, order, current=current)
    return await _with_lines(db, order)


@router.post('/{delivery_order_id}/pickup', response_model=DeliveryOrderResponse)
async def confirm_pickup(
    delivery_order_id: int,
    receiver_name: str = Form(...),
    receiver_id_shown: str = Form(...),
    image: UploadFile = File(...),
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> DeliveryOrderResponse:
    """Same evidentiary standard as a delivery — this is what settles disputes (v2 §5)."""
    order = await _order_or_404(db, delivery_order_id)
    try:
        filename = await image_service.save_proof_image(await image.read(), settings.pod_dir)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)
        ) from e

    order = await delivery_order_service.confirm_pickup(
        db,
        order,
        receiver_name=receiver_name,
        receiver_id_shown=receiver_id_shown,
        image_file=filename,
        current=current,
    )
    return await _with_lines(db, order)


# ── Proof of delivery ─────────────────────────────────────────────────────────


async def _proof_or_404(db: AsyncSession, order) -> ProofOfDelivery:  # noqa: ANN001
    if order.proof_of_delivery is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail='This delivery order has no proof of delivery yet',
        )
    proof = await db.get(ProofOfDelivery, order.proof_of_delivery)
    if proof is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Proof not found')
    return proof


@router.get('/{delivery_order_id}/proof', response_model=ProofOfDeliveryResponse)
async def get_proof(
    delivery_order_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ProofOfDeliveryResponse:
    order = await _order_or_404(db, delivery_order_id)
    return ProofOfDeliveryResponse.model_validate(await _proof_or_404(db, order))


@router.get('/{delivery_order_id}/proof/image')
async def get_proof_image(
    delivery_order_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Streamed behind the privilege check, never a static URL.

    A signature paired with a name and an address is personal data. The `/images` mount is
    unauthenticated — correct for product photos, wrong for this (FR-044a).
    """
    order = await _order_or_404(db, delivery_order_id)
    proof = await _proof_or_404(db, order)

    path = os.path.join(settings.pod_dir, proof.image_file)
    if not os.path.exists(path):
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail='Proof image is missing from storage'
        )
    return FileResponse(path, media_type='image/png')
