"""Render contexts for the printed documents (#230).

Plain dicts, one per template. Values arrive pre-formatted (research R9) and every conditional
block has its flag here, so the templates only print and test.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path, PurePosixPath
from types import SimpleNamespace
from urllib.parse import urlsplit

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_today
from app.core.config import settings
from app.enums import (
    CurrencyCode,
    DeliveryOrderStatus,
    FulfillmentType,
    PaymentMethod,
    PaymentTerms,
    PaymentType,
)
from app.models.core import (
    Address,
    CashSession,
    Contact,
    Employee,
    Facility,
    PaymentMethodOption,
    Vehicle,
    VehicleOperator,
    Warehouse,
)
from app.models.customer import Customer
from app.models.fiscal import FiscalDocument, TaxpayerBatch, TaxpayerIssuer
from app.models.logistics import (
    DeliveriesItinerary,
    DeliveriesItineraryDetail,
    DeliveryOrder,
    DeliveryOrderDetail,
    DeliveryOrderEvent,
)
from app.models.sales import (
    CreditNote,
    CustomerPayment,
    CustomerRefund,
    CustomerRefundDetail,
    SalesOrder,
    SalesOrderDetail,
    SalesQuote,
)
from app.models.sat_catalog import SatCfdiUsage, SatTaxRegime
from app.rendering import formatting, words
from app.services import (
    cash_session_service,
    cfdi,
    customer_payment_service,
    delivery_itinerary_service,
    delivery_order_service,
    sales_order_service,
    totals,
)

_CARD_METHODS = {PaymentMethod.CREDIT_CARD, PaymentMethod.DEBIT_CARD}


def _image_uri(value: str | None) -> str | None:
    """A `file://` URL for an image under `images_dir`, or None when it is not there.

    mbe-api stores a bare file name; legacy renders the column with `Url.Content`, so its rows
    can hold a path or URL ('/images/x.png', '~/Content/x.png'). Only the file name is looked up,
    and only inside `images_dir`.
    """
    name = PurePosixPath(urlsplit(value or '').path).name
    if name:
        path = Path(settings.images_dir, name)
        if path.is_file():
            return path.resolve().as_uri()
    return None


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

    return {
        'title': title,
        'name': facility.name,
        'logo': _image_uri(facility.logo),
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


def _ship_to_lines(ship_to: Address | None) -> list[str]:
    """The ship-to block legacy's letter documents print, one line per non-empty group."""
    if ship_to is None:
        return []
    parts = [
        (' ', (ship_to.street, ship_to.exterior_number, ship_to.interior_number)),
        (', ', (ship_to.neighborhood, ship_to.borough)),
        (', ', (ship_to.state, ship_to.country, ship_to.postal_code)),
        ('', (ship_to.comment,)),
    ]
    lines = []
    for separator, values in parts:
        line = separator.join(v.strip() for v in values if v and v.strip())
        if line:
            lines.append(line)
    return lines


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

    ship_to_lines = _ship_to_lines(ship_to)

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


async def sales_quote_context(db: AsyncSession, quote: SalesQuote) -> tuple[str, dict]:
    """The letter-size cotización, ported from legacy `Quotations/Print.cshtml` (spec 024).

    `quote` must already have been through `sales_quote_service.attach_derived`, whose totals the
    document prints, so it cannot disagree with the quote's own figures. A line shows its net unit
    price after its discount, so that quantity × price is the line amount. Legacy left the payment
    terms off; this prints them, with the customer's credit days on a credit quote.
    """
    money = formatting.money
    header = await header_context(
        db, quote.facility, f'Cotización - {formatting.pad8(quote.sales_quote_id)}', letter=True
    )
    # Legacy blanked the taxpayer on quotes too (`Print.cshtml:10-11`).
    header['rfc'] = header['taxpayer_name'] = ''
    customer = await db.get(Customer, quote.customer)
    salesperson = await db.get(Employee, quote.salesperson)
    contact = await db.get(Contact, quote.contact) if quote.contact is not None else None
    ship_to = await db.get(Address, quote.ship_to) if quote.ship_to is not None else None

    phone = None
    if contact is not None:
        if contact.mobile and contact.mobile.strip():
            phone = contact.mobile.strip()
        elif contact.phone and contact.phone.strip():
            phone = contact.phone.strip()
            if contact.phone_ext and contact.phone_ext.strip():
                phone += f' ext. {contact.phone_ext.strip()}'

    terms = formatting.terms_name(quote.payment_terms)
    if quote.payment_terms == PaymentTerms.NET_D:
        terms += f' – {customer.credit_days or 0} días'

    lines = []
    for line in quote.lines:
        unit, _ = totals.line_amounts(
            quantity=Decimal(1),
            price=line.price + line.price_adjustment,
            discount_rate=line.discount_rate,
            tax_rate=line.tax_rate,
            tax_included=line.tax_included,
        )
        lines.append(
            {
                'quantity': formatting.qty(line.quantity),
                'code': line.product_code,
                'name': line.product_name,
                'comment': line.comment or '',
                'price': money(unit),
                'amount': money(line.subtotal),
            }
        )

    comment = (quote.comment or '').strip()
    context = {
        'header': header,
        'quote_id': quote.sales_quote_id,
        'customer': customer.name,
        'contact': contact.name if contact is not None else None,
        'contact_phone': phone,
        'ship_to': _ship_to_lines(ship_to),
        'comment': comment or None,
        'salesperson': f'{salesperson.first_name} {salesperson.last_name}'.strip(),
        'date': formatting.date_short(quote.date),
        'due_date': formatting.date_short(quote.due_date),
        'terms': terms,
        'lines': lines,
        'subtotal': money(quote.subtotal),
        'taxes': money(quote.tax_total),
        'total': money(quote.total),
        'amount_in_words': words.amount_in_words(quote.total, quote.currency),
    }
    return 'sales_quote.html', context


# ── CFDI (spec 020) ───────────────────────────────────────────────────────────

_CFDI_CURRENCIES = {'MXN': CurrencyCode.MXN, 'USD': CurrencyCode.USD, 'EUR': CurrencyCode.EUR}


def _cfdi_payments(doc: cfdi.Cfdi) -> list[dict]:
    """A payment receipt's Pagos 2.0 complement: each payment and the documents it settled."""
    money = formatting.money
    payments = []
    for pago in doc.pagos:
        attrs = pago['attrs']
        currency = attrs.get('MonedaP', 'MXN')
        rate = attrs.get('TipoCambioP')
        documents = []
        for number, settled in enumerate(pago['documentos'], 1):
            settled_currency = settled.get('MonedaDR', currency)
            if settled_currency != currency and settled.get('EquivalenciaDR'):
                settled_currency += f' / {settled["EquivalenciaDR"]}'
            # Padded like every other folio: legacy prints `{Batch} {Serial:D6}`.
            folio = f'{settled.get("Serie", "")} {settled.get("Folio", "").zfill(6)}'.strip()
            documents.append(
                {
                    'number': number,
                    'uuid': settled.get('IdDocumento', ''),
                    'folio': folio,
                    'installment': settled.get('NumParcialidad', ''),
                    'currency': settled_currency,
                    'previous': money(Decimal(settled.get('ImpSaldoAnt') or 0)),
                    'paid': money(Decimal(settled.get('ImpPagado') or 0)),
                    'outstanding': money(Decimal(settled.get('ImpSaldoInsoluto') or 0)),
                }
            )
        payments.append(
            {
                'date': attrs.get('FechaPago', '')[:10],
                'form': formatting.payment_form_label(attrs.get('FormaDePagoP')),
                'reference': attrs.get('NumOperacion') or None,
                'foreign_bank': attrs.get('NomBancoOrdExt') or None,
                'currency': currency,
                'exchange_rate': (
                    formatting.price4(Decimal(rate)) if currency != 'MXN' and rate else None
                ),
                'amount': money(Decimal(attrs.get('Monto') or 0)),
                'documents': documents,
            }
        )
    return payments


def _coded(code: str | None, description: str | None) -> str:
    """A SAT code followed by its description, as legacy printed regimes and uses."""
    return f'{code} {description}' if description else (code or '')


async def fiscal_document_context(
    db: AsyncSession, document: FiscalDocument, xml: str
) -> tuple[str, dict]:
    """The printed representation of a stamped CFDI 4.0, ported from legacy `Print40T02Blue`.

    Every amount, code, seal, certificate number, UUID and date is the stamped XML's, so the
    printout says what SAT holds (research R1). The database supplies only what the XML does not
    carry: catalog descriptions, the issuer's postal code, line comments, the reference, the batch
    settings and the cancellation. `document` comes from `fiscal_document_service.get_document`,
    with its `lines`. Raises `cfdi.CfdiError` when `xml` is not a stamped CFDI.
    """
    money = formatting.money
    doc = cfdi.parse(xml)
    comprobante, emisor, receptor, timbre = doc.comprobante, doc.emisor, doc.receptor, doc.timbre

    batch_query = select(TaxpayerBatch).where(
        TaxpayerBatch.taxpayer == document.issuer, TaxpayerBatch.batch == document.batch
    )
    batch = (await db.execute(batch_query)).scalars().first()
    batch_settings = cfdi.batch_settings(batch.template if batch is not None else None)
    issuer = await db.get(TaxpayerIssuer, emisor.get('Rfc'))
    regime = await db.get(SatTaxRegime, receptor.get('RegimenFiscalReceptor'))
    usage = await db.get(SatCfdiUsage, receptor.get('UsoCFDI'))

    is_payment = comprobante.get('TipoDeComprobante') == 'P'
    # Comments exist only in the database. Pair them by position, and only when the counts agree,
    # so a comment is never printed under the wrong concept.
    comments = [line.comment for line in document.lines]
    if len(comments) != len(doc.conceptos):
        comments = [None] * len(doc.conceptos)
    lines = [
        {
            'number': number,
            'quantity': formatting.qty(Decimal(concepto['Cantidad'])),
            'unit': ' / '.join(
                v for v in (concepto.get('ClaveUnidad'), concepto.get('Unidad')) if v
            ),
            'code': concepto.get('ClaveProdServ', ''),
            'description': concepto.get('Descripcion', ''),
            'product_code': concepto.get('NoIdentificacion') or None,
            'comment': comment or None,
            'price': formatting.price4(Decimal(concepto['ValorUnitario'])),
            'amount': formatting.price4(Decimal(concepto['Importe'])),
        }
        for number, (concepto, comment) in enumerate(zip(doc.conceptos, comments), 1)
        if not is_payment
    ]

    currency = comprobante.get('Moneda', 'MXN')
    exchange_rate = comprobante.get('TipoCambio')
    total = Decimal(comprobante['Total'])
    discount = Decimal(comprobante.get('Descuento') or 0)
    payments = _cfdi_payments(doc) if is_payment else []
    # A payment receipt's stamped total is 0: its amount in words is what the payments add up to.
    if is_payment:
        words_amount = sum((Decimal(p['attrs'].get('Monto') or 0) for p in doc.pagos), Decimal(0))
        words_currency = doc.pagos[0]['attrs'].get('MonedaP', 'MXN') if doc.pagos else 'MXN'
    else:
        words_amount, words_currency = total, currency
    context = {
        'title': formatting.fiscal_type_title(document.type),
        'logo': _image_uri(batch_settings.logo_name),
        'folio': f'{comprobante.get("Serie", "")} {comprobante.get("Folio", "").zfill(6)}'.strip(),
        'certificate': comprobante.get('NoCertificado', ''),
        'uuid': timbre['UUID'],
        'expedition': (
            f'C.P. {comprobante.get("LugarExpedicion", "")} / {comprobante["Fecha"][:10]}'
        ),
        'recipient': {
            'rfc': receptor.get('Rfc', ''),
            'name': receptor.get('Nombre', ''),
            'postal_code': receptor.get('DomicilioFiscalReceptor', ''),
            'usage': _coded(receptor.get('UsoCFDI'), usage.description if usage else None),
            'regime': _coded(
                receptor.get('RegimenFiscalReceptor'), regime.description if regime else None
            ),
        },
        'issuer': {
            'rfc': emisor.get('Rfc', ''),
            'name': emisor.get('Nombre', ''),
            'regime': _coded(emisor.get('RegimenFiscal'), document.issuer_regime_name),
            'postal_code': (issuer.postal_code or '') if issuer is not None else '',
        },
        'qr': formatting.qr_data_uri(cfdi.sat_qr_payload(doc)),
        'reference': document.reference or None,
        'is_payment': is_payment,
        'lines': lines,
        'payment_method': formatting.payment_method_label(comprobante.get('MetodoPago')),
        'payment_form': formatting.payment_form_label(comprobante.get('FormaPago')),
        'currency': currency,
        'exchange_rate': (
            formatting.price4(Decimal(exchange_rate))
            if currency != 'MXN' and exchange_rate
            else None
        ),
        # A payment receipt has no concept totals: its figures are the payments'.
        'subtotal': None if is_payment else money(Decimal(comprobante.get('SubTotal') or 0)),
        'discount': money(discount) if discount and not is_payment else None,
        'taxes': None if is_payment else money(Decimal(doc.traslados_total or 0)),
        'retentions': money(Decimal(doc.retenciones_total)) if doc.retenciones_total else None,
        'total': None if is_payment else money(total),
        'amount_in_words': words.amount_in_words(
            words_amount, _CFDI_CURRENCIES.get(words_currency, CurrencyCode.MXN)
        ),
        # A payment receipt's related documents are its settled invoices, printed as payments.
        'related': []
        if is_payment
        else [(formatting.relation_label(code), uuids) for code, uuids in doc.relacionados],
        'payments': payments,
        'stamp': {
            'sat_certificate': timbre.get('NoCertificadoSAT', ''),
            'date': timbre.get('FechaTimbrado', '').replace('T', ' '),
            'pac': timbre.get('RfcProvCertif', ''),
            'original_string': cfdi.tfd_original_string(timbre),
            'issuer_seal': comprobante.get('Sello', ''),
            'sat_seal': timbre.get('SelloSAT', ''),
        },
        'footer': {
            'height_mm': batch_settings.footer_height_mm,
            'accounts': batch_settings.accounts,
        },
        'cancelled': (
            {
                'date': formatting.date_short(document.cancellation_date)
                if document.cancellation_date
                else None
            }
            if document.cancelled
            else None
        ),
    }
    return 'fiscal_document.html', context


# ── Delivery documents (spec 026) ─────────────────────────────────────────────


def _order_status_label(
    terms: int, paid: bool, due_date: date | datetime, today: date
) -> str | None:
    """`DeliveryTicket.cshtml:33-50` (research R4): nothing once paid, "No Pagado" on cash terms,
    on credit "Vencido" once the due date has passed, else "Por Cobrar"."""
    if paid:
        return None
    if terms == PaymentTerms.IMMEDIATE:
        return 'No Pagado'
    due = due_date.date() if isinstance(due_date, datetime) else due_date
    return 'Vencido' if due < today else 'Por Cobrar'


def _unit_label(unit: object | None, *, symbol: bool) -> str:
    """The unit of measurement as printed (research R5): its name, or with `symbol` its symbol
    falling back to the name when there is none. `unit` carries `name` and `symbol`."""
    if unit is None:
        return ''
    if symbol and unit.symbol and unit.symbol.strip():
        return unit.symbol.strip()
    return unit.name


def _note_groups(lines: Sequence) -> list[tuple[tuple, list]]:
    """Lines grouped by their delivery order's (customer, ship_to, contact, comment), in order of
    first appearance (research R6). Each line carries those four attributes."""
    groups: dict[tuple, list] = {}
    for line in lines:
        key = (line.customer, line.ship_to, line.contact, line.comment)
        groups.setdefault(key, []).append(line)
    return list(groups.items())


async def _delivery_sales_orders(
    db: AsyncSession, details: Sequence[DeliveryOrderDetail]
) -> list[SalesOrder]:
    """The distinct sales orders the lines draw on, by id, each through `attach_derived`."""
    detail_ids = {d.sales_order_detail for d in details if d.sales_order_detail is not None}
    if not detail_ids:
        return []
    order_ids = set(
        (
            await db.execute(
                select(SalesOrderDetail.sales_order).where(
                    SalesOrderDetail.sales_order_detail_id.in_(detail_ids)
                )
            )
        )
        .scalars()
        .all()
    )
    orders = await _by_id(db, SalesOrder, SalesOrder.sales_order_id, order_ids)
    for order_id in sorted(orders):
        await sales_order_service.attach_derived(db, orders[order_id])
    return [orders[order_id] for order_id in sorted(orders)]


async def _payments_on_delivery(db: AsyncSession, sales_orders: Sequence[SalesOrder]) -> list[dict]:
    """Research R3: the standing applications whose payment has no cash session, as
    `{order, amount, method}` with the payment's own amount, as legacy printed it."""
    applications = [
        a
        for order in sales_orders
        for a in await customer_payment_service.list_order_applications(
            db, order.sales_order_id
        )
        if not a['cancelled']
    ]
    payments = await _by_id(
        db,
        CustomerPayment,
        CustomerPayment.customer_payment_id,
        {a['customer_payment'] for a in applications},
    )
    return [
        {
            'order': a['sales_order'],
            'amount': payments[a['customer_payment']].amount,
            'method': payments[a['customer_payment']].method,
        }
        for a in applications
        if payments[a['customer_payment']].cash_session is None
    ]


def _full_name(employee: Employee) -> str:
    return f'{employee.first_name} {employee.last_name}'.strip()


async def delivery_ticket_context(
    db: AsyncSession, order: DeliveryOrder, *, warehouse: int | None = None
) -> tuple[str, dict]:
    """The 72 mm delivery ticket, or the counter ticket for a pickup (spec 026, research R1).

    Reads only. The warehouse filter builds a new list; the sales orders, the payments on
    delivery and the amount to collect still describe the whole order (research R9).
    """
    money = formatting.money
    pickup = order.fulfillment_type == FulfillmentType.PICKUP
    header = await header_context(
        db, order.facility, 'Entrega en Mostrador' if pickup else 'Orden de Entrega'
    )
    if pickup:
        header['rfc'] = ''
    customer = await db.get(Customer, order.customer)
    contact = await db.get(Contact, order.contact) if order.contact is not None else None
    ship_to = await db.get(Address, order.ship_to) if order.ship_to is not None else None

    every_line = sorted(
        await delivery_order_service.lines_of(db, order.delivery_order_id),
        key=lambda line: line.delivery_order_detail_id,
    )
    sales_orders = await _delivery_sales_orders(db, every_line)
    payments = await _payments_on_delivery(db, sales_orders)
    source = {
        line.sales_order_detail_id: (sales_order.sales_order_id, line.comment)
        for sales_order in sales_orders
        for line in sales_order.lines
    }
    units = await sales_order_service.units_by_product(db, {row.product for row in every_line})

    lines = [row for row in every_line if warehouse is None or row.warehouse == warehouse]
    if not pickup:
        lines = [row for row in lines if row.quantity > 0]

    today = local_today()
    walk_in = ''
    if pickup and order.customer == settings.default_customer_id and sales_orders:
        walk_in = (sales_orders[0].customer_name or '').strip()

    delivered_by = ''
    if pickup:
        event = (
            await db.execute(
                select(DeliveryOrderEvent)
                .where(
                    DeliveryOrderEvent.delivery_order == order.delivery_order_id,
                    DeliveryOrderEvent.to_status == DeliveryOrderStatus.PICKED_UP,
                )
                .order_by(DeliveryOrderEvent.delivery_order_event_id.desc())
                .limit(1)
            )
        ).scalar_one_or_none()
        employee = await db.get(Employee, event.employee if event is not None else order.updater)
        delivered_by = _full_name(employee)

    to_collect = sum(
        (
            sales_order.balance
            for sales_order in sales_orders
            if sales_order.payment_terms == PaymentTerms.IMMEDIATE and not sales_order.paid
        ),
        Decimal(0),
    )
    url = (ship_to.url_address or '').strip() if ship_to is not None else ''
    order_id = formatting.pad8(order.delivery_order_id)
    cancelled = order.status == DeliveryOrderStatus.CANCELLED
    context = {
        'header': header,
        'order_id': order_id,
        'folio': formatting.pad8(order.serial) if order.serial is not None else '',
        'customer': customer.name,
        'customer_name': walk_in,
        'date': formatting.date_short(order.date or order.creation_time),
        'sales_orders': [
            {
                'id': sales_order.sales_order_id,
                'terms': formatting.terms_name(sales_order.payment_terms),
                'status': _order_status_label(
                    sales_order.payment_terms, sales_order.paid, sales_order.due_date, today
                ),
            }
            for sales_order in sales_orders
        ],
        'contact': (
            {'name': contact.name, 'mobile': (contact.mobile or '').strip()}
            if contact is not None
            else None
        ),
        'ship_to': _ship_to_lines(ship_to),
        'comment': (order.comment or '').strip() or None,
        'lines': [
            {
                'quantity': formatting.qty(line.quantity),
                'unit': _unit_label(units.get(line.product), symbol=False),
                'name': line.product_name,
                'sales_order': source.get(line.sales_order_detail, ('', None))[0],
                'comment': source.get(line.sales_order_detail, ('', None))[1] or '',
            }
            for line in lines
        ],
        'payments': [
            {
                'order': p['order'],
                'amount': money(p['amount']),
                'method': formatting.method_name(p['method']),
            }
            for p in payments
        ],
        'to_collect': money(to_collect) if to_collect > 0 else None,
        'link': (
            {
                'nickname': (ship_to.nickname or '').strip(),
                'url': url,
                'qr': formatting.qr_data_uri(url),
            }
            if url
            else None
        ),
        'delivered_by': delivered_by,
        'barcode': formatting.barcode_data_uri(order_id),
        'cancelled': cancelled,
        'cancelled_at': formatting.date_time(order.modification_time) if cancelled else None,
    }
    return ('pickup_ticket.html' if pickup else 'delivery_ticket.html'), context


_DASH = '—'


async def _itinerary_data(db: AsyncSession, itinerary: DeliveriesItinerary, title: str) -> dict:
    """What both itinerary documents print: the header, the trip block and the stops.

    Every stop line is a `SimpleNamespace` carrying the four `_note_groups` keys (its delivery
    order's customer, ship-to, contact and comment) next to what the templates print. Quantities
    are the sent ones, fixed at departure. Reads only.
    """
    stops = await delivery_itinerary_service.stops_of(db, itinerary.deliveries_itinerary_id)
    rows = []
    for stop in stops:
        stop_rows = await delivery_itinerary_service.lines_of_stop(
            db, stop.deliveries_itinerary_stop_id
        )
        rows.append((stop, sorted(stop_rows, key=lambda row: row.deliveries_itinerary_detail_id)))
    every = [line for _, lines in rows for line in lines]
    details = await _by_id(
        db,
        DeliveryOrderDetail,
        DeliveryOrderDetail.delivery_order_detail_id,
        {line.delivery_order_detail for line in every},
    )
    order_ids = {d.delivery_order for d in details.values()}
    orders = await _by_id(db, DeliveryOrder, DeliveryOrder.delivery_order_id, order_ids)
    sales_ids = {
        d.sales_order_detail for d in details.values() if d.sales_order_detail is not None
    }
    sales_lines = await _by_id(
        db, SalesOrderDetail, SalesOrderDetail.sales_order_detail_id, sales_ids
    )
    placed = list(orders.values())
    customers = await _by_id(db, Customer, Customer.customer_id, {o.customer for o in placed})
    contacts = await _by_id(
        db, Contact, Contact.contact_id, {o.contact for o in placed if o.contact is not None}
    )
    addresses = await _by_id(
        db, Address, Address.address_id, {o.ship_to for o in placed if o.ship_to is not None}
    )
    units = await sales_order_service.units_by_product(
        db, {d.product for d in details.values()}
    )

    # The store: the dispatch warehouse's, else the first order's (research R7).
    warehouse = await db.get(Warehouse, itinerary.warehouse) if itinerary.warehouse else None
    if warehouse is not None:
        facility_id = warehouse.facility
    elif every:
        facility_id = orders[details[every[0].delivery_order_detail].delivery_order].facility
    else:
        facility_id = (await db.execute(select(func.min(Facility.facility_id)))).scalar_one()
    header = await header_context(db, facility_id, title)

    vehicle = await db.get(Vehicle, itinerary.vehicle) if itinerary.vehicle is not None else None
    operator = (
        await db.get(VehicleOperator, itinerary.vehicle_operator)
        if itinerary.vehicle_operator is not None
        else None
    )
    driver = await db.get(Employee, operator.driver) if operator is not None else None

    def stop_lines(lines: Sequence[DeliveriesItineraryDetail]) -> list[SimpleNamespace]:
        built = []
        for line in lines:
            detail = details[line.delivery_order_detail]
            order = orders[detail.delivery_order]
            sales_line = sales_lines.get(detail.sales_order_detail)
            contact = contacts.get(order.contact)
            built.append(
                SimpleNamespace(
                    customer=order.customer,
                    ship_to=order.ship_to,
                    contact=order.contact,
                    comment=(order.comment or '').strip() or None,
                    customer_name=customers[order.customer].name,
                    contact_name=contact.name if contact is not None else None,
                    contact_mobile=(contact.mobile or '').strip() if contact is not None else '',
                    address=addresses.get(order.ship_to),
                    quantity=formatting.qty(line.sent_quantity),
                    unit=_unit_label(units.get(detail.product), symbol=True),
                    name=detail.product_name,
                    delivery_order=order.delivery_order_id,
                    sales_order=sales_line.sales_order if sales_line is not None else '',
                )
            )
        return built

    return {
        'header': header,
        'itinerary_id': formatting.pad6(itinerary.deliveries_itinerary_id),
        'operator': _full_name(driver) if driver is not None else _DASH,
        'vehicle': f'{vehicle.nickname} · {vehicle.license_plate}' if vehicle else _DASH,
        'date': formatting.date_short(itinerary.date),
        'comment': (itinerary.comment or '').strip() or None,
        'stops': [(stop, stop_lines(lines)) for stop, lines in rows],
    }


async def itinerary_ticket_context(
    db: AsyncSession, itinerary: DeliveriesItinerary
) -> tuple[str, dict]:
    """The 72 mm itinerary ticket: the trip, then its stops in sequence (spec 026, research R6)."""
    data = await _itinerary_data(db, itinerary, 'Itinerario de Entrega')
    stops = []
    for stop, lines in data.pop('stops'):
        names = list(dict.fromkeys(line.customer_name for line in lines))
        first = _ship_to_lines(lines[0].address)[:1] if lines else []
        stops.append(
            {
                'number': stop.sequence,
                'customers': ', '.join(names),
                'ship_to': first[0] if first else '',
                'lines': lines,
            }
        )
    data['stops'] = stops
    return 'itinerary_ticket.html', data


async def delivery_notes_context(
    db: AsyncSession, itinerary: DeliveriesItinerary
) -> tuple[str, dict]:
    """The acknowledgements and receipts, one pair per group within each stop (research R6)."""
    data = await _itinerary_data(db, itinerary, 'Acuses de Recibido')
    groups = []
    for _, lines in data.pop('stops'):
        for _key, group in _note_groups(lines):
            first = group[0]
            url = (first.address.url_address or '').strip() if first.address else ''
            groups.append(
                {
                    'customer': first.customer_name,
                    'contact': first.contact_name,
                    'mobile': first.contact_mobile,
                    'ship_to': _ship_to_lines(first.address),
                    'comment': first.comment,
                    'link': (
                        {
                            'nickname': (first.address.nickname or '').strip(),
                            'url': url,
                            'qr': formatting.qr_data_uri(url),
                        }
                        if url
                        else None
                    ),
                    'receiver': first.contact_name or first.customer_name,
                    'lines': group,
                }
            )
    data['groups'] = groups
    return 'delivery_notes.html', data


async def delivery_order_document_context(
    db: AsyncSession, order: DeliveryOrder
) -> tuple[str, dict]:
    """The letter-size delivery order, after legacy `DeliveryFormatRamos` (spec 026, research R10).

    Every line prints; this document has no warehouse filter. The taxpayer is blanked as on the
    pedido, and the contact's phone is the contact's own, which corrects the legacy copy-paste.
    """
    money = formatting.money
    order_id = formatting.pad8(order.delivery_order_id)
    header = await header_context(db, order.facility, f'Orden de Entrega {order_id}', letter=True)
    header['rfc'] = header['taxpayer_name'] = ''
    customer = await db.get(Customer, order.customer)
    contact = await db.get(Contact, order.contact) if order.contact is not None else None
    ship_to = await db.get(Address, order.ship_to) if order.ship_to is not None else None

    lines = sorted(
        await delivery_order_service.lines_of(db, order.delivery_order_id),
        key=lambda line: line.delivery_order_detail_id,
    )
    sales_orders = await _delivery_sales_orders(db, lines)
    payments = await _payments_on_delivery(db, sales_orders)
    comments = {
        line.sales_order_detail_id: line.comment
        for sales_order in sales_orders
        for line in sales_order.lines
    }

    terms = []
    for sales_order in sales_orders:
        name = formatting.terms_name(sales_order.payment_terms)
        if sales_order.payment_terms == PaymentTerms.NET_D:
            name += f' – {customer.credit_days or 0} días'
        if name not in terms:
            terms.append(name)

    phone = None
    if contact is not None:
        phone = (contact.mobile or '').strip() or (contact.phone or '').strip() or None

    cancelled = order.status == DeliveryOrderStatus.CANCELLED
    context = {
        'header': header,
        'order_id': order_id,
        'customer': customer.name,
        'contact': contact.name if contact is not None else None,
        'contact_phone': phone,
        'ship_to': _ship_to_lines(ship_to),
        'comment': (order.comment or '').strip() or None,
        'date': formatting.date_short(order.date or order.creation_time),
        'terms': ', '.join(terms),
        'lines': [
            {
                'quantity': formatting.qty(line.quantity),
                'code': line.product_code,
                'name': line.product_name,
                'comment': comments.get(line.sales_order_detail) or '',
            }
            for line in lines
        ],
        'payments': [
            {
                'order': p['order'],
                'amount': money(p['amount']),
                'method': formatting.method_name(p['method']),
            }
            for p in payments
        ],
        'cancelled': cancelled,
        'cancelled_at': formatting.date_time(order.modification_time) if cancelled else None,
    }
    return 'delivery_order.html', context
