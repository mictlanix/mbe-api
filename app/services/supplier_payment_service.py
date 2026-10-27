"""Supplier payments: numbered payments, their applications to purchase orders, and the balances
(spec 032, US4–US6).

**Locking (research R4).** One order on every path: the cash session row, then the payment row,
then the PO rows ascending, then the facility row (`documents.assign_folio`). Each path pre-reads,
commits, takes its locks, and only then reads what it checks, so its REPEATABLE READ snapshot sees
what earlier lock holders committed: two applications of one payment queue on the payment row, two
payments applied to one PO queue on the PO row, and neither side goes below 0 (FR-017).

**The cutoff (research R6).** A PO counts, with a balance and applications, when it was confirmed
on or after `supplier_balance_cutoff.cutoff`. Until that is set no PO counts and payments are all
credit (FR-020).
"""

from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import date, datetime, timedelta
from decimal import Decimal
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.enums import PaymentMethod, SourceType
from app.models.core import CashDrawer, Facility
from app.models.inventory import InventoryReceipt
from app.models.purchases import (
    PurchaseOrder,
    PurchaseOrderDetail,
    PurchaseOrderPayment,
    SupplierBalanceCutoff,
)
from app.models.supplier import Supplier, SupplierPayment
from app.schemas.sales_order import DocumentStatus
from app.schemas.supplier_payments import (
    ApplicationItem,
    ApplicationsCreate,
    PaymentStatus,
    SupplierPaymentCreate,
)
from app.services import cash_session_service, documents, incidences, totals
from app.services import inventory_documents as inv
from app.services.purchase_order_service import order_status, order_totals

CUTOFF_NOT_SET = 'Balances start at the cutoff, which is not set yet'


def _conflict(reason: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=reason)


def _not_found(what: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f'{what} not found')


def _unprocessable(reason: str) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=reason)


# ── Rules (pure) ──────────────────────────────────────────────────────────────


def po_counts(order: PurchaseOrder, cutoff: datetime | None) -> bool:
    """Confirmed, not cancelled, on or after the cutoff; a confirmed PO's `modification_time` is
    its confirmation time, since spec 031 refuses every later write (R6)."""
    return (
        cutoff is not None
        and order_status(order.completed, order.cancelled) is DocumentStatus.COMPLETED
        and order.modification_time >= cutoff
    )


def payment_credit(payment: SupplierPayment, applied: Decimal) -> Decimal:
    """What is left to apply: 0 once cancelled, and on a legacy payment (clarification Q1)."""
    if payment.cancelled or payment.facility is None:
        return Decimal(0)
    return (payment.amount - applied).quantize(totals.CENTS)


def _assert_applicable(payment: SupplierPayment) -> None:
    if payment.cancelled:
        raise _conflict('The payment is cancelled')
    if payment.facility is None:
        raise _conflict('A legacy payment cannot be applied')


def _check_applications(
    items: Sequence[ApplicationItem],
    *,
    supplier: int,
    credit: Decimal,
    cutoff: datetime | None,
    orders: dict[int, PurchaseOrder],
    balances: dict[int, Decimal],
) -> None:
    """Refuse the whole call on the first item refused (FR-014, FR-020)."""
    if cutoff is None:
        raise _conflict(CUTOFF_NOT_SET)
    for item in items:
        order = orders[item.purchase_order]
        po = order.purchase_order_id
        if order_status(order.completed, order.cancelled) is not DocumentStatus.COMPLETED:
            raise _conflict(f'Purchase order {po} is not confirmed')
        if order.supplier != supplier:
            raise _conflict(f'Purchase order {po} belongs to another supplier')
        if not po_counts(order, cutoff):
            raise _conflict(f'Purchase order {po} was confirmed before the cutoff')
        if item.amount > balances[po]:
            raise _conflict(
                f'The amount exceeds the balance of purchase order {po}: {balances[po]}'
            )
    if sum((item.amount for item in items), Decimal(0)) > credit:
        raise _conflict(f"The amounts exceed the payment's credit: {credit}")


# ── Reads ─────────────────────────────────────────────────────────────────────


async def get_cutoff(db: AsyncSession) -> datetime | None:
    row = await db.get(SupplierBalanceCutoff, 1)
    return None if row is None else row.cutoff


async def _applied(db: AsyncSession, column: Any, ids: Iterable[int]) -> dict[int, Decimal]:
    wanted = set(ids)
    if not wanted:
        return {}
    rows = await db.execute(
        select(column, func.sum(PurchaseOrderPayment.amount))
        .where(column.in_(wanted), PurchaseOrderPayment.cancelled.is_(False))
        .group_by(column)
    )
    return dict(rows.all())


async def po_applied(db: AsyncSession, po_ids: Iterable[int]) -> dict[int, Decimal]:
    """Per PO, the sum of its live applications; a PO without any is absent."""
    return await _applied(db, PurchaseOrderPayment.purchase_order, po_ids)


async def payment_applied(db: AsyncSession, payment_ids: Iterable[int]) -> dict[int, Decimal]:
    """Per payment, the sum of its live applications; a payment without any is absent."""
    return await _applied(db, PurchaseOrderPayment.supplier_payment, payment_ids)


async def _po_totals(db: AsyncSession, po_ids: Iterable[int]) -> dict[int, Decimal]:
    """Per PO, its ordered total with tax (spec 031's `order_totals`, FR-018)."""
    wanted = set(po_ids)
    lines: dict[int, list[PurchaseOrderDetail]] = defaultdict(list)
    if wanted:
        for line in (
            await db.execute(
                select(PurchaseOrderDetail).where(PurchaseOrderDetail.purchase_order.in_(wanted))
            )
        ).scalars():
            lines[line.purchase_order].append(line)
    return {po: order_totals(lines[po])[2] for po in wanted}


async def _balances(db: AsyncSession, po_ids: Iterable[int]) -> dict[int, Decimal]:
    totals_ = await _po_totals(db, po_ids)
    applied = await po_applied(db, totals_)
    return {po: total - applied.get(po, Decimal(0)) for po, total in totals_.items()}


async def _applications(db: AsyncSession, payment_id: int) -> list[PurchaseOrderPayment]:
    return list(
        (
            await db.execute(
                select(PurchaseOrderPayment)
                .where(PurchaseOrderPayment.supplier_payment == payment_id)
                .order_by(PurchaseOrderPayment.purchase_order_payment_id)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )


async def _summaries(db: AsyncSession, payments: Sequence[SupplierPayment]) -> None:
    """`status`, `supplier_name`, `applied` and `credit`, with two grouped reads."""
    if not payments:
        return
    names = dict(
        (
            await db.execute(
                select(Supplier.supplier_id, Supplier.name).where(
                    Supplier.supplier_id.in_({p.supplier for p in payments})
                )
            )
        ).all()
    )
    applied = await payment_applied(db, [p.supplier_payment_id for p in payments])
    for payment in payments:
        live = applied.get(payment.supplier_payment_id, Decimal(0))
        payment.__dict__.update(
            status=PaymentStatus.CANCELLED if payment.cancelled else PaymentStatus.COMPLETED,
            supplier_name=names.get(payment.supplier),
            applied=live,
            credit=payment_credit(payment, live),
        )


async def attach(db: AsyncSession, payment: SupplierPayment) -> SupplierPayment:
    await _summaries(db, [payment])
    payment.__dict__['applications'] = await _applications(db, payment.supplier_payment_id)
    return payment


async def get_payment(db: AsyncSession, payment_id: int) -> SupplierPayment | None:
    payment = await db.get(SupplierPayment, payment_id)
    return None if payment is None else await attach(db, payment)


async def list_payments(
    db: AsyncSession,
    *,
    supplier: int | None = None,
    facility: int | None = None,
    method: int | None = None,
    state: PaymentStatus | None = None,
    date_from: date | None = None,
    date_to: date | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[list[SupplierPayment], int]:
    """Newest first, across facilities: payments have no facility limit on reading (FR-022)."""
    header = SupplierPayment
    clauses: list[ColumnElement[bool]] = []
    if supplier is not None:
        clauses.append(header.supplier == supplier)
    if facility is not None:
        clauses.append(header.facility == facility)
    if method is not None:
        clauses.append(header.method == method)
    if state is not None:
        clauses.append(header.cancelled.is_(state is PaymentStatus.CANCELLED))
    if date_from is not None:
        clauses.append(header.date >= date_from)
    if date_to is not None:
        clauses.append(header.date < date_to + timedelta(days=1))
    total: int = (
        await db.execute(select(func.count()).select_from(header).where(*clauses))
    ).scalar_one()
    items = list(
        (
            await db.execute(
                select(header)
                .where(*clauses)
                .order_by(header.supplier_payment_id.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    await _summaries(db, items)
    return items, total


# ── Locks (R4) ────────────────────────────────────────────────────────────────


async def _lock_payment(db: AsyncSession, payment_id: int) -> SupplierPayment:
    payment = (
        await db.execute(
            select(SupplierPayment)
            .where(SupplierPayment.supplier_payment_id == payment_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if payment is None:
        raise _not_found('Supplier payment')
    return payment


async def _lock_orders(db: AsyncSession, po_ids: Iterable[int]) -> dict[int, PurchaseOrder]:
    """The PO rows `FOR UPDATE`, ascending, so two paths never take them crosswise."""
    ordered = sorted(set(po_ids))
    if not ordered:
        return {}
    orders = {
        order.purchase_order_id: order
        for order in (
            await db.execute(
                select(PurchaseOrder)
                .where(PurchaseOrder.purchase_order_id.in_(ordered))
                .order_by(PurchaseOrder.purchase_order_id)
                .with_for_update()
                .execution_options(populate_existing=True)
            )
        ).scalars()
    }
    if len(orders) != len(ordered):
        raise _not_found('Purchase order')
    return orders


def _add_applications(
    db: AsyncSession, payment_id: int, items: Sequence[ApplicationItem], current: CurrentUser
) -> None:
    now = local_now()
    for item in items:
        db.add(
            PurchaseOrderPayment(
                supplier_payment=payment_id,
                purchase_order=item.purchase_order,
                amount=item.amount,
                applier=current.employee_id,
                date=now,
                cancelled=False,
            )
        )


# ── Create and apply (US4) ────────────────────────────────────────────────────


def _non_cash_facility(data: SupplierPaymentCreate, current: CurrentUser) -> int:
    """The caller's facility; only an administrator without one names it (research R5)."""
    if data.facility is not None:
        if not current.administrator or current.facility_id is not None:
            raise _unprocessable('Only an administrator without a facility can name one')
        return data.facility
    if current.facility_id is None:
        raise _unprocessable('A facility is required')
    return current.facility_id


async def create_payment(
    db: AsyncSession, data: SupplierPaymentCreate, *, current: CurrentUser
) -> SupplierPayment:
    """Record a numbered payment and, optionally, its applications, in one transaction (R4).

    A cash payment comes out of the caller's open session, in its drawer's facility (FR-012).
    """
    items = data.applications
    async with inv.busy_as_conflict(db):
        if await db.get(Supplier, data.supplier) is None:
            raise _not_found('Supplier')
        session_id = None
        if data.method is PaymentMethod.CASH:
            session = await cash_session_service.open_session_for_cashier(db, current.employee_id)
            if session is None:
                raise _conflict('An open cash session is required')
            session_id = session.cash_session_id
            facility = (await db.get(CashDrawer, session.cash_drawer)).facility
        else:
            facility = _non_cash_facility(data, current)
        await db.commit()

        if session_id is not None:
            await cash_session_service.lock_open_session(db, session_id)
        orders = await _lock_orders(db, [item.purchase_order for item in items])
        serial = await documents.assign_folio(db, SupplierPayment, facility=facility)

        if items:
            _check_applications(
                items,
                supplier=data.supplier,
                credit=data.amount,
                cutoff=await get_cutoff(db),
                orders=orders,
                balances=await _balances(db, orders),
            )
        now = local_now()
        payment = SupplierPayment(
            supplier=data.supplier,
            amount=data.amount,
            method=data.method,
            date=now if data.date is None else data.date,
            reference=data.reference,
            comment=data.comment,
            creator=current.employee_id,
            facility=facility,
            serial=serial,
            cash_session=session_id,
            cancelled=False,
            creation_time=now,
            updater=current.employee_id,
            modification_time=now,
        )
        db.add(payment)
        await db.flush()
        _add_applications(db, payment.supplier_payment_id, items, current)
        await db.commit()
    await db.refresh(payment)
    return await attach(db, payment)


async def apply_payment(
    db: AsyncSession, payment_id: int, data: ApplicationsCreate, *, current: CurrentUser
) -> SupplierPayment:
    """Apply a payment's credit to POs of its supplier, all or nothing (FR-014, R4)."""
    items = data.applications
    async with inv.busy_as_conflict(db):
        await db.commit()
        payment = await _lock_payment(db, payment_id)
        _assert_applicable(payment)
        orders = await _lock_orders(db, [item.purchase_order for item in items])

        applied = (await payment_applied(db, [payment_id])).get(payment_id, Decimal(0))
        _check_applications(
            items,
            supplier=payment.supplier,
            credit=payment_credit(payment, applied),
            cutoff=await get_cutoff(db),
            orders=orders,
            balances=await _balances(db, orders),
        )
        _add_applications(db, payment_id, items, current)
        await db.commit()
    return await attach(db, payment)


# ── Reverse and cancel (US5) ──────────────────────────────────────────────────


async def reverse_application(
    db: AsyncSession, payment_id: int, application_id: int, reason: str, *, current: CurrentUser
) -> SupplierPayment:
    """Mark one application reversed, with a reason (FR-015). It is kept, never deleted."""
    async with inv.busy_as_conflict(db):
        application = await db.get(PurchaseOrderPayment, application_id)
        if application is None or application.supplier_payment != payment_id:
            raise _not_found('Application')
        await db.commit()
        payment = await _lock_payment(db, payment_id)
        await _lock_orders(db, [application.purchase_order])

        application = (
            await db.execute(
                select(PurchaseOrderPayment)
                .where(PurchaseOrderPayment.purchase_order_payment_id == application_id)
                .execution_options(populate_existing=True)
            )
        ).scalar_one()
        if application.cancelled:
            raise _conflict('The application is already reversed')
        application.cancelled = True
        incidences.record(
            db,
            source=SourceType.SUPPLIER_PAYMENT,
            instance_id=payment_id,
            updater=current.employee_id,
            reason=reason,
            context=(
                f'Application to purchase order {application.purchase_order} reversed: '
                f'{application.amount}'
            ),
        )
        await db.commit()
    return await attach(db, payment)


async def cancel_payment(
    db: AsyncSession, payment_id: int, reason: str, *, current: CurrentUser
) -> SupplierPayment:
    """Cancel a payment and reverse every live application (FR-016).

    A cash payment cancels only while its session is open: the session row is locked first, so a
    racing close either counts the payment or sees it cancelled (research R2).
    """
    async with inv.busy_as_conflict(db):
        payment = await db.get(SupplierPayment, payment_id)
        if payment is None:
            raise _not_found('Supplier payment')
        session_id = payment.cash_session
        await db.commit()

        if session_id is not None:
            await cash_session_service.lock_open_session(db, session_id)
        payment = await _lock_payment(db, payment_id)
        if payment.cancelled:
            raise _conflict('The payment is already cancelled')
        # No application can be added past the payment lock, so these are all of them.
        live = [a for a in await _applications(db, payment_id) if not a.cancelled]
        await _lock_orders(db, [a.purchase_order for a in live])

        for application in live:
            application.cancelled = True
        payment.cancelled = True
        payment.updater = current.employee_id
        payment.modification_time = local_now()
        incidences.record(
            db,
            source=SourceType.SUPPLIER_PAYMENT,
            instance_id=payment_id,
            updater=current.employee_id,
            reason=reason,
            context='Supplier payment cancelled',
        )
        await db.commit()
    return await attach(db, payment)


# ── Balances (US6, research R7) ───────────────────────────────────────────────


async def purchase_order_balance(db: AsyncSession, order: PurchaseOrder) -> dict[str, Any]:
    """A PO's total, what is applied, and its balance, null when it doesn't count (FR-019)."""
    po = order.purchase_order_id
    counts = po_counts(order, await get_cutoff(db))
    total = (await _po_totals(db, [po]))[po]
    applied = (await po_applied(db, [po])).get(po, Decimal(0))
    rows = (
        await db.execute(
            select(PurchaseOrderPayment, SupplierPayment.serial)
            .join(
                SupplierPayment,
                SupplierPayment.supplier_payment_id == PurchaseOrderPayment.supplier_payment,
            )
            .where(PurchaseOrderPayment.purchase_order == po)
            .order_by(PurchaseOrderPayment.purchase_order_payment_id)
        )
    ).all()
    applications = []
    for application, serial in rows:
        application.__dict__['serial'] = serial
        applications.append(application)
    return {
        'purchase_order_id': po,
        'supplier': order.supplier,
        'status': order_status(order.completed, order.cancelled),
        'counts': counts,
        'total': total,
        'applied': applied,
        'balance': total - applied if counts else None,
        'applications': applications,
    }


async def open_purchase_orders(
    db: AsyncSession, *, supplier: int | None = None
) -> list[dict[str, Any]]:
    """The counting POs with more than 0.01 left, oldest confirmed first, each with its confirmed
    receipts (spec 033, FR-008). None while the cutoff is NULL.

    A receipt's confirmation stamps its `modification_time` and nothing edits a confirmed receipt
    afterwards, so the earliest one dates the PO's due date (research R6). A cancelled receipt
    drops out, and the next confirmed one dates it.
    """
    cutoff = await get_cutoff(db)
    if cutoff is None:
        return []
    query = (
        select(PurchaseOrder)
        .where(
            PurchaseOrder.supplier.is_not(None),
            PurchaseOrder.completed.is_(True),
            PurchaseOrder.cancelled.is_(False),
            PurchaseOrder.modification_time >= cutoff,
        )
        .order_by(PurchaseOrder.modification_time, PurchaseOrder.purchase_order_id)
    )
    if supplier is not None:
        query = query.where(PurchaseOrder.supplier == supplier)
    counting = list((await db.execute(query)).scalars().all())
    po_ids = [order.purchase_order_id for order in counting]
    totals_ = await _po_totals(db, po_ids)
    applied = await po_applied(db, po_ids)

    receipts: dict[int, list[dict[str, Any]]] = defaultdict(list)
    if po_ids:
        rows = await db.execute(
            select(
                InventoryReceipt.purchase_order,
                InventoryReceipt.inventory_receipt_id,
                Facility.code,
                InventoryReceipt.serial,
                InventoryReceipt.modification_time,
            )
            .join(Facility, Facility.facility_id == InventoryReceipt.facility)
            .where(
                InventoryReceipt.purchase_order.in_(po_ids),
                InventoryReceipt.completed.is_(True),
                InventoryReceipt.cancelled.is_(False),
            )
            .order_by(InventoryReceipt.modification_time, InventoryReceipt.inventory_receipt_id)
        )
        for po, receipt_id, code, serial, confirmed in rows.all():
            receipts[po].append({'id': receipt_id, 'label': f'{code}-{serial}', 'time': confirmed})

    open_ = []
    for order in counting:
        po = order.purchase_order_id
        live = applied.get(po, Decimal(0))
        if totals_[po] - live > totals.SETTLED:
            open_.append(
                {
                    'order': order,
                    'total': totals_[po],
                    'applied': live,
                    'balance': totals_[po] - live,
                    'receipts': receipts[po],
                    'first_receipt': receipts[po][0]['time'] if receipts[po] else None,
                }
            )
    return open_


async def payments_with_credit(
    db: AsyncSession, *, suppliers: Sequence[int]
) -> list[tuple[SupplierPayment, Decimal]]:
    """The suppliers' payments with credit left, oldest first, with that credit (FR-019)."""
    if not suppliers:
        return []
    candidates = (
        (
            await db.execute(
                select(SupplierPayment)
                .where(
                    SupplierPayment.supplier.in_(list(suppliers)),
                    SupplierPayment.cancelled.is_(False),
                    SupplierPayment.facility.is_not(None),
                )
                .order_by(SupplierPayment.date, SupplierPayment.supplier_payment_id)
            )
        )
        .scalars()
        .all()
    )
    used = await payment_applied(db, [p.supplier_payment_id for p in candidates])
    with_credit = []
    for payment in candidates:
        credit = payment_credit(payment, used.get(payment.supplier_payment_id, Decimal(0)))
        if credit > 0:
            with_credit.append((payment, credit))
    return with_credit


async def credit_by_supplier(db: AsyncSession, supplier_ids: Sequence[int]) -> dict[int, Decimal]:
    """Each supplier's credit, the total `supplier_balance` reports, batched (spec 033)."""
    credit: dict[int, Decimal] = {}
    for payment, left in await payments_with_credit(db, suppliers=supplier_ids):
        credit[payment.supplier] = credit.get(payment.supplier, Decimal(0)) + left
    return credit


async def supplier_balance(db: AsyncSession, supplier_id: int) -> dict[str, Any]:
    """The counting POs with a balance and the payments with credit, oldest first, and the totals
    owed, in credit and net (FR-019). The AP view reads the same two lists (spec 033)."""
    supplier = await db.get(Supplier, supplier_id)
    if supplier is None:
        raise _not_found('Supplier')
    cutoff = await get_cutoff(db)

    orders = [
        {
            'purchase_order_id': row['order'].purchase_order_id,
            'confirmed': row['order'].modification_time,
            'total': row['total'],
            'applied': row['applied'],
            'balance': row['balance'],
        }
        for row in await open_purchase_orders(db, supplier=supplier_id)
    ]
    payments = [
        {
            'supplier_payment_id': payment.supplier_payment_id,
            'facility': payment.facility,
            'serial': payment.serial,
            'date': payment.date,
            'amount': payment.amount,
            'credit': credit,
        }
        for payment, credit in await payments_with_credit(db, suppliers=[supplier_id])
    ]

    owed = sum((o['balance'] for o in orders), Decimal(0))
    credit = sum((p['credit'] for p in payments), Decimal(0))
    return {
        'supplier': supplier_id,
        'supplier_name': supplier.name,
        'cutoff': cutoff,
        'owed': owed,
        'credit': credit,
        'net': owed - credit,
        'orders': orders,
        'payments': payments,
    }
