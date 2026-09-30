"""Render contexts for the printed documents (#230).

Plain dicts, one per template. Values arrive pre-formatted (research R9) and every conditional
block has its flag here, so the templates only print and test.
"""

from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath
from urllib.parse import urlsplit

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.enums import PaymentMethod, PaymentTerms, PaymentType
from app.models.core import (
    Address,
    CashSession,
    Contact,
    Employee,
    Facility,
    PaymentMethodOption,
)
from app.models.customer import Customer
from app.models.fiscal import TaxpayerIssuer
from app.models.sales import (
    CreditNote,
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
)
from app.rendering import formatting, words
from app.services import cash_session_service, customer_payment_service, totals

_CARD_METHODS = {PaymentMethod.CREDIT_CARD, PaymentMethod.DEBIT_CARD}


async def header_context(
    db: AsyncSession,
    facility_id: int,
    title: str,
    *,
    skip_address: bool = False,
    letter: bool = False,
) -> dict:
    """The store block both layouts print. `letter` splits the address as `_PrintLayout` did,
    otherwise as `_TicketLayout`."""
    facility = await db.get(Facility, facility_id)
    address = await db.get(Address, facility.address)
    issuer = await db.get(TaxpayerIssuer, facility.taxpayer) if facility.taxpayer else None

    street = f'{address.street} {address.exterior_number}'
    if address.interior_number and address.interior_number.strip():
        street += f' - {address.interior_number}'
    street = street.strip()
    locality = f' {address.locality},' if address.locality else ''
    if letter:
        lines = [
            street,
            f'{address.neighborhood},{locality} {address.postal_code}',
            f'{address.borough}, {address.state}',
        ]
    else:
        lines = [
            f'{street} {address.neighborhood},{locality}',
            f'{address.postal_code} {address.borough}, {address.state}',
        ]

    logo = None
    # mbe-api stores a bare file name; legacy renders the column with `Url.Content`, so its rows
    # can hold a path or URL ('/images/x.png', '~/Content/x.png'). Only the file name is looked up,
    # and only inside `images_dir`.
    name = PurePosixPath(urlsplit(facility.logo or '').path).name
    if name:
        path = Path(settings.images_dir, name)
        if path.is_file():
            logo = path.resolve().as_uri()

    return {
        'title': title,
        'name': facility.name,
        'logo': logo,
        'skip_address': skip_address,
        'rfc': issuer.taxpayer_issuer_id if issuer else '',
        'taxpayer_name': (issuer.name or '') if issuer else '',
        'address': lines,
    }


def gross_subtotal_and_discount(order: SalesOrder) -> tuple[Decimal, Decimal]:
    """Legacy's pre-discount Subtotal and its Descuento, both before tax.

    The discount is each line's net amount without its discount less its net amount with it,
    back-derived for tax-included lines exactly as `attach_derived` does. The subtotal is derived
    from `attach_derived`'s figures so that Subtotal − Descuento + IVA = Total holds to the cent.
    """
    discount = Decimal(0)
    for line in order.lines:
        values = dict(
            quantity=line.quantity,
            price=line.price,
            tax_rate=line.tax_rate,
            tax_included=line.tax_included,
        )
        undiscounted, _ = totals.line_amounts(discount_rate=Decimal(0), **values)
        discounted, _ = totals.line_amounts(discount_rate=line.discount_rate, **values)
        discount += undiscounted - discounted
    discount = discount.quantize(totals.CENTS, ROUND_HALF_UP)
    return order.total - order.tax_total + discount, discount


async def sale_ticket_context(db: AsyncSession, order: SalesOrder) -> tuple[str, dict]:
    """The pre-payment ticket for an order not yet completed, the final receipt once it is.

    `order` must already have been through `sales_order_service.attach_derived`.
    """
    money = formatting.money
    completed = order.completed
    header = await header_context(
        db, order.facility, 'Ticket de Venta' if completed else 'Punto de Venta'
    )
    facility = await db.get(Facility, order.facility)
    customer = await db.get(Customer, order.customer)
    salesperson = await db.get(Employee, order.salesperson)

    applications = [
        a
        for a in await customer_payment_service.list_order_applications(db, order.sales_order_id)
        if not a['cancelled']
    ]
    payments = await _by_id(
        db,
        CustomerPayment,
        CustomerPayment.customer_payment_id,
        {a['customer_payment'] for a in applications},
    )
    options = await _by_id(
        db,
        PaymentMethodOption,
        PaymentMethodOption.payment_method_option_id,
        {p.payment_charge for p in payments.values() if p.payment_charge is not None},
    )

    payment_rows = []
    for application in applications:
        payment = payments[application['customer_payment']]
        if payment.payment_type == PaymentType.IMMEDIATE:
            option = options.get(payment.payment_charge)
            label = (
                option.name
                if option is not None and option.display_on_ticket
                else formatting.method_name(payment.method)
            )
            amount = payment.amount
        else:
            label = (
                f'{formatting.payment_type_name(payment.payment_type)} - '
                f'{payment.customer_payment_id}'
            )
            amount = application['amount']
        payment_rows.append({'label': label, 'amount': money(amount)})

    live_payments = [payments[a['customer_payment']] for a in applications]
    paid = sum((a['amount'] for a in applications), Decimal(0))
    change = sum((a['amount_change'] for a in applications), Decimal(0))
    subtotal, discount = gross_subtotal_and_discount(order)

    is_credit = order.payment_terms != PaymentTerms.IMMEDIATE
    due_date = formatting.date_short(order.due_date)
    customer_name = (order.customer_name or '').strip()
    if order.customer != settings.default_customer_id:
        customer_name = ''

    promissory_note = None
    if is_credit and not order.paid:
        promissory_note = settings.promissory_note_template.format(
            customer=customer.name,
            balance=money(order.balance),
            due_date=due_date,
            issuer=header['taxpayer_name'],
        )

    order_id = formatting.pad8(order.sales_order_id)
    context = {
        'header': header,
        'sales_order_id': order.sales_order_id,
        'order_id': order_id,
        'folio': formatting.pad8(order.serial) if order.serial is not None else '',
        'date': formatting.date_short(order.date),
        'salesperson': (
            salesperson.nickname
            if completed
            else f'{salesperson.first_name} {salesperson.last_name}'.strip()
        ),
        'customer': customer.name,
        'customer_name': customer_name,
        'terms': formatting.terms_name(order.payment_terms),
        'is_credit': is_credit,
        'due_date': due_date,
        'completed': completed,
        'cancelled': order.cancelled,
        'is_paid': order.paid,
        'lines': [
            {
                'quantity': formatting.qty(line.quantity),
                'price': money(line.price),
                'discount': formatting.percent(line.discount_rate)
                if line.discount_rate > 0
                else '',
                'code': line.product_code,
                'name': line.product_name,
                'comment': line.comment or '',
                'total': money(line.total),
            }
            for line in order.lines
        ],
        'subtotal': money(subtotal),
        'discount': money(discount) if discount else None,
        'taxes': money(order.tax_total),
        'total': money(order.total),
        'refunds': await _refund_rows(db, order.sales_order_id),
        'payments': payment_rows,
        'change': money(change) if change > 0 else None,
        'credit_notes': await _credit_note_rows(db, order.sales_order_id),
        'paid': money(paid),
        'balance': money(order.balance),
        'on_delivery_payments': [
            {'method': formatting.method_name(p.method), 'amount': money(p.amount)}
            for p in live_payments
            if p.cash_session is None
        ],
        'por_cobrar': (completed and not is_credit and not order.paid and order.balance > 0),
        'has_card_payment': any(p.method in _CARD_METHODS for p in live_payments),
        'promissory_note': promissory_note,
        'receipt_message': facility.receipt_message or None,
        'cancelled_at': (
            formatting.date_time(order.modification_time) if order.cancelled else None
        ),
        'barcode': formatting.barcode_data_uri(order_id),
    }
    return ('sale_receipt.html' if completed else 'sale_ticket.html'), context


async def _by_id(db: AsyncSession, model: type, key: object, ids: set) -> dict:
    if not ids:
        return {}
    rows = (await db.execute(select(model).where(key.in_(ids)))).scalars().all()
    return {getattr(row, key.key): row for row in rows}


async def _refund_rows(db: AsyncSession, sales_order_id: int) -> list[dict]:
    """Completed, non-cancelled refunds, each totalled as its own document."""
    rows = (
        await db.execute(
            select(CustomerRefund, CustomerRefundDetail)
            .join(
                CustomerRefundDetail,
                CustomerRefundDetail.customer_refund == CustomerRefund.customer_refund_id,
            )
            .where(
                CustomerRefund.sales_order == sales_order_id,
                CustomerRefund.completed.is_(True),
                CustomerRefund.cancelled.is_(False),
            )
            .order_by(CustomerRefund.customer_refund_id)
        )
    ).all()
    lines: dict[int, list[totals.Line]] = {}
    for refund, detail in rows:
        lines.setdefault(refund.customer_refund_id, []).append(
            totals.Line(
                quantity=detail.quantity,
                price=detail.price,
                discount_rate=detail.discount,
                tax_rate=detail.tax_rate,
                tax_included=detail.tax_included,
            )
        )
    return [
        {
            'label': f'Devolución {formatting.pad8(refund_id)}',
            'amount': formatting.money(totals.document_totals(refund_lines).total),
        }
        for refund_id, refund_lines in lines.items()
    ]


async def _credit_note_rows(db: AsyncSession, sales_order_id: int) -> list[dict]:
    rows = (
        await db.execute(
            select(CreditNote, CustomerPayment)
            .join(
                CustomerPayment,
                CustomerPayment.customer_payment_id == CreditNote.customer_payment,
            )
            .where(CreditNote.sales_order == sales_order_id)
            .order_by(CreditNote.credit_note_id)
        )
    ).all()
    return [
        {
            'label': (
                f'{formatting.payment_type_name(payment.payment_type)} - '
                f'{payment.customer_payment_id}'
            ),
            'amount': formatting.money(payment.amount),
        }
        for _note, payment in rows
    ]


async def cash_cut_context(db: AsyncSession, session: CashSession) -> tuple[str, dict]:
    """The "Corte de Caja" of a closed session, ported from legacy `_CashCountTicket`.

    `session` must already have been through `cash_session_service.attach_derived`, which expands
    the drawer and the cashier.
    """
    money = formatting.money
    drawer = session.cash_drawer_detail
    cashier = session.cashier_detail
    figures = await cash_session_service.cut_figures(db, session.cash_session_id)
    session_id = formatting.pad6(session.cash_session_id)
    context = {
        'header': await header_context(db, drawer.facility, 'Corte de Caja', skip_address=True),
        'session_id': session_id,
        'drawer': drawer.name,
        'start': formatting.date_time(session.start),
        'end': formatting.date_time(session.end),
        'cashier': f'{cashier.first_name} {cashier.last_name}'.strip(),
        'sales': [
            {'method': formatting.method_name(method), 'amount': money(amount)}
            for method, amount in figures.sales_by_method
        ],
        'sales_total': money(figures.sales_total),
        'refunds': [
            {'method': formatting.method_name(method), 'amount': money(amount)}
            for method, amount in figures.refunds_by_method
        ],
        # Every expense is cash, so legacy's by-method list is at most this one row.
        'expenses': (
            [
                {
                    'method': formatting.method_name(PaymentMethod.CASH),
                    'amount': money(figures.expenses_total),
                }
            ]
            if figures.expenses_total
            else []
        ),
        'starting_cash': money(figures.starting_cash),
        'cash_sales': money(figures.cash_sales),
        'expenses_total': money(figures.expenses_total),
        'cash_refunds': money(figures.cash_refunds),
        'counted_cash': money(figures.counted_cash),
        'difference_label': 'Faltante' if figures.is_shortage else 'Sobrante',
        'difference': money(figures.difference),
        'barcode': formatting.barcode_data_uri(session_id),
    }
    return 'cash_cut.html', context


async def sales_order_context(db: AsyncSession, order: SalesOrder) -> tuple[str, dict]:
    """The letter-size pedido, ported from legacy `SalesOrders/Print`, for an order in any state.

    `order` must already have been through `sales_order_service.attach_derived`. Lines show the
    net unit price and the undiscounted net amount, as legacy's did, so the totals read
    Subtotal − Descuento + IVA = Total.
    """
    money = formatting.money
    order_id = formatting.pad8(order.sales_order_id)
    header = await header_context(db, order.facility, f'Pedido - {order_id}', letter=True)
    # Legacy blanked the taxpayer on this document only.
    header['rfc'] = header['taxpayer_name'] = ''
    customer = await db.get(Customer, order.customer)
    salesperson = await db.get(Employee, order.salesperson)
    contact = await db.get(Contact, order.contact) if order.contact is not None else None
    ship_to = await db.get(Address, order.ship_to) if order.ship_to is not None else None

    ship_to_lines = []
    if ship_to is not None:
        parts = [
            (' ', (ship_to.street, ship_to.exterior_number, ship_to.interior_number)),
            (', ', (ship_to.neighborhood, ship_to.borough)),
            (', ', (ship_to.state, ship_to.country, ship_to.postal_code)),
            ('', (ship_to.comment,)),
        ]
        for separator, values in parts:
            line = separator.join(v.strip() for v in values if v and v.strip())
            if line:
                ship_to_lines.append(line)

    lines = []
    for line in order.lines:
        values = dict(
            tax_rate=line.tax_rate, tax_included=line.tax_included, discount_rate=Decimal(0)
        )
        unit, _ = totals.line_amounts(quantity=Decimal(1), price=line.price, **values)
        amount, _ = totals.line_amounts(quantity=line.quantity, price=line.price, **values)
        lines.append(
            {
                'quantity': formatting.qty(line.quantity),
                'code': line.product_code,
                'name': line.product_name,
                'comment': line.comment or '',
                'price': money(unit),
                'amount': money(amount),
            }
        )

    subtotal, discount = gross_subtotal_and_discount(order)
    comment = (order.comment or '').strip()
    context = {
        'header': header,
        'order_id': order_id,
        'customer': customer.name,
        'contact': contact.name if contact is not None else None,
        'ship_to': ship_to_lines,
        'comment': comment or None,
        'salesperson': f'{salesperson.first_name} {salesperson.last_name}'.strip(),
        'date': formatting.date_short(order.date),
        'promise_date': formatting.date_long(order.promise_date),
        'terms': formatting.terms_name(order.payment_terms),
        'is_credit': order.payment_terms != PaymentTerms.IMMEDIATE,
        'due_date': formatting.date_short(order.due_date),
        'lines': lines,
        'subtotal': money(subtotal),
        'discount': money(discount) if discount else None,
        'taxes': money(order.tax_total),
        'total': money(order.total),
        'amount_in_words': words.amount_in_words(order.total, order.currency),
        'savings': f'¡Usted ahorró {money(discount)} en esta compra!' if discount > 0 else None,
    }
    return 'sales_order.html', context
