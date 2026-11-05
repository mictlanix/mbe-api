"""The dashboard (spec 037, FR-031–FR-033, research R9, R14): every tile for a period in one call.

Every KPI and series is a query definition assembled by the engine, so each number equals what
`POST /analytics/query` answers for the same measure, period and scope (FR-031). The tiles and
alerts first list the fact queries they read; each distinct one runs once (tiles share totals,
comparisons and the alerts' facts), concurrently, each on its own session from the request's
engine; on SQLite, where one connection can't serve concurrent queries, they run in sequence.

Every tile is without VAT except average ticket, which is with VAT (FR-004); each drill and each
alert's detail states its VAT treatment explicitly, so following it gives the same number.
"""

import asyncio
import statistics
from collections.abc import Awaitable, Callable, Sequence
from datetime import date, timedelta
from decimal import Decimal
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import local_now
from app.core.config import settings
from app.core.deps import CurrentUser
from app.schemas.analytics import (
    AnalyticsAlert,
    AnalyticsDashboardRequest,
    AnalyticsDashboardResponse,
    AnalyticsDetailRequest,
    AnalyticsFilter,
    AnalyticsKpi,
    AnalyticsPoint,
    AnalyticsQueryRequest,
    AnalyticsQueryResponse,
    AnalyticsSeries,
    Comparison,
    DetailFlag,
    DetailKind,
    DimensionKey,
    Grain,
    MeasureKey,
    StockOptions,
    VatTreatment,
)
from app.services import sales_order_service, supplier_payment_service
from app.services.analytics import balances, engine, facts
from app.services.analytics.catalog import MEASURE_FAMILIES, Fact, Family

#: Sparklines: day grain up to 62 days, week up to a year, then month (contracts/api.md "Tiles").
DAY_LIMIT = 62
WEEK_LIMIT = 366
TOP = 10
#: The refund-rate alert's thresholds (FR-032, research R14).
RATE_FLOOR = Decimal('0.20')
RATE_MULTIPLE = 2
REFUND_FLOOR = Decimal('20000')
BASELINE_MONTHS = 12


def sparkline_grain(start: date, end: date) -> Grain:
    days = (end - start).days + 1
    if days <= DAY_LIMIT:
        return Grain.DAY
    if days <= WEEK_LIMIT:
        return Grain.WEEK
    return Grain.MONTH


def refund_rate_alert(
    period_rate: Decimal | None, refunds: Decimal, monthly: Sequence[Decimal]
) -> bool:
    """Whether a facility's refund rate is above normal (FR-032).

    `monthly` are the facility's refund rates of the calendar months before the period starts, at
    most 12. It fires only when the period's rate exceeds both twice their median and 20%, with at
    least $20,000 refunded in the period. Without a prior month there is no normal to exceed.
    """
    if period_rate is None or not monthly:
        return False
    median = Decimal(statistics.median(monthly))
    return (
        period_rate > RATE_MULTIPLE * median
        and period_rate > RATE_FLOOR
        and refunds >= REFUND_FLOOR
    )


def trend_start(today: date) -> date:
    """The receivable trend's first month: 12 month-ends before today's month, and today, from the
    first stored month-end on (spec 038, R8)."""
    total = today.year * 12 + today.month - 1 - BASELINE_MONTHS
    return max(date(total // 12, total % 12 + 1, 1), engine.RECEIVABLE_HISTORY_START.replace(day=1))


def baseline_months(start: date) -> tuple[date, date]:
    """The 12 calendar months before the month the period starts in."""
    first = start.replace(day=1)
    total = first.year * 12 + first.month - 1 - BASELINE_MONTHS
    return date(total // 12, total % 12 + 1, 1), first - timedelta(days=1)


# ── Running the tiles ─────────────────────────────────────────────────────────


async def concurrently(db: AsyncSession, jobs: Sequence[Callable[[AsyncSession], Awaitable[Any]]]):
    """Run independent jobs at once, each on its own session; one after another on SQLite."""
    if db.bind.dialect.name == 'sqlite':
        return [await job(db) for job in jobs]
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)

    async def run(job: Callable[[AsyncSession], Awaitable[Any]]) -> Any:
        async with sessions() as session:
            return await job(session)

    return await asyncio.gather(*(run(job) for job in jobs))


def _fact_job(fact: Fact, slice_: facts.Slice) -> Callable[[AsyncSession], Awaitable[list[Any]]]:
    async def job(session: AsyncSession) -> list[Any]:
        return await engine.run_fact(session, fact, slice_)

    return job


async def _overdue_job(session: AsyncSession, facility: int | None) -> list[Any]:
    return await sales_order_service.open_credit_orders(
        session, facility=facility, due_before=local_now()
    )


def _definition(
    request: AnalyticsDashboardRequest, facility: int | None, **kw: Any
) -> AnalyticsQueryRequest:
    values: dict[str, Any] = {
        'start': request.start,
        'end': request.end,
        'facility': facility,
        'comparison': request.comparison,
        'vat': VatTreatment.EXCLUDED,
    }
    return AnalyticsQueryRequest(**{**values, **kw})


def _prepared(
    definition: AnalyticsQueryRequest, facility: int | None, held: set[Family]
) -> engine.Prepared:
    return engine.Prepared(definition, engine.parse_filters(definition.filters), facility, held)


def _kpi(
    key: str,
    measure: MeasureKey,
    result: AnalyticsQueryResponse,
    request: AnalyticsDashboardRequest,
    facility: int | None,
    grain: Grain,
    start: date | None = None,
    end: date | None = None,
) -> AnalyticsKpi:
    totals = result.totals
    name = measure.value
    points = {row.keys[0].value: row.values.get(name) for row in result.rows}
    empty = engine.select_measures(engine.derive({}, VatTreatment.EXCLUDED), [measure])[name]
    start, end = start or request.start, end or request.end
    vat = VatTreatment.INCLUDED if measure is MeasureKey.AVERAGE_TICKET else VatTreatment.EXCLUDED
    return AnalyticsKpi(
        key=key,
        value=totals.values.get(name),
        comparison=(totals.comparison or {}).get(name),
        change=(totals.change or {}).get(name),
        change_pct=(totals.change_pct or {}).get(name),
        sparkline=[
            AnalyticsPoint(date=bucket, value=points.get(bucket.isoformat(), empty))
            for bucket in engine.buckets(start, end, grain)
        ],
        drill=AnalyticsQueryRequest(
            measures=[measure],
            start=start,
            end=end,
            facility=facility,
            comparison=request.comparison,
            vat=vat,
        ),
    )


async def build(
    db: AsyncSession,
    request: AnalyticsDashboardRequest,
    current: CurrentUser,
    held: set[Family] | None = None,
) -> AnalyticsDashboardResponse:
    """Every tile and alert the caller may see for the period and scope (FR-031–FR-033).

    `held` replaces the caller's families when they were checked earlier (a background export).
    """
    engine.validate_period(request.start, request.end)
    if held is None:
        held = await engine.families(db, current)
    if not held & set(MEASURE_FAMILIES):
        raise engine.family_forbidden(Family.SALES)
    facility = await engine.resolve_scope(db, current, request.facility)
    comparison = request.comparison or Comparison.PREVIOUS_PERIOD
    request = request.model_copy(update={'facility': facility, 'comparison': comparison})
    before_start, before_end = engine.comparison_period(request.start, request.end, comparison)
    grain = sparkline_grain(request.start, request.end)
    sales, cost, payments = (Family.SALES in held, Family.COST in held, Family.PAYMENTS in held)
    # Stock is read only within its history (spec 038, R3), as the query refuses before it.
    inventory = Family.INVENTORY in held and request.start >= settings.stock_history_start
    today = local_now().date()
    # Receivables from the first stored month-end, the comparison's included (R8); payables once
    # go-live sets the balance cutoff (R10).
    receivables = Family.RECEIVABLES in held and (
        balances.read_day(min(before_end, today), today) >= engine.RECEIVABLE_HISTORY_START
    )
    payables = Family.PAYABLES in held and await supplier_payment_service.get_cutoff(db) is not None
    fiscal = Family.FISCAL in held

    planned: list[tuple[str, str, AnalyticsQueryRequest]] = []

    def plan(name: str, chart: str, measures: list[MeasureKey], *dims: DimensionKey, **kw: Any):
        definition = _definition(request, facility, measures=measures, dimensions=list(dims), **kw)
        planned.append((name, chart, definition))

    if sales:
        over_time = [
            MeasureKey.NET_SALES,
            MeasureKey.ORDERS,
            MeasureKey.AVERAGE_TICKET,
            MeasureKey.REFUNDS,
        ]
        if cost:
            over_time += [MeasureKey.GROSS_MARGIN, MeasureKey.MARGIN_PCT, MeasureKey.COST_COVERAGE]
        net = [MeasureKey.NET_SALES]
        plan('sales_over_time', 'line', over_time, DimensionKey.DATE, grain=grain)
        plan('sales_by_facility', 'bar', net, DimensionKey.FACILITY)
        plan('sales_by_label', 'bar', net, DimensionKey.LABEL, top=TOP)
        plan('sales_by_brand', 'bar', net, DimensionKey.BRAND, top=TOP)
        plan('top_products', 'bar', net, DimensionKey.PRODUCT, top=TOP)
        plan('top_customers', 'bar', net, DimensionKey.CUSTOMER, top=TOP)
        plan(
            'heatmap', 'heatmap', [MeasureKey.ORDERS], DimensionKey.WEEKDAY, DimensionKey.HOUR,
            comparison=None,
        )  # fmt: skip
    if payments:
        collected = [MeasureKey.COLLECTED]
        plan('collected', '', collected, DimensionKey.DATE, grain=grain)
        plan(
            'collected_today', '', collected, DimensionKey.DATE, start=today, end=today,
            comparison=Comparison.PREVIOUS_PERIOD,
        )  # fmt: skip
        plan('payment_mix', 'donut', collected, DimensionKey.PAYMENT_METHOD)
    if inventory and cost:
        value = [MeasureKey.STOCK_VALUE]
        plan('stock_value', '', value, DimensionKey.DATE, grain=grain)
        # With the alerts' options, so its totals are the alerts' stock query (R18).
        plan(
            'stock_value_by_facility', 'bar', value, DimensionKey.FACILITY, comparison=None,
            stock_options=StockOptions(),
        )  # fmt: skip
    if receivables:
        overdue = [MeasureKey.RECEIVABLE_OVERDUE]
        plan('receivable_overdue', '', overdue, DimensionKey.DATE, grain=grain)
        if engine.whole_months(request.start, request.end):
            plan('dso', '', [MeasureKey.DSO])
        plan(
            'receivable_overdue_trend', 'line', overdue, DimensionKey.DATE, grain=Grain.MONTH,
            start=trend_start(today), end=today, comparison=None,
        )  # fmt: skip
        plan(
            'top_debtors', 'bar', [MeasureKey.RECEIVABLE], DimensionKey.CUSTOMER, top=TOP,
            comparison=None,
        )  # fmt: skip
        plan(
            'collected_vs_due', 'bar',
            [MeasureKey.DUE_IN_PERIOD, MeasureKey.DUE_COLLECTED, MeasureKey.COLLECTED_ON_CREDIT],
            comparison=None,
        )  # fmt: skip
    if fiscal:
        uninvoiced = [MeasureKey.UNINVOICED_SALES]
        plan('uninvoiced_sales', '', uninvoiced, DimensionKey.DATE, grain=grain)
        plan('invoice_coverage', 'bar', [MeasureKey.INVOICE_COVERAGE], DimensionKey.FACILITY)

    prepared = [_prepared(definition, facility, held) for _, _, definition in planned]
    wanted = [pair for one in prepared for pair in engine.wanted(one)]
    if sales:
        wanted += _alert_facts(request, facility)
    stock_alerts = _prepared(_stock_alert_definition(request, facility), facility, held)
    if inventory:
        wanted += engine.wanted(stock_alerts)
    payable_alert = _prepared(_payable_alert_definition(request, facility, today), facility, held)
    if payables:
        wanted += engine.wanted(payable_alert)
    distinct = {engine.fact_key(fact, slice_): (fact, slice_) for fact, slice_ in wanted}
    jobs = [_fact_job(fact, slice_) for fact, slice_ in distinct.values()]
    if sales:
        jobs.append(lambda session: _overdue_job(session, facility))
    results = await concurrently(db, jobs)
    found = dict(zip(distinct, results[: len(distinct)], strict=True))
    overdue = results[-1] if sales else []
    answered = {
        name: await engine.assemble(db, one, found)
        for (name, _, _), one in zip(planned, prepared, strict=True)
    }

    kpis: list[AnalyticsKpi] = []
    if sales:
        over_time = answered['sales_over_time']
        kpis.append(_kpi('net_sales', MeasureKey.NET_SALES, over_time, request, facility, grain))
        if cost:
            kpis.append(
                _kpi('margin_pct', MeasureKey.MARGIN_PCT, over_time, request, facility, grain)
            )
            kpis.append(
                _kpi('cost_coverage', MeasureKey.COST_COVERAGE, over_time, request, facility, grain)
            )
        kpis.append(_kpi('orders', MeasureKey.ORDERS, over_time, request, facility, grain))
        kpis.append(
            _kpi('average_ticket', MeasureKey.AVERAGE_TICKET, over_time, request, facility, grain)
        )
    if payments:
        kpis.append(
            _kpi('collected', MeasureKey.COLLECTED, answered['collected'], request, facility, grain)
        )
        kpis.append(
            _kpi(
                'collected_today', MeasureKey.COLLECTED, answered['collected_today'], request,
                facility, Grain.DAY, today, today,
            )
        )  # fmt: skip
    if sales:
        kpis.append(
            _kpi(
                'refunds', MeasureKey.REFUNDS, answered['sales_over_time'], request, facility, grain
            )
        )
    if inventory and cost:
        kpis.append(
            _kpi(
                'stock_value', MeasureKey.STOCK_VALUE, answered['stock_value'], request, facility,
                grain,
            )
        )  # fmt: skip
    if receivables:
        kpis.append(
            _kpi(
                'receivable_overdue', MeasureKey.RECEIVABLE_OVERDUE,
                answered['receivable_overdue'], request, facility, grain,
            )
        )  # fmt: skip
        if 'dso' in answered:
            kpis.append(_kpi('dso', MeasureKey.DSO, answered['dso'], request, facility, grain))
    if fiscal:
        kpis.append(
            _kpi(
                'uninvoiced_sales', MeasureKey.UNINVOICED_SALES, answered['uninvoiced_sales'],
                request, facility, grain,
            )
        )  # fmt: skip

    series = [
        AnalyticsSeries(key=name, chart=chart, query=answered[name])
        for name, chart, _ in planned
        if chart
    ]
    alerts = _alerts(request, facility, found, overdue, sales=sales, cost=cost)
    if inventory:
        alerts += _stock_alerts(request, facility, found, stock_alerts, cost=cost)
    if payables:
        alerts += _payable_alerts(found, payable_alert)
    return AnalyticsDashboardResponse(
        as_of=local_now(),
        definition=request,
        comparison_start=before_start,
        comparison_end=before_end,
        kpis=kpis,
        series=series,
        alerts=alerts,
    )


# ── Alerts (R14) ──────────────────────────────────────────────────────────────


def _detail(
    request: AnalyticsDashboardRequest, facility: int | None, **kw: Any
) -> AnalyticsDetailRequest:
    return AnalyticsDetailRequest(
        start=request.start, end=request.end, facility=facility, vat=VatTreatment.EXCLUDED, **kw
    )


def _alert_slices(
    request: AnalyticsDashboardRequest, facility: int | None
) -> tuple[facts.Slice, facts.Slice, facts.Slice]:
    """The period's totals, the period by facility, and the 12 months before by month × facility."""
    first, last = baseline_months(request.start)
    return (
        facts.Slice(start=request.start, end=request.end, facility=facility),
        facts.Slice(
            (DimensionKey.FACILITY,), start=request.start, end=request.end, facility=facility
        ),
        facts.Slice(
            (DimensionKey.DATE, DimensionKey.FACILITY), Grain.MONTH, start=first, end=last,
            facility=facility,
        ),
    )  # fmt: skip


def _alert_facts(
    request: AnalyticsDashboardRequest, facility: int | None
) -> list[tuple[Fact, facts.Slice]]:
    period, by_facility, history = _alert_slices(request, facility)
    return [
        (Fact.SALE_LINE, period),
        *((fact, s) for s in (by_facility, history) for fact in (Fact.SALE_LINE, Fact.REFUND_LINE)),
    ]


def _alerts(
    request: AnalyticsDashboardRequest,
    facility: int | None,
    found: engine.Found,
    overdue: Sequence[Any],
    *,
    sales: bool,
    cost: bool,
) -> list[AnalyticsAlert]:
    if not sales:
        return []
    alerts: list[AnalyticsAlert] = []
    period, by_facility, history = _alert_slices(request, facility)

    if overdue:
        alerts.append(
            AnalyticsAlert(
                key='overdue_credit_orders',
                count=len(overdue),
                amount=sum((balance.balance for _, balance in overdue), Decimal(0)),
                detail=_detail(request, facility, kind=DetailKind.ORDERS, flag=DetailFlag.OVERDUE),
            )
        )

    if cost:
        lines = found[engine.fact_key(Fact.SALE_LINE, period)]
        sums = lines[0] if lines else {}
        for key, count, amount, flag in (
            ('lines_below_cost', 'below', 'below_sub_c', DetailFlag.BELOW_COST),
            ('lines_without_cost', 'uncosted', 'uncosted_sub_c', DetailFlag.WITHOUT_COST),
        ):
            if sums.get(count):
                alerts.append(
                    AnalyticsAlert(
                        key=key,
                        count=int(sums[count]),
                        amount=engine.money(sums[amount]),
                        detail=_detail(request, facility, kind=DetailKind.LINES, flag=flag),
                    )
                )

    alerts += _refund_rate_alerts(request, facility, found, by_facility, history)
    return alerts


def _stock_alert_definition(
    request: AnalyticsDashboardRequest, facility: int | None
) -> AnalyticsQueryRequest:
    """The stock as of the period's end with the default options: one stock query counts every
    inventory alert, the same pairs the `stock` detail lists with each flag (spec 038)."""
    return _definition(
        request, facility, measures=[MeasureKey.STOCK_OUT_PAIRS], comparison=None,
        stock_options=StockOptions(),
    )  # fmt: skip


#: Each inventory alert: its key, the stock fact's count and amount columns, its flag, and whether
#: it needs the cost privilege. Dead stock means no movement in 180 days, the default.
STOCK_ALERTS = (
    ('stock_outs', 'stock_outs', None, DetailFlag.STOCK_OUT, False),
    ('below_reorder', 'below', None, DetailFlag.BELOW_REORDER, False),
    ('dead_stock', 'dead_pairs', 'dead_value_c', DetailFlag.DEAD, True),
    ('uncosted_stock', 'uncosted_pairs', None, DetailFlag.UNCOSTED, False),
)


def _stock_alerts(
    request: AnalyticsDashboardRequest,
    facility: int | None,
    found: engine.Found,
    prepared: engine.Prepared,
    *,
    cost: bool,
) -> list[AnalyticsAlert]:
    rows = [
        row
        for fact, slice_ in engine.wanted(prepared)[:1]
        for row in found[engine.fact_key(fact, slice_)]
    ]
    alerts: list[AnalyticsAlert] = []
    for key, count, amount, flag, sensitive in STOCK_ALERTS:
        total = sum(int(row.get(count, 0) or 0) for row in rows)
        if not total or (sensitive and not cost):
            continue
        alerts.append(
            AnalyticsAlert(
                key=key,
                count=total,
                amount=engine.money(sum(int(row.get(amount, 0)) for row in rows))
                if amount
                else None,
                detail=_detail(request, facility, kind=DetailKind.STOCK, flag=flag),
            )
        )
    return alerts


def _payable_alert_definition(
    request: AnalyticsDashboardRequest, facility: int | None, today: date
) -> AnalyticsQueryRequest:
    """Payables exist only now (R10), whatever the period."""
    return _definition(
        request, facility, measures=[MeasureKey.PAYABLE_OVERDUE], start=today, end=today,
        comparison=None,
    )  # fmt: skip


def _payable_alerts(found: engine.Found, prepared: engine.Prepared) -> list[AnalyticsAlert]:
    """The open purchase orders past due now. There is no payables detail kind, so no detail."""
    rows = [
        row
        for fact, slice_ in engine.wanted(prepared)[:1]
        for row in found[engine.fact_key(fact, slice_)]
    ]
    count = sum(int(row.get('overdue_orders', 0) or 0) for row in rows)
    if not count:
        return []
    amount = engine.money(sum(int(row.get('overdue_c', 0) or 0) for row in rows))
    return [AnalyticsAlert(key='payables_overdue', count=count, amount=amount, detail=None)]


def _refund_rate_alerts(
    request: AnalyticsDashboardRequest,
    facility: int | None,
    found: engine.Found,
    by_facility: facts.Slice,
    history: facts.Slice,
) -> list[AnalyticsAlert]:
    """Refunds ÷ gross sales per facility, against each facility's prior 12 calendar months."""
    needed = (Fact.SALE_LINE, Fact.REFUND_LINE)

    def merged(slice_: facts.Slice, width: int) -> engine.Merged:
        return engine.merge({f: found[engine.fact_key(f, slice_)] for f in needed}, width)

    period = merged(by_facility, 1)
    monthly: dict[Any, list[Decimal]] = {}
    for (month, place), sums in merged(history, 2).sums.items():
        rate = _rate(sums)
        if rate is not None:
            monthly.setdefault(place, []).append(rate)

    alerts: list[AnalyticsAlert] = []
    for (place,), sums in sorted(period.sums.items(), key=lambda item: str(item[0])):
        refunds = engine.money(sums.get('refund_line.sub_c', 0))
        if place is not None and refund_rate_alert(_rate(sums), refunds, monthly.get(place, [])):
            alerts.append(
                AnalyticsAlert(
                    key='refund_rate',
                    facility=place,
                    count=int(sums.get('refund_line.documents', 0)),
                    amount=refunds,
                    detail=_detail(
                        request,
                        facility,
                        kind=DetailKind.REFUNDS,
                        filters=[
                            AnalyticsFilter(dimension=DimensionKey.FACILITY, values=[str(place)])
                        ],
                    ),
                )
            )
    return alerts


def _rate(sums: dict[str, Any]) -> Decimal | None:
    gross = int(sums.get('sale_line.sub_c', 0) or 0)
    refunds = int(sums.get('refund_line.sub_c', 0) or 0)
    return engine.ratio(Decimal(refunds), Decimal(gross))
