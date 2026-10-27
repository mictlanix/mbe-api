"""Supplier payments (spec 032, US4–US6): create, apply, reverse, cancel, and the balances.

The service runs against an in-memory SQLite database with foreign keys off, so each test seeds only
the rows it reads. SQLite has no row locks; the lock order is checked from the statements issued.
"""

import importlib
import pkgutil
from collections.abc import AsyncIterator
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from pydantic import ValidationError
from sqlalchemy import event, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models
from app.core.deps import CurrentUser
from app.db.base import Base
from app.enums import EntityStatus, FacilityType, PaymentMethod, SourceType
from app.models.core import CashDrawer, CashSession, Facility
from app.models.incidence import Incidence
from app.models.purchases import (
    PurchaseOrder,
    PurchaseOrderDetail,
    PurchaseOrderPayment,
    SupplierBalanceCutoff,
)
from app.models.supplier import Supplier, SupplierPayment
from app.schemas.supplier_payments import ApplicationsCreate, SupplierPaymentCreate
from app.services import supplier_payment_service as service

for _, _name, _ in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f'app.models.{_name}')

NOW = datetime(2026, 10, 27, 12, 0)
CUTOFF = datetime(2026, 10, 1)
CASH, EFT = PaymentMethod.CASH, PaymentMethod.EFT


@pytest.fixture
async def db() -> AsyncIterator[AsyncSession]:
    engine = create_async_engine('sqlite+aiosqlite://', poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with async_sessionmaker(engine, expire_on_commit=False)() as session:
        for facility_id in (1, 2):
            session.add(
                Facility(
                    facility_id=facility_id,
                    code=f'F{facility_id}',
                    name=f'Sucursal {facility_id}',
                    type=FacilityType.STORE,
                    location='06000',
                    address=1,
                    taxpayer='AAA010101AAA',
                    status=EntityStatus.ACTIVE,
                )
            )
        # The drawer is in facility 2, the cashier's token in facility 1 (research R5).
        session.add(CashDrawer(cash_drawer_id=1, facility=2, code='CD1', name='Cajón 1'))
        for supplier_id in (1, 2):
            session.add(
                Supplier(
                    supplier_id=supplier_id,
                    code=f'S{supplier_id}',
                    name=f'Proveedor {supplier_id}',
                    credit_limit=Decimal(0),
                    credit_days=0,
                )
            )
        session.add(SupplierBalanceCutoff(supplier_balance_cutoff_id=1, cutoff=CUTOFF))
        await session.commit()
        with patch.object(service, 'local_now', return_value=NOW):
            yield session
    await engine.dispose()


def _user(*, administrator: bool = False, facility_id: int | None = 1) -> CurrentUser:
    return CurrentUser(
        user_id='t',
        session_version=1,
        administrator=administrator,
        facility_id=facility_id,
        employee_id=7,
    )


async def _order(
    db: AsyncSession,
    total: str,
    *,
    supplier: int = 1,
    completed: bool = True,
    cancelled: bool = False,
    confirmed: datetime = CUTOFF + timedelta(days=1),
) -> int:
    """A PO with one untaxed line, so its total is `total`."""
    order = PurchaseOrder(
        supplier=supplier,
        creation_time=confirmed,
        modification_time=confirmed,
        creator=1,
        updater=1,
        completed=completed,
        cancelled=cancelled,
        approved=False,
    )
    db.add(order)
    await db.flush()
    db.add(
        PurchaseOrderDetail(
            purchase_order=order.purchase_order_id,
            product=1,
            quantity=Decimal(1),
            price=Decimal(total),
            discount=Decimal(0),
            tax_rate=Decimal(0),
            product_code='P1',
            product_name='Producto',
            exchange_rate=Decimal(1),
            currency=1,
            tax_included=False,
        )
    )
    await db.commit()
    return order.purchase_order_id


async def _session(db: AsyncSession, *, cashier: int = 7) -> int:
    session = CashSession(start=NOW - timedelta(hours=3), cashier=cashier, cash_drawer=1)
    db.add(session)
    await db.commit()
    return session.cash_session_id


async def _create(db: AsyncSession, current: CurrentUser | None = None, **body) -> SupplierPayment:
    body = {'supplier': 1, 'amount': '10000', 'method': EFT} | body
    return await service.create_payment(
        db, SupplierPaymentCreate(**body), current=current or _user()
    )


async def _apply(db: AsyncSession, payment: int, *items: tuple[int, str]) -> SupplierPayment:
    data = ApplicationsCreate(
        applications=[{'purchase_order': po, 'amount': amount} for po, amount in items]
    )
    return await service.apply_payment(db, payment, data, current=_user())


async def _applications(db: AsyncSession, payment: int) -> list[PurchaseOrderPayment]:
    return list(
        (
            await db.execute(
                select(PurchaseOrderPayment)
                .where(PurchaseOrderPayment.supplier_payment == payment)
                .order_by(PurchaseOrderPayment.purchase_order_payment_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _incidences(db: AsyncSession) -> list[Incidence]:
    return list((await db.execute(select(Incidence))).scalars().all())


async def _refused(call, code: int = 409) -> str:  # noqa: ANN001
    with pytest.raises(HTTPException) as exc:
        await call
    assert exc.value.status_code == code, exc.value.detail
    return exc.value.detail


def _locks(db: AsyncSession) -> list[tuple[str, bool]]:
    """`(table, locked)` of every statement the session runs from here on, and `('COMMIT', …)`."""
    log: list[tuple[str, bool]] = []

    @event.listens_for(db.sync_session, 'do_orm_execute')
    def _statement(state) -> None:  # noqa: ANN001
        if state.is_select:
            entity = state.statement.column_descriptions[0].get('entity')
            table = getattr(entity, '__tablename__', None)
            log.append((table, state.statement._for_update_arg is not None))

    @event.listens_for(db.sync_session, 'after_commit')
    def _commit(_session) -> None:  # noqa: ANN001
        log.append(('COMMIT', False))

    return log


def _after_last_commit_before_writes(log: list[tuple[str, bool]]) -> list[tuple[str, bool]]:
    commits = [i for i, (table, _) in enumerate(log) if table == 'COMMIT']
    return log[commits[-2] + 1 : commits[-1]]


def _assert_locks_first(steps: list[tuple[str, bool]], locked: list[str]) -> None:
    """The locks come in `locked`'s order, and every ordinary read after the last of them."""
    assert [table for table, lock in steps if lock] == locked
    last_lock = max(i for i, (_, lock) in enumerate(steps) if lock)
    assert all(lock for _, lock in steps[: last_lock + 1])


# ── Create (FR-010 to FR-013) ─────────────────────────────────────────────────


@pytest.mark.parametrize(
    'body',
    [
        {'amount': '0'},
        {'amount': '-5'},
        {'amount': '1.001'},
        {'amount': '100000000'},
        {'date': (datetime.now() + timedelta(days=1)).isoformat()},
        {'method': 77},
        {'reference': 'x' * 51},
        {'comment': 'x' * 501},
        {'method': CASH, 'date': '2026-10-01T10:00:00'},
        {'method': CASH, 'facility': 2},
        {'applications': [{'purchase_order': 1, 'amount': '1'}] * 2},
        {'applications': [{'purchase_order': n, 'amount': '1'} for n in range(51)]},
        {'applications': [{'purchase_order': 1, 'amount': '0'}]},
    ],
)
def test_a_bad_body_is_refused(body: dict) -> None:
    with pytest.raises(ValidationError):
        SupplierPaymentCreate(**({'supplier': 1, 'amount': '10', 'method': EFT} | body))


@pytest.mark.parametrize(
    'items',
    [[], [{'purchase_order': 1, 'amount': '1'}] * 2, [{'purchase_order': 1, 'amount': '-1'}]],
)
def test_a_bad_applications_body_is_refused(items: list) -> None:
    with pytest.raises(ValidationError):
        ApplicationsCreate(applications=items)


def test_fifty_distinct_applications_are_accepted() -> None:
    items = [{'purchase_order': n, 'amount': '1'} for n in range(50)]
    assert len(ApplicationsCreate(applications=items).applications) == 50


async def test_a_non_cash_payment_takes_the_callers_facility_and_no_session(db) -> None:
    payment = await _create(db, reference='SPEI-1', comment='Factura A')

    assert (payment.facility, payment.serial, payment.cash_session) == (1, 1, None)
    assert (payment.creator, payment.updater) == (7, 7)
    assert payment.date == payment.creation_time == payment.modification_time == NOW
    assert (payment.reference, payment.comment, payment.cancelled) == ('SPEI-1', 'Factura A', False)
    assert (payment.applied, payment.credit) == (Decimal(0), Decimal('10000.00'))


async def test_a_non_cash_payment_keeps_the_date_sent(db) -> None:
    payment = await _create(db, date='2026-10-20T09:30:00')
    assert payment.date == datetime(2026, 10, 20, 9, 30)


async def test_a_cash_payment_needs_the_callers_open_session(db) -> None:
    await _session(db, cashier=99)
    detail = await _refused(_create(db, method=CASH))
    assert detail == 'An open cash session is required'


async def test_a_cash_payment_takes_the_session_and_its_drawers_facility(db) -> None:
    session = await _session(db)

    payment = await _create(db, method=CASH)

    assert (payment.cash_session, payment.facility, payment.serial) == (session, 2, 1)
    assert payment.date == NOW


async def test_a_cash_payment_into_a_session_closed_meanwhile_is_refused(db) -> None:
    session = await _session(db)
    real = service.cash_session_service.open_session_for_cashier

    async def _read_then_close(db, cashier):  # noqa: ANN001, ANN202
        found = await real(db, cashier)
        await db.execute(
            update(CashSession)
            .where(CashSession.cash_session_id == session)
            .values(end=NOW)
            .execution_options(synchronize_session=False)
        )
        return found

    with patch.object(service.cash_session_service, 'open_session_for_cashier', _read_then_close):
        detail = await _refused(_create(db, method=CASH))
    assert detail == 'The cash session is closed'


async def test_a_body_facility_from_a_non_administrator_is_422(db) -> None:
    await _refused(_create(db, facility=2), 422)


async def test_a_body_facility_from_an_administrator_with_a_facility_is_422(db) -> None:
    await _refused(_create(db, _user(administrator=True), facility=2), 422)


async def test_an_administrator_without_a_facility_names_it(db) -> None:
    payment = await _create(db, _user(administrator=True, facility_id=None), facility=2)
    assert payment.facility == 2


async def test_an_administrator_without_a_facility_must_name_one(db) -> None:
    await _refused(_create(db, _user(administrator=True, facility_id=None)), 422)


async def test_serials_are_numbered_per_facility(db) -> None:
    await _session(db)
    serials = [
        ((p := await _create(db, **body)).facility, p.serial)
        for body in ({}, {}, {'method': CASH}, {})
    ]
    assert serials == [(1, 1), (1, 2), (2, 1), (1, 3)]


async def test_an_unknown_supplier_is_404(db) -> None:
    await _refused(_create(db, supplier=999), 404)


async def test_create_with_applications(db) -> None:
    first, second = await _order(db, '6000'), await _order(db, '8000')

    payment = await _create(
        db,
        applications=[
            {'purchase_order': second, 'amount': '3000'},
            {'purchase_order': first, 'amount': '6000'},
        ],
    )

    assert (payment.applied, payment.credit) == (Decimal('9000.00'), Decimal('1000.00'))
    rows = await _applications(db, payment.supplier_payment_id)
    assert sorted((a.purchase_order, a.amount, a.applier, a.date) for a in rows) == [
        (first, Decimal('6000'), 7, NOW),
        (second, Decimal('3000'), 7, NOW),
    ]


async def test_cash_create_locks_session_then_orders_ascending_then_facility(db) -> None:
    await _session(db)
    orders = [await _order(db, '10'), await _order(db, '10')]
    log = _locks(db)

    await _create(
        db,
        method=CASH,
        applications=[{'purchase_order': po, 'amount': '1'} for po in reversed(orders)],
    )

    _assert_locks_first(
        _after_last_commit_before_writes(log), ['cash_session', 'purchase_order', 'facility']
    )


# ── Applications (FR-014, FR-017, FR-020) ─────────────────────────────────────


async def _refuse_application(db: AsyncSession, payment: int, po: int, amount: str) -> str:
    before = len(await _applications(db, payment))
    detail = await _refused(_apply(db, payment, (po, amount)))
    assert len(await _applications(db, payment)) == before
    return detail


async def test_draft_and_cancelled_orders_are_refused(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    draft = await _order(db, '100', completed=False)
    cancelled = await _order(db, '100', cancelled=True)

    assert 'not confirmed' in await _refuse_application(db, payment, draft, '1')
    assert 'not confirmed' in await _refuse_application(db, payment, cancelled, '1')


async def test_another_suppliers_order_is_refused(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    other = await _order(db, '100', supplier=2)

    assert 'another supplier' in await _refuse_application(db, payment, other, '1')


async def test_with_no_cutoff_every_application_is_refused(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    po = await _order(db, '100')
    await db.execute(update(SupplierBalanceCutoff).values(cutoff=None))
    await db.commit()

    detail = await _refuse_application(db, payment, po, '1')
    assert detail == 'Balances start at the cutoff, which is not set yet'


async def test_an_order_confirmed_before_the_cutoff_is_refused(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    old = await _order(db, '100', confirmed=CUTOFF - timedelta(seconds=1))
    on_the_cutoff = await _order(db, '100', confirmed=CUTOFF)

    assert 'before the cutoff' in await _refuse_application(db, payment, old, '1')
    await _apply(db, payment, (on_the_cutoff, '1'))


async def test_more_than_the_order_balance_is_refused(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    po = await _order(db, '6000')
    await _apply(db, payment, (po, '5000'))

    detail = await _refused(_apply(db, payment, (po, '1000.01')))
    assert 'balance' in detail
    await _apply(db, payment, (po, '1000'))


async def test_more_than_the_payment_credit_is_refused_across_items(db) -> None:
    payment = (await _create(db, amount='100')).supplier_payment_id
    first, second = await _order(db, '80'), await _order(db, '80')

    assert 'credit' in await _refused(_apply(db, payment, (first, '60'), (second, '40.01')))
    assert await _applications(db, payment) == []
    await _apply(db, payment, (first, '60'), (second, '40'))


async def test_one_refused_item_refuses_the_whole_call(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    good, draft = await _order(db, '100'), await _order(db, '100', completed=False)

    await _refused(_apply(db, payment, (good, '10'), (draft, '10')))
    assert await _applications(db, payment) == []


async def test_a_cancelled_or_legacy_payment_takes_no_applications(db) -> None:
    po = await _order(db, '100')
    cancelled = (await _create(db)).supplier_payment_id
    await service.cancel_payment(db, cancelled, 'error', current=_user())
    legacy = SupplierPayment(
        supplier=1, amount=Decimal(500), method=EFT, date=NOW, creator=1, cancelled=False
    )
    db.add(legacy)
    await db.commit()
    legacy_id = legacy.supplier_payment_id

    assert 'cancelled' in await _refuse_application(db, cancelled, po, '1')
    assert 'legacy' in await _refuse_application(db, legacy_id, po, '1')


async def test_an_unknown_order_or_payment_is_404(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    await _refused(_apply(db, payment, (999, '1')), 404)
    await _refused(_apply(db, 999, (await _order(db, '10'), '1')), 404)


async def test_apply_locks_the_payment_then_the_orders_ascending(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    orders = [await _order(db, '10'), await _order(db, '10')]
    log = _locks(db)

    await _apply(db, payment, *[(po, '1') for po in reversed(orders)])

    _assert_locks_first(
        _after_last_commit_before_writes(log), ['supplier_payment', 'purchase_order']
    )


# ── Arithmetic (FR-018, research R7) ──────────────────────────────────────────


def test_credit_is_the_amount_less_applied_and_0_when_cancelled_or_legacy() -> None:
    live = SupplierPayment(amount=Decimal('100.0000'), facility=1, cancelled=False)
    cancelled = SupplierPayment(amount=Decimal(100), facility=1, cancelled=True)
    legacy = SupplierPayment(amount=Decimal(100), facility=None, cancelled=False)

    assert service.payment_credit(live, Decimal('30')) == Decimal('70.00')
    assert service.payment_credit(cancelled, Decimal(0)) == 0
    assert service.payment_credit(legacy, Decimal(0)) == 0


@pytest.mark.parametrize(
    ('completed', 'cancelled', 'confirmed', 'cutoff', 'counts'),
    [
        (True, False, CUTOFF, CUTOFF, True),
        (True, False, CUTOFF - timedelta(seconds=1), CUTOFF, False),
        (True, False, CUTOFF, None, False),
        (False, False, CUTOFF, CUTOFF, False),
        (True, True, CUTOFF, CUTOFF, False),
    ],
)
def test_which_orders_count(completed, cancelled, confirmed, cutoff, counts) -> None:  # noqa: ANN001
    order = PurchaseOrder(completed=completed, cancelled=cancelled, modification_time=confirmed)
    assert service.po_counts(order, cutoff) is counts


async def test_a_reversed_application_counts_for_neither_side(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    po = await _order(db, '6000')
    await _apply(db, payment, (po, '6000'))
    (application,) = await _applications(db, payment)
    await service.reverse_application(
        db, payment, application.purchase_order_payment_id, 'wrong PO', current=_user()
    )

    assert await service.payment_applied(db, [payment]) == {}
    assert await service.po_applied(db, [po]) == {}
    await _apply(db, payment, (po, '6000'))


# ── Reverse and cancel (US5, FR-015, FR-016) ──────────────────────────────────


async def test_reversing_marks_the_application_and_writes_one_incidence(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    po = await _order(db, '6000')
    await _apply(db, payment, (po, '2500.50'))
    (application,) = await _applications(db, payment)
    app_id = application.purchase_order_payment_id

    reversed_ = await service.reverse_application(
        db, payment, app_id, ' wrong PO ', current=_user()
    )

    assert (reversed_.applied, reversed_.credit) == (Decimal(0), Decimal('10000.00'))
    assert (await _applications(db, payment))[0].cancelled is True
    (incidence,) = await _incidences(db)
    assert (incidence.source, incidence.instance_id) == (SourceType.SUPPLIER_PAYMENT, payment)
    assert incidence.content == f'Application to purchase order {po} reversed: 2500.50'
    assert (incidence.comment, incidence.updater) == ('wrong PO', 7)

    detail = await _refused(service.reverse_application(db, payment, app_id, 'x', current=_user()))
    assert detail == 'The application is already reversed'


async def test_an_application_of_another_payment_is_404(db) -> None:
    po = await _order(db, '6000')
    payment = (await _create(db)).supplier_payment_id
    other = (await _create(db)).supplier_payment_id
    await _apply(db, payment, (po, '10'))
    (application,) = await _applications(db, payment)

    call = service.reverse_application(
        db, other, application.purchase_order_payment_id, 'x', current=_user()
    )
    await _refused(call, 404)
    await _refused(service.reverse_application(db, payment, 999, 'x', current=_user()), 404)


async def test_reverse_locks_the_payment_then_the_order(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    await _apply(db, payment, (await _order(db, '10'), '1'))
    (application,) = await _applications(db, payment)
    log = _locks(db)

    await service.reverse_application(
        db, payment, application.purchase_order_payment_id, 'x', current=_user()
    )

    _assert_locks_first(
        _after_last_commit_before_writes(log), ['supplier_payment', 'purchase_order']
    )


async def test_cancelling_reverses_every_live_application(db) -> None:
    payment = (await _create(db)).supplier_payment_id
    first, second = await _order(db, '6000'), await _order(db, '8000')
    await _apply(db, payment, (first, '6000'), (second, '3000'))

    cancelled = await service.cancel_payment(db, payment, 'duplicated', current=_user())

    assert cancelled.cancelled is True
    assert (cancelled.updater, cancelled.modification_time) == (7, NOW)
    assert (cancelled.status, cancelled.credit) == ('cancelled', Decimal(0))
    assert [a.cancelled for a in await _applications(db, payment)] == [True, True]
    assert await service.po_applied(db, [first, second]) == {}
    (incidence,) = await _incidences(db)
    assert (incidence.source, incidence.instance_id) == (SourceType.SUPPLIER_PAYMENT, payment)
    assert (incidence.content, incidence.comment) == ('Supplier payment cancelled', 'duplicated')

    detail = await _refused(service.cancel_payment(db, payment, 'again', current=_user()))
    assert detail == 'The payment is already cancelled'
    assert 'cancelled' in await _refuse_application(db, payment, first, '1')


async def test_a_cash_payment_cancels_only_while_its_session_is_open(db) -> None:
    session = await _session(db)
    payment = (await _create(db, method=CASH)).supplier_payment_id
    await db.execute(
        update(CashSession).where(CashSession.cash_session_id == session).values(end=NOW)
    )
    await db.commit()

    detail = await _refused(service.cancel_payment(db, payment, 'x', current=_user()))
    assert detail == 'The cash session is closed'


async def test_cancel_locks_session_then_payment_then_orders_ascending(db) -> None:
    await _session(db)
    payment = (await _create(db, method=CASH)).supplier_payment_id
    orders = [await _order(db, '10'), await _order(db, '10')]
    await _apply(db, payment, *[(po, '1') for po in orders])
    log = _locks(db)

    await service.cancel_payment(db, payment, 'x', current=_user())

    steps = _after_last_commit_before_writes(log)
    assert [table for table, lock in steps if lock] == [
        'cash_session',
        'supplier_payment',
        'purchase_order',
    ]


async def test_cancelling_an_unknown_payment_is_404(db) -> None:
    await _refused(service.cancel_payment(db, 999, 'x', current=_user()), 404)


# ── Balances (US6, FR-019, FR-020) ────────────────────────────────────────────


async def test_an_order_balance_with_its_applications(db) -> None:
    po = await _order(db, '6000')
    payment = await _create(db)
    await _apply(
        db,
        payment.supplier_payment_id,
        (po, '1000'),
    )
    await _apply(db, payment.supplier_payment_id, (po, '500'))
    (first, _) = await _applications(db, payment.supplier_payment_id)
    await service.reverse_application(
        db, payment.supplier_payment_id, first.purchase_order_payment_id, 'x', current=_user()
    )

    balance = await service.purchase_order_balance(db, await db.get(PurchaseOrder, po))

    assert (balance['status'], balance['counts']) == ('completed', True)
    assert (balance['total'], balance['applied'], balance['balance']) == (
        Decimal('6000.00'),
        Decimal('500.00'),
        Decimal('5500.00'),
    )
    assert [
        (a.supplier_payment, a.serial, a.amount, a.cancelled) for a in balance['applications']
    ] == [
        (payment.supplier_payment_id, 1, Decimal(1000), True),
        (payment.supplier_payment_id, 1, Decimal(500), False),
    ]


@pytest.mark.parametrize('cutoff', [None, 'after'])
async def test_an_order_that_does_not_count_has_no_balance(db, cutoff) -> None:  # noqa: ANN001
    po = await _order(db, '6000')
    value = None if cutoff is None else CUTOFF + timedelta(days=30)
    await db.execute(update(SupplierBalanceCutoff).values(cutoff=value))
    await db.commit()

    balance = await service.purchase_order_balance(db, await db.get(PurchaseOrder, po))

    assert (balance['counts'], balance['balance'], balance['total']) == (
        False,
        None,
        Decimal('6000.00'),
    )


async def test_the_supplier_summary(db) -> None:
    paid = await _order(db, '6000', confirmed=CUTOFF + timedelta(days=1))
    open_ = await _order(db, '8000', confirmed=CUTOFF + timedelta(days=3))
    older = await _order(db, '700', confirmed=CUTOFF + timedelta(days=2))
    await _order(db, '999', confirmed=CUTOFF - timedelta(days=1))
    await _order(db, '999', completed=False)
    await _order(db, '999', supplier=2)
    first = await _create(db, amount='10000', date='2026-10-20T09:00:00')
    await _apply(db, first.supplier_payment_id, (paid, '6000'), (open_, '3000'))
    spent = await _create(db, amount='700', date='2026-10-10T09:00:00')
    await _apply(db, spent.supplier_payment_id, (older, '700'))
    cancelled = await _create(db, amount='50')
    await service.cancel_payment(db, cancelled.supplier_payment_id, 'x', current=_user())
    db.add(SupplierPayment(supplier=1, amount=Decimal(9), method=EFT, date=NOW, creator=1))
    second = await _create(db, amount='200', date='2026-10-05T09:00:00')
    await db.commit()

    summary = await service.supplier_balance(db, 1)

    assert (summary['supplier'], summary['supplier_name'], summary['cutoff']) == (
        1,
        'Proveedor 1',
        CUTOFF,
    )
    assert [
        (o['purchase_order_id'], o['total'], o['applied'], o['balance']) for o in summary['orders']
    ] == [(open_, Decimal('8000.00'), Decimal('3000.00'), Decimal('5000.00'))]
    assert summary['orders'][0]['confirmed'] == CUTOFF + timedelta(days=3)
    assert [(p['supplier_payment_id'], p['credit']) for p in summary['payments']] == [
        (second.supplier_payment_id, Decimal('200.00')),
        (first.supplier_payment_id, Decimal('1000.00')),
    ]
    assert (summary['owed'], summary['credit'], summary['net']) == (
        Decimal('5000.00'),
        Decimal('1200.00'),
        Decimal('3800.00'),
    )


async def test_the_supplier_summary_with_no_cutoff_is_all_credit(db) -> None:
    await _order(db, '6000')
    await _create(db, amount='100')
    await db.execute(update(SupplierBalanceCutoff).values(cutoff=None))
    await db.commit()

    summary = await service.supplier_balance(db, 1)

    assert (summary['cutoff'], summary['orders'], summary['owed']) == (None, [], Decimal(0))
    assert summary['credit'] == summary['net'] * -1 == Decimal('100.00')


async def test_the_summary_of_an_unknown_supplier_is_404(db) -> None:
    await _refused(service.supplier_balance(db, 999), 404)
