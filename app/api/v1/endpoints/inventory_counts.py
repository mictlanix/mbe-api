from fastapi import APIRouter, Body, Depends, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.models.inventory import InventoryCount
from app.schemas import ListResponse
from app.schemas.inventory import (
    CountConfirm,
    CountCreate,
    CountLinesSet,
    CountLineUpdate,
    CountUpdate,
    InventoryCountConfirmResponse,
    InventoryCountResponse,
    InventoryCountSummary,
)
from app.services import inventory_count_service
from app.services import inventory_documents as inv

router = APIRouter()

_READ = require_privilege(SystemObject.PHYSICAL_COUNT_ADJUSTMENT, AccessRight.READ)
_CREATE = require_privilege(SystemObject.PHYSICAL_COUNT_ADJUSTMENT, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.PHYSICAL_COUNT_ADJUSTMENT, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.PHYSICAL_COUNT_ADJUSTMENT, AccessRight.DELETE)


async def _count_or_404(db: AsyncSession, count_id: int):  # noqa: ANN202
    """Header only: the state-changing services load lines after they lock (research R2)."""
    count = await inv.get_document(db, inv.COUNT, count_id)
    if count is None:
        raise not_found(InventoryCount, count_id, 'Count not found')
    return count


@router.get('', response_model=ListResponse[InventoryCountSummary])
async def list_inventory_counts(
    count_status: str | None = Query(None, alias='status'),
    warehouse: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[InventoryCountSummary]:
    items, total = await inventory_count_service.list_counts(
        db, current=current, status=count_status, warehouse=warehouse, skip=skip, limit=limit
    )
    return ListResponse(
        items=[InventoryCountSummary.model_validate(inv.attach_status(c)) for c in items],
        total=total,
    )


@router.post('', response_model=InventoryCountResponse, status_code=status.HTTP_201_CREATED)
async def create_inventory_count(
    data: CountCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryCountResponse:
    count = await inventory_count_service.create(db, data, current=current)
    return InventoryCountResponse.model_validate(count)


@router.get('/{count_id}', response_model=InventoryCountResponse)
async def get_inventory_count(
    count_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> InventoryCountResponse:
    count = await _count_or_404(db, count_id)
    await inventory_count_service.attach_derived(db, count)
    return InventoryCountResponse.model_validate(count)


@router.put('/{count_id}', response_model=InventoryCountResponse)
async def update_inventory_count(
    count_id: int,
    data: CountUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryCountResponse:
    await _count_or_404(db, count_id)
    count = await inventory_count_service.update(db, count_id, data, current=current)
    return InventoryCountResponse.model_validate(count)


@router.put(
    '/{count_id}/lines',
    response_model=InventoryCountResponse,
    responses={status.HTTP_422_UNPROCESSABLE_CONTENT: {'description': 'Bad entries, by index'}},
)
async def set_inventory_count_lines(
    count_id: int,
    data: CountLinesSet,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryCountResponse:
    """Set many counted quantities at once; the batch applies whole or not at all (R3)."""
    await _count_or_404(db, count_id)
    try:
        count = await inventory_count_service.set_lines(db, count_id, data.lines, current=current)
    except inventory_count_service.BatchRefused as refusal:
        raise DomainError(
            ErrorCode.INVENTORY_COUNT_BATCH_INVALID, str(refusal), errors=refusal.errors
        ) from refusal
    return InventoryCountResponse.model_validate(count)


@router.put('/{count_id}/lines/{line_id}', response_model=InventoryCountResponse)
async def update_inventory_count_line(
    count_id: int,
    line_id: int,
    data: CountLineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryCountResponse:
    await _count_or_404(db, count_id)
    count = await inventory_count_service.update_line(
        db, count_id, line_id, data.quantity, current=current
    )
    return InventoryCountResponse.model_validate(count)


@router.delete('/{count_id}/lines/{line_id}', response_model=InventoryCountResponse)
async def remove_inventory_count_line(
    count_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryCountResponse:
    await _count_or_404(db, count_id)
    count = await inventory_count_service.remove_line(db, count_id, line_id, current=current)
    return InventoryCountResponse.model_validate(count)


@router.post('/{count_id}/confirm', response_model=InventoryCountConfirmResponse)
async def confirm_inventory_count(
    count_id: int,
    data: CountConfirm | None = Body(None),
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryCountConfirmResponse:
    """With `include_uncounted`, every other product with stock here is counted at 0 (FR-005)."""
    await _count_or_404(db, count_id)
    count = await inventory_count_service.confirm(
        db,
        count_id,
        include_uncounted=(data or CountConfirm()).include_uncounted,
        current=current,
    )
    return InventoryCountConfirmResponse.model_validate(count)


@router.post('/{count_id}/cancel', response_model=InventoryCountResponse)
async def cancel_inventory_count(
    count_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> InventoryCountResponse:
    """A draft only; a confirmed count is final (FR-006, FR-007)."""
    await _count_or_404(db, count_id)
    count = await inventory_count_service.cancel(db, count_id, current=current)
    return InventoryCountResponse.model_validate(count)
