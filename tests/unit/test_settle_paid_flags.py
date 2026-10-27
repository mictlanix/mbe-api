"""The zero time the stale-flag correction gives an order (spec 033, FR-012).

The zero time dates an order's commission month (spec 025), so only orders from 2026 on get one:
the audit's decision 4 keeps earlier orders out of commissions.
"""

from datetime import datetime

from app.services.customer_payment_service import stale_zero_time


def test_an_order_before_2026_gets_none() -> None:
    assert stale_zero_time(datetime(2025, 12, 31, 23, 59), [datetime(2026, 2, 1)]) is None


def test_an_order_from_2026_gets_its_latest_event() -> None:
    events = [datetime(2026, 3, 1), datetime(2026, 5, 2, 10, 0), datetime(2026, 4, 1)]

    assert stale_zero_time(datetime(2026, 1, 1), events) == datetime(2026, 5, 2, 10, 0)


def test_no_events_gets_none() -> None:
    assert stale_zero_time(datetime(2026, 6, 1), []) is None
