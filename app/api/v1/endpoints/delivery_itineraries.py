import json
from datetime import date as date_type

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, ItineraryStatus, SystemObject
from app.schemas import ListResponse
from app.schemas.delivery_itinerary import (
    CommitLineRequest,
    CommitLineUpdate,
    CommitOrderRequest,
    ItineraryCreate,
    ItineraryLineResponse,
    ItineraryResponse,
    ItineraryStopResponse,
    ItinerarySummary,
    ItineraryUpdate,
    PendingDeliveriesResponse,
    PendingDeliveryBucket,
    PendingDeliveryLine,
    StopCreate,
)
from app.services import delivery_itinerary_service, image_service

router = APIRouter()

_FOR_DELIVER = require_privilege(SystemObject.FOR_DELIVER, AccessRight.READ)
_READ = require_privilege(SystemObject.DELIVERY_ITINERARIES, AccessRight.READ)
_CREATE = require_privilege(SystemObject.DELIVERY_ITINERARIES, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.DELIVERY_ITINERARIES, AccessRight.UPDATE)

_BUCKET_DATES = {
    'earlier': None,
    'yesterday': -1,
    'today': 0,
    'tomorrow': 1,
    'day_after': 2,
    'later': None,
}


async def _itinerary_or_404(db: AsyncSession, itinerary_id: int):  # noqa: ANN202
    itinerary = await delivery_itinerary_service.get_itinerary(db, itinerary_id)
    if itinerary is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Itinerary not found')
    return itinerary


async def _stop_or_404(db: AsyncSession, itinerary, stop_id: int):  # noqa: ANN001, ANN202
    for stop in await delivery_itinerary_service.stops_of(db, itinerary.deliveries_itinerary_id):
        if stop.deliveries_itinerary_stop_id == stop_id:
            return stop
    raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Stop not found')


async def _full(  # noqa: ANN001
    db: AsyncSession, itinerary, warnings: list[str] | None = None
) -> ItineraryResponse:
    body = ItineraryResponse.model_validate(itinerary)
    body.warnings = warnings or []
    body.stops = []
    for stop in await delivery_itinerary_service.stops_of(db, itinerary.deliveries_itinerary_id):
        rendered = ItineraryStopResponse.model_validate(stop)
        rendered.lines = [
            ItineraryLineResponse.model_validate(line)
            for line in await delivery_itinerary_service.lines_of_stop(
                db, stop.deliveries_itinerary_stop_id
            )
        ]
        body.stops.append(rendered)
    return body


# ── Pending deliveries ────────────────────────────────────────────────────────
#
# Registered before `/{itinerary_id}` so "deliveries" is not matched as an id.


@router.get('/deliveries', response_model=PendingDeliveriesResponse)
async def pending_deliveries(
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=100),
    current: CurrentUser = Depends(_FOR_DELIVER),
    db: AsyncSession = Depends(get_db),
) -> PendingDeliveriesResponse:
    """Six buckets, always present, possibly empty. Paging applies per bucket (FR-030 – FR-032)."""
    today = date_type.today()
    grouped = await delivery_itinerary_service.pending_deliveries(db, current=current, today=today)

    from datetime import timedelta

    buckets = []
    for key, items in grouped.items():
        offset = _BUCKET_DATES[key]
        buckets.append(
            PendingDeliveryBucket(
                key=key,
                date=(today + timedelta(days=offset)) if offset is not None else None,
                items=[PendingDeliveryLine(**item) for item in items[skip : skip + limit]],
                total=len(items),
            )
        )
    return PendingDeliveriesResponse(buckets=buckets)


# ── Itineraries ───────────────────────────────────────────────────────────────


@router.get('', response_model=ListResponse[ItinerarySummary])
async def list_itineraries(
    date_from: date_type | None = Query(None),
    date_to: date_type | None = Query(None),
    vehicle: int | None = Query(None),
    vehicle_operator: int | None = Query(None),
    warehouse: int | None = Query(None),
    itinerary_status: ItineraryStatus | None = Query(None, alias='status'),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[ItinerarySummary]:
    items, total = await delivery_itinerary_service.list_itineraries(
        db,
        date_from=date_from,
        date_to=date_to,
        vehicle=vehicle,
        vehicle_operator=vehicle_operator,
        warehouse=warehouse,
        itinerary_status=itinerary_status,
        skip=skip,
        limit=limit,
    )
    return ListResponse(items=[ItinerarySummary.model_validate(i) for i in items], total=total)


@router.post('', response_model=ItineraryResponse, status_code=status.HTTP_201_CREATED)
async def create_itinerary(
    data: ItineraryCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    itinerary, warnings = await delivery_itinerary_service.create_itinerary(
        db,
        current=current,
        date=data.date,
        vehicle=data.vehicle,
        vehicle_operator=data.vehicle_operator,
        warehouse=data.warehouse,
        comment=data.comment,
    )
    return await _full(db, itinerary, warnings)


@router.get('/{itinerary_id}', response_model=ItineraryResponse)
async def get_itinerary(
    itinerary_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    return await _full(db, await _itinerary_or_404(db, itinerary_id))


@router.put('/{itinerary_id}', response_model=ItineraryResponse)
async def update_itinerary(
    itinerary_id: int,
    data: ItineraryUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    itinerary = await _itinerary_or_404(db, itinerary_id)
    itinerary = await delivery_itinerary_service.update_itinerary(
        db, itinerary, data, current=current
    )
    return await _full(db, itinerary)


@router.post('/{itinerary_id}/cancel', response_model=ItineraryResponse)
async def cancel_itinerary(
    itinerary_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    itinerary = await _itinerary_or_404(db, itinerary_id)
    itinerary = await delivery_itinerary_service.cancel_itinerary(db, itinerary, current=current)
    return await _full(db, itinerary)


@router.post('/{itinerary_id}/depart', response_model=ItineraryResponse)
async def depart(
    itinerary_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    itinerary = await _itinerary_or_404(db, itinerary_id)
    itinerary = await delivery_itinerary_service.depart(db, itinerary, current=current)
    return await _full(db, itinerary)


# ── Stops and commitments ─────────────────────────────────────────────────────


@router.post('/{itinerary_id}/stops', response_model=ItineraryResponse)
async def add_stop(
    itinerary_id: int,
    data: StopCreate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    itinerary = await _itinerary_or_404(db, itinerary_id)
    await delivery_itinerary_service.add_stop(
        db, itinerary, data.delivery_order, comment=data.comment
    )
    return await _full(db, itinerary)


@router.delete('/{itinerary_id}/stops/{stop_id}', response_model=ItineraryResponse)
async def remove_stop(
    itinerary_id: int,
    stop_id: int,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    itinerary = await _itinerary_or_404(db, itinerary_id)
    await delivery_itinerary_service.remove_stop(db, itinerary, stop_id)
    return await _full(db, itinerary)


@router.post('/{itinerary_id}/stops/{stop_id}/lines', response_model=ItineraryResponse)
async def commit_line(
    itinerary_id: int,
    stop_id: int,
    data: CommitLineRequest,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    """Takes the row lock. Exactly one of two concurrent callers wins (SC-004)."""
    itinerary = await _itinerary_or_404(db, itinerary_id)
    stop = await _stop_or_404(db, itinerary, stop_id)
    await delivery_itinerary_service.commit_line(
        db,
        itinerary,
        stop,
        delivery_order_detail=data.delivery_order_detail,
        quantity=data.quantity,
        comment=data.comment,
    )
    return await _full(db, itinerary)


@router.post('/{itinerary_id}/stops/{stop_id}/lines/all', response_model=ItineraryResponse)
async def commit_whole_order(
    itinerary_id: int,
    stop_id: int,
    data: CommitOrderRequest,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    itinerary = await _itinerary_or_404(db, itinerary_id)
    stop = await _stop_or_404(db, itinerary, stop_id)
    await delivery_itinerary_service.commit_whole_order(db, itinerary, stop, data.delivery_order)
    return await _full(db, itinerary)


@router.put('/{itinerary_id}/stops/{stop_id}/lines/{line_id}', response_model=ItineraryResponse)
async def adjust_commitment(
    itinerary_id: int,
    stop_id: int,
    line_id: int,
    data: CommitLineUpdate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    itinerary = await _itinerary_or_404(db, itinerary_id)
    await _stop_or_404(db, itinerary, stop_id)
    await delivery_itinerary_service.adjust_commitment(db, itinerary, line_id, data.quantity)
    return await _full(db, itinerary)


@router.delete('/{itinerary_id}/stops/{stop_id}/lines/{line_id}', response_model=ItineraryResponse)
async def release_commitment(
    itinerary_id: int,
    stop_id: int,
    line_id: int,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    itinerary = await _itinerary_or_404(db, itinerary_id)
    await _stop_or_404(db, itinerary, stop_id)
    await delivery_itinerary_service.release_commitment(db, itinerary, line_id)
    return await _full(db, itinerary)


# ── Closing a stop ────────────────────────────────────────────────────────────


@router.post('/{itinerary_id}/stops/{stop_id}/close', response_model=ItineraryResponse)
async def close_stop(
    itinerary_id: int,
    stop_id: int,
    receiver_name: str = Form(...),
    receiver_id_shown: str = Form(...),
    lines: str = Form(...),
    image: UploadFile = File(...),
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ItineraryResponse:
    """Proof plus per-line outcome, settled in one transaction.

    `lines` is JSON in a multipart field because the signature travels with it: splitting the
    evidence from the quantities across two calls would allow a stop closed without proof.
    """
    itinerary = await _itinerary_or_404(db, itinerary_id)
    stop = await _stop_or_404(db, itinerary, stop_id)

    try:
        outcomes = json.loads(lines)
    except json.JSONDecodeError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='`lines` must be a JSON array of per-line outcomes',
        ) from e

    try:
        filename = await image_service.save_proof_image(await image.read(), settings.pod_dir)
    except ValueError as e:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(e)
        ) from e

    await delivery_itinerary_service.close_stop(
        db,
        itinerary,
        stop,
        outcomes=outcomes,
        receiver_name=receiver_name,
        receiver_id_shown=receiver_id_shown,
        image_file=filename,
        current=current,
    )
    await db.refresh(itinerary)
    return await _full(db, itinerary)
