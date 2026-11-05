"""Spec 037, T030: the dashboard's own rules, on in-memory figures (no database)."""

from datetime import date, timedelta
from decimal import Decimal

import pytest

from app.schemas.analytics import Grain
from app.services.analytics import engine
from app.services.analytics.dashboard import baseline_months, refund_rate_alert, sparkline_grain

START = date(2026, 1, 1)


@pytest.mark.parametrize(
    ('days', 'grain'),
    [(1, Grain.DAY), (62, Grain.DAY), (63, Grain.WEEK), (366, Grain.WEEK), (367, Grain.MONTH)],
)
def test_sparkline_grain(days: int, grain: Grain) -> None:
    assert sparkline_grain(START, START + timedelta(days=days - 1)) is grain


NORMAL = [Decimal('0.04')] * 6 + [Decimal('0.05')] * 6  # median 0.045


@pytest.mark.parametrize(
    ('rate', 'refunds', 'fires'),
    [
        (Decimal('0.25'), Decimal('25000'), True),
        (Decimal('0.25'), Decimal('19999.99'), False),  # under the $20,000 floor
        (Decimal('0.20'), Decimal('25000'), False),  # not above 20%
        (Decimal('0.2001'), Decimal('25000'), True),
    ],
)
def test_the_refund_rate_needs_every_threshold(
    rate: Decimal, refunds: Decimal, fires: bool
) -> None:
    assert refund_rate_alert(rate, refunds, NORMAL) is fires


def test_twice_the_median_is_a_threshold_of_its_own() -> None:
    high = [Decimal('0.12')] * 12  # median 0.12, so 2× is 0.24

    assert refund_rate_alert(Decimal('0.24'), Decimal('30000'), high) is False
    assert refund_rate_alert(Decimal('0.2401'), Decimal('30000'), high) is True


def test_the_median_not_the_mean() -> None:
    """One bad month lifts a mean; the median ignores it."""
    months = [Decimal('0.05')] * 11 + [Decimal('0.90')]
    # Mean 0.1208: twice it (0.2417) would hold 0.21 back; twice the median (0.10) doesn't.

    assert refund_rate_alert(Decimal('0.21'), Decimal('30000'), months) is True


def test_fewer_months_use_what_there_is_and_none_means_no_alert() -> None:
    assert refund_rate_alert(Decimal('0.30'), Decimal('30000'), [Decimal('0.05')]) is True
    assert refund_rate_alert(Decimal('0.90'), Decimal('99999'), []) is False
    assert refund_rate_alert(None, Decimal('30000'), NORMAL) is False


def test_the_baseline_is_the_twelve_months_before_the_period_starts() -> None:
    assert baseline_months(date(2026, 3, 15)) == (date(2025, 3, 1), date(2026, 2, 28))
    assert baseline_months(date(2026, 1, 1)) == (date(2025, 1, 1), date(2025, 12, 31))


def test_a_kpi_against_a_zero_comparison_has_no_percentage() -> None:
    change, change_pct = engine.change_figures(
        {'net_sales': Decimal('500.00')}, {'net_sales': Decimal('0.00')}
    )

    assert change == {'net_sales': Decimal('500.00')}
    assert change_pct == {'net_sales': None}
