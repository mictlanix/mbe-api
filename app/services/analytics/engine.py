"""The analytics engine (spec 037, FR-010–FR-015, FR-061): one query, answered the same everywhere.

`run` validates a query definition against the catalog, runs the facts its measures need, merges
them on the dimension values, and derives every measure from integer cents, so rows add up to the
totals and ratios are always recomputed from their parts. The dashboard, the detail and the exports
call the same functions, which is what makes their numbers match (FR-050).
"""

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.deps import CurrentUser, can_see_every_facility, forbidden, has_any_privilege
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import AccessRight, PaymentMethod, SystemObject
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
    VatTreatment,
)
from app.services.analytics import facts
from app.services.analytics.catalog import (
    FAMILY_PRIVILEGES,
    LINE_DIMENSIONS,
    MEASURES,
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
    """The analytics families the caller holds (FR-060)."""
    if current.administrator:
        return set(Family)
    return {
        family
        for family, objects in FAMILY_PRIVILEGES.items()
        if await has_any_privilege(db, current.user_id, [(o, AccessRight.READ) for o in objects])
    }


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
        case DimensionKey.CUSTOMER_TYPE | DimensionKey.PAYMENT_TERMS:
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
        match grain:
            case Grain.DAY:
                current += timedelta(days=1)
            case Grain.WEEK:
                current += timedelta(days=7)
            case Grain.MONTH | Grain.QUARTER | Grain.YEAR:
                months = {Grain.MONTH: 1, Grain.QUARTER: 3, Grain.YEAR: 12}[grain]
                total = current.year * 12 + current.month - 1 + months
                current = date(total // 12, total % 12 + 1, 1)
    return result


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
    return {
        fact: list((await db.execute(facts.query(fact, slice_))).mappings().all())
        for fact in sorted(needed)
    }


# ── Deriving measures ─────────────────────────────────────────────────────────


def _money(cents: Any) -> Decimal:
    return (Decimal(int(cents)) / 100).quantize(CENT)


def _quantity(value: Any) -> Decimal:
    return Decimal(str(value)).quantize(QUANTITY, rounding=ROUND_HALF_UP)


def ratio(numerator: Decimal | None, denominator: Decimal | None) -> Decimal | None:
    if numerator is None or not denominator:
        return None
    return (numerator / denominator).quantize(RATIO, rounding=ROUND_HALF_UP)


def derive(sums: Mapping[str, Any], vat: VatTreatment) -> dict[MeasureKey, Decimal | None]:
    """Every measure from a group's sums (FR-004). Money stays in cents until the end."""

    def get(name: str) -> int:
        return int(sums.get(name, 0) or 0)

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
    sold = _quantity(sums.get('sale_line.qty', 0) or 0)
    refunded = _quantity(sums.get('refund_line.qty', 0) or 0)

    values: dict[MeasureKey, Decimal | None] = {
        MeasureKey.GROSS_SALES: _money(gross),
        MeasureKey.DISCOUNTS: _money(discounts),
        MeasureKey.REFUNDS: _money(refunds),
        MeasureKey.CREDIT_NOTES: _money(notes),
        MeasureKey.NET_SALES: _money(gross - refunds - notes),
        MeasureKey.COSTED_NET_SALES: _money(costed),
        MeasureKey.COST_COVERAGE: ratio(Decimal(costed), Decimal(net_without_vat)),
        MeasureKey.COST_OF_SALES: _money(cost),
        MeasureKey.GROSS_MARGIN: _money(costed - cost),
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
        MeasureKey.COLLECTED: _money(get('payment.collected_c')),
        MeasureKey.REFUNDS_PAID_OUT: _money(get('payment.paid_out_c')),
        MeasureKey.STORE_CREDIT_ISSUED: _money(get('payment.store_c')),
        MeasureKey.PAYMENTS: Decimal(get('payment.payments')),
    }
    return values


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
            case DimensionKey.SALESPERSON:
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


async def prepare(
    db: AsyncSession, definition: AnalyticsQueryRequest, current: CurrentUser
) -> Prepared:
    validate(definition)
    held = await check_privileges(db, current, definition.measures)
    facility = await resolve_scope(db, current, definition.facility)
    filters = parse_filters(definition.filters)
    await check_facility_filters(db, current, filters)
    normalized = definition.model_copy(update={'facility': facility})
    return Prepared(normalized, filters, facility, held)


async def run(
    db: AsyncSession, definition: AnalyticsQueryRequest, current: CurrentUser
) -> AnalyticsQueryResponse:
    return await execute(db, await prepare(db, definition, current))


async def execute(db: AsyncSession, prepared: Prepared) -> AnalyticsQueryResponse:
    """Run a prepared definition: rows, others, totals and comparisons (FR-010–FR-013)."""
    definition = prepared.definition
    as_of = local_now()
    needed = facts_for(definition.measures)
    dimensions = tuple(definition.dimensions)
    width = len(dimensions)
    base = facts.Slice(
        dimensions=dimensions,
        grain=definition.grain,
        filters=prepared.filters,
        start=definition.start,
        end=definition.end,
        facility=prepared.facility,
    )
    merged = merge(await run_facts(db, needed, base), width)
    total_slice = facts.Slice(
        grain=definition.grain,
        filters=prepared.filters,
        start=definition.start,
        end=definition.end,
        facility=prepared.facility,
    )
    total_sums = merge(await run_facts(db, needed, total_slice), 0).sums.get((), {})

    previous: Merged | None = None
    previous_totals: Sums = {}
    position: dict[date, date] = {}
    if definition.comparison is not None:
        before_start, before_end = comparison_period(
            definition.start, definition.end, definition.comparison
        )
        previous = merge(
            await run_facts(db, needed, _shifted(base, before_start, before_end)),
            width,
        )
        previous_totals = merge(
            await run_facts(db, needed, _shifted(total_slice, before_start, before_end)), 0
        ).sums.get((), {})
        if DimensionKey.DATE in dimensions:
            here = buckets(definition.start, definition.end, definition.grain)
            there = buckets(before_start, before_end, definition.grain)
            position = dict(zip(here, there, strict=False))

    measures = definition.measures
    totals_values = select_measures(derive(total_sums, definition.vat), measures)
    items = [
        (key, select_measures(derive(sums, definition.vat), measures))
        for key, sums in merged.sums.items()
    ]
    ordered = order_rows(items, definition)
    shown = ordered[: definition.top] if definition.top is not None else ordered
    folded = ordered[definition.top :] if definition.top is not None else []

    looked_up = await resolve_labels(db, dimensions, [key for key, _ in shown])

    def comparison_of(key: Key) -> dict[str, Decimal | None] | None:
        if previous is None:
            return None
        before = previous.sums.get(_previous_key(key, dimensions, position), {})
        return select_measures(derive(before, definition.vat), measures)

    def row(key: Key, values: dict[str, Decimal | None], before: dict | None) -> AnalyticsRow:
        change, change_pct = change_figures(values, before) if before is not None else (None, None)
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
            share=shares(values, totals_values),
            comparison=before,
            change=change,
            change_pct=change_pct,
            drill=drill(definition, dimensions, key),
        )

    rows = [row(key, values, comparison_of(key)) for key, values in shown]
    others = None
    if folded:
        rest = subtract(total_sums, (merged.sums[key] for key, _ in shown))
        before_rest = (
            select_measures(
                derive(
                    subtract(
                        previous_totals,
                        (
                            previous.sums.get(_previous_key(key, dimensions, position), {})
                            for key, _ in shown
                        ),
                    ),
                    definition.vat,
                ),
                measures,
            )
            if previous is not None
            else None
        )
        others_values = select_measures(derive(rest, definition.vat), measures)
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
    totals_before = (
        select_measures(derive(previous_totals, definition.vat), measures)
        if previous is not None
        else None
    )
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
    return facts.Slice(slice_.dimensions, slice_.grain, filters, start, end, slice_.facility)


def drill(
    definition: AnalyticsQueryRequest, dimensions: Sequence[DimensionKey], key: Key
) -> list[AnalyticsFilter]:
    """The filters that reproduce a row: the request's, narrowed to the row's values (FR-040)."""
    narrowed = {f.dimension: list(f.values) for f in definition.filters}
    for dimension, value in zip(dimensions, key, strict=True):
        narrowed[dimension] = [key_value(value) or '']
    return [AnalyticsFilter(dimension=d, values=v) for d, v in narrowed.items()]
