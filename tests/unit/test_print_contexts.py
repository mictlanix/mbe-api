"""What goes into a printed document, built from rows (#230).

`db` is a fake that answers `get` by (model, key) and `execute` by the statement's first selected
entity, so the tests do not depend on the order the builder issues its queries in.
"""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.config import settings
from app.enums import CurrencyCode, PaymentMethod, PaymentTerms, PaymentType
from app.models.core import Address, Contact, Employee, Facility, PaymentMethodOption
from app.models.customer import Customer
from app.models.fiscal import TaxpayerIssuer
from app.models.sales import CreditNote, CustomerPayment, CustomerRefund
from app.rendering.formatting import barcode_data_uri
from app.services.cash_session_service import CashCutFigures
from app.services.print_contexts import (
    cash_cut_context,
    gross_subtotal_and_discount,
    header_context,
    sale_ticket_context,
    sales_order_context,
)

ISSUER = 'BBB020202BB1'


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> '_Result':
        return self

    def all(self) -> list:
        return self._rows


class _Db:
    def __init__(self, rows: dict, results: dict) -> None:
        self.rows = rows
        self.results = results

    async def get(self, model: type, key: object) -> object:
        return self.rows.get((model, key))

    async def execute(self, statement: object) -> _Result:
        entity = statement.column_descriptions[0]['entity']
        return _Result(list(self.results.get(entity, [])))


@pytest.fixture(autouse=True)
def _images(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(settings, 'images_dir', str(tmp_path))
    monkeypatch.setattr(settings, 'default_customer_id', 1)
    return tmp_path


def _facility(**overrides) -> SimpleNamespace:
    base = dict(
        facility_id=1,
        name='Matriz',
        address=1,
        taxpayer=ISSUER,
        logo='logo.png',
        receipt_message=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _address(**overrides) -> SimpleNamespace:
    base = dict(
        street='Av. Reforma',
        exterior_number='100',
        interior_number=None,
        neighborhood='Centro',
        locality=None,
        postal_code='06000',
        borough='Cuauhtémoc',
        state='CDMX',
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _line(**overrides) -> SimpleNamespace:
    base = dict(
        quantity=Decimal('10'),
        price=Decimal('100'),
        discount_rate=Decimal('0'),
        tax_rate=Decimal('0.16'),
        tax_included=False,
        product_code='P1',
        product_name='Producto Uno',
        comment=None,
        total=Decimal('1160.00'),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _order(**overrides) -> SimpleNamespace:
    """An order as `attach_derived` leaves it."""
    base = dict(
        sales_order_id=42,
        facility=1,
        serial=15,
        customer=1,
        customer_name=None,
        salesperson=3,
        payment_terms=PaymentTerms.IMMEDIATE,
        date=datetime(2026, 9, 24, 15, 3),
        due_date=datetime(2026, 10, 24),
        modification_time=datetime(2026, 9, 24, 16, 0, 5),
        completed=False,
        cancelled=False,
        paid=False,
        lines=[_line()],
        subtotal=Decimal('1000.00'),
        tax_total=Decimal('160.00'),
        total=Decimal('1160.00'),
        balance=Decimal('1160.00'),
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _application(payment_id: int, **overrides) -> dict:
    base = dict(
        sales_order_payment_id=payment_id * 10,
        customer_payment=payment_id,
        amount=Decimal('1160'),
        amount_change=Decimal('0'),
        cancelled=False,
        method=PaymentMethod.CASH,
        payment_type=PaymentType.IMMEDIATE,
    )
    base.update(overrides)
    return base


def _payment(payment_id: int, **overrides) -> SimpleNamespace:
    base = dict(
        customer_payment_id=payment_id,
        amount=Decimal('1200'),
        method=PaymentMethod.CASH,
        payment_charge=None,
        cash_session=1,
        payment_type=PaymentType.IMMEDIATE,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _db(
    *,
    facility: SimpleNamespace | None = None,
    address: SimpleNamespace | None = None,
    issuer: SimpleNamespace | None = None,
    customer: SimpleNamespace | None = None,
    payments: tuple = (),
    options: tuple = (),
    refunds: tuple = (),
    credit_notes: tuple = (),
) -> _Db:
    facility = facility or _facility()
    rows = {
        (Facility, 1): facility,
        (Address, 1): address or _address(),
        (Customer, 1): customer or SimpleNamespace(name='Cliente Uno'),
        (Employee, 3): SimpleNamespace(first_name='Ana', last_name='Ruiz', nickname='ana'),
    }
    if facility.taxpayer:
        rows[(TaxpayerIssuer, facility.taxpayer)] = issuer or SimpleNamespace(
            taxpayer_issuer_id=facility.taxpayer, name='Mictlanix SA de CV'
        )
    return _Db(
        rows,
        {
            CustomerPayment: payments,
            PaymentMethodOption: options,
            CustomerRefund: refunds,
            CreditNote: credit_notes,
        },
    )


async def _context(order: SimpleNamespace, db: _Db | None = None, applications: tuple = ()):
    with patch(
        'app.services.customer_payment_service.list_order_applications',
        AsyncMock(return_value=list(applications)),
    ):
        return await sale_ticket_context(db or _db(), order)


# ── Header ────────────────────────────────────────────────────────────────────


class TestHeader:
    async def test_fields_and_address_lines(self) -> None:
        header = await header_context(_db(), 1, 'Punto de Venta')

        assert header == {
            'title': 'Punto de Venta',
            'name': 'Matriz',
            'logo': None,
            'skip_address': False,
            'rfc': ISSUER,
            'taxpayer_name': 'Mictlanix SA de CV',
            'address': ['Av. Reforma 100 Centro,', '06000 Cuauhtémoc, CDMX'],
        }

    async def test_address_carries_interior_number_and_locality(self) -> None:
        db = _db(address=_address(interior_number='B', locality='Tlalpan'))

        header = await header_context(db, 1, 'x')

        assert header['address'][0] == 'Av. Reforma 100 - B Centro, Tlalpan,'

    async def test_skip_address_is_passed_through(self) -> None:
        assert (await header_context(_db(), 1, 'x', skip_address=True))['skip_address'] is True

    async def test_missing_logo_file_gives_none(self) -> None:
        assert (await header_context(_db(), 1, 'x'))['logo'] is None

    async def test_no_logo_configured_gives_none(self) -> None:
        assert (await header_context(_db(facility=_facility(logo=None)), 1, 'x'))['logo'] is None

    async def test_existing_logo_is_a_file_uri(self, _images: Path) -> None:
        (_images / 'logo.png').write_bytes(b'png')

        header = await header_context(_db(), 1, 'x')

        assert header['logo'] == (_images / 'logo.png').resolve().as_uri()

    @pytest.mark.parametrize(
        'stored', ['/images/logo.png', '~/Content/images/logo.png', 'https://x.test/images/logo.png']
    )
    async def test_a_legacy_path_logo_is_found_by_its_file_name(
        self, _images: Path, stored: str
    ) -> None:
        """Legacy renders `facility.logo` with `Url.Content`, so a row can hold a path or URL, not
        the bare file name mbe-api's upload stores. Only its file name inside `images_dir` counts;
        a path is never followed outside it."""
        (_images / 'logo.png').write_bytes(b'png')
        header = await header_context(_db(facility=_facility(logo=stored)), 1, 'x')
        assert header['logo'] == (_images / 'logo.png').resolve().as_uri()

    async def test_a_directory_only_logo_gives_none(self) -> None:
        """`/images/`, the one non-null `facility.logo` in mbe_dev, names no file."""
        header = await header_context(_db(facility=_facility(logo='/images/')), 1, 'x')
        assert header['logo'] is None

    async def test_no_taxpayer_leaves_rfc_and_name_blank(self) -> None:
        header = await header_context(_db(facility=_facility(taxpayer=None)), 1, 'x')

        assert header['rfc'] == ''
        assert header['taxpayer_name'] == ''


# ── Template and header rows ──────────────────────────────────────────────────


class TestTemplate:
    async def test_not_completed_is_the_pre_payment_ticket(self) -> None:
        template, context = await _context(_order())

        assert template == 'sale_ticket.html'
        assert context['header']['title'] == 'Punto de Venta'

    async def test_cancelled_draft_is_still_the_pre_payment_ticket(self) -> None:
        template, _ = await _context(_order(cancelled=True))

        assert template == 'sale_ticket.html'

    async def test_completed_is_the_receipt(self) -> None:
        template, context = await _context(_order(completed=True))

        assert template == 'sale_receipt.html'
        assert context['header']['title'] == 'Ticket de Venta'


class TestHeaderRows:
    async def test_values(self) -> None:
        _, context = await _context(_order())

        assert context['folio'] == '00000015'
        assert context['order_id'] == '00000042'
        assert context['sales_order_id'] == 42
        assert context['date'] == '2026-09-24'
        assert context['customer'] == 'Cliente Uno'
        assert context['terms'] == 'Contado'
        assert context['is_credit'] is False
        assert context['due_date'] == '2026-10-24'

    async def test_draft_without_serial_has_a_blank_folio(self) -> None:
        _, context = await _context(_order(serial=None))

        assert context['folio'] == ''

    async def test_per_order_name_on_the_default_customer(self) -> None:
        _, context = await _context(_order(customer_name='Juan'))

        assert context['customer_name'] == 'Juan'

    async def test_blank_per_order_name_is_not_printed(self) -> None:
        _, context = await _context(_order(customer_name='   '))

        assert context['customer_name'] == ''

    async def test_per_order_name_ignored_for_another_customer(self, monkeypatch) -> None:
        monkeypatch.setattr(settings, 'default_customer_id', 99)

        _, context = await _context(_order(customer_name='Juan'))

        assert context['customer_name'] == ''

    async def test_salesperson_full_name_on_the_pre_payment_ticket(self) -> None:
        _, context = await _context(_order())

        assert context['salesperson'] == 'Ana Ruiz'

    async def test_salesperson_nickname_on_the_receipt(self) -> None:
        _, context = await _context(_order(completed=True))

        assert context['salesperson'] == 'ana'

    async def test_barcode_carries_the_padded_order_id(self) -> None:
        with patch('app.rendering.formatting.barcode_data_uri', return_value='data:x') as barcode:
            _, context = await _context(_order())

        barcode.assert_called_once_with('00000042')
        assert context['barcode'] == 'data:x'

    async def test_barcode_is_a_data_uri(self) -> None:
        _, context = await _context(_order())

        assert context['barcode'] == barcode_data_uri('00000042')


# ── Lines and totals ──────────────────────────────────────────────────────────


class TestLines:
    async def test_line_values(self) -> None:
        line = _line(
            quantity=Decimal('2.5000'),
            price=Decimal('10.5'),
            discount_rate=Decimal('0.1'),
            comment='sin cebolla',
            total=Decimal('27.41'),
        )

        _, context = await _context(_order(lines=[line]))

        assert context['lines'] == [
            {
                'quantity': '2.5',
                'price': '$10.50',
                'discount': '10.00 %',
                'code': 'P1',
                'name': 'Producto Uno',
                'comment': 'sin cebolla',
                'total': '$27.41',
            }
        ]

    async def test_no_discount_rate_prints_blank(self) -> None:
        _, context = await _context(_order())

        assert context['lines'][0]['discount'] == ''
        assert context['lines'][0]['comment'] == ''

    async def test_totals(self) -> None:
        _, context = await _context(_order())

        assert (context['subtotal'], context['taxes'], context['total']) == (
            '$1,000.00',
            '$160.00',
            '$1,160.00',
        )
        assert context['balance'] == '$1,160.00'

    async def test_discount_is_tax_exclusive_and_the_totals_add_up(self) -> None:
        lines = [
            # Tax included: 116 → 100 net undiscounted, 104.40 → 90 net discounted.
            _line(
                quantity=Decimal('1'),
                price=Decimal('116'),
                discount_rate=Decimal('0.1'),
                tax_included=True,
            ),
            # Tax added: 100 net undiscounted, 75 net discounted.
            _line(quantity=Decimal('2'), price=Decimal('50'), discount_rate=Decimal('0.25')),
        ]
        order = _order(
            lines=lines,
            subtotal=Decimal('165.00'),
            tax_total=Decimal('26.40'),
            total=Decimal('191.40'),
        )

        _, context = await _context(order)

        assert context['discount'] == '$35.00'
        assert context['subtotal'] == '$200.00'
        assert context['taxes'] == '$26.40'
        assert context['total'] == '$191.40'

    def test_subtotal_less_discount_plus_taxes_is_the_total(self) -> None:
        lines = [
            _line(
                quantity=Decimal('3'),
                price=Decimal('10.005'),
                discount_rate=Decimal('0.1'),
                tax_included=True,
            ),
            _line(quantity=Decimal('2.5'), price=Decimal('38.9'), discount_rate=Decimal('0.15')),
        ]
        order = _order(lines=lines, tax_total=Decimal('13.08'), total=Decimal('109.52'))

        subtotal, discount = gross_subtotal_and_discount(order)

        # 3.0015 / 1.16 = 2.5875 and 14.5875, so 17.175 → 17.18.
        assert discount == Decimal('17.18')
        assert subtotal - discount + order.tax_total == order.total

    async def test_zero_discount_is_not_shown(self) -> None:
        _, context = await _context(_order())

        assert context['discount'] is None


# ── Payments ──────────────────────────────────────────────────────────────────


class TestPayments:
    async def test_immediate_payment_without_option_uses_the_method_name(self) -> None:
        db = _db(payments=(_payment(5),))

        _, context = await _context(_order(completed=True), db, (_application(5),))

        assert context['payments'] == [{'label': 'Efectivo', 'amount': '$1,200.00'}]

    async def test_option_shown_on_ticket_uses_its_name(self) -> None:
        payment = _payment(5, method=PaymentMethod.CREDIT_CARD, payment_charge=8)
        option = SimpleNamespace(
            payment_method_option_id=8, name='BBVA 3 MSI', display_on_ticket=True
        )
        db = _db(payments=(payment,), options=(option,))

        _, context = await _context(_order(completed=True), db, (_application(5),))

        assert context['payments'][0]['label'] == 'BBVA 3 MSI'

    async def test_option_hidden_from_ticket_uses_the_method_name(self) -> None:
        payment = _payment(5, method=PaymentMethod.CREDIT_CARD, payment_charge=8)
        option = SimpleNamespace(
            payment_method_option_id=8, name='BBVA 3 MSI', display_on_ticket=False
        )
        db = _db(payments=(payment,), options=(option,))

        _, context = await _context(_order(completed=True), db, (_application(5),))

        assert context['payments'][0] == {'label': 'T. de Crédito', 'amount': '$1,200.00'}

    async def test_non_immediate_payment_is_type_and_id_with_the_applied_amount(self) -> None:
        payment = _payment(5, payment_type=PaymentType.CREDIT_PAYMENT, amount=Decimal('5000'))
        db = _db(payments=(payment,))

        _, context = await _context(
            _order(completed=True), db, (_application(5, amount=Decimal('300')),)
        )

        assert context['payments'] == [{'label': 'Pago de Crédito - 5', 'amount': '$300.00'}]

    async def test_cancelled_applications_never_appear(self) -> None:
        db = _db(payments=(_payment(5), _payment(6)))
        applications = (
            _application(5, amount=Decimal('100'), amount_change=Decimal('0')),
            _application(6, amount=Decimal('1060'), amount_change=Decimal('40'), cancelled=True),
        )

        _, context = await _context(_order(completed=True), db, applications)

        assert [row['label'] for row in context['payments']] == ['Efectivo']
        assert context['paid'] == '$100.00'
        assert context['change'] is None

    async def test_change_is_summed_over_live_applications(self) -> None:
        db = _db(payments=(_payment(5), _payment(6)))
        applications = (
            _application(5, amount=Decimal('500'), amount_change=Decimal('20')),
            _application(6, amount=Decimal('660'), amount_change=Decimal('20.5')),
        )

        _, context = await _context(_order(completed=True), db, applications)

        assert context['change'] == '$40.50'
        assert context['paid'] == '$1,160.00'

    async def test_no_change_is_not_shown(self) -> None:
        db = _db(payments=(_payment(5),))

        _, context = await _context(_order(completed=True), db, (_application(5),))

        assert context['change'] is None

    async def test_no_payments(self) -> None:
        _, context = await _context(_order())

        assert context['payments'] == []
        assert context['paid'] == '$0.00'

    @pytest.mark.parametrize(
        ('method', 'expected'),
        [
            (PaymentMethod.CREDIT_CARD, True),
            (PaymentMethod.DEBIT_CARD, True),
            (PaymentMethod.CASH, False),
            (PaymentMethod.EFT, False),
        ],
    )
    async def test_card_payment_flag(self, method: PaymentMethod, expected: bool) -> None:
        db = _db(payments=(_payment(5, method=method),))

        _, context = await _context(_order(completed=True), db, (_application(5),))

        assert context['has_card_payment'] is expected

    async def test_cancelled_card_application_raises_no_card_flag(self) -> None:
        db = _db(payments=(_payment(5, method=PaymentMethod.CREDIT_CARD),))

        _, context = await _context(_order(completed=True), db, (_application(5, cancelled=True),))

        assert context['has_card_payment'] is False

    async def test_payments_with_no_cash_session_are_on_delivery(self) -> None:
        db = _db(
            payments=(
                _payment(5, cash_session=None, method=PaymentMethod.EFT, amount=Decimal('300')),
                _payment(6),
            )
        )
        applications = (_application(5), _application(6))

        _, context = await _context(_order(completed=True), db, applications)

        assert context['on_delivery_payments'] == [
            {'method': 'Transferencia Electrónica', 'amount': '$300.00'}
        ]

    async def test_no_on_delivery_payments(self) -> None:
        db = _db(payments=(_payment(5),))

        _, context = await _context(_order(completed=True), db, (_application(5),))

        assert context['on_delivery_payments'] == []


# ── Refunds and credit notes ──────────────────────────────────────────────────


class TestRefundsAndCreditNotes:
    async def test_refund_rows_total_each_refund(self) -> None:
        refund = SimpleNamespace(customer_refund_id=9)
        detail = SimpleNamespace(
            quantity=Decimal('2'),
            price=Decimal('100'),
            discount=Decimal('0'),
            tax_rate=Decimal('0.16'),
            tax_included=False,
        )
        db = _db(refunds=((refund, detail), (refund, detail)))

        _, context = await _context(_order(completed=True), db)

        assert context['refunds'] == [{'label': 'Devolución 00000009', 'amount': '$464.00'}]

    async def test_credit_note_rows(self) -> None:
        note = SimpleNamespace(credit_note_id=3)
        payment = _payment(
            12, payment_type=PaymentType.CREDIT_NOTE, method=PaymentMethod.NA, amount=Decimal('50')
        )
        db = _db(credit_notes=((note, payment),))

        _, context = await _context(_order(completed=True), db)

        assert context['credit_notes'] == [
            {'label': 'Nota de Crédito - 12', 'amount': '$50.00'}
        ]


# ── Conditional blocks ────────────────────────────────────────────────────────


class TestFlags:
    @pytest.mark.parametrize(
        ('completed', 'terms', 'paid', 'balance', 'expected'),
        [
            (True, PaymentTerms.IMMEDIATE, False, Decimal('10'), True),
            (False, PaymentTerms.IMMEDIATE, False, Decimal('10'), False),
            (True, PaymentTerms.NET_D, False, Decimal('10'), False),
            (True, PaymentTerms.IMMEDIATE, True, Decimal('10'), False),
            (True, PaymentTerms.IMMEDIATE, False, Decimal('0'), False),
        ],
    )
    async def test_por_cobrar(
        self, completed: bool, terms: PaymentTerms, paid: bool, balance: Decimal, expected: bool
    ) -> None:
        order = _order(completed=completed, payment_terms=terms, paid=paid, balance=balance)

        _, context = await _context(order)

        assert context['por_cobrar'] is expected

    async def test_promissory_note_on_unpaid_credit(self) -> None:
        order = _order(completed=True, payment_terms=PaymentTerms.NET_D, balance=Decimal('860'))

        _, context = await _context(order)

        assert context['is_credit'] is True
        assert context['promissory_note'] == settings.promissory_note_template.format(
            customer='Cliente Uno',
            balance='$860.00',
            due_date='2026-10-24',
            issuer='Mictlanix SA de CV',
        )

    @pytest.mark.parametrize(
        ('terms', 'paid'), [(PaymentTerms.NET_D, True), (PaymentTerms.IMMEDIATE, False)]
    )
    async def test_no_promissory_note(self, terms: PaymentTerms, paid: bool) -> None:
        _, context = await _context(_order(completed=True, payment_terms=terms, paid=paid))

        assert context['promissory_note'] is None

    async def test_promissory_issuer_blank_without_taxpayer(self) -> None:
        order = _order(completed=True, payment_terms=PaymentTerms.NET_D)

        _, context = await _context(order, _db(facility=_facility(taxpayer=None)))

        assert 'orden de ,' in context['promissory_note']

    async def test_receipt_message_is_passed_through(self) -> None:
        db = _db(facility=_facility(receipt_message='¡Gracias por su compra!'))

        _, context = await _context(_order(completed=True), db)

        assert context['receipt_message'] == '¡Gracias por su compra!'

    async def test_no_receipt_message(self) -> None:
        _, context = await _context(_order(completed=True))

        assert context['receipt_message'] is None

    async def test_cancelled_at_only_when_cancelled(self) -> None:
        _, cancelled = await _context(_order(completed=True, cancelled=True))
        _, live = await _context(_order(completed=True))

        assert cancelled['cancelled'] is True
        assert cancelled['cancelled_at'] == '2026-09-24 16:00:05'
        assert live['cancelled_at'] is None

    async def test_paid_flag(self) -> None:
        _, context = await _context(_order(completed=True, paid=True))

        assert context['is_paid'] is True
        assert context['completed'] is True


# ── Cash cut ──────────────────────────────────────────────────────────────────


def _figures(**overrides) -> CashCutFigures:
    base = dict(
        sales_by_method=[
            (PaymentMethod.CASH, Decimal('1160')),
            (PaymentMethod.CREDIT_CARD, Decimal('300')),
        ],
        sales_total=Decimal('1460'),
        refunds_by_method=[(PaymentMethod.NA, Decimal('116'))],
        expenses_total=Decimal('80'),
        starting_cash=Decimal('500'),
        cash_sales=Decimal('1160'),
        cash_refunds=Decimal('100'),
        cash_in_drawer=Decimal('1480'),
        counted_cash=Decimal('1450'),
        difference=Decimal('30'),
        is_shortage=True,
    )
    base.update(overrides)
    return CashCutFigures(**base)


async def _cut(figures: CashCutFigures) -> tuple[str, dict]:
    session = SimpleNamespace(
        cash_session_id=12,
        start=datetime(2026, 9, 24, 9),
        end=datetime(2026, 9, 24, 21, 30, 5),
        cash_drawer_detail=SimpleNamespace(name='Cajón 1', facility=1),
        cashier_detail=SimpleNamespace(first_name='Ana', last_name='Ruiz'),
    )
    with patch(
        'app.services.cash_session_service.cut_figures', AsyncMock(return_value=figures)
    ) as cut:
        result = await cash_cut_context(_db(), session)
    cut.assert_awaited_once()
    assert cut.await_args.args[1] == 12
    return result


class TestCashCut:
    async def test_values(self) -> None:
        template, context = await _cut(_figures())

        assert template == 'cash_cut.html'
        assert context['header']['title'] == 'Corte de Caja'
        assert context['header']['skip_address'] is True
        assert context['session_id'] == '000012'
        assert context['barcode'] == barcode_data_uri('000012')
        assert (context['drawer'], context['cashier']) == ('Cajón 1', 'Ana Ruiz')
        assert (context['start'], context['end']) == ('2026-09-24 09:00:00', '2026-09-24 21:30:05')
        assert context['sales'] == [
            {'method': 'Efectivo', 'amount': '$1,160.00'},
            {'method': 'T. de Crédito', 'amount': '$300.00'},
        ]
        assert context['sales_total'] == '$1,460.00'
        assert context['refunds'] == [{'method': 'N/A', 'amount': '$116.00'}]
        assert context['expenses'] == [{'method': 'Efectivo', 'amount': '$80.00'}]
        assert context['cash_refunds'] == '$100.00'
        assert context['counted_cash'] == '$1,450.00'
        assert (context['difference_label'], context['difference']) == ('Faltante', '$30.00')

    async def test_overage_and_no_expenses(self) -> None:
        _, context = await _cut(_figures(is_shortage=False, expenses_total=Decimal('0')))

        assert context['difference_label'] == 'Sobrante'
        assert context['expenses'] == []
        assert context['expenses_total'] == '$0.00'


# ── Sales order document ──────────────────────────────────────────────────────


def _document_order(**overrides) -> SimpleNamespace:
    base = dict(
        contact=None,
        ship_to=None,
        comment=None,
        promise_date=datetime(2026, 9, 24, 12),
        currency=CurrencyCode.MXN,
    )
    base.update(overrides)
    return _order(**base)


async def _document(order: SimpleNamespace, **ship_to: object) -> tuple[str, dict]:
    db = _db()
    db.rows[(Contact, 4)] = SimpleNamespace(name='Juan Pérez')
    fields = dict(
        street='Calle 5',
        exterior_number='12',
        interior_number=None,
        neighborhood='Del Valle',
        borough='Benito Juárez',
        state='CDMX',
        country='México',
        postal_code='03100',
        comment='Portón negro',
    )
    fields.update(ship_to)
    db.rows[(Address, 2)] = _address(**fields)
    return await sales_order_context(db, order)


class TestSalesOrderDocument:
    async def test_template_and_values(self) -> None:
        template, context = await _document(_document_order())

        assert template == 'sales_order.html'
        assert context['header']['title'] == 'Pedido - 00000042'
        assert context['order_id'] == '00000042'
        assert context['customer'] == 'Cliente Uno'
        assert context['salesperson'] == 'Ana Ruiz'
        assert context['date'] == '2026-09-24'
        assert context['terms'] == 'Contado'
        assert context['total'] == '$1,160.00'
        assert context['amount_in_words'] == 'MIL CIENTO SESENTA PESOS 00/100 M. N.'

    async def test_header_follows_the_letter_layout_without_taxpayer(self) -> None:
        _, context = await _document(_document_order())

        assert context['header']['address'] == [
            'Av. Reforma 100',
            'Centro, 06000',
            'Cuauhtémoc, CDMX',
        ]
        assert (context['header']['rfc'], context['header']['taxpayer_name']) == ('', '')

    async def test_promise_date_is_long(self) -> None:
        _, context = await _document(_document_order())

        assert context['promise_date'] == 'jueves, septiembre 24, 2026'

    async def test_ship_to_lines(self) -> None:
        _, context = await _document(_document_order(ship_to=2))

        assert context['ship_to'] == [
            'Calle 5 12',
            'Del Valle, Benito Juárez',
            'CDMX, México, 03100',
            'Portón negro',
        ]

    async def test_ship_to_lines_omit_empty_parts(self) -> None:
        order = _document_order(ship_to=2)

        _, context = await _document(order, interior_number='B', borough='', comment=None)

        assert context['ship_to'] == ['Calle 5 12 B', 'Del Valle', 'CDMX, México, 03100']

    async def test_no_ship_to(self) -> None:
        _, context = await _document(_document_order())

        assert context['ship_to'] == []

    async def test_contact_only_when_set(self) -> None:
        _, without = await _document(_document_order())
        _, with_contact = await _document(_document_order(contact=4))

        assert without['contact'] is None
        assert with_contact['contact'] == 'Juan Pérez'

    async def test_comment_only_when_not_blank(self) -> None:
        _, blank = await _document(_document_order(comment='  '))
        _, set_ = await _document(_document_order(comment='Llamar antes'))

        assert blank['comment'] is None
        assert set_['comment'] == 'Llamar antes'

    async def test_due_date_only_on_credit(self) -> None:
        _, cash = await _document(_document_order())
        _, credit = await _document(_document_order(payment_terms=PaymentTerms.NET_D))

        assert cash['is_credit'] is False
        assert credit['is_credit'] is True
        assert credit['due_date'] == '2026-10-24'
        assert credit['terms'] == 'Crédito'

    async def test_lines_carry_net_price_and_undiscounted_amount(self) -> None:
        line = _line(
            quantity=Decimal('2'),
            price=Decimal('116'),
            discount_rate=Decimal('0.1'),
            tax_included=True,
            comment='rojo',
        )

        _, context = await _document(_document_order(lines=[line]))

        assert context['lines'] == [
            {
                'quantity': '2',
                'code': 'P1',
                'name': 'Producto Uno',
                'comment': 'rojo',
                'price': '$100.00',
                'amount': '$200.00',
            }
        ]

    async def test_totals_add_up_with_a_discount_and_savings(self) -> None:
        line = _line(
            quantity=Decimal('1'),
            price=Decimal('116'),
            discount_rate=Decimal('0.1'),
            tax_included=True,
        )
        order = _document_order(lines=[line], tax_total=Decimal('14.40'), total=Decimal('104.40'))

        _, context = await _document(order)

        assert (context['subtotal'], context['discount'], context['taxes'], context['total']) == (
            '$100.00',
            '$10.00',
            '$14.40',
            '$104.40',
        )
        assert context['savings'] == '¡Usted ahorró $10.00 en esta compra!'

    async def test_no_discount_no_savings(self) -> None:
        _, context = await _document(_document_order())

        assert context['discount'] is None
        assert context['savings'] is None

    async def test_amount_in_words_receives_the_order_currency(self) -> None:
        with patch('app.rendering.words.amount_in_words', return_value='X') as words:
            _, context = await _document(_document_order(currency=CurrencyCode.USD))

        words.assert_called_once_with(Decimal('1160.00'), CurrencyCode.USD)
        assert context['amount_in_words'] == 'X'
