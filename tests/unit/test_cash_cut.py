"""The cash cut's figures (#230, data-model.md › CashCutFigures).

Payments are classified by type *and* sign, because the shared database holds four refund and
tender conventions and legacy's type-only filters handle one. Each convention has a case here.

`db.execute` is a fake that answers by the statement's first selected entity, so the tests do not
depend on query order; the starting cash comes from the existing `opening_amount`, patched.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from app.enums import PaymentMethod, PaymentType
from app.models.core import CashCount
from app.models.purchases import ExpenseVoucher
from app.models.sales import CustomerPayment, SalesOrderPayment
from app.services import cash_session_service
from app.services.cash_session_service import CashCutFigures

CASH, CARD, NA = PaymentMethod.CASH, PaymentMethod.CREDIT_CARD, PaymentMethod.NA


class _Result:
    def __init__(self, rows: list) -> None:
        self._rows = rows

    def scalars(self) -> '_Result':
        return self

    def all(self) -> list:
        return self._rows


class _Db:
    def __init__(self, results: dict) -> None:
        self.results = results

    async def execute(self, statement: object) -> _Result:
        entity = statement.column_descriptions[0]['entity']
        return _Result(list(self.results.get(entity, [])))


_ids = iter(range(1, 10_000))


def _payment(amount: str, method: int = CASH, payment_type: int = PaymentType.IMMEDIATE):
    return SimpleNamespace(
        customer_payment_id=next(_ids),
        amount=Decimal(amount),
        method=method,
        payment_type=payment_type,
    )


def _application(payment: SimpleNamespace, change: str, *, cancelled: bool = False):
    return SimpleNamespace(
        customer_payment=payment.customer_payment_id,
        amount_change=Decimal(change),
        cancelled=cancelled,
    )


def _voucher(*amounts: str, completed: bool | None = True, cancelled: bool | None = False):
    voucher = SimpleNamespace(
        expense_voucher_id=next(_ids), completed=completed, cancelled=cancelled
    )
    return [(voucher, SimpleNamespace(amount=Decimal(a))) for a in amounts]


def _count(denomination: str, quantity: int) -> SimpleNamespace:
    return SimpleNamespace(denomination=Decimal(denomination), quantity=quantity)


async def _figures(
    *,
    payments: tuple = (),
    applications: tuple = (),
    vouchers: tuple = (),
    counted: tuple = (),
    starting: str = '0',
) -> CashCutFigures:
    db = _Db(
        {
            CustomerPayment: payments,
            SalesOrderPayment: applications,
            ExpenseVoucher: [row for voucher in vouchers for row in voucher],
            CashCount: counted,
        }
    )
    with patch.object(
        cash_session_service, 'opening_amount', AsyncMock(return_value=Decimal(starting))
    ):
        return await cash_session_service.cut_figures(db, 5)


class TestSales:
    async def test_sales_by_method_net_of_change(self) -> None:
        cash = _payment('1200')
        card = _payment('300', CARD)

        figures = await _figures(payments=(cash, card), applications=(_application(cash, '40'),))

        assert figures.sales_by_method == [(CASH, Decimal('1160.00')), (CARD, Decimal('300.00'))]
        assert figures.sales_total == Decimal('1460.00')

    async def test_every_positive_non_credit_note_type_is_a_sale(self) -> None:
        payments = (
            _payment('10', payment_type=PaymentType.NA),  # legacy, before 2025-03
            _payment('20', payment_type=PaymentType.CREDIT_PAYMENT),
            _payment('30', payment_type=PaymentType.PAYMENT_IN_ADVANCE),
        )

        figures = await _figures(payments=payments)

        assert figures.sales_by_method == [(CASH, Decimal('60.00'))]
        assert figures.refunds_by_method == []

    async def test_cancelled_application_change_is_not_subtracted(self) -> None:
        cash = _payment('1200')
        applications = (_application(cash, '40'), _application(cash, '25', cancelled=True))

        figures = await _figures(payments=(cash,), applications=applications)

        assert figures.cash_sales == Decimal('1160.00')

    async def test_change_across_several_applications_is_summed(self) -> None:
        cash = _payment('1000')
        applications = (_application(cash, '10'), _application(cash, '5.5'))

        figures = await _figures(payments=(cash,), applications=applications)

        assert figures.cash_sales == Decimal('984.50')


class TestRefundConventions:
    async def test_store_credit_refund_has_no_cash_impact(self) -> None:
        """CREDIT_NOTE, method N/A, positive — current legacy."""
        note = _payment('116', NA, PaymentType.CREDIT_NOTE)

        figures = await _figures(payments=(note,), starting='500')

        assert figures.refunds_by_method == [(NA, Decimal('116.00'))]
        assert figures.sales_by_method == []
        assert figures.cash_refunds == Decimal('0')
        assert figures.cash_in_drawer == Decimal('500.00')

    async def test_legacy_negative_cash_credit_note_is_a_cash_refund(self) -> None:
        """CREDIT_NOTE, cash, negative — legacy 2025-04 → 2025-07."""
        note = _payment('-80', CASH, PaymentType.CREDIT_NOTE)

        figures = await _figures(payments=(note,), starting='500')

        assert figures.refunds_by_method == [(CASH, Decimal('80.00'))]
        assert figures.cash_refunds == Decimal('80.00')
        assert figures.cash_in_drawer == Decimal('420.00')

    async def test_mbe_api_cash_payout_is_a_cash_refund_never_a_sale(self) -> None:
        """IMMEDIATE, cash, negative — `customer_refund_service`'s payout."""
        sale = _payment('1160')
        payout = _payment('-100')

        figures = await _figures(payments=(sale, payout))

        assert figures.sales_by_method == [(CASH, Decimal('1160.00'))]
        assert figures.refunds_by_method == [(CASH, Decimal('100.00'))]
        assert figures.cash_sales == Decimal('1160.00')
        assert figures.cash_refunds == Decimal('100.00')

    async def test_card_refund_is_not_a_cash_refund(self) -> None:
        figures = await _figures(payments=(_payment('-50', CARD),))

        assert figures.refunds_by_method == [(CARD, Decimal('50.00'))]
        assert figures.cash_refunds == Decimal('0')


class TestExpenses:
    async def test_completed_non_cancelled_vouchers_sum_their_details(self) -> None:
        vouchers = (
            _voucher('50', '30'),
            _voucher('999', cancelled=True),
            _voucher('888', completed=False),
            _voucher('777', completed=None),
            _voucher('12.5', cancelled=None),
        )

        figures = await _figures(vouchers=vouchers, starting='500')

        assert figures.expenses_total == Decimal('92.50')
        assert figures.cash_in_drawer == Decimal('407.50')


class TestBalance:
    async def test_cash_in_drawer_and_shortage(self) -> None:
        cash = _payment('1200')
        figures = await _figures(
            payments=(cash, _payment('300', CARD), _payment('-100')),
            applications=(_application(cash, '40'),),
            vouchers=(_voucher('50', '30'),),
            counted=(_count('1000', 1), _count('200', 2), _count('50', 1)),
            starting='500',
        )

        assert figures.starting_cash == Decimal('500.00')
        # 500 + 1160 − 80 − 100
        assert figures.cash_in_drawer == Decimal('1480.00')
        assert figures.counted_cash == Decimal('1450.00')
        assert figures.difference == Decimal('30.00')
        assert figures.is_shortage is True

    async def test_no_counted_rows_is_a_shortage_of_everything(self) -> None:
        figures = await _figures(payments=(_payment('250'),), starting='100')

        assert figures.counted_cash == Decimal('0')
        assert figures.is_shortage is True
        assert figures.difference == figures.cash_in_drawer == Decimal('350.00')

    async def test_counted_above_in_drawer_is_an_overage(self) -> None:
        figures = await _figures(counted=(_count('500', 1), _count('20', 1)), starting='500')

        assert figures.is_shortage is False
        assert figures.difference == Decimal('20.00')

    async def test_exact_count_is_not_a_shortage(self) -> None:
        figures = await _figures(counted=(_count('500', 1),), starting='500')

        assert figures.is_shortage is False
        assert figures.difference == Decimal('0.00')

    async def test_amounts_are_rounded_to_cents(self) -> None:
        cash = _payment('100.005')
        figures = await _figures(
            payments=(cash, _payment('-0.004')),
            applications=(_application(cash, '0.0001'),),
            counted=(_count('0.125', 1),),
        )

        for value in (
            figures.sales_total,
            figures.cash_sales,
            figures.cash_refunds,
            figures.counted_cash,
            figures.cash_in_drawer,
            figures.difference,
            figures.sales_by_method[0][1],
        ):
            assert value == value.quantize(Decimal('0.01')), value
        assert figures.cash_sales == Decimal('100.00')  # 100.0049 → 100.00
