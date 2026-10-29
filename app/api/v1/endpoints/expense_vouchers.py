from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.deps import CurrentUser, require_privilege
from app.core.errors import not_found
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.models.purchases import ExpenseVoucher
from app.schemas import ListResponse
from app.schemas.customer_payment import ReversalRequest
from app.schemas.expense_vouchers import (
    ExpenseVoucherCreate,
    ExpenseVoucherLineCreate,
    ExpenseVoucherLineUpdate,
    ExpenseVoucherResponse,
    ExpenseVoucherSummary,
    ExpenseVoucherUpdate,
)
from app.schemas.sales_order import DocumentStatus
from app.services import expense_voucher_service as service
from app.services import inventory_documents as inv
from app.services import print_contexts

router = APIRouter()

_READ = require_privilege(SystemObject.EXPENSE_TICKET, AccessRight.READ)
_CREATE = require_privilege(SystemObject.EXPENSE_TICKET, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.EXPENSE_TICKET, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.EXPENSE_TICKET, AccessRight.DELETE)


async def _voucher_or_404(db: AsyncSession, voucher_id: int, current: CurrentUser):  # noqa: ANN202
    """Another facility's voucher answers 403 to a non-administrator (FR-008)."""
    voucher = await service.get_voucher(db, voucher_id)
    if voucher is None:
        raise not_found(ExpenseVoucher, voucher_id)
    inv.assert_facility(current, voucher.facility, resource='expense_voucher')
    return voucher


@router.get('', response_model=ListResponse[ExpenseVoucherSummary])
async def list_expense_vouchers(
    voucher_status: str | None = Query(None, alias='status'),
    facility: int | None = Query(None),
    cash_session: int | None = Query(None),
    creator: int | None = Query(None),
    date_from: date | None = Query(None, alias='from'),
    date_to: date | None = Query(None, alias='to'),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[ExpenseVoucherSummary]:
    items, total = await service.list_vouchers(
        db,
        current=current,
        state=voucher_status,
        facility=facility,
        cash_session=cash_session,
        creator=creator,
        date_from=date_from,
        date_to=date_to,
        skip=skip,
        limit=limit,
    )
    return ListResponse(
        items=[ExpenseVoucherSummary.model_validate(item) for item in items], total=total
    )


@router.post('', response_model=ExpenseVoucherResponse, status_code=status.HTTP_201_CREATED)
async def create_expense_voucher(
    data: ExpenseVoucherCreate,
    current: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> ExpenseVoucherResponse:
    voucher = await service.create_voucher(db, data, current=current)
    return ExpenseVoucherResponse.model_validate(voucher)


@router.get('/{voucher_id}', response_model=ExpenseVoucherResponse)
async def get_expense_voucher(
    voucher_id: int,
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> ExpenseVoucherResponse:
    return ExpenseVoucherResponse.model_validate(await _voucher_or_404(db, voucher_id, current))


@router.put('/{voucher_id}', response_model=ExpenseVoucherResponse)
async def update_expense_voucher(
    voucher_id: int,
    data: ExpenseVoucherUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ExpenseVoucherResponse:
    voucher = await service.update_voucher(db, voucher_id, data, current=current)
    return ExpenseVoucherResponse.model_validate(voucher)


@router.post('/{voucher_id}/lines', response_model=ExpenseVoucherResponse)
async def add_expense_voucher_line(
    voucher_id: int,
    data: ExpenseVoucherLineCreate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ExpenseVoucherResponse:
    voucher = await service.add_line(db, voucher_id, data, current=current)
    return ExpenseVoucherResponse.model_validate(voucher)


@router.put('/{voucher_id}/lines/{line_id}', response_model=ExpenseVoucherResponse)
async def update_expense_voucher_line(
    voucher_id: int,
    line_id: int,
    data: ExpenseVoucherLineUpdate,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ExpenseVoucherResponse:
    voucher = await service.update_line(db, voucher_id, line_id, data, current=current)
    return ExpenseVoucherResponse.model_validate(voucher)


@router.delete('/{voucher_id}/lines/{line_id}', response_model=ExpenseVoucherResponse)
async def remove_expense_voucher_line(
    voucher_id: int,
    line_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ExpenseVoucherResponse:
    voucher = await service.remove_line(db, voucher_id, line_id, current=current)
    return ExpenseVoucherResponse.model_validate(voucher)


@router.post('/{voucher_id}/confirm', response_model=ExpenseVoucherResponse)
async def confirm_expense_voucher(
    voucher_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ExpenseVoucherResponse:
    voucher = await service.confirm_voucher(db, voucher_id, current=current)
    return ExpenseVoucherResponse.model_validate(voucher)


@router.post('/{voucher_id}/cancel', response_model=ExpenseVoucherResponse)
async def cancel_expense_voucher(
    voucher_id: int,
    current: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> ExpenseVoucherResponse:
    """A draft only; a confirmed voucher is voided instead."""
    voucher = await service.cancel_draft(db, voucher_id, current=current)
    return ExpenseVoucherResponse.model_validate(voucher)


@router.post('/{voucher_id}/void', response_model=ExpenseVoucherResponse)
async def void_expense_voucher(
    voucher_id: int,
    data: ReversalRequest,
    current: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> ExpenseVoucherResponse:
    """A confirmed voucher, while its session is open (FR-004, FR-005)."""
    voucher = await service.void_voucher(db, voucher_id, data.reason, current=current)
    return ExpenseVoucherResponse.model_validate(voucher)


@router.get('/{voucher_id}/ticket', response_class=Response, responses=rendering.PDF_RESPONSE)
async def print_expense_voucher(
    voucher_id: int,
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The ticket the person who takes the cash signs (FR-009)."""
    voucher = await _voucher_or_404(db, voucher_id, current)
    if service.voucher_state(voucher) is not DocumentStatus.COMPLETED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Only a confirmed voucher can be printed',
        )
    template, context = await print_contexts.expense_voucher_context(db, voucher)
    pdf = await rendering.render_pdf(template, context)
    return rendering.pdf_response(pdf, f'gastos-{voucher.expense_voucher_id:08d}.pdf')
