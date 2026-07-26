"""Customer refunds — goods coming back off a paid order.

The lifecycle rule that shapes this whole module: a refund requires a **paid** order (FR-060).
Two consequences follow and neither is incidental.

1. The source order's balance is therefore always zero, so the legacy behaviour of applying a
   refund against a remaining balance cannot arise. FR-064 says explicitly that it must not be
   implemented, and the order's `paid` flag is never touched here.
2. The entire refund total is owed back to the customer, as cash from the open session or as a
   credit note — the cashier chooses at confirmation (FR-065).

Confirmation re-validates every quantity under a lock on the source order (research R2), because
the check at edit time can go stale between two clerks working the same order.
"""

from collections.abc import Sequence
from datetime import datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.enums import CurrencyCode, PaymentMethod, PaymentType, TransactionType
from app.models.product import Product
from app.models.sales import (
    CreditNote,
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
)
from app.schemas.customer_refund import CustomerRefundLineUpdate, RefundPayout
from app.schemas.sales_order import derive_status
from app.services import cash_session_service, documents, stock_ledger, totals

# ── Decision rules (pure) ─────────────────────────────────────────────────────


def assert_refundable_order(order: object) -> None:
    """FR-060 — goods are returnable only once they have been paid for.

    The two refusals are deliberately distinct: "not completed" means confirm it, "not paid" means
    this order is unwound by cancelling instead. A single message would leave the clerk guessing.
    """
    if not getattr(order, 'completed', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Order is not completed; only a completed, paid order can be refunded',
        )
    if not getattr(order, 'paid', False):
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=(
                'Order is not paid; an unpaid or partly-paid order is unwound by cancelling it, '
                'not by refunding it'
            ),
        )


def refundable_quantity(sold: Decimal, already_refunded: Decimal) -> Decimal:
    """FR-061 — what was sold on a line less what completed refunds already returned."""
    remaining = sold - already_refunded
    return remaining if remaining > 0 else Decimal(0)


def assert_quantity_refundable(requested: Decimal, refundable: Decimal) -> None:
    if requested > refundable:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=f'Return quantity {requested} exceeds the refundable quantity {refundable}',
        )


def reconcile_lines(
    lines: Sequence[tuple[object, Decimal]],
) -> tuple[list[tuple[object, Decimal]], list[object]]:
    """Fit each line to what is currently refundable, at confirmation time (FR-063).

    Returns the lines to keep with their adjusted quantities, and the lines to drop. A line is
    dropped when its quantity is zero or nothing is refundable any more; it is adjusted downward
    when another refund has consumed part of what it claimed.
    """
    keep: list[tuple[object, Decimal]] = []
    drop: list[object] = []

    for line, refundable in lines:
        requested = getattr(line, 'quantity', Decimal(0))
        if requested <= 0 or refundable <= 0:
            drop.append(line)
            continue
        keep.append((line, requested if requested <= refundable else refundable))

    return keep, drop


# ── Context ───────────────────────────────────────────────────────────────────


def _employee(current: CurrentUser) -> int:
    if current.employee_id is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='Your user account is not linked to an employee and cannot author documents',
        )
    return current.employee_id


# ── Derived values ────────────────────────────────────────────────────────────


async def _already_refunded(db: AsyncSession, sales_order_detail: int) -> Decimal:
    """Quantity returned on completed, uncancelled refunds of one order line."""
    total = (
        await db.execute(
            select(func.sum(CustomerRefundDetail.quantity))
            .join(
                CustomerRefund,
                CustomerRefund.customer_refund_id == CustomerRefundDetail.customer_refund,
            )
            .where(
                CustomerRefundDetail.sales_order_detail == sales_order_detail,
                CustomerRefund.completed.is_(True),
                CustomerRefund.cancelled.is_(False),
            )
        )
    ).scalar_one_or_none()
    return total if total is not None else Decimal(0)


async def line_refundable(db: AsyncSession, order_line: SalesOrderDetail) -> Decimal:
    return refundable_quantity(
        order_line.quantity, await _already_refunded(db, order_line.sales_order_detail_id)
    )


async def attach_derived(db: AsyncSession, refund: CustomerRefund) -> CustomerRefund:
    lines = list(
        (
            await db.execute(
                select(CustomerRefundDetail)
                .where(CustomerRefundDetail.customer_refund == refund.customer_refund_id)
                .order_by(CustomerRefundDetail.customer_refund_detail_id)
            )
        )
        .scalars()
        .all()
    )
    for line in lines:
        subtotal, tax = totals.line_amounts(
            quantity=line.quantity,
            price=line.price,
            discount_rate=line.discount,
            tax_rate=line.tax_rate,
            tax_included=line.tax_included,
        )
        line.__dict__['subtotal'] = subtotal.quantize(totals.CENTS)
        line.__dict__['tax_total'] = tax.quantize(totals.CENTS)
        line.__dict__['total'] = line.__dict__['subtotal'] + line.__dict__['tax_total']
        order_line = await db.get(SalesOrderDetail, line.sales_order_detail)
        line.__dict__['refundable_quantity'] = (
            await line_refundable(db, order_line) if order_line else Decimal(0)
        )

    computed = totals.document_totals(
        [
            totals.Line(
                quantity=line.quantity,
                price=line.price,
                discount_rate=line.discount,
                tax_rate=line.tax_rate,
                tax_included=line.tax_included,
            )
            for line in lines
        ]
    )

    refund.__dict__['lines'] = lines
    refund.__dict__['subtotal'] = computed.subtotal
    refund.__dict__['tax_total'] = computed.tax_total
    refund.__dict__['total'] = computed.total
    refund.__dict__['status'] = derive_status(
        completed=refund.completed, cancelled=refund.cancelled
    ).value
    return refund


# ── Opening ───────────────────────────────────────────────────────────────────


async def open_refund(
    db: AsyncSession, sales_order_id: int, *, current: CurrentUser
) -> CustomerRefund:
    """Pre-populate a refund with every line that still has something to give back (FR-060)."""
    employee = _employee(current)

    order = await db.get(SalesOrder, sales_order_id)
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sales order not found')

    assert_refundable_order(order)

    order_lines = (
        (
            await db.execute(
                select(SalesOrderDetail).where(SalesOrderDetail.sales_order == sales_order_id)
            )
        )
        .scalars()
        .all()
    )

    refundable = [(line, await line_refundable(db, line)) for line in order_lines]
    available = [(line, qty) for line, qty in refundable if qty > 0]
    if not available:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='No refundable items remain on this order',
        )

    now = datetime.now()
    refund = CustomerRefund(
        sales_order=order.sales_order_id,
        customer=order.customer,
        creator=employee,
        updater=employee,
        sales_person=order.salesperson,
        creation_time=now,
        modification_time=now,
        completed=False,
        cancelled=False,
        facility=order.facility,
        serial=None,
        date=None,
        currency=order.currency,
        exchange_rate=order.exchange_rate,
    )
    db.add(refund)
    await db.flush()

    for line, _ in available:
        db.add(
            CustomerRefundDetail(
                customer_refund=refund.customer_refund_id,
                sales_order_detail=line.sales_order_detail_id,
                # Starts at zero — the clerk enters what is actually coming back
                quantity=Decimal(0),
                product=line.product,
                price=line.price,
                product_code=line.product_code,
                product_name=line.product_name,
                tax_rate=line.tax_rate,
                discount=line.discount_rate,
                exchange_rate=line.exchange_rate,
                currency=line.currency,
                tax_included=line.tax_included,
                warehouse=line.warehouse,
            )
        )

    await db.commit()
    await db.refresh(refund)
    return await attach_derived(db, refund)


async def get_refund(db: AsyncSession, customer_refund_id: int) -> CustomerRefund | None:
    return await db.get(CustomerRefund, customer_refund_id)


async def get_line(
    db: AsyncSession, refund: CustomerRefund, line_id: int
) -> CustomerRefundDetail | None:
    line = await db.get(CustomerRefundDetail, line_id)
    if line is None or line.customer_refund != refund.customer_refund_id:
        return None
    return line


async def list_refunds(
    db: AsyncSession,
    *,
    current: CurrentUser,
    customer: int | None = None,
    sales_order: int | None = None,
    refund_status: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[CustomerRefund], int]:
    base = select(CustomerRefund)
    count_q = select(func.count()).select_from(CustomerRefund)

    def both(clause):  # noqa: ANN001, ANN202
        nonlocal base, count_q
        base = base.where(clause)
        count_q = count_q.where(clause)

    both(CustomerRefund.facility == current.facility_id)
    if customer is not None:
        both(CustomerRefund.customer == customer)
    if sales_order is not None:
        both(CustomerRefund.sales_order == sales_order)
    if refund_status == 'draft':
        both(CustomerRefund.completed.is_(False))
        both(CustomerRefund.cancelled.is_(False))
    elif refund_status == 'completed':
        both(CustomerRefund.completed.is_(True))
    elif refund_status == 'cancelled':
        both(CustomerRefund.cancelled.is_(True))

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(CustomerRefund.customer_refund_id.desc()).offset(skip).limit(limit)
    items = (await db.execute(page)).scalars().all()
    for refund in items:
        await attach_derived(db, refund)
    return items, total


async def update_line(
    db: AsyncSession,
    refund: CustomerRefund,
    line: CustomerRefundDetail,
    data: CustomerRefundLineUpdate,
    *,
    current: CurrentUser,
) -> CustomerRefund:
    documents.assert_editable(refund)
    changes = data.model_dump(exclude_unset=True)

    if 'quantity' in changes and changes['quantity'] is not None:
        order_line = await db.get(SalesOrderDetail, line.sales_order_detail)
        if order_line is not None:
            assert_quantity_refundable(changes['quantity'], await line_refundable(db, order_line))
        line.quantity = changes['quantity']
    if 'warehouse' in changes:
        line.warehouse = changes['warehouse']

    refund.updater = _employee(current)
    refund.modification_time = datetime.now()
    await db.commit()
    await db.refresh(refund)
    return await attach_derived(db, refund)


# ── Transitions ───────────────────────────────────────────────────────────────


async def confirm_refund(
    db: AsyncSession,
    refund: CustomerRefund,
    *,
    payout: RefundPayout,
    current: CurrentUser,
) -> CustomerRefund:
    """Restock the goods, number the document, and pay the customer back (FR-063, FR-065)."""
    documents.assert_editable(refund)
    employee = _employee(current)

    session = await cash_session_service.open_session_for_cashier(db, employee)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='An open cash session is required to confirm a refund',
        )

    # Lock the source order so two clerks cannot together refund more than was sold (R2)
    order = (
        await db.execute(
            select(SalesOrder)
            .where(SalesOrder.sales_order_id == refund.sales_order)
            .with_for_update()
        )
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sales order not found')

    lines = list(
        (
            await db.execute(
                select(CustomerRefundDetail).where(
                    CustomerRefundDetail.customer_refund == refund.customer_refund_id
                )
            )
        )
        .scalars()
        .all()
    )

    with_refundable = []
    for line in lines:
        order_line = await db.get(SalesOrderDetail, line.sales_order_detail)
        with_refundable.append(
            (line, await line_refundable(db, order_line) if order_line else Decimal(0))
        )

    keep, drop = reconcile_lines(with_refundable)
    if not keep:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Refund has no lines with a returnable quantity',
        )

    for line in drop:
        await db.delete(line)
    for line, adjusted in keep:
        line.quantity = adjusted

    stocked = await _stocked_products(db, {line.product for line, _ in keep})
    for line, adjusted in keep:
        if line.product in stocked and line.warehouse is not None:
            stock_ledger.post_movement(
                db,
                source=TransactionType.CUSTOMER_REFUND,
                reference=refund.customer_refund_id,
                product=line.product,
                warehouse=line.warehouse,
                quantity=adjusted,
                outbound=False,
            )

    refund_total = totals.document_totals(
        [
            totals.Line(
                quantity=adjusted,
                price=line.price,
                discount_rate=line.discount,
                tax_rate=line.tax_rate,
                tax_included=line.tax_included,
            )
            for line, adjusted in keep
        ]
    ).total

    now = datetime.now()
    refund.serial = await documents.assign_folio(db, CustomerRefund, facility=refund.facility)
    refund.date = now
    refund.completed = True
    refund.updater = employee
    refund.modification_time = now

    await _pay_customer_back(
        db,
        refund=refund,
        order=order,
        amount=refund_total,
        payout=payout,
        session_id=session.cash_session_id,
        employee=employee,
    )

    # FR-064 — the order was paid before this refund and stays paid; balance_zeroed_time
    # belongs to a supervisor zeroing an unpaid balance, not to this path (FR-065a).
    await db.commit()
    await db.refresh(refund)
    return await attach_derived(db, refund)


async def _pay_customer_back(
    db: AsyncSession,
    *,
    refund: CustomerRefund,
    order: SalesOrder,
    amount: Decimal,
    payout: RefundPayout,
    session_id: int,
    employee: int,
) -> None:
    """Return the full refund total as cash or store credit (FR-065)."""
    now = datetime.now()

    payment = CustomerPayment(
        customer=refund.customer,
        # Negative: money leaving the drawer, not arriving
        amount=-amount if payout == RefundPayout.CASH else amount,
        method=int(PaymentMethod.CASH if payout == RefundPayout.CASH else PaymentMethod.NA),
        commission=None,
        payment_charge=None,
        date=now,
        cash_session=session_id,
        reference=f'Refund {refund.customer_refund_id}',
        facility=refund.facility,
        serial=0,
        creator=employee,
        updater=employee,
        verifier=None,
        creation_time=now,
        modification_time=now,
        currency=CurrencyCode(refund.currency),
        payment_type=int(
            PaymentType.IMMEDIATE if payout == RefundPayout.CASH else PaymentType.CREDIT_NOTE
        ),
    )
    db.add(payment)
    await db.flush()

    if payout == RefundPayout.CREDIT_NOTE:
        db.add(
            CreditNote(
                sales_order=order.sales_order_id,
                customer_refund=refund.customer_refund_id,
                customer_payment=payment.customer_payment_id,
                customer=refund.customer,
                refunded=amount,
                cash_session=session_id,
                date=now,
            )
        )


async def cancel_refund(
    db: AsyncSession, refund: CustomerRefund, *, current: CurrentUser
) -> CustomerRefund:
    """FR-066 — a completed refund is final; only a draft can be cancelled."""
    if refund.completed:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='A completed refund cannot be cancelled',
        )
    if refund.cancelled:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='Refund is already cancelled'
        )

    refund.cancelled = True
    refund.updater = _employee(current)
    refund.modification_time = datetime.now()
    await db.commit()
    await db.refresh(refund)
    return await attach_derived(db, refund)


async def _stocked_products(db: AsyncSession, product_ids: set[int]) -> set[int]:
    if not product_ids:
        return set()
    rows = (
        (
            await db.execute(
                select(Product.product_id).where(
                    Product.product_id.in_(product_ids),
                    Product.stock_verification.is_(True),
                    Product.stockable.is_(True),
                )
            )
        )
        .scalars()
        .all()
    )
    return set(rows)
