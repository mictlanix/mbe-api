from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.inventory import (
    InTransitResponse,
    InventoryTransferResponse,
    InventoryTransferSummary,
    LineAdd,
    LineUpdate,
    SalesOrderItemsAdd,
    TransferCreate,
    TransferReceive,
    TransferStatus,
    TransferUpdate,
)
from app.services import inventory_documents as inv
from app.services import inventory_transfer_service

router = APIRouter()

_READ = require_privilege(SystemObject.INVENTORY_TRANSFERS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.INVENTORY_TRANSFERS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.INVENTORY_TRANSFERS, AccessRight.UPDATE)
# Closing the remainder and cancelling undo stock movements (FR-010, FR-043)
_DELETE = require_privilege(SystemObject.INVENTORY_TRANSFERS, AccessRight.DELETE)

# The routes that change a transfer pass only its id: the service locks the transfer before reading
# it (research R2), and that lock answers 404 for an unknown one.


async def _response(db: AsyncSession, transfer) -> InventoryTransferResponse:  # noqa: ANN001
    await inventory_transfer_service.attach_derived(db, transfer)
    return InventoryTransferResponse.model_validate(transfer)


@router.get('', response_model=ListResponse[InventoryTransferSummary])
async def list_inventory_transfers(
    transfer_status: TransferStatus | None = Query(None, alias='status'),
    warehouse: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[InventoryTransferSummary]:
    items, total = await inv.list_documents(
        db,
        inv.TRANSFER,
        current=current,
        status=transfer_status,
        warehouse=warehouse,
        skip=skip,
        limit=limit,
        status_clause=inventory_transfer_service.transfer_status_clause,
    )
    return ListResponse(
        items=[
            InventoryTransferSummary.model_validate(inventory_transfer_service.attach_status(t))
            for t in items
        ],
        total=total,
    )


# Declared before `/{inventory_transfer_id}`, which would otherwise take `in-transit` as an id.
@router.get('/in-transit', response_model=InTransitResponse)
async def get_inventory_in_transit(
    facility: int | None = Query(None),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> InTransitResponse:
    facility = facility if facility is not None else current.facility_id
    if facility is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail='facility is required'
        )
    return await inventory_transfer_service.in_transit_view(db, facility)


@router.post('', response_model=InventoryTransferResponse, status_code=status.HTTP_201_CREATED)
async def create_inventory_transfer(
    data: TransferCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inv.create_draft(
        db,
        inv.TRANSFER,
        warehouse=data.warehouse,
        warehouse_to=data.warehouse_to,
        comment=data.comment,
        current=current,
    )
    return await _response(db, transfer)


@router.get('/{inventory_transfer_id}', response_model=InventoryTransferResponse)
async def get_inventory_transfer(
    inventory_transfer_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inv.get_document(db, inv.TRANSFER, inventory_transfer_id)
    if transfer is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Transfer not found')
    return await _response(db, transfer)


@router.put('/{inventory_transfer_id}', response_model=InventoryTransferResponse)
async def update_inventory_transfer(
    inventory_transfer_id: int,
    data: TransferUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inv.update_header(
        db, inv.TRANSFER, inventory_transfer_id, data, current=current
    )
    return await _response(db, transfer)


@router.post('/{inventory_transfer_id}/lines', response_model=InventoryTransferResponse)
async def add_inventory_transfer_line(
    inventory_transfer_id: int,
    data: LineAdd,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inv.add_line(
        db,
        inv.TRANSFER,
        inventory_transfer_id,
        product=data.product,
        quantity=data.quantity,
        current=current,
    )
    return await _response(db, transfer)


@router.put('/{inventory_transfer_id}/lines/{line_id}', response_model=InventoryTransferResponse)
async def update_inventory_transfer_line(
    inventory_transfer_id: int,
    line_id: int,
    data: LineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inv.update_line(
        db, inv.TRANSFER, inventory_transfer_id, line_id, quantity=data.quantity, current=current
    )
    return await _response(db, transfer)


@router.delete('/{inventory_transfer_id}/lines/{line_id}', response_model=InventoryTransferResponse)
async def remove_inventory_transfer_line(
    inventory_transfer_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inv.remove_line(
        db, inv.TRANSFER, inventory_transfer_id, line_id, current=current
    )
    return await _response(db, transfer)


@router.post('/{inventory_transfer_id}/sales-order-items', response_model=InventoryTransferResponse)
async def add_inventory_transfer_sales_order_items(
    inventory_transfer_id: int,
    data: SalesOrderItemsAdd,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inventory_transfer_service.add_sales_order_items(
        db, inventory_transfer_id, data.sales_order, current=current
    )
    return await _response(db, transfer)


@router.post('/{inventory_transfer_id}/dispatch', response_model=InventoryTransferResponse)
async def dispatch_inventory_transfer(
    inventory_transfer_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inventory_transfer_service.dispatch(db, inventory_transfer_id, current=current)
    return await _response(db, transfer)


@router.post('/{inventory_transfer_id}/receive', response_model=InventoryTransferResponse)
async def receive_inventory_transfer(
    inventory_transfer_id: int,
    data: TransferReceive | None = None,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    """No body, or no `lines`, receives everything outstanding."""
    requested = (
        None
        if data is None or data.lines is None
        else [(line.line_id, line.quantity) for line in data.lines]
    )
    transfer = await inventory_transfer_service.receive(
        db, inventory_transfer_id, requested, current=current
    )
    return await _response(db, transfer)


@router.post('/{inventory_transfer_id}/close', response_model=InventoryTransferResponse)
async def close_inventory_transfer(
    inventory_transfer_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inventory_transfer_service.close_remainder(
        db, inventory_transfer_id, current=current
    )
    return await _response(db, transfer)


@router.post('/{inventory_transfer_id}/cancel', response_model=InventoryTransferResponse)
async def cancel_inventory_transfer(
    inventory_transfer_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> InventoryTransferResponse:
    transfer = await inventory_transfer_service.cancel(db, inventory_transfer_id, current=current)
    return await _response(db, transfer)
