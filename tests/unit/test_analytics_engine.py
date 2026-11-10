"""Spec 037, T021: the engine's arithmetic, on in-memory fact results (no database).

Spec 038, T003: the balance kind, over a fake balance fact.
"""

from datetime import date
from decimal import Decimal

import pytest

from app.core.clock import local_now
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.schemas.analytics import (
    AnalyticsQueryRequest,
    Comparison,
    DimensionKey,
    Grain,
    MeasureKey,
    StockOptions,
    VatTreatment,
)
from app.services.analytics import engine, facts
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


# ── Spec 038, T003: the balance kind (research R1) ────────────────────────────

TODAY = date(2026, 11, 5)

#: A fake stock fact: on hand per warehouse at the end of each day it is asked for.
ON_HAND = {
    date(2025, 12, 31): {1: 4, 2: 4},
    date(2026, 1, 31): {1: 5, 2: 5},
    date(2026, 2, 28): {1: 12, 2: 8},
    date(2026, 3, 31): {1: 30, 2: 10},
}


def _stock_rows(slice_: facts.Slice) -> list[dict]:
    """What a balance fact returns: one row per moment and group, with `moment`, the keys of the
    slice's dimensions (never date), and its columns."""
    assert DimensionKey.DATE not in slice_.dimensions
    assert DimensionKey.DATE not in slice_.filters
    rows = []
    for moment in slice_.moments:
        by_warehouse = ON_HAND.get(moment, {})
        if slice_.dimensions == (DimensionKey.WAREHOUSE,):
            rows += [
                {'moment': moment, 'k0': w, 'l0': None, 'on_hand': q}
                for w, q in by_warehouse.items()
            ]
        elif by_warehouse:
            rows.append({'moment': moment, 'on_hand': sum(by_warehouse.values())})
    return rows


def _sale_rows(slice_: facts.Slice) -> list[dict]:
    """A flow: 100.00 of sales per month, in month buckets when grouped by date."""
    months = engine.buckets(slice_.start, slice_.end, Grain.MONTH)
    if DimensionKey.DATE in slice_.dimensions:
        return [_sale(month, 10000) for month in months]
    row = _sale(None, 10000 * len(months))
    return [{k: v for k, v in row.items() if k not in ('k0', 'l0')}]


def _prepared(**kw: object) -> engine.Prepared:
    definition = AnalyticsQueryRequest(
        **{'measures': [MeasureKey.ON_HAND], 'start': date(2026, 1, 1), 'end': date(2026, 3, 31)}
        | kw
    )
    return engine.Prepared(definition, engine.parse_filters(definition.filters), None, set(), TODAY)


def _found(prepared: engine.Prepared) -> dict:
    found = {}
    for fact, slice_ in engine.wanted(prepared):
        rows = {Fact.STOCK: _stock_rows, Fact.SALE_LINE: _sale_rows}.get(fact, lambda _: [])
        found[engine.fact_key(fact, slice_)] = rows(slice_)
    return found


async def _answer(**kw: object):
    prepared = _prepared(**kw)
    return await engine.assemble(None, prepared, _found(prepared))  # type: ignore[arg-type]


def _stock_slices(prepared: engine.Prepared) -> list[facts.Slice]:
    return [slice_ for fact, slice_ in engine.wanted(prepared) if fact is Fact.STOCK]


def test_a_balance_fact_is_asked_for_moments_not_a_period() -> None:
    by_month = _stock_slices(_prepared(dimensions=[DimensionKey.DATE], grain=Grain.MONTH))
    undated = _stock_slices(_prepared())

    month_ends = (date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31))
    assert [s.moments for s in by_month] == [month_ends, month_ends]  # rows and totals
    assert all(s.dimensions == () for s in by_month)
    assert [s.moments for s in undated] == [(date(2026, 3, 31),)] * 2


async def test_with_a_month_dimension_each_bucket_holds_its_end_value() -> None:
    answer = await _answer(dimensions=[DimensionKey.DATE], grain=Grain.MONTH)

    assert [(r.keys[0].value, r.values['on_hand']) for r in answer.rows] == [
        ('2026-01-01', Decimal('10.0000')),
        ('2026-02-01', Decimal('20.0000')),
        ('2026-03-01', Decimal('40.0000')),
    ]


async def test_the_totals_row_is_the_value_at_the_periods_end_not_a_sum() -> None:
    by_month = await _answer(dimensions=[DimensionKey.DATE], grain=Grain.MONTH)
    undated = await _answer()

    assert by_month.totals.values['on_hand'] == Decimal('40.0000')
    assert undated.totals.values['on_hand'] == Decimal('40.0000')


async def test_shares_are_taken_within_the_end_moment() -> None:
    answer = await _answer(dimensions=[DimensionKey.WAREHOUSE])

    assert {r.keys[0].value: r.share['on_hand'] for r in answer.rows} == {
        '1': Decimal('0.7500'),
        '2': Decimal('0.2500'),
    }


async def test_by_date_each_bucket_shares_within_its_own_moment() -> None:
    answer = await _answer(
        dimensions=[DimensionKey.DATE, DimensionKey.WAREHOUSE], grain=Grain.MONTH
    )

    shares = {(r.keys[0].value, r.keys[1].value): r.share['on_hand'] for r in answer.rows}
    assert shares[('2026-01-01', '1')] == Decimal('0.5000')
    assert shares[('2026-02-01', '1')] == Decimal('0.6000')
    assert shares[('2026-03-01', '2')] == Decimal('0.2500')


async def test_a_comparison_reads_the_value_at_the_comparison_periods_end() -> None:
    answer = await _answer(
        start=date(2026, 3, 1), end=date(2026, 3, 31), comparison=Comparison.PREVIOUS_PERIOD
    )

    # The previous period is 2026-01-29 … 2026-02-28.
    assert answer.totals.comparison == {'on_hand': Decimal('20.0000')}
    assert answer.totals.change == {'on_hand': Decimal('20.0000')}


async def test_a_comparison_by_month_aligns_bucket_ends() -> None:
    answer = await _answer(
        start=date(2026, 2, 1),
        end=date(2026, 3, 31),
        dimensions=[DimensionKey.DATE],
        grain=Grain.MONTH,
        comparison=Comparison.PREVIOUS_PERIOD,
    )

    # 2026-02-01 … 03-31 against 2025-12-04 … 2026-01-31: December's end, then January's.
    assert [r.comparison for r in answer.rows] == [
        {'on_hand': Decimal('8.0000')},
        {'on_hand': Decimal('10.0000')},
    ]


async def test_a_flow_and_a_balance_merge_on_the_dimension_tuple() -> None:
    answer = await _answer(
        measures=[MeasureKey.NET_SALES, MeasureKey.ON_HAND],
        dimensions=[DimensionKey.DATE],
        grain=Grain.MONTH,
    )

    assert [(r.keys[0].value, r.values) for r in answer.rows] == [
        ('2026-01-01', {'net_sales': Decimal('100.00'), 'on_hand': Decimal('10.0000')}),
        ('2026-02-01', {'net_sales': Decimal('100.00'), 'on_hand': Decimal('20.0000')}),
        ('2026-03-01', {'net_sales': Decimal('100.00'), 'on_hand': Decimal('40.0000')}),
    ]
    # The flow sums the period; the balance is its end.
    assert answer.totals.values == {'net_sales': Decimal('300.00'), 'on_hand': Decimal('40.0000')}


async def test_buckets_after_today_have_no_moment() -> None:
    prepared = _prepared(dimensions=[DimensionKey.DATE], grain=Grain.MONTH)
    prepared.today = date(2026, 2, 10)

    assert _stock_slices(prepared)[0].moments == (date(2026, 1, 31), date(2026, 2, 10))


async def test_top_folds_the_rest_of_the_end_moment_into_others() -> None:
    answer = await _answer(dimensions=[DimensionKey.WAREHOUSE], top=1)

    assert [r.keys[0].value for r in answer.rows] == ['1']
    assert answer.others is not None
    assert answer.others.values == {'on_hand': Decimal('10.0000')}


def test_an_opening_measure_also_asks_for_the_day_before_each_bucket() -> None:
    prepared = _prepared(
        measures=[MeasureKey.TURNOVER], dimensions=[DimensionKey.DATE], grain=Grain.MONTH
    )

    assert _stock_slices(prepared)[0].moments == (
        date(2025, 12, 31), date(2026, 1, 31), date(2026, 2, 28), date(2026, 3, 31),
    )  # fmt: skip


def test_opening_values_are_placed_as_start_columns() -> None:
    rows = [
        {'moment': date(2025, 12, 31), 'on_hand': 8},
        {'moment': date(2026, 1, 31), 'on_hand': 10},
    ]
    plan = [engine.Placement(None, date(2026, 1, 31), date(2025, 12, 31))]

    placed = engine.place(rows, plan, None)

    assert engine.merge({Fact.STOCK: placed}, 0).sums == {
        (): {'stock.on_hand': 10, 'stock.start_on_hand': 8}
    }


def test_flow_facts_are_asked_exactly_as_before() -> None:
    prepared = _prepared(measures=[MeasureKey.NET_SALES], dimensions=[DimensionKey.DATE])

    assert all(s.moments == () for _, s in engine.wanted(prepared))
    assert {s.dimensions for _, s in engine.wanted(prepared)} == {(DimensionKey.DATE,), ()}


def test_the_fact_key_tells_moments_apart() -> None:
    by_month = _stock_slices(_prepared(dimensions=[DimensionKey.DATE], grain=Grain.MONTH))[1]
    undated = _stock_slices(_prepared())[1]

    assert by_month.dimensions == undated.dimensions == ()
    assert engine.fact_key(Fact.STOCK, by_month) != engine.fact_key(Fact.STOCK, undated)


# ── Spec 038, T011: the history and month-end guards ──────────────────────────


def test_stock_before_its_history_is_refused() -> None:
    with pytest.raises(DomainError) as refused:
        engine.history_guard([MeasureKey.ON_HAND], date(2025, 2, 6), date(2025, 3, 31))

    assert refused.value.code is ErrorCode.ANALYTICS_BEFORE_HISTORY
    assert refused.value.params == {
        'history': 'stock',
        'requested': '2025-02-06',
        'earliest': '2025-02-07',
    }
    engine.history_guard([MeasureKey.UNITS_IN], date(2025, 2, 7), date(2025, 3, 31))
    engine.history_guard([MeasureKey.NET_SALES], date(2020, 1, 1), date(2020, 1, 31))


def test_a_receivable_month_end_before_2025_01_31_is_refused() -> None:
    with pytest.raises(DomainError) as refused:
        engine.history_guard([MeasureKey.RECEIVABLE], date(2024, 12, 31), date(2025, 6, 30))

    assert refused.value.code is ErrorCode.ANALYTICS_BEFORE_HISTORY
    assert refused.value.params['history'] == 'receivables'
    assert refused.value.params['earliest'] == '2025-01-31'
    engine.history_guard([MeasureKey.RECEIVABLE], date(2025, 1, 31), date(2025, 6, 30))


@pytest.mark.parametrize(
    ('start', 'end'),
    [(date(2026, 1, 2), date(2026, 1, 31)), (date(2026, 1, 1), date(2026, 1, 30))],
)
def test_dso_needs_a_period_from_a_first_to_a_month_end(start: date, end: date) -> None:
    with pytest.raises(DomainError) as refused:
        engine.month_end_guard([MeasureKey.NET_SALES, MeasureKey.DSO], start, end)

    assert refused.value.code is ErrorCode.ANALYTICS_PERIOD_NOT_MONTH_END
    assert refused.value.params == {
        'measure': 'dso',
        'start': start.isoformat(),
        'end': end.isoformat(),
    }


def test_cei_may_end_today_and_other_measures_are_not_checked() -> None:
    today = local_now().date()

    engine.month_end_guard([MeasureKey.CEI], today.replace(day=1), today)
    engine.month_end_guard([MeasureKey.CEI], date(2026, 1, 1), date(2026, 2, 28))
    engine.month_end_guard([MeasureKey.NET_SALES], date(2026, 1, 2), date(2026, 1, 30))


# ── Spec 038, T008: stock options (research R5) ───────────────────────────────


@pytest.mark.parametrize(
    ('option', 'value', 'low', 'high'),
    [
        ('velocity_days', 20, 30, 365),
        ('dead_days', 120, 90, 365),
        ('slow_cover_days', 731, 30, 730),
        ('lead_days', -1, 0, 120),
        ('cover_days', 0, 1, 365),
    ],
)
def test_a_stock_option_out_of_range_is_refused(option: str, value: int, low: int, high: int):
    with pytest.raises(DomainError) as refused:
        StockOptions(**{option: value})

    assert refused.value.code is ErrorCode.ANALYTICS_STOCK_OPTIONS_INVALID
    assert refused.value.params == {'option': option, 'value': value, 'min': low, 'max': high}


def test_stock_options_default_to_r5_and_the_settings() -> None:
    assert StockOptions().model_dump() == {
        'velocity_days': 90,
        'dead_days': 180,
        'slow_cover_days': 180,
        'lead_days': 7,
        'cover_days': 30,
    }
