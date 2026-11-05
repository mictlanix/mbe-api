"""Spec 037, T021: the engine's arithmetic, on in-memory fact results (no database)."""

from datetime import date
from decimal import Decimal

import pytest

from app.schemas.analytics import (
    AnalyticsQueryRequest,
    Comparison,
    DimensionKey,
    Grain,
    MeasureKey,
    VatTreatment,
)
from app.services.analytics import engine
from app.services.analytics.catalog import Fact


def _sale(key: object, sub: int, tax: int = 0, *, cost: int = 0, costed: int = 0, orders: int = 1):
    return {
        'k0': key, 'l0': None, 'sub_c': sub, 'tax_c': tax, 'disc_sub_c': 0, 'disc_tax_c': 0,
        'cost_c': cost, 'qty': 1, 'costed_sub_c': costed, 'orders': orders, 'uncosted': 0,
        'below': 0,
    }  # fmt: skip


def _refund(key: object, sub: int, *, cost: int = 0, costed: int = 0):
    return {
        'k0': key, 'l0': None, 'sub_c': sub, 'tax_c': 0, 'cost_c': cost, 'qty': 1,
        'costed_sub_c': costed,
    }  # fmt: skip


def test_facts_with_disjoint_keys_merge_as_a_full_outer_join() -> None:
    merged = engine.merge(
        {Fact.SALE_LINE: [_sale(1, 1000)], Fact.REFUND_LINE: [_refund(2, 300)]}, 1
    )

    assert set(merged.sums) == {(1,), (2,)}
    net = {
        key: engine.derive(s, VatTreatment.EXCLUDED)[MeasureKey.NET_SALES]
        for key, s in merged.sums.items()
    }
    assert net == {(1,): Decimal('10.00'), (2,): Decimal('-3.00')}


def test_ratios_are_recomputed_from_their_parts_never_averaged() -> None:
    """Two rows at 50% and 10% margin on very different sales: the total is not 30%."""
    a = {'sale_line.costed_sub_c': 10000, 'sale_line.cost_c': 5000}
    b = {'sale_line.costed_sub_c': 100, 'sale_line.cost_c': 90}
    total = {k: a[k] + b[k] for k in a}

    margin = [engine.derive(s, VatTreatment.EXCLUDED)[MeasureKey.MARGIN_PCT] for s in (a, b, total)]

    assert margin == [Decimal('0.5000'), Decimal('0.1000'), Decimal('0.4960')]


def test_ratio_against_zero_is_null() -> None:
    values = engine.derive({}, VatTreatment.EXCLUDED)

    assert values[MeasureKey.MARGIN_PCT] is None
    assert values[MeasureKey.COST_COVERAGE] is None
    assert values[MeasureKey.AVERAGE_TICKET] is None


def test_average_ticket_is_with_vat_whatever_the_treatment() -> None:
    sums = {'sale_line.sub_c': 10000, 'sale_line.tax_c': 1600, 'sale_line.orders': 4}

    assert engine.derive(sums, VatTreatment.EXCLUDED)[MeasureKey.AVERAGE_TICKET] == Decimal('29.00')


def test_shares_sum_to_one_per_additive_measure() -> None:
    totals = {'net_sales': Decimal('100.00'), 'margin_pct': Decimal('0.5')}
    rows = [{'net_sales': Decimal('25.00')}, {'net_sales': Decimal('75.00')}]

    shares = [engine.shares(r, totals)['net_sales'] for r in rows]

    assert sum(shares) == 1
    assert 'margin_pct' not in engine.shares(totals, totals)


def test_others_is_the_total_less_the_top_rows() -> None:
    total = {'sale_line.sub_c': 1000, 'sale_line.orders': 5}
    top = [{'sale_line.sub_c': 600, 'sale_line.orders': 2}, {'sale_line.sub_c': 300}]

    assert engine.subtract(total, top) == {'sale_line.sub_c': 100, 'sale_line.orders': 3}


@pytest.mark.parametrize(
    ('value', 'before', 'change', 'change_pct'),
    [
        (Decimal('120'), Decimal('100'), Decimal('20'), Decimal('0.2000')),
        (Decimal('80'), Decimal('100'), Decimal('-20'), Decimal('-0.2000')),
        (Decimal('50'), Decimal('0'), Decimal('50'), None),
        (Decimal('-50'), Decimal('-100'), Decimal('50'), Decimal('0.5000')),
        (None, Decimal('10'), None, None),
    ],
)
def test_change_and_change_pct(
    value: Decimal | None, before: Decimal, change: Decimal | None, change_pct: Decimal | None
) -> None:
    changes, pct = engine.change_figures({'m': value}, {'m': before})

    assert (changes['m'], pct['m']) == (change, change_pct)


def test_previous_period_has_the_same_length_and_ends_the_day_before() -> None:
    assert engine.comparison_period(
        date(2026, 3, 1), date(2026, 3, 31), Comparison.PREVIOUS_PERIOD
    ) == (date(2026, 1, 29), date(2026, 2, 28))


def test_same_period_last_year_maps_feb_29_to_feb_28() -> None:
    assert engine.comparison_period(
        date(2028, 2, 1), date(2028, 2, 29), Comparison.SAME_PERIOD_LAST_YEAR
    ) == (date(2027, 2, 1), date(2027, 2, 28))


def test_date_rows_align_by_position() -> None:
    here = engine.buckets(date(2026, 3, 1), date(2026, 3, 31), Grain.WEEK)
    there = engine.buckets(date(2026, 1, 29), date(2026, 2, 28), Grain.WEEK)

    assert here[0] == date(2026, 2, 23)  # the Monday of the week holding March 1
    assert there[0] == date(2026, 1, 26)
    assert len(here) == 6 and len(there) == 5
    assert engine.buckets(date(2026, 11, 15), date(2027, 2, 2), Grain.QUARTER) == [
        date(2026, 10, 1),
        date(2027, 1, 1),
    ]


def _definition(**kw: object) -> AnalyticsQueryRequest:
    return AnalyticsQueryRequest(
        measures=[MeasureKey.NET_SALES, MeasureKey.ORDERS],
        dimensions=[DimensionKey.PRODUCT],
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        **kw,
    )


ITEMS = [
    ((3,), {'net_sales': Decimal('50'), 'orders': Decimal('1')}),
    ((1,), {'net_sales': Decimal('80'), 'orders': Decimal('3')}),
    ((None,), {'net_sales': Decimal('80'), 'orders': Decimal('2')}),
    ((2,), {'net_sales': Decimal('80'), 'orders': None}),
]


def test_top_sorts_by_the_first_measure_descending_with_ties_by_key() -> None:
    ordered = [key for key, _ in engine.order_rows(list(ITEMS), _definition(top=2))]

    assert ordered == [(1,), (2,), (None,), (3,)]


def test_without_sort_rows_follow_the_first_dimension_none_last() -> None:
    ordered = [key for key, _ in engine.order_rows(list(ITEMS), _definition())]

    assert ordered == [(1,), (2,), (3,), (None,)]


def test_a_null_measure_sorts_last_either_way() -> None:
    ordered = [key for key, _ in engine.order_rows(list(ITEMS), _definition(sort='orders'))]

    assert ordered[-1] == (2,)
    assert ordered[0] == (3,)
