"""The analytics engine (spec 037, FR-010–FR-015, FR-061): one query, answered the same everywhere.

`run` validates a query definition against the catalog, runs the facts its measures need, merges
them on the dimension values, and derives every measure from integer cents, so rows add up to the
totals and ratios are always recomputed from their parts. The dashboard, the detail and the exports
call the same functions, which is what makes their numbers match (FR-050).

Spec 038 adds balance facts (research R1): a state at a moment, such as stock on hand. A balance
fact is asked for moments rather than a period, and each moment's values fill the date bucket that
ends on it; the totals are the values at the period's end.
"""

from collections.abc import Awaitable, Callable, Hashable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.config import settings
from app.core.deps import CurrentUser, can_see_every_facility, forbidden, has_any_privilege
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import AccessRight, PaymentMethod, SystemObject
from app.models.commission import CommissionAgent
from app.models.core import Employee, Facility, Label, PointSale
from app.models.customer import Customer
from app.models.product import Product
from app.schemas.analytics import (
    AnalyticsFilter,
    AnalyticsKey,
    AnalyticsQueryRequest,
    AnalyticsQueryResponse,
    AnalyticsRow,
    Comparison,
    DimensionKey,
    Grain,
    MeasureKey,
    MeasureKind,
    StockOptions,
    VatTreatment,
)
from app.services.analytics import facts
from app.services.analytics.catalog import (
    BALANCE_FACTS,
    FAMILY_PRIVILEGES,
    LINE_DIMENSIONS,
    MEASURES,
    STOCK_OPTION_MEASURES,
    Fact,
    Family,
    applicable,
    facts_for,
)

MAX_DIMENSIONS = 3
#: Five years, the longest period a query covers (FR-011).
MAX_DAYS = 5 * 365 + 1
CENT = Decimal('0.01')
RATIO = Decimal('0.0001')
QUANTITY = Decimal('0.0001')
#: Days are shown with one decimal (spec 038, contracts/api.md).
DAYS = Decimal('0.1')
NONE_LABEL = '(none)'

Key = tuple[Any, ...]
#: A fact's summed columns, keyed `fact.column`, as integers (cents, counts) or quantities.
Sums = dict[str, Any]


# ── Scope and privileges ──────────────────────────────────────────────────────


async def sees_every_facility(db: AsyncSession, current: CurrentUser) -> bool:
    return await can_see_every_facility(db, current, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES)


def other_facility() -> DomainError:
    return DomainError(
        ErrorCode.FACILITY_OTHER_FACILITY,
        "Seeing another facility's figures needs SearchCreditsFromAllStores",
        {
            'resource': 'analytics',
            'required': [
                {
                    'system_object': SystemObject.SEARCH_CREDITS_FROM_ALL_STORES,
                    'right': AccessRight.READ,
                }
            ],
        },
    )


async def resolve_scope(db: AsyncSession, current: CurrentUser, facility: int | None) -> int | None:
    """The facility a request covers, None for all (FR-061, as specs 033–035).

    Administrators and holders of 101 get the facility they name, or every one. Anyone else gets
    their own, and naming another is refused.
    """
    if await sees_every_facility(db, current):
        return facility
    if facility is not None and facility != current.facility_id:
        raise other_facility()
    return current.facility_id


async def families(db: AsyncSession, current: CurrentUser) -> set[Family]:
    """The analytics families the caller holds (FR-060). A commission agent holds commissions
    without 112, for their own lines only (`own_commissions`, spec 038 R11)."""
    if current.administrator:
        return set(Family)
    held = {
        family
        for family, objects in FAMILY_PRIVILEGES.items()
        if await has_any_privilege(db, current.user_id, [(o, AccessRight.READ) for o in objects])
    }
    if Family.COMMISSIONS not in held and await is_agent(db, current.employee_id):
        held.add(Family.COMMISSIONS)
    return held


async def is_agent(db: AsyncSession, employee: int) -> bool:
    return await db.get(CommissionAgent, employee) is not None


async def own_commissions(db: AsyncSession, current: CurrentUser) -> int | None:
    """The agent whose commissions alone the caller may read: their own, when they are a commission
    agent without 112 (spec 038, R11). None when they read every agent's, or none at all."""
    if current.administrator:
        return None
    grant = (SystemObject.COMMISSIONS_BY_SALES_PERSON, AccessRight.READ)
    if await has_any_privilege(db, current.user_id, [grant]):
        return None
    return current.employee_id if await is_agent(db, current.employee_id) else None


def only_agent(filters: Sequence[AnalyticsFilter], agent: int) -> list[AnalyticsFilter]:
    """`filters` with the salesperson forced to `agent`, replacing any the caller named."""
    kept = [f for f in filters if f.dimension is not DimensionKey.SALESPERSON]
    return [*kept, AnalyticsFilter(dimension=DimensionKey.SALESPERSON, values=[str(agent)])]


def family_forbidden(family: Family) -> DomainError:
    return forbidden([(o, AccessRight.READ) for o in FAMILY_PRIVILEGES[family]])


async def check_privileges(
    db: AsyncSession, current: CurrentUser, measures: Iterable[MeasureKey]
) -> set[Family]:
    """Refuse a measure whose family the caller lacks; a sensitive one needs 37–39 (FR-005)."""
    held = await families(db, current)
    for key in measures:
        measure = MEASURES[key]
        if measure.family not in held:
            raise family_forbidden(measure.family)
        if measure.sensitive and Family.COST not in held:
            raise family_forbidden(Family.COST)
    return held


# ── Validation ────────────────────────────────────────────────────────────────


def validate(definition: AnalyticsQueryRequest) -> None:
    """The catalog's refusals, each with the `analytics.*` code naming the offending key."""
    if len(definition.dimensions) > MAX_DIMENSIONS:
        raise DomainError(
            ErrorCode.ANALYTICS_TOO_MANY_DIMENSIONS,
            f'At most {MAX_DIMENSIONS} dimensions',
            {'max': MAX_DIMENSIONS, 'given': len(definition.dimensions)},
        )
    validate_period(definition.start, definition.end)
    used = [*definition.dimensions, *(f.dimension for f in definition.filters)]
    for measure in definition.measures:
        for dimension in used:
            if not applicable(measure, dimension):
                raise DomainError(
                    ErrorCode.ANALYTICS_DIMENSION_NOT_APPLICABLE,
                    f'{measure.value} cannot be grouped or filtered by {dimension.value}',
                    {'measure': measure.value, 'dimension': dimension.value},
                )
    if definition.sort is not None:
        name = definition.sort.removeprefix('-')
        requested = {m.value for m in definition.measures} | {
            d.value for d in definition.dimensions
        }
        if name not in requested:
            raise DomainError(
                ErrorCode.ANALYTICS_SORT_INVALID,
                'Sort on a requested measure or dimension',
                {'sort': definition.sort},
            )


def validate_period(start: date, end: date) -> None:
    if end < start or (end - start).days >= MAX_DAYS:
        raise DomainError(
            ErrorCode.ANALYTICS_PERIOD_INVALID,
            'The end must not precede the start, and a period spans at most five years',
            {'start': start.isoformat(), 'end': end.isoformat(), 'max_days': MAX_DAYS},
        )


#: The first receivable month-end stored (spec 038, FR-121, research R8).
RECEIVABLE_HISTORY_START = date(2025, 1, 31)
#: Measures that need the receivable at both ends of whole months (research R9).
MONTH_END_MEASURES = frozenset({MeasureKey.DSO, MeasureKey.CEI})


def history_guard(measures: Iterable[MeasureKey], start: date, end: date) -> None:
    """Refuse a period reaching before a history the measures read (spec 038, R3, R8).

    Stock and movements are reliable from `settings.stock_history_start`, the day after the ledger
    reset; receivable month-ends are stored from 2025-01-31. `start` is the first day read: a
    caller that reads a balance at the end of the day before the period passes that day.
    """
    read = facts_for(measures)
    histories: list[tuple[str, date]] = []
    if read & {Fact.STOCK, Fact.MOVEMENT}:
        histories.append(('stock', settings.stock_history_start))
    if Fact.RECEIVABLE in read:
        histories.append(('receivables', RECEIVABLE_HISTORY_START))
    for history, earliest in histories:
        if start < earliest:
            raise DomainError(
                ErrorCode.ANALYTICS_BEFORE_HISTORY,
                f'The {history} history starts on {earliest}; asked for {start} to {end}',
                {
                    'history': history,
                    'requested': start.isoformat(),
                    'earliest': earliest.isoformat(),
                },
            )


def whole_months(start: date, end: date) -> bool:
    """A period from a 1st to a month-end or today, as DSO and CEI need (spec 038, R9)."""
    return start.day == 1 and (end == local_now().date() or (end + timedelta(days=1)).day == 1)


def month_end_guard(measures: Iterable[MeasureKey], start: date, end: date) -> None:
    """DSO and CEI need a period from a 1st to a month-end or today (spec 038, R9)."""
    for measure in measures:
        if measure in MONTH_END_MEASURES and not whole_months(start, end):
            raise DomainError(
                ErrorCode.ANALYTICS_PERIOD_NOT_MONTH_END,
                f'{measure.value} needs a period from a 1st to a month-end or today',
                {'measure': measure.value, 'start': start.isoformat(), 'end': end.isoformat()},
            )


def stock_guard(definition: AnalyticsQueryRequest) -> None:
    """`history_guard` for a query's inventory measures (R3): the first day they read is the
    period's start, the day before it for turnover's opening, and the same for a comparison."""
    measures = [m for m in definition.measures if MEASURES[m].family is Family.INVENTORY]
    if not measures:
        return
    starts = [definition.start]
    if definition.comparison is not None:
        starts.append(comparison_period(definition.start, definition.end, definition.comparison)[0])
    first = min(starts) - timedelta(days=1 if any(MEASURES[m].opening for m in measures) else 0)
    history_guard(measures, first, definition.end)


def receivable_guard(prepared: 'Prepared') -> None:
    """`history_guard` for a query's receivable measures (R8): the first month-end they read, at
    any of the definition's balance moments, the comparison's included."""
    measures = [m for m in prepared.definition.measures if Fact.RECEIVABLE in MEASURES[m].facts]
    if not measures:
        return
    read = [
        facts.month_end_on_or_before(moment) if moment != prepared.today else moment
        for slice_ in _slices(prepared)
        for moment in fact_slice(prepared, Fact.RECEIVABLE, slice_).moments
    ]
    if read:
        history_guard(measures, min(read), prepared.definition.end)


def parse_value(dimension: DimensionKey, value: str) -> Any:
    """A filter value as the facts compare it; an empty string is "none". The schema has already
    checked that it fits the dimension."""
    if value == '':
        return None
    match dimension:
        case DimensionKey.DATE:
            return date.fromisoformat(value)
        case DimensionKey.BRAND | DimensionKey.MODEL:
            return value.strip().lower()
        case (
            DimensionKey.CUSTOMER_TYPE
            | DimensionKey.PAYMENT_TERMS
            | DimensionKey.AGING_BUCKET
            | DimensionKey.DUE_BUCKET
            | DimensionKey.COMMISSION_STATUS
        ):
            return value
    return int(value)


def parse_filters(filters: Sequence[AnalyticsFilter]) -> dict[DimensionKey, tuple[Any, ...]]:
    parsed: dict[DimensionKey, tuple[Any, ...]] = {}
    for item in filters:
        values = tuple(parse_value(item.dimension, v) for v in item.values)
        parsed[item.dimension] = parsed.get(item.dimension, ()) + values
    return parsed


async def check_facility_filters(
    db: AsyncSession, current: CurrentUser, filters: Mapping[DimensionKey, Sequence[Any]]
) -> None:
    """A `facility` filter outside the caller's scope is refused like the parameter (FR-061)."""
    named = filters.get(DimensionKey.FACILITY, ())
    if any(v is not None and v != current.facility_id for v in named) and not (
        await sees_every_facility(db, current)
    ):
        raise other_facility()


# ── Periods ───────────────────────────────────────────────────────────────────


def comparison_period(start: date, end: date, comparison: Comparison) -> tuple[date, date]:
    """The previous period of the same length, or the same dates a year earlier (Feb 29 → 28)."""
    if comparison is Comparison.PREVIOUS_PERIOD:
        length = (end - start).days
        previous_end = start - timedelta(days=1)
        return previous_end - timedelta(days=length), previous_end
    return _year_earlier(start), _year_earlier(end)


def _year_earlier(day: date) -> date:
    try:
        return day.replace(year=day.year - 1)
    except ValueError:
        return day.replace(year=day.year - 1, day=28)


def bucket_start(day: date, grain: Grain) -> date:
    match grain:
        case Grain.DAY:
            return day
        case Grain.WEEK:
            return day - timedelta(days=day.weekday())
        case Grain.MONTH:
            return day.replace(day=1)
        case Grain.QUARTER:
            return day.replace(month=(day.month - 1) // 3 * 3 + 1, day=1)
        case Grain.YEAR:
            return day.replace(month=1, day=1)


def buckets(start: date, end: date, grain: Grain) -> list[date]:
    """The period starts at `grain` that the period touches, in order."""
    result: list[date] = []
    current = bucket_start(start, grain)
    while current <= end:
        result.append(current)
        current = next_bucket(current, grain)
    return result


def next_bucket(current: date, grain: Grain) -> date:
    match grain:
        case Grain.DAY:
            return current + timedelta(days=1)
        case Grain.WEEK:
            return current + timedelta(days=7)
        case Grain.MONTH | Grain.QUARTER | Grain.YEAR:
            months = {Grain.MONTH: 1, Grain.QUARTER: 3, Grain.YEAR: 12}[grain]
            total = current.year * 12 + current.month - 1 + months
            return date(total // 12, total % 12 + 1, 1)


# ── Balance moments (spec 038, research R1) ───────────────────────────────────

#: The prefix of a balance fact's values at the start of a bucket (an opening measure's).
OPENING = 'start_'


@dataclass(frozen=True)
class Placement:
    """Where a balance fact's values land: at the end of `end` they fill `bucket` (None without a
    date dimension); at the end of `opening`, the day before the bucket starts, they fill the same
    bucket as `start_` columns. `opening` is None unless a measure reads it (turnover, CEI)."""

    bucket: date | None
    end: date
    opening: date | None


def balance_plan(
    slice_: facts.Slice, *, dated: bool, opening: bool, today: date
) -> list[Placement]:
    """The moments a balance fact is read at for `slice_`, one per date bucket when `dated` or
    filtered by date, else one for the period. A bucket ends at its last day, the period's end, or
    today, whichever comes first; a bucket that starts after today has no value yet."""
    date_filter = slice_.filters.get(DimensionKey.DATE)
    if not dated and date_filter is None:
        if slice_.start > today:
            return []
        before = slice_.start - timedelta(days=1) if opening else None
        return [Placement(None, min(slice_.end, today), before)]
    plan: list[Placement] = []
    for bucket in buckets(slice_.start, slice_.end, slice_.grain):
        first = max(bucket, slice_.start)
        if (date_filter is not None and bucket not in date_filter) or first > today:
            continue
        last = min(next_bucket(bucket, slice_.grain) - timedelta(days=1), slice_.end, today)
        plan.append(Placement(bucket, last, first - timedelta(days=1) if opening else None))
    return plan if dated else _collapsed(plan)


def _collapsed(plan: Sequence[Placement]) -> list[Placement]:
    """One placement for the whole period: the last bucket's end, the first bucket's opening."""
    return [Placement(None, plan[-1].end, plan[0].opening)] if plan else []


def balance_slice(slice_: facts.Slice, plan: Sequence[Placement]) -> facts.Slice:
    """What a balance fact is asked for: the slice without dates, and the plan's moments."""
    moments = {p.end for p in plan} | {p.opening for p in plan if p.opening is not None}
    return replace(
        slice_,
        dimensions=tuple(d for d in slice_.dimensions if d is not DimensionKey.DATE),
        filters={d: v for d, v in slice_.filters.items() if d is not DimensionKey.DATE},
        grain=Grain.DAY,
        moments=tuple(sorted(moments)),
    )


def _is_key(name: str) -> bool:
    return name[0] in 'kl' and name[1:].isdigit()


def place(
    rows: Iterable[Mapping[str, Any]], plan: Sequence[Placement], date_index: int | None
) -> list[dict[str, Any]]:
    """A balance fact's rows as flow rows: each moment's values in the bucket of its placement,
    with the bucket inserted as the key at `date_index` (none when None)."""
    at: dict[date, list[Mapping[str, Any]]] = {}
    for row in rows:
        at.setdefault(_normalize(row['moment']), []).append(row)
    placed: list[dict[str, Any]] = []
    for placement in plan:
        for prefix, day in (('', placement.end), (OPENING, placement.opening)):
            for row in at.get(day, []) if day is not None else []:
                width = sum(1 for name in row if name[0] == 'k' and _is_key(name))
                keys = [row[f'k{i}'] for i in range(width)]
                labels = [row.get(f'l{i}') for i in range(width)]
                if date_index is not None:
                    keys.insert(date_index, placement.bucket)
                    labels.insert(date_index, None)
                out: dict[str, Any] = {f'k{i}': key for i, key in enumerate(keys)}
                out |= {f'l{i}': label for i, label in enumerate(labels)}
                out |= {
                    prefix + name: value
                    for name, value in row.items()
                    if name != 'moment' and not _is_key(name)
                }
                placed.append(out)
    return placed


# ── Running the facts and merging them ────────────────────────────────────────


@dataclass
class Merged:
    """Every group's sums across facts, and the display text the facts gave (brand and model)."""

    sums: dict[Key, Sums] = field(default_factory=dict)
    labels: dict[Key, list[str | None]] = field(default_factory=dict)


def merge(results: Mapping[Fact, Iterable[Mapping[str, Any]]], width: int) -> Merged:
    """Join the facts' grouped rows on their dimension values: a full outer join, in Python."""
    merged = Merged()
    for fact, rows in results.items():
        for row in rows:
            key = tuple(_normalize(row[f'k{i}']) for i in range(width))
            sums = merged.sums.setdefault(key, {})
            labels = merged.labels.setdefault(key, [None] * width)
            for name, value in row.items():
                if name[0] in 'kl' and name[1:].isdigit():
                    continue
                sums[f'{fact.value}.{name}'] = sums.get(f'{fact.value}.{name}', 0) + (value or 0)
            for i in range(width):
                labels[i] = labels[i] or row.get(f'l{i}')
    return merged


def _normalize(value: Any) -> Any:
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, Decimal) and value == value.to_integral_value():
        return int(value)
    return value


async def run_facts(
    db: AsyncSession, needed: Iterable[Fact], slice_: facts.Slice
) -> dict[Fact, list[Mapping[str, Any]]]:
    return {fact: await run_fact(db, fact, slice_) for fact in sorted(needed)}


#: A fact computed in Python rather than by one `facts.query` (spec 038): every balance fact, and
#: flows built from another service's results (commissions). It returns grouped rows shaped like
#: the SQL facts': `k0…` keys and `l0…` labels for `slice_.dimensions`, then its columns. A
#: balance fact's rows also carry `moment`, one of `slice_.moments`.
Runner = Callable[[AsyncSession, facts.Slice], Awaitable[Sequence[Mapping[str, Any]]]]

#: The Python facts, by fact. Every other fact is `facts.query`.
RUNNERS: dict[Fact, Runner] = {}


async def run_fact(db: AsyncSession, fact: Fact, slice_: facts.Slice) -> list[Mapping[str, Any]]:
    if fact in BALANCE_FACTS and not slice_.moments:
        return []  # the whole period lies after today
    if fact in RUNNERS:
        return list(await RUNNERS[fact](db, slice_))
    return list((await db.execute(facts.query(fact, slice_))).mappings().all())


def fact_key(fact: Fact, slice_: facts.Slice) -> Hashable:
    """What makes two fact queries the same SQL, so a caller running many definitions (the
    dashboard) runs each once. The grain only matters with a date dimension or date filter."""
    dated = DimensionKey.DATE in slice_.dimensions or DimensionKey.DATE in slice_.filters
    return (
        fact,
        slice_.dimensions,
        slice_.grain if dated else None,
        tuple(sorted((d.value, tuple(v)) for d, v in slice_.filters.items())),
        slice_.start,
        slice_.end,
        slice_.facility,
        slice_.moments,
        slice_.stock_options,
    )


#: Fact rows by `fact_key`.
Found = Mapping[Hashable, list[Mapping[str, Any]]]


# ── Deriving measures ─────────────────────────────────────────────────────────


def money(cents: Any) -> Decimal:
    return (Decimal(int(cents)) / 100).quantize(CENT)


def quantity(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(QUANTITY, rounding=ROUND_HALF_UP)


def ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or not denominator:
        return None
    return (numerator / denominator).quantize(RATIO, rounding=ROUND_HALF_UP)


def derive(
    sums: Mapping[str, Any], vat: VatTreatment, days: int | None = None
) -> dict[MeasureKey, Decimal | None]:
    """Every measure from a group's sums (FR-004). Money stays in cents until the end. `days` is
    the length of the group's period (or date bucket), for days of inventory."""

    def get(name: str) -> int:
        return int(sums.get(name, 0) or 0)

    def present(name: str) -> Decimal | None:
        """A measure a fact gives only at some moments (today's reservations): null where absent."""
        return quantity(sums[name] or 0) if name in sums else None

    with_vat = vat is VatTreatment.INCLUDED
    sale_gross = get('sale_line.sub_c') + get('sale_line.tax_c')
    gross = sale_gross if with_vat else get('sale_line.sub_c')
    discounts = get('sale_line.disc_sub_c') + (get('sale_line.disc_tax_c') if with_vat else 0)
    refunds = get('refund_line.sub_c') + (get('refund_line.tax_c') if with_vat else 0)
    notes = get('credit_note.sub_c') + (get('credit_note.tax_c') if with_vat else 0)
    net_without_vat = get('sale_line.sub_c') - get('refund_line.sub_c') - get('credit_note.sub_c')
    costed = get('sale_line.costed_sub_c') - get('refund_line.costed_sub_c')
    cost = get('sale_line.cost_c') - get('refund_line.cost_c')
    orders = get('sale_line.orders')
    sold = quantity(sums.get('sale_line.qty', 0) or 0)
    refunded = quantity(sums.get('refund_line.qty', 0) or 0)

    values: dict[MeasureKey, Decimal | None] = {
        MeasureKey.GROSS_SALES: money(gross),
        MeasureKey.DISCOUNTS: money(discounts),
        MeasureKey.REFUNDS: money(refunds),
        MeasureKey.CREDIT_NOTES: money(notes),
        MeasureKey.NET_SALES: money(gross - refunds - notes),
        MeasureKey.COSTED_NET_SALES: money(costed),
        MeasureKey.COST_COVERAGE: ratio(Decimal(costed), Decimal(net_without_vat)),
        MeasureKey.COST_OF_SALES: money(cost),
        MeasureKey.GROSS_MARGIN: money(costed - cost),
        MeasureKey.MARGIN_PCT: ratio(Decimal(costed - cost), Decimal(costed)),
        MeasureKey.UNITS_SOLD: sold,
        MeasureKey.UNITS_REFUNDED: refunded,
        MeasureKey.UNITS: sold - refunded,
        MeasureKey.ORDERS: Decimal(orders),
        MeasureKey.AVERAGE_TICKET: (
            (Decimal(sale_gross) / 100 / orders).quantize(CENT, rounding=ROUND_HALF_UP)
            if orders
            else None
        ),
        MeasureKey.COLLECTED: money(get('payment.collected_c')),
        MeasureKey.REFUNDS_PAID_OUT: money(get('payment.paid_out_c')),
        MeasureKey.STORE_CREDIT_ISSUED: money(get('payment.store_c')),
        MeasureKey.PAYMENTS: Decimal(get('payment.payments')),
        # Spec 038: inventory (research R2–R5)
        MeasureKey.ON_HAND: quantity(sums.get('stock.on_hand', 0) or 0),
        MeasureKey.RESERVED: present('stock_today.reserved'),
        MeasureKey.AVAILABLE: present('stock_today.available'),
        MeasureKey.IN_TRANSIT: quantity(sums.get('stock.in_transit', 0) or 0),
        MeasureKey.STOCK_VALUE: money(get('stock.value_c')),
        MeasureKey.STOCK_COST_COVERAGE: ratio(
            Decimal(get('stock.costed')), Decimal(get('stock.stocked'))
        ),
        MeasureKey.UNCOSTED_QUANTITY: quantity(sums.get('stock.uncosted_qty', 0) or 0),
        MeasureKey.UNITS_IN: quantity(sums.get('movement.units_in', 0) or 0),
        MeasureKey.UNITS_OUT: quantity(sums.get('movement.units_out', 0) or 0),
        MeasureKey.MOVEMENT_DOCUMENTS: Decimal(get('movement.documents')),
        # Spec 038: commissions (research R11)
        MeasureKey.COMMISSION: money(get('commission.commission_c')),
        MeasureKey.COMMISSION_BASE: money(get('commission.base_c')),
        MeasureKey.COMMISSION_LINES: Decimal(get('commission.lines')),
    }
    values |= _stock_measures(sums, cost, days)
    values |= _receivable_measures(sums, days)
    return values


def _receivable_measures(
    sums: Mapping[str, Any], days: int | None
) -> dict[MeasureKey, Decimal | None]:
    """Receivables, collection and payables (spec 038, research R9, R10). DSO and CEI are computed
    from their parts. Payables exist only now: null at any other moment."""

    def get(name: str) -> int:
        return int(sums.get(name, 0) or 0)

    def now_only(name: str, figure: Callable[[int], Decimal]) -> Decimal | None:
        return figure(get(name)) if name in sums else None

    end, current = get('receivable.balance_c'), get('receivable.current_c')
    sales = get('credit_flow.credit_sales_c')
    start = get('receivable.start_balance_c')
    return {
        MeasureKey.RECEIVABLE: money(end),
        MeasureKey.RECEIVABLE_CURRENT: money(current),
        MeasureKey.RECEIVABLE_OVERDUE: money(get('receivable.overdue_c')),
        MeasureKey.OPEN_ORDERS: Decimal(get('receivable.orders')),
        MeasureKey.CREDIT_SALES: money(sales),
        MeasureKey.COLLECTED_ON_CREDIT: money(get('credit_flow.collected_c')),
        MeasureKey.DUE_IN_PERIOD: money(get('credit_flow.due_c')),
        MeasureKey.DUE_COLLECTED: money(get('credit_flow.due_collected_c')),
        MeasureKey.DSO: (
            (Decimal(end) / sales * days).quantize(DAYS, rounding=ROUND_HALF_UP)
            if sales > 0 and days
            else None
        ),
        MeasureKey.CEI: ratio(Decimal(start + sales - end), Decimal(start + sales - current)),
        MeasureKey.CREDIT_LIMIT: money(get('receivable.limit_c')),
        MeasureKey.CREDIT_UTILIZATION: ratio(
            Decimal(get('receivable.limited_c')), Decimal(get('receivable.limit_c'))
        ),
        MeasureKey.PAYABLE: now_only('payable.payable_c', money),
        MeasureKey.PAYABLE_CURRENT: now_only('payable.current_c', money),
        MeasureKey.PAYABLE_OVERDUE: now_only('payable.overdue_c', money),
        MeasureKey.OPEN_PURCHASE_ORDERS: now_only('payable.orders', Decimal),
    }


def _stock_measures(
    sums: Mapping[str, Any], cost_c: int, days: int | None
) -> dict[MeasureKey, Decimal | None]:
    """The R5 measures (velocity, cover, restock, dead and slow stock) and turnover (R4)."""

    def get(name: str) -> int:
        return int(sums.get(name, 0) or 0)

    velocity = Decimal(str(sums.get('stock.velocity', 0) or 0))
    cover = Decimal(str(sums.get('stock.cover_qty', 0) or 0))
    # Turnover's average value is in cents ×2: (start + end) without halving, so it stays exact.
    doubled = get('stock.start_value_c') + get('stock.value_c')
    turnover = Decimal(2 * cost_c) / doubled if doubled > 0 and cost_c > 0 else None
    return {
        MeasureKey.SALES_VELOCITY: quantity(velocity),
        MeasureKey.DAYS_OF_COVER: (
            (cover / velocity).quantize(DAYS, rounding=ROUND_HALF_UP) if velocity > 0 else None
        ),
        MeasureKey.STOCK_OUT_PAIRS: Decimal(get('stock.stock_outs')),
        MeasureKey.BELOW_REORDER_PAIRS: Decimal(get('stock.below')),
        MeasureKey.SUGGESTED_QUANTITY: quantity(sums.get('stock.suggested', 0) or 0),
        MeasureKey.DEAD_STOCK_QUANTITY: quantity(sums.get('stock.dead_qty', 0) or 0),
        MeasureKey.DEAD_STOCK_VALUE: money(get('stock.dead_value_c')),
        MeasureKey.SLOW_STOCK_QUANTITY: quantity(sums.get('stock.slow_qty', 0) or 0),
        MeasureKey.SLOW_STOCK_VALUE: money(get('stock.slow_value_c')),
        MeasureKey.TURNOVER: (
            turnover.quantize(RATIO, rounding=ROUND_HALF_UP) if turnover is not None else None
        ),
        MeasureKey.DAYS_OF_INVENTORY: (
            (Decimal(days) / turnover).quantize(DAYS, rounding=ROUND_HALF_UP)
            if turnover is not None and days
            else None
        ),
    }


def subtract(total: Mapping[str, Any], parts: Iterable[Mapping[str, Any]]) -> Sums:
    """`total` minus every one of `parts`, sum by sum: what top N leaves for "others"."""
    rest = dict(total)
    for part in parts:
        for name, value in part.items():
            rest[name] = rest.get(name, 0) - (value or 0)
    return rest


def change_figures(
    values: Mapping[str, Decimal | None], previous: Mapping[str, Decimal | None]
) -> tuple[dict[str, Decimal | None], dict[str, Decimal | None]]:
    """`(change, change_pct)` per measure; % change is null against a zero or missing base."""
    change: dict[str, Decimal | None] = {}
    change_pct: dict[str, Decimal | None] = {}
    for key, value in values.items():
        before = previous.get(key)
        change[key] = None if value is None or before is None else value - before
        change_pct[key] = (
            ratio(change[key], abs(before)) if change[key] is not None and before else None
        )
    return change, change_pct


def shares(
    values: Mapping[str, Decimal | None], totals: Mapping[str, Decimal | None]
) -> dict[str, Decimal | None]:
    """Each additive measure's share of the totals (FR-013)."""
    return {
        key: ratio(value, totals.get(key))
        for key, value in values.items()
        if MEASURES[MeasureKey(key)].additive
    }


def moment_shares(
    values: Mapping[str, Decimal | None],
    totals: Mapping[str, Decimal | None],
    at_moment: Mapping[str, Decimal | None] | None,
) -> dict[str, Decimal | None]:
    """`shares`, except that a balance measure in a date bucket shares within the bucket's own
    moment (`at_moment`, its totals then) rather than the period's end (spec 038, R1)."""
    result = shares(values, totals)
    if at_moment is not None:
        for key in result:
            if MEASURES[MeasureKey(key)].kind is MeasureKind.BALANCE:
                result[key] = ratio(values[key], at_moment.get(key))
    return result


# ── Labels ────────────────────────────────────────────────────────────────────


async def resolve_labels(
    db: AsyncSession, dimensions: Sequence[DimensionKey], keys: Iterable[Key]
) -> dict[tuple[DimensionKey, Any], str]:
    """Display text for every entity value in `keys`: one query per entity dimension."""
    keys = list(keys)
    found: dict[tuple[DimensionKey, Any], str] = {}
    for index, dimension in enumerate(dimensions):
        ids = sorted({key[index] for key in keys if isinstance(key[index], int)})
        if not ids:
            continue
        match dimension:
            case DimensionKey.FACILITY:
                rows = select(Facility.facility_id, Facility.code).where(
                    Facility.facility_id.in_(ids)
                )
            case DimensionKey.POINT_OF_SALE:
                rows = select(PointSale.point_sale_id, PointSale.name).where(
                    PointSale.point_sale_id.in_(ids)
                )
            case DimensionKey.SALESPERSON | DimensionKey.SALES_AGENT:
                rows = select(
                    Employee.employee_id, Employee.first_name + ' ' + Employee.last_name
                ).where(Employee.employee_id.in_(ids))
            case DimensionKey.CUSTOMER:
                rows = select(Customer.customer_id, Customer.name).where(
                    Customer.customer_id.in_(ids)
                )
            case DimensionKey.PRODUCT:
                rows = select(Product.product_id, Product.code + ' ' + Product.name).where(
                    Product.product_id.in_(ids)
                )
            case DimensionKey.LABEL:
                rows = select(Label.label_id, Label.name).where(Label.label_id.in_(ids))
            case _:
                continue
        for value, text in (await db.execute(rows)).all():
            found[(dimension, value)] = text
    return found


def key_label(
    dimension: DimensionKey,
    value: Any,
    grain: Grain,
    looked_up: Mapping[tuple[DimensionKey, Any], str],
    fact_label: str | None,
) -> str:
    if value is None:
        return NONE_LABEL
    if fact_label:
        return fact_label
    if (dimension, value) in looked_up:
        return looked_up[(dimension, value)]
    match dimension:
        case DimensionKey.DATE:
            return date_label(value, grain)
        case DimensionKey.HOUR:
            return f'{value:02d}:00'
        case DimensionKey.PAYMENT_METHOD:
            try:
                return PaymentMethod(value).name
            except ValueError:
                return str(value)
        case DimensionKey.CASH_SESSION:
            return f'#{value}'
    return str(value)


def date_label(day: date, grain: Grain) -> str:
    match grain:
        case Grain.MONTH:
            return day.strftime('%Y-%m')
        case Grain.QUARTER:
            return f'{day.year}-Q{(day.month - 1) // 3 + 1}'
        case Grain.YEAR:
            return str(day.year)
        case Grain.WEEK:
            year, week, _ = day.isocalendar()
            return f'{year}-W{week:02d}'
    return day.isoformat()


def key_value(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, date):
        return value.isoformat()
    return str(value)


# ── The query ─────────────────────────────────────────────────────────────────


def _tiebreak(key: Key) -> tuple:
    return tuple((v is None, str(v)) for v in key)


def order_rows(
    items: list[tuple[Key, dict[str, Decimal | None]]],
    definition: AnalyticsQueryRequest,
) -> list[tuple[Key, dict[str, Decimal | None]]]:
    """The requested sort; otherwise the first dimension ascending, or with `top` the first
    measure descending. Ties break by key, and "none" or null values go last, so the order is
    stable across runs."""
    dimensions = [d.value for d in definition.dimensions]
    sort = definition.sort
    if sort is None:
        if definition.top is not None:
            sort = f'-{definition.measures[0].value}'
        elif dimensions:
            sort = dimensions[0]
        else:
            return sorted(items, key=lambda item: _tiebreak(item[0]))
    name = sort.removeprefix('-')

    def value(item: tuple[Key, dict[str, Decimal | None]]) -> Any:
        key, values = item
        return key[dimensions.index(name)] if name in dimensions else values.get(name)

    present = sorted((i for i in items if value(i) is not None), key=lambda i: _tiebreak(i[0]))
    present.sort(key=value, reverse=sort.startswith('-'))  # stable: ties keep the key order
    missing = sorted((i for i in items if value(i) is None), key=lambda i: _tiebreak(i[0]))
    return present + missing


def select_measures(
    derived: Mapping[MeasureKey, Decimal | None], measures: Sequence[MeasureKey]
) -> dict[str, Decimal | None]:
    return {m.value: derived[m] for m in measures}


def overlapping(definition: AnalyticsQueryRequest) -> bool:
    """Whether a document can count in several rows: by label, or orders by a line dimension."""
    dims = set(definition.dimensions)
    counts_documents = {MeasureKey.ORDERS, MeasureKey.AVERAGE_TICKET} & set(definition.measures)
    return DimensionKey.LABEL in dims or bool(counts_documents and dims & LINE_DIMENSIONS)


@dataclass
class Prepared:
    """A definition checked and resolved: what `run` executes, and what the cache keys on."""

    definition: AnalyticsQueryRequest
    filters: dict[DimensionKey, tuple[Any, ...]]
    facility: int | None
    families: set[Family]
    #: The business day balances are read up to (spec 038): no balance moment is later.
    today: date = field(default_factory=lambda: local_now().date())


async def prepare(
    db: AsyncSession, definition: AnalyticsQueryRequest, current: CurrentUser
) -> Prepared:
    validate(definition)
    stock_guard(definition)
    receivable_guard(Prepared(definition, parse_filters(definition.filters), None, set()))
    month_end_guard(definition.measures, definition.start, definition.end)
    held = await check_privileges(db, current, definition.measures)
    definition = await commission_definition(db, definition, current)
    facility = await resolve_scope(db, current, definition.facility)
    filters = parse_filters(definition.filters)
    await check_facility_filters(db, current, filters)
    normalized = definition.model_copy(update={'facility': facility})
    return Prepared(normalized, filters, facility, held)


async def commission_definition(
    db: AsyncSession, definition: AnalyticsQueryRequest, current: CurrentUser
) -> AnalyticsQueryRequest:
    """With a commission measure (spec 038, R11): a commission line belongs to a month, so a day
    or week grain is raised to the month; and an agent without 112 gets their own lines only."""
    if Family.COMMISSIONS not in {MEASURES[m].family for m in definition.measures}:
        return definition
    if definition.grain in (Grain.DAY, Grain.WEEK):
        definition = definition.model_copy(update={'grain': Grain.MONTH})
    agent = await own_commissions(db, current)
    if agent is not None:
        filters = only_agent(definition.filters, agent)
        definition = definition.model_copy(update={'filters': filters})
    return definition


async def run(
    db: AsyncSession, definition: AnalyticsQueryRequest, current: CurrentUser
) -> AnalyticsQueryResponse:
    return await execute(db, await prepare(db, definition, current))


def _slices(prepared: Prepared) -> list[facts.Slice]:
    """The rows, the totals, and with a comparison the same two over the comparison period."""
    definition = prepared.definition
    base = facts.Slice(
        dimensions=tuple(definition.dimensions),
        grain=definition.grain,
        filters=prepared.filters,
        start=definition.start,
        end=definition.end,
        facility=prepared.facility,
    )
    totals = replace(base, dimensions=())
    if definition.comparison is None:
        return [base, totals]
    before_start, before_end = comparison_period(
        definition.start, definition.end, definition.comparison
    )
    return [
        base,
        totals,
        _shifted(base, before_start, before_end),
        _shifted(totals, before_start, before_end),
    ]


def wanted(prepared: Prepared) -> list[tuple[Fact, facts.Slice]]:
    """Every fact query `assemble` reads for this definition. A balance fact is asked for moments
    in place of the slice's dates."""
    needed = sorted(facts_for(prepared.definition.measures))
    return [
        (fact, fact_slice(prepared, fact, slice_))
        for slice_ in _slices(prepared)
        for fact in needed
    ]


def stock_options(definition: AnalyticsQueryRequest) -> StockOptions | None:
    """The options the stock fact computes R5's measures with: the request's, or the defaults when
    one of those measures is asked; None skips them (spec 038)."""
    if definition.stock_options is not None:
        return definition.stock_options
    return StockOptions() if set(definition.measures) & STOCK_OPTION_MEASURES else None


def fact_slice(prepared: Prepared, fact: Fact, slice_: facts.Slice) -> facts.Slice:
    """What `fact` is asked for one of `_slices(prepared)`: a balance fact gets moments in place
    of the slice's dates, and the stock fact its options."""
    if fact not in BALANCE_FACTS:
        return slice_
    asked = balance_slice(slice_, _plan(prepared, slice_))
    if fact is Fact.STOCK:
        asked = replace(asked, stock_options=stock_options(prepared.definition))
    return asked


def _plan(prepared: Prepared, slice_: facts.Slice) -> list[Placement]:
    """The balance moments of one of `_slices(prepared)`. The totals slice of a definition by date
    gets every bucket's too, for the shares within each moment."""
    definition = prepared.definition
    return balance_plan(
        slice_,
        dated=DimensionKey.DATE in definition.dimensions,
        opening=any(MEASURES[m].opening for m in definition.measures),
        today=prepared.today,
    )


async def execute(db: AsyncSession, prepared: Prepared) -> AnalyticsQueryResponse:
    """Run a prepared definition: rows, others, totals and comparisons (FR-010–FR-013)."""
    found = {
        fact_key(fact, slice_): await run_fact(db, fact, slice_)
        for fact, slice_ in wanted(prepared)
    }
    return await assemble(db, prepared, found)


async def assemble(db: AsyncSession, prepared: Prepared, found: Found) -> AnalyticsQueryResponse:
    """The answer from the fact rows of `wanted(prepared)`; `db` only looks up labels."""
    definition = prepared.definition
    as_of = local_now()
    needed = sorted(facts_for(definition.measures))
    dimensions = tuple(definition.dimensions)
    width = len(dimensions)
    at = dimensions.index(DimensionKey.DATE) if DimensionKey.DATE in dimensions else None
    balances = [fact for fact in needed if fact in BALANCE_FACTS]

    def rows_of(fact: Fact, slice_: facts.Slice, by_bucket: bool = False) -> list:
        if fact not in BALANCE_FACTS:
            return found[fact_key(fact, slice_)]
        plan = _plan(prepared, slice_)
        rows = found[fact_key(fact, fact_slice(prepared, fact, slice_))]
        if DimensionKey.DATE in slice_.dimensions:
            return place(rows, plan, slice_.dimensions.index(DimensionKey.DATE))
        return place(rows, plan, 0) if by_bucket else place(rows, _collapsed(plan), None)

    def merged_of(slice_: facts.Slice, size: int) -> Merged:
        return merge({fact: rows_of(fact, slice_) for fact in needed}, size)

    def values_of(sums: Sums, slice_: facts.Slice, bucket: Any = None) -> dict:
        """The requested measures of a group of `slice_`, in date bucket `bucket` (None: the
        whole period), which days of inventory needs the length of."""
        last = min(slice_.end, prepared.today)
        first, end = slice_.start, last
        if bucket is not None:
            first = max(bucket, slice_.start)
            end = min(next_bucket(bucket, definition.grain) - timedelta(days=1), last)
        days = max((end - first).days + 1, 0)
        return select_measures(derive(sums, definition.vat, days), definition.measures)

    base, total_slice, *before = _slices(prepared)
    merged = merged_of(base, width)
    total_sums = merged_of(total_slice, 0).sums.get((), {})

    # A balance measure by date shares within its bucket's moment: the totals of each moment.
    at_moments: dict[Any, dict[str, Decimal | None]] = {}
    if at is not None and any(MEASURES[m].kind is MeasureKind.BALANCE for m in definition.measures):
        by_bucket = merge({f: rows_of(f, total_slice, by_bucket=True) for f in balances}, 1)
        at_moments = {
            key[0]: values_of(sums, total_slice, key[0]) for key, sums in by_bucket.sums.items()
        }

    def at_ends(sums: Sums, bucket: Any, plan: Sequence[Placement]) -> Sums:
        """A shown row's sums as "others" subtracts them from the totals: a balance's only from
        the bucket the totals were read at, the last (its opening, the first)."""
        if at is None or not balances or not plan:
            return sums
        prefixes = tuple(f'{fact.value}.' for fact in balances)
        return {
            name: value
            for name, value in sums.items()
            if not name.startswith(prefixes)
            or bucket == (plan[0] if name.split('.', 1)[1].startswith(OPENING) else plan[-1]).bucket
        }

    previous: Merged | None = None
    previous_totals: Sums = {}
    position: dict[date, date] = {}
    if before:
        before_base, before_totals = before
        before_start, before_end = before_base.start, before_base.end
        previous = merged_of(before_base, width)
        previous_totals = merged_of(before_totals, 0).sums.get((), {})
        if DimensionKey.DATE in dimensions:
            here = buckets(definition.start, definition.end, definition.grain)
            there = buckets(before_start, before_end, definition.grain)
            position = dict(zip(here, there, strict=False))

    totals_values = values_of(total_sums, total_slice)
    # Without dimensions the totals are the answer; a lone row would only repeat them.
    items = [
        (key, values_of(sums, base, key[at] if at is not None else None))
        for key, sums in merged.sums.items()
        if width
    ]
    ordered = order_rows(items, definition)
    shown = ordered[: definition.top] if definition.top is not None else ordered
    folded = ordered[definition.top :] if definition.top is not None else []

    looked_up = await resolve_labels(db, dimensions, [key for key, _ in shown])

    def comparison_of(key: Key) -> dict[str, Decimal | None] | None:
        if previous is None:
            return None
        there = _previous_key(key, dimensions, position)
        return values_of(
            previous.sums.get(there, {}), before[0], there[at] if at is not None else None
        )

    def row(key: Key, values: dict[str, Decimal | None], before: dict | None) -> AnalyticsRow:
        change, change_pct = change_figures(values, before) if before is not None else (None, None)
        moment = at_moments.get(key[at], {}) if at is not None and at_moments else None
        return AnalyticsRow(
            keys=[
                AnalyticsKey(
                    dimension=dimension,
                    value=key_value(key[i]),
                    label=key_label(
                        dimension,
                        key[i],
                        definition.grain,
                        looked_up,
                        merged.labels.get(key, [None] * width)[i],
                    ),
                )
                for i, dimension in enumerate(dimensions)
            ],
            values=values,
            share=moment_shares(values, totals_values, moment),
            comparison=before,
            change=change,
            change_pct=change_pct,
            drill=drill(definition, dimensions, key),
        )

    rows = [row(key, values, comparison_of(key)) for key, values in shown]
    others = None
    if folded:
        plan = _plan(prepared, base) if balances else []
        rest = subtract(
            total_sums,
            (
                at_ends(merged.sums[key], key[at] if at is not None else None, plan)
                for key, _ in shown
            ),
        )
        before_plan = _plan(prepared, before[0]) if balances and before else []
        before_keys = [_previous_key(key, dimensions, position) for key, _ in shown]
        before_rest = (
            values_of(
                subtract(
                    previous_totals,
                    (
                        at_ends(
                            previous.sums.get(key, {}),
                            key[at] if at is not None else None,
                            before_plan,
                        )
                        for key in before_keys
                    ),
                ),
                before[1],
            )
            if previous is not None
            else None
        )
        others_values = values_of(rest, base)
        change, change_pct = (
            change_figures(others_values, before_rest) if before_rest is not None else (None, None)
        )
        others = AnalyticsRow(
            keys=[],
            values=others_values,
            share=shares(others_values, totals_values),
            comparison=before_rest,
            change=change,
            change_pct=change_pct,
            drill=list(definition.filters),
        )
    totals_before = values_of(previous_totals, before[1]) if previous is not None else None
    change, change_pct = (
        change_figures(totals_values, totals_before) if totals_before is not None else (None, None)
    )
    totals_row = AnalyticsRow(
        keys=[],
        values=totals_values,
        share=shares(totals_values, totals_values),
        comparison=totals_before,
        change=change,
        change_pct=change_pct,
        drill=list(definition.filters),
    )
    return AnalyticsQueryResponse(
        as_of=as_of,
        definition=definition,
        rows=rows,
        others=others,
        totals=totals_row,
        overlapping=overlapping(definition),
    )


def _previous_key(key: Key, dimensions: Sequence[DimensionKey], position: Mapping) -> Key:
    if DimensionKey.DATE not in dimensions:
        return key
    index = list(dimensions).index(DimensionKey.DATE)
    return (*key[:index], position.get(key[index]), *key[index + 1 :])


def _shifted(slice_: facts.Slice, start: date, end: date) -> facts.Slice:
    """The same slice over another period. A date filter moves with it, bucket for bucket."""
    filters = dict(slice_.filters)
    if DimensionKey.DATE in filters:
        here = buckets(slice_.start, slice_.end, slice_.grain)
        there = buckets(start, end, slice_.grain)
        moved = dict(zip(here, there, strict=False))
        filters[DimensionKey.DATE] = tuple(moved.get(v, v) for v in filters[DimensionKey.DATE])
    return replace(slice_, filters=filters, start=start, end=end)


def drill(
    definition: AnalyticsQueryRequest, dimensions: Sequence[DimensionKey], key: Key
) -> list[AnalyticsFilter]:
    """The filters that reproduce a row: the request's, narrowed to the row's values (FR-040)."""
    narrowed = {f.dimension: list(f.values) for f in definition.filters}
    for dimension, value in zip(dimensions, key, strict=True):
        narrowed[dimension] = [key_value(value) or '']
    return [AnalyticsFilter(dimension=d, values=v) for d, v in narrowed.items()]


# The Python facts register here, below everything they use of this module (spec 038).
from app.services.analytics import balances, commissions  # noqa: E402

RUNNERS.update(
    {
        Fact.STOCK: balances.stock,
        Fact.STOCK_TODAY: balances.stock_today,
        Fact.RECEIVABLE: balances.receivable,
        Fact.PAYABLE: balances.payable,
        Fact.COMMISSION: commissions.fact,
    }
)
