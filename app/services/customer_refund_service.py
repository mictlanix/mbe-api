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
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import exists, func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.enums import CurrencyCode, PaymentMethod, PaymentType, TransactionType
from app.models.fiscal import FiscalDocument, FiscalDocumentDetail, FiscalDocumentXml
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
from app.services import (
    cash_session_service,
    customer_payment_service,
    documents,
    inventory_documents,
    sales_order_service,
    stock_ledger,
    totals,
    warehouse_service,
)

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


def refundable_quantity(
    sold: Decimal, already_refunded: Decimal, invoiced_live: Decimal = Decimal(0)
) -> Decimal:
    """FR-061, FR-011 — what was sold on a line less what completed refunds already returned and
    what sits on live invoices."""
    remaining = sold - already_refunded - invoiced_live
    return remaining if remaining > 0 else Decimal(0)


def assert_not_invoiced(
    requested: Decimal,
    *,
    sold: Decimal,
    refunded: Decimal,
    invoiced: Decimal,
    product_code: str | None,
    invoice: str | None,
) -> None:
    """FR-011 — refuse a quantity that only live-invoice units stand in the way of.

    A request beyond what was sold less refunded is not the invoice's doing; that stays the 422 of
    `assert_quantity_refundable`.
    """
    if requested <= refundable_quantity(sold, refunded):
        if requested > refundable_quantity(sold, refunded, invoiced):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail=(
                    f'Units of line {product_code} are on invoice {invoice}; '
                    'cancel the invoice to refund them'
                ),
            )


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


async def _invoiced_live(db: AsyncSession, sales_order_detail: int) -> tuple[Decimal, str | None]:
    """Quantity of one order line on live invoices, and the first such invoice's series and folio.

    Live means type 0, not cancelled, and completed or sealed: the predicate of
    `invoice_service.remaining_by_order_line`. A draft reserves nothing.
    """
    sealed = exists().where(
        FiscalDocumentXml.fiscal_document_xml_id == FiscalDocument.fiscal_document_id
    )
    rows = (
        await db.execute(
            select(FiscalDocument.batch, FiscalDocument.serial, FiscalDocumentDetail.quantity)
            .join(
                FiscalDocument, FiscalDocument.fiscal_document_id == FiscalDocumentDetail.document
            )
            .where(
                FiscalDocumentDetail.order_detail == sales_order_detail,
                FiscalDocument.type == 0,
                FiscalDocument.cancelled.is_(False),
                or_(FiscalDocument.completed.is_(True), sealed),
            )
            .order_by(FiscalDocument.fiscal_document_id)
        )
    ).all()
    if not rows:
        return Decimal(0), None
    batch, serial, _ = rows[0]
    return sum((quantity for _, _, quantity in rows), Decimal(0)), f'{batch or ""}{serial or ""}'


async def line_refundable(db: AsyncSession, order_line: SalesOrderDetail) -> Decimal:
    line_id = order_line.sales_order_detail_id
    invoiced, _ = await _invoiced_live(db, line_id)
    return refundable_quantity(order_line.quantity, await _already_refunded(db, line_id), invoiced)


async def _assert_line_quantity(
    db: AsyncSession, order_line: SalesOrderDetail, requested: Decimal
) -> Decimal:
    """Refuse a quantity held by a live invoice (409), and return the line's refundable quantity."""
    line_id = order_line.sales_order_detail_id
    refunded = await _already_refunded(db, line_id)
    invoiced, invoice = await _invoiced_live(db, line_id)
    assert_not_invoiced(
        requested,
        sold=order_line.quantity,
        refunded=refunded,
        invoiced=invoiced,
        product_code=order_line.product_code,
        invoice=invoice,
    )
    return refundable_quantity(order_line.quantity, refunded, invoiced)


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


async def attach_summary_totals(db: AsyncSession, refunds: Sequence[CustomerRefund]) -> None:
    """Totals for a whole page in one query.

    Deliberately does not compute each line's `refundable_quantity`: that needs a per-line lookup
    against the source order and only the detail view shows it. A summary row does not.
    """
    ids = [r.customer_refund_id for r in refunds]
    if not ids:
        return

    grouped: dict[int, list[totals.Line]] = {rid: [] for rid in ids}
    rows = (
        (
            await db.execute(
                select(CustomerRefundDetail).where(CustomerRefundDetail.customer_refund.in_(ids))
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        grouped[row.customer_refund].append(
            totals.Line(
                quantity=row.quantity,
                price=row.price,
                discount_rate=row.discount,
                tax_rate=row.tax_rate,
                tax_included=row.tax_included,
            )
        )

    for refund in refunds:
        computed = totals.document_totals(grouped.get(refund.customer_refund_id, []))
        refund.__dict__['subtotal'] = computed.subtotal
        refund.__dict__['tax_total'] = computed.tax_total
        refund.__dict__['total'] = computed.total
        refund.__dict__['status'] = derive_status(
            completed=refund.completed, cancelled=refund.cancelled
        ).value


# ── Opening ───────────────────────────────────────────────────────────────────


async def open_refund(
    db: AsyncSession, sales_order_id: int, *, current: CurrentUser
) -> CustomerRefund:
    """Pre-populate a refund with every line that still has something to give back (FR-060)."""
    employee = current.employee_id

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
        # FR-011 — when a live invoice is what leaves nothing to refund, name it.
        for line in sorted(order_lines, key=lambda d: d.sales_order_detail_id):
            sold_less_refunded = refundable_quantity(
                line.quantity, await _already_refunded(db, line.sales_order_detail_id)
            )
            await _assert_line_quantity(db, line, sold_less_refunded)
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='No refundable items remain on this order',
        )

    now = local_now()
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
    await attach_summary_totals(db, items)
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
            refundable = await _assert_line_quantity(db, order_line, changes['quantity'])
            assert_quantity_refundable(changes['quantity'], refundable)
        line.quantity = changes['quantity']
    if 'warehouse' in changes:
        if changes['warehouse'] is not None:
            await warehouse_service.assert_sellable(db, changes['warehouse'])
        line.warehouse = changes['warehouse']

    refund.updater = current.employee_id
    refund.modification_time = local_now()
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
    """Restock the goods, number the document, and pay the customer back (FR-063, FR-065).

    Spec 035's numbering rule (research R2, R3): pre-read the cashier's open session, commit away
    the request's snapshot, then lock the session, the refund, the source order, its lines, the
    facility and the lines' warehouses, and only then read. The session goes first because the
    payout's foreign key takes a share lock on it, as cash supplier payments already order it.
    """
    documents.assert_editable(refund)
    employee = current.employee_id

    session = await cash_session_service.open_session_for_cashier(db, employee)
    if session is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='An open cash session is required to confirm a refund',
        )
    session_id = session.cash_session_id

    await db.commit()
    async with inventory_documents.busy_as_conflict(db):
        return await _confirm_locked(
            db, refund.customer_refund_id, payout=payout, session_id=session_id, employee=employee
        )


async def _confirm_locked(
    db: AsyncSession, refund_id: int, *, payout: RefundPayout, session_id: int, employee: int
) -> CustomerRefund:
    # A close that got the session first has set `end`: 409 "The cash session is closed".
    await cash_session_service.lock_open_session(db, session_id)
    refund = (
        await db.execute(
            select(CustomerRefund)
            .where(CustomerRefund.customer_refund_id == refund_id)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one()
    documents.assert_editable(refund)

    # Lock the source order so two clerks cannot together refund more than was sold (R2)
    order = (
        await db.execute(
            select(SalesOrder)
            .where(SalesOrder.sales_order_id == refund.sales_order)
            .with_for_update()
            .execution_options(populate_existing=True)
        )
    ).scalar_one_or_none()
    if order is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Sales order not found')

    lines = list(
        (
            await db.execute(
                select(CustomerRefundDetail)
                .where(CustomerRefundDetail.customer_refund == refund.customer_refund_id)
                .with_for_update(read=True)
                .execution_options(populate_existing=True)
            )
        )
        .scalars()
        .all()
    )
    await documents.lock_facility(db, refund.facility)
    warehouses = {line.warehouse for line in lines if line.warehouse is not None}
    if warehouses:
        await inventory_documents.lock_warehouses(db, warehouses)

    # A line short only because another refund got there first is trimmed (FR-063); one short
    # because its units went onto a live invoice is refused (FR-011).
    with_refundable = []
    for line in lines:
        order_line = await db.get(SalesOrderDetail, line.sales_order_detail)
        if order_line is None:
            with_refundable.append((line, Decimal(0)))
            continue
        sold_less_refunded = refundable_quantity(
            order_line.quantity, await _already_refunded(db, order_line.sales_order_detail_id)
        )
        claim = min(line.quantity, sold_less_refunded)
        with_refundable.append((line, await _assert_line_quantity(db, order_line, claim)))

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

    now = local_now()
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
        session_id=session_id,
        employee=employee,
    )

    # FR-064 — the order was paid before this refund (FR-060) and stays paid, so `settle` leaves it
    # and its balance_zeroed_time alone (FR-065a). It's called anyway, so that a refund settling an
    # unpaid order would flag it paid without depending on FR-060 staying as it is (spec 033).
    await db.flush()
    balances = await sales_order_service.order_balances(db, [order])
    customer_payment_service.settle(order, balances[order.sales_order_id].balance)
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
    now = local_now()

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
                # The current system's shape (`CustomerRefundsController.cs:353-378`, spec 034
                # FR-005): the amount and the cash session live on the backing payment. On the note,
                # `refunded` and `cash_session` mean it was paid back in cash, so a note issued
                # here carries neither, or neither system would let it be spent. NULL explicitly:
                # the column defaults to 0.
                refunded=Decimal(0),
                cash_session=None,
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
    refund.updater = current.employee_id
    refund.modification_time = local_now()
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
