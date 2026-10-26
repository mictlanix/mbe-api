from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.inventory import (
    InventoryReceiptResponse,
    InventoryReceiptSummary,
    LineAdd,
    LineUpdate,
    ReceiptCreate,
    ReceiptUpdate,
)
from app.services import inventory_documents as inv
from app.services import inventory_receipt_service

router = APIRouter()

_READ = require_privilege(SystemObject.INVENTORY_RECEIPTS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.INVENTORY_RECEIPTS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.INVENTORY_RECEIPTS, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.INVENTORY_RECEIPTS, AccessRight.DELETE)


async def _receipt_or_404(db: AsyncSession, receipt_id: int):  # noqa: ANN202
    """Header only: the state-changing services load lines after they lock (research R2)."""
    receipt = await inv.get_document(db, inv.RECEIPT, receipt_id)
    if receipt is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Receipt not found')
    return receipt


@router.get('', response_model=ListResponse[InventoryReceiptSummary])
async def list_inventory_receipts(
    receipt_status: str | None = Query(None, alias='status'),
    warehouse: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[InventoryReceiptSummary]:
    items, total = await inv.list_documents(
        db,
        inv.RECEIPT,
        current=current,
        status=receipt_status,
        warehouse=warehouse,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[InventoryReceiptSummary.model_validate(inv.attach_status(r)) for r in items],
        total=total,
    )


@router.post('', response_model=InventoryReceiptResponse, status_code=status.HTTP_201_CREATED)
async def create_inventory_receipt(
    data: ReceiptCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryReceiptResponse:
    receipt = await inventory_receipt_service.create(db, data, current=current)
    return InventoryReceiptResponse.model_validate(receipt)


@router.get('/{receipt_id}', response_model=InventoryReceiptResponse)
async def get_inventory_receipt(
    receipt_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> InventoryReceiptResponse:
    receipt = await _receipt_or_404(db, receipt_id)
    await inventory_receipt_service.attach_derived(db, receipt)
    return InventoryReceiptResponse.model_validate(receipt)


@router.put('/{receipt_id}', response_model=InventoryReceiptResponse)
async def update_inventory_receipt(
    receipt_id: int,
    data: ReceiptUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryReceiptResponse:
    await _receipt_or_404(db, receipt_id)
    receipt = await inventory_receipt_service.update(db, receipt_id, data, current=current)
    return InventoryReceiptResponse.model_validate(receipt)


@router.post('/{receipt_id}/lines', response_model=InventoryReceiptResponse)
async def add_inventory_receipt_line(
    receipt_id: int,
    data: LineAdd,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryReceiptResponse:
    await _receipt_or_404(db, receipt_id)
    receipt = await inventory_receipt_service.add_line(db, receipt_id, data, current=current)
    return InventoryReceiptResponse.model_validate(receipt)


@router.put('/{receipt_id}/lines/{line_id}', response_model=InventoryReceiptResponse)
async def update_inventory_receipt_line(
    receipt_id: int,
    line_id: int,
    data: LineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryReceiptResponse:
    await _receipt_or_404(db, receipt_id)
    receipt = await inventory_receipt_service.update_line(
        db, receipt_id, line_id, data, current=current
    )
    return InventoryReceiptResponse.model_validate(receipt)


@router.delete('/{receipt_id}/lines/{line_id}', response_model=InventoryReceiptResponse)
async def remove_inventory_receipt_line(
    receipt_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryReceiptResponse:
    await _receipt_or_404(db, receipt_id)
    receipt = await inventory_receipt_service.remove_line(db, receipt_id, line_id, current=current)
    return InventoryReceiptResponse.model_validate(receipt)


@router.post('/{receipt_id}/confirm', response_model=InventoryReceiptResponse)
async def confirm_inventory_receipt(
    receipt_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryReceiptResponse:
    await _receipt_or_404(db, receipt_id)
    receipt = await inventory_receipt_service.confirm(db, receipt_id, current=current)
    return InventoryReceiptResponse.model_validate(receipt)


@router.post('/{receipt_id}/cancel', response_model=InventoryReceiptResponse)
async def cancel_inventory_receipt(
    receipt_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> InventoryReceiptResponse:
    await _receipt_or_404(db, receipt_id)
    receipt = await inventory_receipt_service.cancel(db, receipt_id, current=current)
    return InventoryReceiptResponse.model_validate(receipt)
