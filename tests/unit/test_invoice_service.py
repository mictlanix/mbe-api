"""The invoice editor's pure rules (spec 027, research R6 and R7)."""

from datetime import datetime
from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.services import invoice_service as svc
from tests.unit.test_inventory_locks import COMMIT, FakeResult, FakeSession

_T0 = datetime(2026, 10, 1, 9, 0)


def _at(hours: int) -> datetime:
    return _T0.replace(hour=9 + hours)


def _invoice(hours: int, quantity: str) -> svc.LineEvent:
    return svc.LineEvent(_at(hours), svc.INVOICE, Decimal(quantity))


def _refund(hours: int, quantity: str) -> svc.LineEvent:
    return svc.LineEvent(_at(hours), svc.REFUND, Decimal(quantity))


@pytest.mark.parametrize(
    ('events', 'expected'),
    [
        # The spec's two examples (FR-006).
        ([_refund(0, '3'), _invoice(1, '7')], '0'),
        ([_invoice(0, '7'), _refund(1, '3')], '0'),
        # A refund before any invoice takes those units out of what can be invoiced.
        ([_refund(0, '3')], '7'),
        # A refund only reaches the units not yet invoiced: 10 − 4 − 3 − 2 = 1.
        ([_invoice(0, '4'), _refund(1, '3'), _invoice(2, '2')], '1'),
        # A refund of already-invoiced goods frees nothing: that needs a credit note.
        ([_invoice(0, '10'), _refund(1, '4')], '0'),
        ([_invoice(0, '8'), _refund(1, '5')], '0'),
        # At equal times the refund goes first, the conservative order.
        ([_invoice(0, '7'), _refund(0, '3')], '0'),
        ([_invoice(0, '4'), _refund(0, '3')], '3'),
        # Never negative.
        ([_invoice(0, '12')], '0'),
        ([], '10'),
    ],
)
def test_remaining_quantity(events: list[svc.LineEvent], expected: str) -> None:
    assert svc.remaining_quantity(Decimal('10'), events) == Decimal(expected)


def test_remaining_quantity_sorts_its_events() -> None:
    assert svc.remaining_quantity(Decimal('10'), [_invoice(1, '7'), _refund(0, '3')]) == 0


def _batch(batch: str, type_: int = 0) -> SimpleNamespace:
    return SimpleNamespace(batch=batch, type=type_)


def test_default_batch_is_the_stores_when_it_is_an_invoice_batch_of_the_issuer() -> None:
    assert svc.default_batch('B', [_batch('A'), _batch('B')]) == 'B'


@pytest.mark.parametrize('store_default', [None, '', 'Z'])
def test_default_batch_falls_back_to_the_first(store_default: str | None) -> None:
    assert svc.default_batch(store_default, [_batch('A'), _batch('B')]) == 'A'


def test_default_batch_without_invoice_batches_is_none() -> None:
    assert svc.default_batch('A', []) is None


@pytest.mark.parametrize(
    ('credit_days', 'credit_limit', 'expected'),
    [
        (30, Decimal('1000'), svc.PaymentTerms.NET_D),
        (0, Decimal('1000'), svc.PaymentTerms.IMMEDIATE),
        (30, Decimal('0'), svc.PaymentTerms.IMMEDIATE),
        (0, Decimal('0'), svc.PaymentTerms.IMMEDIATE),
    ],
)
def test_default_terms_follow_legacy_has_credit(
    credit_days: int, credit_limit: Decimal, expected: svc.PaymentTerms
) -> None:
    customer = SimpleNamespace(credit_days=credit_days, credit_limit=credit_limit)
    assert svc.default_terms(customer) == expected


@pytest.mark.parametrize(
    ('issued', 'ok'),
    [
        (datetime(2026, 10, 25, 12, 0), True),
        (datetime(2026, 10, 22, 12, 0, 1), True),
        (datetime(2026, 10, 22, 12, 0), False),
        (datetime(2026, 10, 25, 12, 0, 1), False),
    ],
)
def test_issue_date_window(issued: datetime, ok: bool) -> None:
    assert svc.issue_date_ok(issued, now=datetime(2026, 10, 25, 12, 0)) is ok


# ── Payment receipt balances (spec 028, research R2) ──────────────────────────


@pytest.mark.parametrize(('earlier', 'expected'), [(0, 1), (1, 2), (4, 5)])
def test_installment_follows_the_earlier_receipts(earlier: int, expected: int) -> None:
    assert svc.installment(earlier) == expected


@pytest.mark.parametrize(
    ('total', 'paid', 'credited', 'expected'),
    [
        ('10000.00', '0', '0', '10000.00'),
        ('10000.00', '4000.00', '0', '6000.00'),
        ('10000.00', '4000.00', '1160.00', '4840.00'),
        ('100.00', '100.00', '0', '0.00'),
    ],
)
def test_previous_balance(total: str, paid: str, credited: str, expected: str) -> None:
    assert svc.previous_balance(Decimal(total), Decimal(paid), Decimal(credited)) == Decimal(
        expected
    )


@pytest.mark.parametrize(
    ('balance', 'unassigned', 'expected'),
    [
        ('6000.00', '4000.00', '4000.00'),
        ('3000.00', '4000.00', '3000.00'),
        ('3000.00', None, '3000.00'),
        ('3000.00', '0', '0'),
        ('3000.00', '-5', '0'),
    ],
)
def test_default_amount_is_the_smaller_of_balance_and_unassigned(
    balance: str, unassigned: str | None, expected: str
) -> None:
    left = Decimal(unassigned) if unassigned is not None else None
    assert svc.default_amount(Decimal(balance), left) == Decimal(expected)


# ── Reopen and a stamp in flight (spec 039, research R15 rule 8) ──────────────


async def test_reopen_refuses_a_row_locked_as_stamping_though_read_as_sealed() -> None:
    """A stamp committed its marker after the route read the document as sealed."""
    locked = SimpleNamespace(
        fiscal_document_id=9,
        type=0,
        completed=False,
        cancelled=False,
        stamping_since=datetime(2026, 11, 17, 10, 0),
    )
    session = FakeSession(lambda sql: [locked] if sql.rstrip().endswith('FOR UPDATE') else [])
    route_copy = SimpleNamespace(fiscal_document_id=9, type=0, status='sealed')

    with pytest.raises(DomainError) as caught:
        await svc.reopen(session, SimpleNamespace(employee_id=1), route_copy)  # type: ignore[arg-type]

    assert caught.value.code == ErrorCode.FISCAL_DOCUMENT_NOT_SEALED
    [lock] = session.statements
    assert lock._for_update_arg is not None
    assert lock.get_execution_options().get('populate_existing') is True
    # It runs in the route's transaction: no commit before the lock.
    assert COMMIT not in session.log


# ── An invoice being cancelled takes no receipt or credit note (spec 039, R8) ──


def _stamped_invoice(**overrides: object) -> SimpleNamespace:
    values = dict(
        fiscal_document_id=5,
        type=0,
        completed=True,
        cancelled=False,
        stamp_uuid='U1',
        payment_terms=1,
        issuer='EKU9003173C9',
        recipient='XAÑ010101AB1',
        issued=datetime(2026, 10, 1, 9, 0),
        cancelling_since=None,
    )
    values.update(overrides)
    return SimpleNamespace(**values)


_RECEIPT = SimpleNamespace(
    fiscal_document_id=20,
    issuer='EKU9003173C9',
    recipient='XAÑ010101AB1',
    payment_date=datetime(2026, 10, 2).date(),
    payment_amount=Decimal('100.00'),
    payment_method=3,
    taxpayer_regime='601',
    taxpayer_postal_code='06000',
)
_CANCELLING = datetime(2026, 11, 17, 9, 0)


def test_an_invoice_being_cancelled_is_neither_payable_nor_creditable() -> None:
    assert svc._payable(_RECEIPT, _stamped_invoice()) is True
    assert svc._creditable(_stamped_invoice()) is True
    assert svc._payable(_RECEIPT, _stamped_invoice(cancelling_since=_CANCELLING)) is False
    assert svc._creditable(_stamped_invoice(cancelling_since=_CANCELLING)) is False


class _Rows(FakeResult):
    def __iter__(self):  # noqa: ANN204
        return iter(self._rows)

    def scalars(self) -> '_Rows':
        return _Rows(super().scalars()._rows)


class _ConfirmSession(FakeSession):
    """Answers the confirm's reads: one paid relation, one line, and the locked invoice."""

    def __init__(self, invoice: SimpleNamespace) -> None:
        super().__init__(self._answer)
        self.invoice = invoice

    def _answer(self, sql: str) -> list:
        if sql.rstrip().endswith('FOR UPDATE'):
            return [self.invoice]
        if sql.startswith('SELECT fiscal_document_relation.relation'):
            return [(5,)]
        if 'FROM fiscal_document_relation' in sql:
            return [SimpleNamespace(relation=5)]
        if 'FROM fiscal_document_detail' in sql:
            return [SimpleNamespace(product_service='01010101', unit_of_measurement='H87')]
        return []

    async def execute(self, statement):  # noqa: ANN001, ANN201
        return _Rows((await super().execute(statement))._rows)


@pytest.mark.parametrize(
    ('prepare', 'code'),
    [
        (svc._prepare_receipt, ErrorCode.FISCAL_DOCUMENT_INVOICE_NOT_PAYABLE),
        (svc._prepare_credit_note, ErrorCode.FISCAL_DOCUMENT_INVOICE_NOT_CREDITABLE),
    ],
    ids=['receipt', 'credit_note'],
)
async def test_confirm_reads_the_cancellation_from_the_locked_invoice(prepare, code) -> None:  # noqa: ANN001
    """The lock refreshes the invoice the request loaded earlier, so its flag is the row's."""
    session = _ConfirmSession(_stamped_invoice(cancelling_since=_CANCELLING))

    with pytest.raises(DomainError) as caught:
        await prepare(session, _RECEIPT)  # type: ignore[arg-type]

    assert caught.value.code == code
    [lock] = [s for s in session.statements if s._for_update_arg is not None]
    assert lock.get_execution_options().get('populate_existing') is True
