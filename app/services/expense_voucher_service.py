"""Expense vouchers: cash paid out of a drawer during a cash session (spec 032, US1 and US2).

**States (research R1)** come from the legacy flags, NULL counted as false: draft (0, 0),
`completed` (1, 0) and cancelled (any, 1). A void keeps `completed` and sets `cancelled`, so
`voided` tells it apart from a cancelled draft. The cut already counts `completed AND NOT
cancelled`.

**Locking (research R2, R3).** Confirm and void follow spec 029's pattern: plain pre-reads, a
commit, the session row (`lock_open_session`) and then the voucher row, then ordinary reads. A
close takes the same session row first, so a closed session's figures never change. Edits and
cancelling a draft lock the voucher row only: a draft does not touch the cut.
"""

from collections.abc import Sequence
from datetime import date, datetime, time, timedelta
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.core.errors import not_found
from app.enums import SourceType
from app.models.core import CashDrawer, CashSession, Employee, Expense
from app.models.purchases import ExpenseVoucher, ExpenseVoucherDetail
from app.schemas.expense_vouchers import (
    ExpenseVoucherCreate,
    ExpenseVoucherLineCreate,
    ExpenseVoucherLineUpdate,
    ExpenseVoucherUpdate,
)
from app.schemas.sales_order import DocumentStatus, derive_status
from app.services import cash_session_service, incidences
from app.services import inventory_documents as inv
from app.services.fk_expansion import batch_fetch

_SESSION_REQUIRED = 'An open cash session is required'

# ── States (R1) ───────────────────────────────────────────────────────────────


def voucher_state(voucher: object) -> DocumentStatus:
    return derive_status(
        completed=bool(getattr(voucher, 'completed', None)),
        cancelled=bool(getattr(voucher, 'cancelled', None)),
    )


def is_voided(voucher: object) -> bool:
    return bool(getattr(voucher, 'completed', None)) and bool(getattr(voucher, 'cancelled', None))


def _state_clause(value: str) -> ColumnElement[bool]:
    header = ExpenseVoucher
    if value == DocumentStatus.DRAFT:
        return and_(header.completed.is_not(True), header.cancelled.is_not(True))
    if value == DocumentStatus.COMPLETED:
        return and_(header.completed.is_(True), header.cancelled.is_not(True))
    if value == DocumentStatus.CANCELLED:
        return header.cancelled.is_(True)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='status must be one of draft, completed, cancelled',
    )


def _conflict(reason: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)


def _assert_draft_by(voucher: ExpenseVoucher, current: CurrentUser) -> None:
    """FR-002, FR-003: only the creator, its cashier, changes a draft."""
    if voucher.creator != current.employee_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Only the voucher's cashier can change it",
        )
    if voucher_state(voucher) is not DocumentStatus.DRAFT:
        raise _conflict('Only a draft expense voucher can be changed')


def _assert_voidable(voucher: ExpenseVoucher) -> None:
    if voucher_state(voucher) is not DocumentStatus.COMPLETED:
        raise _conflict('Only a confirmed voucher can be voided')


def _touch(voucher: ExpenseVoucher, current: CurrentUser) -> None:
    voucher.updater = current.employee_id
    voucher.modification_time = local_now()


# ── Reads ─────────────────────────────────────────────────────────────────────


def _name(employee: Employee | None) -> str | None:
    return f'{employee.first_name} {employee.last_name}'.strip() if employee else None


def _summarize(
    voucher: ExpenseVoucher, total: Decimal, count: int, creator: Employee | None
) -> None:
    voucher.__dict__.update(
        status=voucher_state(voucher),
        voided=is_voided(voucher),
        total=total,
        line_count=count,
        creator_name=_name(creator),
    )


async def attach(db: AsyncSession, voucher: ExpenseVoucher) -> ExpenseVoucher:
    """The lines with their category names, the total and the creator's name."""
    rows = await db.execute(
        select(ExpenseVoucherDetail, Expense.expense)
        .outerjoin(Expense, Expense.expense_id == ExpenseVoucherDetail.expense)
        .where(ExpenseVoucherDetail.expense_voucher == voucher.expense_voucher_id)
        .order_by(ExpenseVoucherDetail.expense_voucher_detail_id)
    )
    lines = []
    for line, name in rows.all():
        line.__dict__['expense_name'] = name
        lines.append(line)
    voucher.__dict__['lines'] = lines
    total = sum((line.amount for line in lines), Decimal(0))
    _summarize(voucher, total, len(lines), await db.get(Employee, voucher.creator))
    return voucher


async def get_voucher(db: AsyncSession, voucher_id: int) -> ExpenseVoucher | None:
    voucher = await db.get(ExpenseVoucher, voucher_id)
    return None if voucher is None else await attach(db, voucher)


async def _summaries(db: AsyncSession, vouchers: Sequence[ExpenseVoucher]) -> None:
    """Totals, line counts and creator names for a whole page, in two queries."""
    ids = [voucher.expense_voucher_id for voucher in vouchers]
    if not ids:
        return
    detail = ExpenseVoucherDetail
    rows = await db.execute(
        select(detail.expense_voucher, func.sum(detail.amount), func.count())
        .where(detail.expense_voucher.in_(ids))
        .group_by(detail.expense_voucher)
    )
    figures = {voucher_id: (total, count) for voucher_id, total, count in rows.all()}
    creators = await batch_fetch(
        db, Employee, Employee.employee_id, (voucher.creator for voucher in vouchers)
    )
    for voucher in vouchers:
        total, count = figures.get(voucher.expense_voucher_id, (Decimal(0), 0))
        _summarize(voucher, total, count, creators.get(voucher.creator))


async def list_vouchers(
    db: AsyncSession,
    *,
    current: CurrentUser,
    state: str | None = None,
    facility: int | None = None,
    cash_session: int | None = None,
    creator: int | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[ExpenseVoucher], int]:
    """Newest first. A non-administrator sees their own facility's only, and naming another answers
    403; an administrator sees every facility unless `facility` narrows it (FR-008)."""
    header = ExpenseVoucher
    clauses: list[ColumnElement[bool]] = []
    if facility is not None:
        inv.assert_facility(current, facility)
    if not current.administrator:
        clauses.append(header.facility == current.facility_id)
    elif facility is not None:
        clauses.append(header.facility == facility)
    if state is not None:
        clauses.append(_state_clause(state))
    if cash_session is not None:
        clauses.append(header.cash_session == cash_session)
    if creator is not None:
        clauses.append(header.creator == creator)
    if date_from is not None:
        clauses.append(header.date >= datetime.combine(date_from, time()))
    if date_to is not None:
        clauses.append(header.date < datetime.combine(date_to + timedelta(days=1), time()))
    total: int = (
        await db.execute(select(func.count()).select_from(header).where(*clauses))
    ).scalar_one()
    items = list(
        (
            await db.execute(
                select(header)
                .where(*clauses)
                .order_by(header.expense_voucher_id.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    await _summaries(db, items)
    return items, total


# ── Locks ─────────────────────────────────────────────────────────────────────


async def _lock_voucher(db: AsyncSession, voucher_id: int) -> ExpenseVoucher:
    voucher = (
        await db.execute(
            select(ExpenseVoucher)
            .where(ExpenseVoucher.expense_voucher_id == voucher_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if voucher is None:
        raise not_found(ExpenseVoucher, voucher_id)
    return voucher


async def _begin_draft(db: AsyncSession, voucher_id: int, current: CurrentUser) -> ExpenseVoucher:
    await db.commit()
    voucher = await _lock_voucher(db, voucher_id)
    _assert_draft_by(voucher, current)
    return voucher


async def _facility_of(db: AsyncSession, session: CashSession) -> int:
    """A voucher's facility is its session's drawer's."""
    return (await db.get(CashDrawer, session.cash_drawer)).facility


# ── Drafts (FR-001, FR-002) ───────────────────────────────────────────────────


async def create_voucher(
    db: AsyncSession, data: ExpenseVoucherCreate, *, current: CurrentUser
) -> ExpenseVoucher:
    """A draft in the caller's open session. It takes no lock: a draft does not touch the cut."""
    session = await cash_session_service.open_session_for_cashier(db, current.employee_id)
    if session is None:
        raise _conflict(_SESSION_REQUIRED)
    now = local_now()
    voucher = ExpenseVoucher(
        creator=current.employee_id,
        updater=current.employee_id,
        facility=await _facility_of(db, session),
        cash_session=session.cash_session_id,
        comment=data.comment,
        date=now,
        creation_time=now,
        modification_time=now,
        completed=False,
        cancelled=False,
    )
    db.add(voucher)
    await db.commit()
    return await attach(db, voucher)


async def update_voucher(
    db: AsyncSession, voucher_id: int, data: ExpenseVoucherUpdate, *, current: CurrentUser
) -> ExpenseVoucher:
    changes = data.model_dump(exclude_unset=True)
    async with inv.busy_as_conflict(db):
        voucher = await _begin_draft(db, voucher_id, current)
        if 'comment' in changes:
            voucher.comment = changes['comment']
        _touch(voucher, current)
        await db.commit()
    return await attach(db, voucher)


async def _expense(db: AsyncSession, expense_id: int) -> Expense:
    expense = await db.get(Expense, expense_id)
    if expense is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail='Unknown expense category'
        )
    return expense


async def add_line(
    db: AsyncSession, voucher_id: int, data: ExpenseVoucherLineCreate, *, current: CurrentUser
) -> ExpenseVoucher:
    async with inv.busy_as_conflict(db):
        voucher = await _begin_draft(db, voucher_id, current)
        expense = await _expense(db, data.expense)
        db.add(
            ExpenseVoucherDetail(
                expense_voucher=voucher_id,
                expense=data.expense,
                amount=data.amount,
                comment=data.comment if data.comment is not None else expense.comment,
            )
        )
        _touch(voucher, current)
        await db.commit()
    return await attach(db, voucher)


async def _locked_line(
    db: AsyncSession, voucher_id: int, line_id: int, current: CurrentUser
) -> tuple[ExpenseVoucher, ExpenseVoucherDetail]:
    voucher = await _begin_draft(db, voucher_id, current)
    line = (
        await db.execute(
            select(ExpenseVoucherDetail)
            .where(ExpenseVoucherDetail.expense_voucher_detail_id == line_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if line is None or line.expense_voucher != voucher_id:
        raise not_found(ExpenseVoucherDetail, line_id, detail='Line not found')
    return voucher, line


async def update_line(
    db: AsyncSession,
    voucher_id: int,
    line_id: int,
    data: ExpenseVoucherLineUpdate,
    *,
    current: CurrentUser,
) -> ExpenseVoucher:
    changes = data.model_dump(exclude_unset=True)
    async with inv.busy_as_conflict(db):
        voucher, line = await _locked_line(db, voucher_id, line_id, current)
        if changes.get('expense') is not None:
            await _expense(db, changes['expense'])
            line.expense = changes['expense']
        if changes.get('amount') is not None:
            line.amount = changes['amount']
        if 'comment' in changes:
            line.comment = changes['comment']
        _touch(voucher, current)
        await db.commit()
    return await attach(db, voucher)


async def remove_line(
    db: AsyncSession, voucher_id: int, line_id: int, *, current: CurrentUser
) -> ExpenseVoucher:
    async with inv.busy_as_conflict(db):
        voucher, line = await _locked_line(db, voucher_id, line_id, current)
        await db.delete(line)
        _touch(voucher, current)
        await db.commit()
    return await attach(db, voucher)


# ── Transitions (FR-003 to FR-005, R3) ────────────────────────────────────────


async def confirm_voucher(
    db: AsyncSession, voucher_id: int, *, current: CurrentUser
) -> ExpenseVoucher:
    """Into the caller's session open now, which may not be the draft's (FR-003)."""
    async with inv.busy_as_conflict(db):
        # 1. Pre-reads, then the commit.
        if await db.get(ExpenseVoucher, voucher_id) is None:
            raise not_found(ExpenseVoucher, voucher_id)
        session = await cash_session_service.open_session_for_cashier(db, current.employee_id)
        if session is None:
            raise _conflict(_SESSION_REQUIRED)
        session_id = session.cash_session_id
        await db.commit()

        # 2. Locks: the session, then the voucher.
        session = await cash_session_service.lock_open_session(db, session_id)
        if session.cashier != current.employee_id:
            raise _conflict(_SESSION_REQUIRED)
        voucher = await _lock_voucher(db, voucher_id)
        _assert_draft_by(voucher, current)

        # 3. Ordinary reads.
        total = (
            await db.execute(
                select(func.sum(ExpenseVoucherDetail.amount)).where(
                    ExpenseVoucherDetail.expense_voucher == voucher_id
                )
            )
        ).scalar_one_or_none()
        if not total or total <= 0:
            raise _conflict('The total must be above 0')

        now = local_now()
        voucher.cash_session = session_id
        voucher.facility = await _facility_of(db, session)
        voucher.date = now
        voucher.completed = True
        voucher.updater = current.employee_id
        voucher.modification_time = now
        await db.commit()
    return await attach(db, voucher)


async def cancel_draft(
    db: AsyncSession, voucher_id: int, *, current: CurrentUser
) -> ExpenseVoucher:
    """By its creator, without a reason (FR-004); a confirmed voucher is voided instead."""
    async with inv.busy_as_conflict(db):
        voucher = await _begin_draft(db, voucher_id, current)
        voucher.cancelled = True
        _touch(voucher, current)
        await db.commit()
    return await attach(db, voucher)


async def void_voucher(
    db: AsyncSession, voucher_id: int, reason: str, *, current: CurrentUser
) -> ExpenseVoucher:
    """A confirmed voucher, while its own session is open; the caller needs no session (FR-004).

    A confirmed voucher's session and facility never change, so they are checked on the pre-read;
    the session row is locked before the voucher's, as a close takes it (FR-005).
    """
    async with inv.busy_as_conflict(db):
        # 1. Pre-reads, then the commit.
        voucher = await db.get(ExpenseVoucher, voucher_id)
        if voucher is None:
            raise not_found(ExpenseVoucher, voucher_id)
        inv.assert_facility(current, voucher.facility)
        _assert_voidable(voucher)
        session_id = voucher.cash_session
        await db.commit()

        # 2. Locks: the voucher's session, then the voucher, re-checked.
        await cash_session_service.lock_open_session(db, session_id)
        voucher = await _lock_voucher(db, voucher_id)
        _assert_voidable(voucher)
        if voucher.cash_session != session_id:
            raise _conflict('Only a confirmed voucher can be voided')

        voucher.cancelled = True
        _touch(voucher, current)
        incidences.record(
            db,
            source=SourceType.EXPENSE_VOUCHER,
            instance_id=voucher_id,
            updater=current.employee_id,
            reason=reason,
            context='Expense voucher voided',
        )
        await db.commit()
    return await attach(db, voucher)
