from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.inventory import (
    InventoryIssueResponse,
    InventoryIssueSummary,
    IssueCreate,
    IssueUpdate,
    LineAdd,
    LineUpdate,
)
from app.services import inventory_documents as inv

router = APIRouter()

_READ = require_privilege(SystemObject.INVENTORY_ISSUES, AccessRight.READ)
_CREATE = require_privilege(SystemObject.INVENTORY_ISSUES, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.INVENTORY_ISSUES, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.INVENTORY_ISSUES, AccessRight.DELETE)


async def _issue_or_404(db: AsyncSession, issue_id: int):  # noqa: ANN202
    """Header only: the state-changing services load lines after they lock (research R2)."""
    issue = await inv.get_document(db, inv.ISSUE, issue_id)
    if issue is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Issue not found')
    return issue


async def _response(db: AsyncSession, issue) -> InventoryIssueResponse:  # noqa: ANN001
    return InventoryIssueResponse.model_validate(await inv.attach_issue(db, issue))


@router.get('', response_model=ListResponse[InventoryIssueSummary])
async def list_inventory_issues(
    issue_status: str | None = Query(None, alias='status'),
    warehouse: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[InventoryIssueSummary]:
    items, total = await inv.list_documents(
        db,
        inv.ISSUE,
        current=current,
        status=issue_status,
        warehouse=warehouse,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[InventoryIssueSummary.model_validate(inv.attach_status(i)) for i in items],
        total=total,
    )


@router.post('', response_model=InventoryIssueResponse, status_code=status.HTTP_201_CREATED)
async def create_inventory_issue(
    data: IssueCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryIssueResponse:
    issue = await inv.create_draft(
        db, inv.ISSUE, warehouse=data.warehouse, comment=data.comment, current=current
    )
    return await _response(db, issue)


@router.get('/{issue_id}', response_model=InventoryIssueResponse)
async def get_inventory_issue(
    issue_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> InventoryIssueResponse:
    return await _response(db, await _issue_or_404(db, issue_id))


@router.put('/{issue_id}', response_model=InventoryIssueResponse)
async def update_inventory_issue(
    issue_id: int,
    data: IssueUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryIssueResponse:
    await _issue_or_404(db, issue_id)
    issue = await inv.update_header(db, inv.ISSUE, issue_id, data, current=current)
    return await _response(db, issue)


@router.post('/{issue_id}/lines', response_model=InventoryIssueResponse)
async def add_inventory_issue_line(
    issue_id: int,
    data: LineAdd,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryIssueResponse:
    await _issue_or_404(db, issue_id)
    issue = await inv.add_line(
        db, inv.ISSUE, issue_id, product=data.product, quantity=data.quantity, current=current
    )
    return await _response(db, issue)


@router.put('/{issue_id}/lines/{line_id}', response_model=InventoryIssueResponse)
async def update_inventory_issue_line(
    issue_id: int,
    line_id: int,
    data: LineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryIssueResponse:
    await _issue_or_404(db, issue_id)
    issue = await inv.update_line(
        db, inv.ISSUE, issue_id, line_id, quantity=data.quantity, current=current
    )
    return await _response(db, issue)


@router.delete('/{issue_id}/lines/{line_id}', response_model=InventoryIssueResponse)
async def remove_inventory_issue_line(
    issue_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryIssueResponse:
    await _issue_or_404(db, issue_id)
    issue = await inv.remove_line(db, inv.ISSUE, issue_id, line_id, current=current)
    return await _response(db, issue)


@router.post('/{issue_id}/confirm', response_model=InventoryIssueResponse)
async def confirm_inventory_issue(
    issue_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> InventoryIssueResponse:
    await _issue_or_404(db, issue_id)
    issue = await inv.confirm_simple(db, inv.ISSUE, issue_id, current=current)
    return await _response(db, issue)


@router.post('/{issue_id}/cancel', response_model=InventoryIssueResponse)
async def cancel_inventory_issue(
    issue_id: int,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> InventoryIssueResponse:
    await _issue_or_404(db, issue_id)
    issue = await inv.cancel(db, inv.ISSUE, issue_id, current=current)
    return await _response(db, issue)
