"""Expense vouchers: states, drafts, confirm, cancel, void and the ticket (spec 032, US1–US3).

The services run against an in-memory SQLite database holding only the tables they touch. SQLite
renders no `FOR UPDATE`, so these prove the checks and the writes; the lock order is pinned by
checking which session `lock_open_session` is handed.
"""

from collections.abc import AsyncIterator, Iterator
from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app import rendering
from app.core.deps import CurrentUser
from app.db.base import Base
from app.enums import SourceType
from app.models.core import CashDrawer, CashSession, Employee, Expense
from app.models.incidence import Incidence
from app.models.purchases import ExpenseVoucher, ExpenseVoucherDetail
from app.schemas.customer_payment import ReversalRequest
from app.schemas.expense_vouchers import (
    ExpenseVoucherCreate,
    ExpenseVoucherLineCreate,
    ExpenseVoucherLineUpdate,
    ExpenseVoucherUpdate,
)
from app.schemas.sales_order import DocumentStatus
from app.services import cash_session_service, print_contexts
from app.services import expense_voucher_service as service

NOW = datetime(2026, 10, 27, 12, 0)
CASHIER = 7
OTHER = 8
SESSION_REQUIRED = 'An open cash session is required'
NOT_THE_CASHIER = "Only the voucher's cashier can change it"


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine('sqlite+aiosqlite://', poolclass=StaticPool)
    tables = [
        CashDrawer.__table__,
        CashSession.__table__,
        Employee.__table__,
        Expense.__table__,
        ExpenseVoucher.__table__,
        ExpenseVoucherDetail.__table__,
        Incidence.__table__,
    ]
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=tables)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        yield session
    await engine.dispose()


@pytest.fixture(autouse=True)
def _clock() -> Iterator[None]:
    with patch.object(service, 'local_now', return_value=NOW):
        yield


def _user(employee: int = CASHIER, *, administrator: bool = False, facility: int = 1):
    return CurrentUser(
        user_id='t',
        session_version=1,
        administrator=administrator,
        facility_id=facility,
        employee_id=employee,
    )


async def _drawer(db: AsyncSession, facility: int = 1) -> int:
    drawer = CashDrawer(facility=facility, code=f'D{facility}', name='Cajón')
    db.add(drawer)
    await db.commit()
    return drawer.cash_drawer_id


async def _session(
    db: AsyncSession,
    *,
    cashier: int = CASHIER,
    facility: int = 1,
    start: datetime = datetime(2026, 10, 27, 9),
) -> CashSession:
    session = CashSession(
        start=start, end=None, cashier=cashier, cash_drawer=await _drawer(db, facility)
    )
    db.add(session)
    await db.commit()
    return session


async def _close(db: AsyncSession, session: CashSession) -> None:
    await db.execute(
        update(CashSession)
        .where(CashSession.cash_session_id == session.cash_session_id)
        .values(end=datetime(2026, 10, 27, 18))
    )
    await db.commit()


async def _category(db: AsyncSession, name: str = 'VARIOS', comment: str | None = None) -> int:
    expense = Expense(expense=name, comment=comment)
    db.add(expense)
    await db.commit()
    return expense.expense_id


async def _draft(db: AsyncSession, *, cashier: int = CASHIER) -> ExpenseVoucher:
    return await service.create_voucher(db, ExpenseVoucherCreate(), current=_user(cashier))


async def _draft_in_session(db: AsyncSession) -> ExpenseVoucher:
    """A draft of the cashier's, opening a session for them when they have none."""
    if await cash_session_service.open_session_for_cashier(db, CASHIER) is None:
        await _session(db)
    return await _draft(db)


async def _with_line(db: AsyncSession, amount: str = '100.50') -> ExpenseVoucher:
    voucher = await _draft_in_session(db)
    category = await _category(db)
    return await service.add_line(
        db,
        voucher.expense_voucher_id,
        ExpenseVoucherLineCreate(expense=category, amount=Decimal(amount)),
        current=_user(),
    )


async def _confirmed(db: AsyncSession) -> ExpenseVoucher:
    voucher = await _with_line(db)
    return await service.confirm_voucher(db, voucher.expense_voucher_id, current=_user())


async def _stored(db: AsyncSession, voucher_id: int) -> ExpenseVoucher:
    return (
        await db.execute(
            select(ExpenseVoucher)
            .where(ExpenseVoucher.expense_voucher_id == voucher_id)
            .execution_options(populate_existing=True)
        )
    ).scalar_one()


async def _refused(  # noqa: ANN202
    call,  # noqa: ANN001
    status: int,
    detail: str | None = None,
    code: str | None = None,
    params: dict | None = None,
):
    with pytest.raises(HTTPException) as exc:
        await call
    assert exc.value.status_code == status
    if detail is not None:
        assert exc.value.detail == detail
    if code is not None:
        assert exc.value.code == code
        assert exc.value.params == (params or {})
    return exc.value


# ── States (research R1) ──────────────────────────────────────────────────────


class TestVoucherState:
    @pytest.mark.parametrize(
        ('completed', 'cancelled', 'state', 'voided'),
        [
            (False, False, DocumentStatus.DRAFT, False),
            (None, None, DocumentStatus.DRAFT, False),
            (True, False, DocumentStatus.COMPLETED, False),
            (True, None, DocumentStatus.COMPLETED, False),
            (False, True, DocumentStatus.CANCELLED, False),
            (None, True, DocumentStatus.CANCELLED, False),
            (True, True, DocumentStatus.CANCELLED, True),
        ],
    )
    def test_flags(
        self, completed: bool | None, cancelled: bool | None, state: DocumentStatus, voided: bool
    ) -> None:
        voucher = SimpleNamespace(completed=completed, cancelled=cancelled)

        assert service.voucher_state(voucher) is state
        assert service.is_voided(voucher) is voided


# ── Create (FR-001) ───────────────────────────────────────────────────────────


class TestCreate:
    async def test_without_an_open_session_it_is_refused(self, db: AsyncSession) -> None:
        await _refused(_draft(db), 409, SESSION_REQUIRED, 'cash_session.not_open')

    async def test_the_draft_takes_the_session_and_its_drawers_facility(
        self, db: AsyncSession
    ) -> None:
        session = await _session(db, facility=3)

        voucher = await service.create_voucher(
            db, ExpenseVoucherCreate(comment='gasolina'), current=_user(facility=1)
        )

        assert voucher.cash_session == session.cash_session_id
        assert voucher.facility == 3
        assert (voucher.creator, voucher.updater) == (CASHIER, CASHIER)
        assert (voucher.date, voucher.creation_time, voucher.modification_time) == (NOW,) * 3
        assert voucher.comment == 'gasolina'
        assert (voucher.status, voucher.voided) == (DocumentStatus.DRAFT, False)
        assert (voucher.total, voucher.line_count, voucher.lines) == (Decimal(0), 0, [])

    async def test_with_two_open_sessions_the_newest_is_used(self, db: AsyncSession) -> None:
        await _session(db, start=datetime(2026, 10, 26, 9))
        newest = await _session(db, start=datetime(2026, 10, 27, 9))

        voucher = await _draft(db)

        assert voucher.cash_session == newest.cash_session_id


# ── Edits and lines (FR-002) ──────────────────────────────────────────────────


#: Every creator-only write: `(db, voucher_id, category, line_id, user)` to an unawaited call.
WRITES = {
    'update': lambda db, v, c, line, user: service.update_voucher(
        db, v, ExpenseVoucherUpdate(), current=user
    ),
    'add_line': lambda db, v, c, line, user: service.add_line(
        db, v, ExpenseVoucherLineCreate(expense=c, amount=Decimal('1')), current=user
    ),
    'update_line': lambda db, v, c, line, user: service.update_line(
        db, v, line, ExpenseVoucherLineUpdate(amount=Decimal('2')), current=user
    ),
    'remove_line': lambda db, v, c, line, user: service.remove_line(db, v, line, current=user),
    'confirm': lambda db, v, c, line, user: service.confirm_voucher(db, v, current=user),
    'cancel': lambda db, v, c, line, user: service.cancel_draft(db, v, current=user),
}


def _write(name: str, db: AsyncSession, voucher: ExpenseVoucher, user: CurrentUser):  # noqa: ANN202
    line = voucher.lines[0]
    return WRITES[name](
        db, voucher.expense_voucher_id, line.expense, line.expense_voucher_detail_id, user
    )


class TestEdits:
    @pytest.mark.parametrize('write', WRITES)
    async def test_another_user_is_refused(self, db: AsyncSession, write: str) -> None:
        voucher = await _with_line(db)
        await _session(db, cashier=OTHER)

        await _refused(
            _write(write, db, voucher, _user(OTHER)),
            403,
            NOT_THE_CASHIER,
            'expense_voucher.not_cashier',
        )

    @pytest.mark.parametrize('write', WRITES)
    async def test_a_confirmed_voucher_is_refused(self, db: AsyncSession, write: str) -> None:
        voucher = await _confirmed(db)

        await _refused(
            _write(write, db, voucher, _user()),
            409,
            code='document.not_editable',
            params={'status': 'completed'},
        )

    async def test_an_unknown_voucher_is_404(self, db: AsyncSession) -> None:
        await _session(db)
        await _refused(service.update_voucher(db, 99, ExpenseVoucherUpdate(), current=_user()), 404)
        await _refused(service.confirm_voucher(db, 99, current=_user()), 404)

    async def test_the_comment_is_changed(self, db: AsyncSession) -> None:
        voucher = await _draft_in_session(db)

        changed = await service.update_voucher(
            db, voucher.expense_voucher_id, ExpenseVoucherUpdate(comment='caja'), current=_user()
        )

        assert changed.comment == 'caja'
        assert changed.updater == CASHIER

    async def test_a_line_note_defaults_to_the_categorys_comment(self, db: AsyncSession) -> None:
        voucher = await _draft_in_session(db)
        category = await _category(db, 'ARENA', comment='Arena para entrega')

        await service.add_line(
            db,
            voucher.expense_voucher_id,
            ExpenseVoucherLineCreate(expense=category, amount=Decimal('10')),
            current=_user(),
        )
        given = await service.add_line(
            db,
            voucher.expense_voucher_id,
            ExpenseVoucherLineCreate(expense=category, amount=Decimal('5'), comment='2 m3'),
            current=_user(),
        )

        assert [line.comment for line in given.lines] == ['Arena para entrega', '2 m3']
        assert [line.expense_name for line in given.lines] == ['ARENA', 'ARENA']
        assert (given.total, given.line_count) == (Decimal('15'), 2)

    async def test_an_unknown_category_is_422(self, db: AsyncSession) -> None:
        voucher = await _with_line(db)
        voucher_id, line = voucher.expense_voucher_id, voucher.lines[0].expense_voucher_detail_id

        await _refused(
            service.add_line(
                db,
                voucher_id,
                ExpenseVoucherLineCreate(expense=99, amount=Decimal('1')),
                current=_user(),
            ),
            422,
            code='expense_voucher.unknown_category',
            params={'expense_id': 99},
        )
        await _refused(
            service.update_line(
                db,
                voucher_id,
                line,
                ExpenseVoucherLineUpdate(expense=99),
                current=_user(),
            ),
            422,
        )

    async def test_a_line_is_changed_and_removed(self, db: AsyncSession) -> None:
        voucher = await _with_line(db)
        line = voucher.lines[0].expense_voucher_detail_id
        other = await _category(db, 'TEPOJAL')

        changed = await service.update_line(
            db,
            voucher.expense_voucher_id,
            line,
            ExpenseVoucherLineUpdate(expense=other, amount=Decimal('7.25'), comment='x'),
            current=_user(),
        )
        assert [(x.expense_name, x.amount, x.comment) for x in changed.lines] == [
            ('TEPOJAL', Decimal('7.25'), 'x')
        ]

        removed = await service.remove_line(db, voucher.expense_voucher_id, line, current=_user())

        assert (removed.lines, removed.total) == ([], Decimal(0))

    async def test_a_line_of_another_voucher_is_404(self, db: AsyncSession) -> None:
        first = await _with_line(db)
        first_id, line = first.expense_voucher_id, first.lines[0].expense_voucher_detail_id
        second = (await _draft(db)).expense_voucher_id

        await _refused(service.remove_line(db, second, line, current=_user()), 404)
        await _refused(service.remove_line(db, first_id, 99, current=_user()), 404)


class TestLineAmounts:
    """Research R12: above 0, in cents, at most 99,999,999.99."""

    @pytest.mark.parametrize('amount', ['0', '-1', '1.001', '100000000.00'])
    def test_out_of_range_is_refused(self, amount: str) -> None:
        with pytest.raises(ValidationError):
            ExpenseVoucherLineCreate(expense=1, amount=Decimal(amount))
        with pytest.raises(ValidationError):
            ExpenseVoucherLineUpdate(amount=Decimal(amount))

    @pytest.mark.parametrize('amount', ['0.01', '99999999.99', '50000'])
    def test_in_range_is_accepted(self, amount: str) -> None:
        assert ExpenseVoucherLineCreate(expense=1, amount=Decimal(amount)).amount == Decimal(amount)

    def test_a_long_comment_is_refused(self) -> None:
        with pytest.raises(ValidationError):
            ExpenseVoucherCreate(comment='x' * 501)
        with pytest.raises(ValidationError):
            ExpenseVoucherLineCreate(expense=1, amount=Decimal('1'), comment='x' * 501)


# ── Confirm (FR-003, research R3) ─────────────────────────────────────────────


class TestConfirm:
    async def test_a_total_of_zero_is_refused(self, db: AsyncSession) -> None:
        voucher_id = (await _draft_in_session(db)).expense_voucher_id

        await _refused(
            service.confirm_voucher(db, voucher_id, current=_user()),
            409,
            code='expense_voucher.total_not_positive',
        )
        assert (await _stored(db, voucher_id)).completed is False

    async def test_without_an_open_session_it_is_refused(self, db: AsyncSession) -> None:
        voucher = await _with_line(db)
        await _close(db, await db.get(CashSession, voucher.cash_session))

        await _refused(
            service.confirm_voucher(db, voucher.expense_voucher_id, current=_user()),
            409,
            SESSION_REQUIRED,
            'cash_session.not_open',
        )

    async def test_it_moves_to_the_callers_open_session(self, db: AsyncSession) -> None:
        voucher = await _with_line(db)
        first = await db.get(CashSession, voucher.cash_session)
        await _close(db, first)
        second = await _session(db, facility=2)

        with patch.object(
            cash_session_service,
            'lock_open_session',
            AsyncMock(wraps=cash_session_service.lock_open_session),
        ) as lock:
            confirmed = await service.confirm_voucher(
                db, voucher.expense_voucher_id, current=_user()
            )

        lock.assert_awaited_once_with(db, second.cash_session_id)
        stored = await _stored(db, voucher.expense_voucher_id)
        assert (stored.cash_session, stored.facility) == (second.cash_session_id, 2)
        assert (stored.completed, stored.cancelled) == (True, False)
        assert (stored.date, stored.updater, stored.modification_time) == (NOW, CASHIER, NOW)
        assert (confirmed.status, confirmed.total) == (DocumentStatus.COMPLETED, Decimal('100.50'))


# ── Cancelling a draft (FR-004) ───────────────────────────────────────────────


class TestCancelDraft:
    async def test_the_draft_is_cancelled_not_voided(self, db: AsyncSession) -> None:
        voucher = await _with_line(db)

        cancelled = await service.cancel_draft(db, voucher.expense_voucher_id, current=_user())

        stored = await _stored(db, voucher.expense_voucher_id)
        assert (stored.completed, stored.cancelled) == (False, True)
        assert (cancelled.status, cancelled.voided) == (DocumentStatus.CANCELLED, False)

    async def test_a_cancelled_draft_cannot_be_cancelled_again(self, db: AsyncSession) -> None:
        voucher = await _with_line(db)
        await service.cancel_draft(db, voucher.expense_voucher_id, current=_user())

        await _refused(
            service.cancel_draft(db, voucher.expense_voucher_id, current=_user()),
            409,
            code='document.not_editable',
            params={'status': 'cancelled'},
        )


# ── Void (FR-004, FR-005, research R3) ────────────────────────────────────────


async def _incidences(db: AsyncSession) -> list[Incidence]:
    return list((await db.execute(select(Incidence))).scalars().all())


class TestVoid:
    async def test_a_draft_or_a_cancelled_voucher_is_refused(self, db: AsyncSession) -> None:
        draft = (await _with_line(db)).expense_voucher_id
        cancelled = (await _with_line(db)).expense_voucher_id
        await service.cancel_draft(db, cancelled, current=_user())

        for voucher_id, state in ((draft, 'draft'), (cancelled, 'cancelled')):
            await _refused(
                service.void_voucher(db, voucher_id, 'error', current=_user()),
                409,
                code='expense_voucher.not_confirmed',
                params={'status': state, 'action': 'void'},
            )
        assert await _incidences(db) == []

    async def test_a_voided_voucher_cannot_be_voided_again(self, db: AsyncSession) -> None:
        voucher = await _confirmed(db)
        await service.void_voucher(db, voucher.expense_voucher_id, 'error', current=_user())

        await _refused(
            service.void_voucher(db, voucher.expense_voucher_id, 'error', current=_user()),
            409,
            code='expense_voucher.not_confirmed',
            params={'status': 'cancelled', 'action': 'void'},
        )

    async def test_a_caller_without_a_session_voids_another_cashiers_voucher(
        self, db: AsyncSession
    ) -> None:
        voucher = await _confirmed(db)

        with patch.object(
            cash_session_service,
            'lock_open_session',
            AsyncMock(wraps=cash_session_service.lock_open_session),
        ) as lock:
            voided = await service.void_voucher(
                db, voucher.expense_voucher_id, 'Capturado dos veces', current=_user(OTHER)
            )

        lock.assert_awaited_once_with(db, voucher.cash_session)
        stored = await _stored(db, voucher.expense_voucher_id)
        assert (stored.completed, stored.cancelled) == (True, True)
        assert (stored.updater, stored.modification_time) == (OTHER, NOW)
        assert (voided.status, voided.voided) == (DocumentStatus.CANCELLED, True)
        [entry] = await _incidences(db)
        assert entry.source == int(SourceType.EXPENSE_VOUCHER) == 14
        assert (entry.instance_id, entry.updater) == (voucher.expense_voucher_id, OTHER)
        assert (entry.comment, entry.content) == ('Capturado dos veces', 'Expense voucher voided')

    async def test_a_closed_session_refuses_the_void(self, db: AsyncSession) -> None:
        voucher = await _confirmed(db)
        voucher_id = voucher.expense_voucher_id
        await _close(db, await db.get(CashSession, voucher.cash_session))

        await _refused(
            service.void_voucher(db, voucher_id, 'error', current=_user()),
            409,
            'The cash session is closed',
        )
        assert (await _stored(db, voucher_id)).cancelled is False

    async def test_another_facility_is_403_for_a_non_administrator(self, db: AsyncSession) -> None:
        voucher_id = (await _confirmed(db)).expense_voucher_id

        await _refused(
            service.void_voucher(db, voucher_id, 'error', current=_user(OTHER, facility=2)), 403
        )
        await service.void_voucher(
            db,
            voucher_id,
            'error',
            current=_user(OTHER, administrator=True, facility=2),
        )

    async def test_a_blank_reason_is_422(self, db: AsyncSession) -> None:
        voucher_id = (await _confirmed(db)).expense_voucher_id

        await _refused(service.void_voucher(db, voucher_id, '  ', current=_user()), 422)
        assert (await _stored(db, voucher_id)).cancelled is False

    async def test_an_unknown_voucher_is_404(self, db: AsyncSession) -> None:
        await _refused(service.void_voucher(db, 99, 'error', current=_user()), 404)

    @pytest.mark.parametrize('body', [{}, {'reason': ''}, {'reason': 'x' * 501}])
    def test_the_reason_is_required_and_at_most_500(self, body: dict) -> None:
        with pytest.raises(ValidationError):
            ReversalRequest(**body)


# ── Reads (FR-008) ────────────────────────────────────────────────────────────


class TestReads:
    async def test_get_names_the_creator(self, db: AsyncSession) -> None:
        db.add(
            Employee(
                employee_id=CASHIER,
                first_name='Ana',
                last_name='Ruiz',
                nickname='ana',
                gender=1,
                birthday=datetime(1990, 1, 1).date(),
                sales_person=False,
                status=0,
                start_job_date=datetime(2020, 1, 1).date(),
            )
        )
        await db.commit()
        voucher = await _with_line(db)

        found = await service.get_voucher(db, voucher.expense_voucher_id)

        assert found.creator_name == 'Ana Ruiz'
        assert [line.expense_name for line in found.lines] == ['VARIOS']
        assert await service.get_voucher(db, 99) is None

    async def test_list_scopes_a_non_administrator_to_their_facility(
        self, db: AsyncSession
    ) -> None:
        await _session(db, facility=1)
        mine = await _draft(db)
        await _session(db, cashier=OTHER, facility=2)
        theirs = await _draft(db, cashier=OTHER)

        own, own_total = await service.list_vouchers(db, current=_user())
        everyone, everyone_total = await service.list_vouchers(
            db, current=_user(administrator=True)
        )
        narrowed, _ = await service.list_vouchers(db, current=_user(administrator=True), facility=2)

        assert ([v.expense_voucher_id for v in own], own_total) == ([mine.expense_voucher_id], 1)
        assert [v.expense_voucher_id for v in everyone] == [
            theirs.expense_voucher_id,
            mine.expense_voucher_id,
        ]
        assert everyone_total == 2
        assert [v.expense_voucher_id for v in narrowed] == [theirs.expense_voucher_id]
        await _refused(service.list_vouchers(db, current=_user(), facility=2), 403)

    async def test_list_filters(self, db: AsyncSession) -> None:
        session = await _session(db)
        draft = await _draft(db)
        confirmed = await _confirmed(db)
        cancelled = await _with_line(db)
        await service.cancel_draft(db, cancelled.expense_voucher_id, current=_user())
        admin = _user(administrator=True)

        async def ids(**filters) -> list[int]:
            items, _ = await service.list_vouchers(db, current=admin, **filters)
            return [v.expense_voucher_id for v in items]

        assert await ids(state='draft') == [draft.expense_voucher_id]
        assert await ids(state='completed') == [confirmed.expense_voucher_id]
        assert await ids(state='cancelled') == [cancelled.expense_voucher_id]
        assert await ids(cash_session=session.cash_session_id) == await ids()
        assert await ids(cash_session=99) == []
        assert await ids(creator=OTHER) == []
        assert await ids(date_from=NOW.date(), date_to=NOW.date()) == await ids()
        assert await ids(date_from=datetime(2026, 10, 28).date()) == []
        assert await ids(date_to=datetime(2026, 10, 26).date()) == []
        assert await ids(skip=1, limit=1) == [confirmed.expense_voucher_id]
        await _refused(
            service.list_vouchers(db, current=admin, state='paid'),
            422,
            code='status_filter.unsupported',
            params={'resource': 'expense_voucher', 'status': 'paid'},
        )

    async def test_list_summaries_carry_totals_and_voided(self, db: AsyncSession) -> None:
        voucher = await _confirmed(db)
        await service.void_voucher(db, voucher.expense_voucher_id, 'error', current=_user())

        [item], _ = await service.list_vouchers(db, current=_user())

        assert (item.total, item.line_count) == (Decimal('100.50'), 1)
        assert (item.status, item.voided) == (DocumentStatus.CANCELLED, True)


# ── Ticket (FR-009, research R10) ─────────────────────────────────────────────


def _attached_voucher() -> SimpleNamespace:
    return SimpleNamespace(
        expense_voucher_id=42,
        facility=1,
        date=datetime(2026, 10, 27, 12, 30, 5),
        creator_name='Ana Ruiz',
        total=Decimal('1150.5'),
        lines=[
            SimpleNamespace(expense_name='VARIOS', comment='Garrafones', amount=Decimal('150.5')),
            SimpleNamespace(expense_name='ARENA', comment=None, amount=Decimal('1000')),
        ],
    )


class TestTicket:
    async def test_context(self) -> None:
        header = {'title': 'Ticket de Gastos', 'rfc': 'X'}
        db = AsyncMock()
        with patch.object(print_contexts, 'header_context', AsyncMock(return_value=header)) as head:
            template, context = await print_contexts.expense_voucher_context(
                db, _attached_voucher()
            )

        assert template == 'expense_voucher_ticket.html'
        assert head.await_args.args[1:] == (1, 'Ticket de Gastos')
        assert context['header'] is header
        # Legacy prints the store's address but not its RFC.
        assert header['rfc'] == ''
        assert context['voucher_id'] == '00000042'
        assert context['cashier'] == 'Ana Ruiz'
        assert context['date'] == '2026-10-27 12:30:05'
        assert context['lines'] == [
            {'expense': 'VARIOS', 'comment': 'Garrafones', 'amount': '$150.50'},
            {'expense': 'ARENA', 'comment': '', 'amount': '$1,000.00'},
        ]
        assert context['total'] == '$1,150.50'

    async def test_the_template_prints_the_lines_total_and_both_signatures(self) -> None:
        header = {
            'title': 'Ticket de Gastos',
            'name': 'Matriz',
            'logo': None,
            'skip_address': True,
            'rfc': '',
            'address': [],
        }
        with patch.object(print_contexts, 'header_context', AsyncMock(return_value=header)):
            template, context = await print_contexts.expense_voucher_context(
                AsyncMock(), _attached_voucher()
            )

        html = rendering.environment.get_template(template).render(context)

        for text in ('00000042', 'Ana Ruiz', 'Garrafones', 'ARENA', '$1,150.50'):
            assert text in html
        assert 'Entregó' in html
        assert 'Recibió' in html
