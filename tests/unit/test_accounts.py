"""The shared core of the AR and AP views: aging, buckets and a party's figures (spec 033)."""

from datetime import date, datetime
from decimal import Decimal

from app.enums import CurrencyCode
from app.services.accounts_service import (
    BUCKETS,
    Document,
    bucket,
    days_past_due,
    po_due_date,
    summarize,
)

TODAY = date(2026, 11, 15)


def _doc(balance: str, *, due: datetime | None, rate: str = '1', day: int = 1) -> Document:
    return Document(
        id=day,
        party=1,
        serial=None,
        date=datetime(2026, 8, day),
        due_date=due,
        facility=None,
        facility_code=None,
        salesperson=None,
        linked=(),
        currency=CurrencyCode.MXN,
        exchange_rate=Decimal(rate),
        total=Decimal(balance),
        deductions=Decimal(0),
        applied=Decimal(0),
        balance=Decimal(balance),
    )


def test_no_due_date_or_a_future_one_is_not_past_due() -> None:
    assert days_past_due(None, TODAY) == 0
    assert days_past_due(datetime(2026, 11, 15, 23, 59), TODAY) == 0
    assert days_past_due(datetime(2026, 12, 1), TODAY) == 0


def test_days_past_due_count_local_dates() -> None:
    assert days_past_due(datetime(2026, 11, 14, 23, 59), TODAY) == 1
    assert days_past_due(datetime(2026, 10, 31, 8, 0), TODAY) == 15


def test_bucket_edges() -> None:
    assert [bucket(d) for d in (0, 1, 30, 31, 60, 61, 90, 91)] == [
        'current', '1-30', '1-30', '31-60', '31-60', '61-90', '61-90', '90+',
    ]  # fmt: skip
    assert BUCKETS == ('current', '1-30', '31-60', '61-90', '90+')


def test_the_buckets_add_up_to_owed_in_base_currency() -> None:
    documents = [
        _doc('100.10', due=None),
        _doc('200.20', due=datetime(2026, 11, 1), day=2),
        _doc('300.30', due=datetime(2026, 9, 1), day=3),
        _doc('10.00', due=datetime(2026, 1, 1), rate='17.5', day=4),
    ]

    figures = summarize(
        documents, today=TODAY, credit=Decimal(0), credit_limit=None, owed_company=Decimal(0)
    )

    assert figures['owed'] == Decimal('775.60')
    assert figures['overdue'] == Decimal('675.50')
    assert figures['buckets'] == {
        'current': Decimal('100.10'),
        '1-30': Decimal('200.20'),
        '31-60': Decimal(0),
        '61-90': Decimal('300.30'),
        '90+': Decimal('175.00'),
    }
    assert sum(figures['buckets'].values()) == figures['owed']
    assert figures['oldest'] == datetime(2026, 8, 1)


def test_net_is_owed_less_credit() -> None:
    figures = summarize(
        [_doc('1000', due=None)],
        today=TODAY,
        credit=Decimal('250'),
        credit_limit=None,
        owed_company=Decimal('1000'),
    )

    assert figures['credit'] == Decimal('250')
    assert figures['net'] == Decimal('750.00')


def test_utilization_reads_what_is_owed_company_wide() -> None:
    """US2 scenario 3: a 50,000 limit, 40,000 owed across the company, whatever the scope."""
    figures = summarize(
        [_doc('5000', due=None)],
        today=TODAY,
        credit=Decimal(0),
        credit_limit=Decimal('50000'),
        owed_company=Decimal('40000'),
    )

    assert figures['owed'] == Decimal('5000.00')
    assert figures['utilization'] == Decimal('0.8000')
    assert figures['available_credit'] == Decimal('10000')


def test_over_the_limit_leaves_negative_available_credit() -> None:
    figures = summarize(
        [],
        today=TODAY,
        credit=Decimal(0),
        credit_limit=Decimal('1000'),
        owed_company=Decimal('1500'),
    )

    assert figures['utilization'] == Decimal('1.5000')
    assert figures['available_credit'] == Decimal('-500')


def test_no_limit_leaves_utilization_and_available_credit_blank() -> None:
    for limit in (None, Decimal(0)):
        figures = summarize(
            [_doc('10', due=None)],
            today=TODAY,
            credit=Decimal(0),
            credit_limit=limit,
            owed_company=Decimal('10'),
        )
        assert figures['utilization'] is None
        assert figures['available_credit'] is None


def test_nothing_open_is_a_zero_summary() -> None:
    figures = summarize(
        [], today=TODAY, credit=Decimal(0), credit_limit=None, owed_company=Decimal(0)
    )

    assert figures['owed'] == 0 and figures['overdue'] == 0
    assert all(value == 0 for value in figures['buckets'].values())
    assert figures['oldest'] is None


def test_a_po_is_due_its_first_receipt_plus_the_suppliers_credit_days() -> None:
    """US3 scenario 1: received 1 October, 30 days, due 31 October."""
    received = datetime(2026, 10, 1, 9, 30)

    assert po_due_date(received, 30) == datetime(2026, 10, 31, 9, 30)
    assert days_past_due(po_due_date(received, 30), date(2026, 11, 15)) == 15


def test_a_po_without_a_receipt_is_not_due() -> None:
    assert po_due_date(None, 30) is None


def test_no_credit_days_is_due_on_receipt() -> None:
    received = datetime(2026, 10, 1)

    assert po_due_date(received, None) == received
    assert po_due_date(received, 0) == received
