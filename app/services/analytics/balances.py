"""Balance facts (spec 038, research R1–R5): stock as of the end of any day since the history start.

On hand at the end of day D is Σ `lot_serial_tracking.quantity` dated before D + 1, the sum spec
030's `stock_ledger.on_hand` and kardex opening take, so the two can't diverge (R2). Every moment
is read in two queries: the opening at the first moment, then the movements between moments,
added on in Python.

Everything is computed per product × warehouse pair and then grouped in Python, because velocity,
cover, stock-outs, dead and slow stock are pair-level rules (R5) that no group sum can reproduce.
The facility of a pair is its warehouse's; stock in a transit warehouse belongs to the facility the
transfer is going to, as spec 030's `split_transit` places it.

Value is today's list-0 cost without VAT (R4). A cost of 0 or less, or at least
`settings.placeholder_cost`, is uncosted: such pairs carry no value.
"""

import itertools
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta
from decimal import ROUND_CEILING, ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.config import settings
from app.core.constants import COST_PRICE_LIST_ID
from app.enums import TransactionType
from app.models.core import Warehouse
from app.models.inventory import LotSerialTracking
from app.models.product import Product, ProductPrice, product_label
from app.schemas.analytics import DimensionKey, StockOptions
from app.services import stock_ledger, stock_query_service
from app.services.analytics import facts

ZERO = Decimal(0)
#: The dimensions that read a product's description rather than the pair's own ids.
DESCRIBED = frozenset(
    {DimensionKey.BRAND, DimensionKey.MODEL, DimensionKey.LABEL, DimensionKey.SUPPLIER}
)
#: The ledger types that sell (R5): an order's departure, an itinerary's, a counter pickup.
SALE_TYPES = (
    int(TransactionType.SALES_ORDER),
    int(TransactionType.DELIVERY_ORDER),
    int(TransactionType.DELIVERY_PICKUP),
)
REFUND_TYPE = int(TransactionType.CUSTOMER_REFUND)


def day_end(day: date) -> datetime:
    """The bound "before the end of `day`": midnight of the next day."""
    return datetime.combine(day + timedelta(days=1), time())


def cents(value: Decimal) -> int:
    return int((value * 100).quantize(Decimal(1), rounding=ROUND_HALF_UP))


# ── One pair ──────────────────────────────────────────────────────────────────


@dataclass
class Pair:
    """A product × warehouse at a moment, with every figure R4 and R5 define on it."""

    product: int
    warehouse: int
    facility: int | None
    on_hand: Decimal = ZERO
    #: The last ledger entry up to the moment, of any type.
    last: datetime | None = None
    in_transit: Decimal = ZERO
    #: Today's list-0 cost; None when uncosted.
    cost: Decimal | None = None
    #: Reserved by confirmed orders; None at a past moment (no history, R2).
    reserved: Decimal | None = None
    #: Units out by sale, net of refunds, over the velocity window; None without options.
    sold: Decimal | None = None
    fractional: bool = False
    # Derived by `assess`, with options:
    #: The velocity window's days.
    window: int = 0
    velocity: Decimal = ZERO
    reorder_point: Decimal = ZERO
    suggested: Decimal = ZERO
    stock_out: bool = False
    below_reorder: bool = False
    dead: bool = False
    slow: bool = False

    @property
    def stocked(self) -> bool:
        return self.on_hand > 0

    @property
    def uncosted(self) -> bool:
        return self.stocked and self.cost is None

    @property
    def value_c(self) -> int:
        """Cents of on hand × cost; negative stock adds no value."""
        return cents(self.on_hand * self.cost) if self.stocked and self.cost is not None else 0

    @property
    def available(self) -> Decimal:
        """On hand less today's reservations; at a past moment, on hand (R5)."""
        return self.on_hand - (self.reserved or ZERO)

    @property
    def cover(self) -> Decimal | None:
        """Days of cover: available ÷ velocity, null without sales. Taken as available × window ÷
        units sold, so a threshold compares exactly."""
        sold = self.sold or ZERO
        return self.available * self.window / sold if sold > 0 else None


def assess(pair: Pair, moment: date, options: StockOptions) -> None:
    """R5's pair-level rules at the end of `moment`."""
    sold = pair.sold or ZERO
    pair.window = options.velocity_days
    pair.velocity = sold / options.velocity_days
    available = pair.available
    selling = sold > 0
    pair.reorder_point = sold * (options.lead_days + options.cover_days) / options.velocity_days
    pair.stock_out = selling and available <= 0
    pair.below_reorder = selling and available < pair.reorder_point
    if pair.below_reorder:
        needed = pair.reorder_point - available
        pair.suggested = needed if pair.fractional else needed.to_integral_value(ROUND_CEILING)
    quiet_since = moment - timedelta(days=options.dead_days)
    pair.dead = pair.stocked and (pair.last is None or pair.last.date() <= quiet_since)
    cover = pair.cover
    pair.slow = (
        pair.stocked and not pair.dead and cover is not None and cover > options.slow_cover_days
    )


def columns(pair: Pair, options: StockOptions | None) -> dict[str, Any]:
    """The stock fact's columns for one pair; the engine's `derive` reads their group sums."""
    value_c = pair.value_c
    out: dict[str, Any] = {
        'on_hand': pair.on_hand,
        'in_transit': pair.in_transit,
        'value_c': value_c,
        'stocked': int(pair.stocked),
        'costed': int(pair.stocked and pair.cost is not None),
        'uncosted_pairs': int(pair.uncosted),
        'uncosted_qty': pair.on_hand if pair.uncosted else ZERO,
    }
    if options is not None:
        out |= {
            'velocity': pair.velocity,
            'cover_qty': pair.available,
            'stock_outs': int(pair.stock_out),
            'below': int(pair.below_reorder),
            'suggested': pair.suggested,
            'dead_pairs': int(pair.dead),
            'dead_qty': pair.on_hand if pair.dead else ZERO,
            'dead_value_c': value_c if pair.dead else 0,
            'slow_pairs': int(pair.slow),
            'slow_qty': pair.on_hand if pair.slow else ZERO,
            'slow_value_c': value_c if pair.slow else 0,
        }
    return out


# ── What a pair groups by ─────────────────────────────────────────────────────


@dataclass(frozen=True)
class ProductInfo:
    code: str | None = None
    name: str | None = None
    brand: str | None = None
    model: str | None = None
    supplier: int | None = None
    labels: tuple[int, ...] = ()


def _text(value: str | None) -> tuple[str | None, str | None]:
    """Brand and model group by their trimmed, case-insensitive value, as the SQL facts do."""
    trimmed = (value or '').strip()
    return (trimmed.lower(), trimmed) if trimmed else (None, None)


def keys_of(pair: Pair, dimension: DimensionKey, info: ProductInfo) -> list[tuple[Any, str | None]]:
    """`(key, label)` of each group the pair falls in; a product with two labels is in both."""
    match dimension:
        case DimensionKey.FACILITY:
            return [(pair.facility, None)]
        case DimensionKey.WAREHOUSE:
            return [(pair.warehouse, None)]
        case DimensionKey.PRODUCT:
            return [(pair.product, None)]
        case DimensionKey.SUPPLIER:
            return [(info.supplier, None)]
        case DimensionKey.BRAND:
            return [_text(info.brand)]
        case DimensionKey.MODEL:
            return [_text(info.model)]
        case DimensionKey.LABEL:
            return [(label, None) for label in info.labels] or [(None, None)]
    return [(None, None)]


def matches(pair: Pair, filters: Mapping[DimensionKey, Sequence[Any]], info: ProductInfo) -> bool:
    return all(
        any(key in values for key, _ in keys_of(pair, dimension, info))
        for dimension, values in filters.items()
    )


# ── Reading the ledger ────────────────────────────────────────────────────────


@dataclass
class Ledger:
    """The pairs at each moment, and what they group by."""

    pairs: dict[date, list[Pair]] = field(default_factory=dict)
    products: dict[int, ProductInfo] = field(default_factory=dict)


def _pushed(query, filters: Mapping[DimensionKey, Sequence[Any]]):  # noqa: ANN001, ANN202
    """Product and warehouse filters narrow the ledger reads themselves."""
    for dimension, column in (
        (DimensionKey.PRODUCT, LotSerialTracking.product),
        (DimensionKey.WAREHOUSE, LotSerialTracking.warehouse),
    ):
        values = filters.get(dimension)
        if values is not None and None not in values:
            query = query.where(column.in_(list(values)))
    return query


def _scoped(query, facility: int | None, filters: Mapping[DimensionKey, Sequence[Any]]):  # noqa: ANN001, ANN202
    if facility is not None:
        query = query.join(Warehouse, Warehouse.warehouse_id == LotSerialTracking.warehouse).where(
            Warehouse.facility == facility
        )
    return _pushed(query, filters)


Balances = dict[tuple[int, int], tuple[Decimal, datetime | None]]


async def _balances(
    db: AsyncSession,
    moments: Sequence[date],
    facility: int | None,
    filters: Mapping[DimensionKey, Sequence[Any]],
) -> list[Balances]:
    """`(on hand, last entry)` per pair at the end of each moment: the opening at the first, then
    each later one adds the entries since the one before (R2)."""
    entry = LotSerialTracking
    pair = (entry.product, entry.warehouse)
    opening = _scoped(
        select(*pair, func.sum(entry.quantity), func.max(entry.date))
        .where(entry.date < day_end(moments[0]))
        .group_by(*pair),
        facility,
        filters,
    )
    state: Balances = {
        (p, w): (Decimal(q or 0), _datetime(last))
        for p, w, q, last in (await db.execute(opening)).all()
    }
    result = [dict(state)]
    if len(moments) == 1:
        return result
    bounds = [day_end(m) for m in moments]
    bucket = case(*((entry.date < bound, i) for i, bound in enumerate(bounds[1:], start=1)))
    movements = _scoped(
        select(*pair, bucket.label('bucket'), func.sum(entry.quantity), func.max(entry.date))
        .where(entry.date >= bounds[0], entry.date < bounds[-1])
        .group_by(*pair, bucket),
        facility,
        filters,
    )
    by_bucket: dict[int, list[tuple]] = {}
    for p, w, index, q, last in (await db.execute(movements)).all():
        by_bucket.setdefault(int(index), []).append(((p, w), Decimal(q or 0), _datetime(last)))
    for index in range(1, len(moments)):
        for key, q, last in by_bucket.get(index, []):
            held, before = state.get(key, (ZERO, None))
            state[key] = (held + q, max((d for d in (before, last) if d), default=None))
        result.append(dict(state))
    return result


def _datetime(value: Any) -> datetime | None:
    """SQLite hands `MAX(date)` back as text."""
    if value is None or isinstance(value, datetime):
        return value
    return datetime.fromisoformat(str(value))


async def _sold(
    db: AsyncSession,
    moment: date,
    days: int,
    facility: int | None,
    filters: Mapping[DimensionKey, Sequence[Any]],
) -> tuple[dict[tuple[int, int], Decimal], set[int]]:
    """Units out by sale, net of refunds, per pair over the `days` ending with `moment` (R5); and
    the products sold in fractions there, whose suggested quantity is not rounded up."""
    entry = LotSerialTracking
    selling = entry.source.in_(SALE_TYPES)
    query = _scoped(
        select(
            entry.product,
            entry.warehouse,
            -func.sum(entry.quantity),
            func.max(
                case((selling & (entry.quantity != func.round(entry.quantity, 0)), 1), else_=0)
            ),
        )
        .where(
            entry.source.in_((*SALE_TYPES, REFUND_TYPE)),
            entry.date >= day_end(moment) - timedelta(days=days),
            entry.date < day_end(moment),
        )
        .group_by(entry.product, entry.warehouse),
        facility,
        filters,
    )
    sold: dict[tuple[int, int], Decimal] = {}
    fractional: set[int] = set()
    for p, w, q, fraction in (await db.execute(query)).all():
        sold[(p, w)] = Decimal(q or 0)
        if fraction:
            fractional.add(p)
    return sold, fractional


async def _products(db: AsyncSession, ids: Iterable[int]) -> dict[int, ProductInfo]:
    ids = sorted(set(ids))
    found: dict[int, ProductInfo] = {}
    labels: dict[int, list[int]] = {}
    for start in range(0, len(ids), 500):
        chunk = ids[start : start + 500]
        for p, label in (
            await db.execute(
                select(product_label.c.product, product_label.c.label).where(
                    product_label.c.product.in_(chunk)
                )
            )
        ).all():
            labels.setdefault(p, []).append(label)
        rows = await db.execute(
            select(
                Product.product_id, Product.code, Product.name, Product.brand, Product.model,
                Product.supplier,
            ).where(Product.product_id.in_(chunk))
        )  # fmt: skip
        for p, code, name, brand, model, supplier in rows.all():
            found[p] = ProductInfo(
                code, name, brand, model, supplier, tuple(sorted(labels.get(p, [])))
            )
    return found


async def costs(db: AsyncSession, ids: Iterable[int]) -> dict[int, Decimal]:
    """Today's costed list-0 cost per product (R4); uncosted products are absent."""
    ids = sorted(set(ids))
    found: dict[int, Decimal] = {}
    for start in range(0, len(ids), 500):
        rows = await db.execute(
            select(ProductPrice.product, ProductPrice.price).where(
                ProductPrice.price_list == COST_PRICE_LIST_ID,
                ProductPrice.product.in_(ids[start : start + 500]),
            )
        )
        for p, price in rows.all():
            if price is not None and 0 < price < settings.placeholder_cost:
                found[p] = Decimal(price)
    return found


async def _warehouses(db: AsyncSession) -> dict[int, tuple[int, bool]]:
    rows = await db.execute(
        select(Warehouse.warehouse_id, Warehouse.facility, Warehouse.in_transit)
    )
    return {w: (f, bool(transit)) for w, f, transit in rows.all()}


async def ledger(
    db: AsyncSession,
    moments: Sequence[date],
    *,
    facility: int | None,
    filters: Mapping[DimensionKey, Sequence[Any]],
    options: StockOptions | None,
    reservations: bool = False,
    described: bool = True,
) -> Ledger:
    """Every pair at each moment, filtered, with cost; with `options`, assessed by R5. Today's
    reservations are read for a moment that is today, when `options` or `reservations` ask.
    `described` loads the products' code, name, brand, model, supplier and labels."""
    today = local_now().date()
    warehouses = await _warehouses(db)
    transit = {w: f for w, (f, moving) in warehouses.items() if moving}
    states = await _balances(db, moments, facility, filters)
    reserved: dict[tuple[int, int], Decimal] = {}
    reads_reserved = today in moments and (options is not None or reservations)
    if reads_reserved:
        held = {p for state in states for p, _ in state}
        reserved = {
            key: Decimal(q)
            for key, q in (await stock_ledger.reserved_by_warehouse(db, products=held)).items()
            if facility is None or warehouses.get(key[1], (None,))[0] == facility
        }
    products = {p for state in states for p, _ in state} | {p for p, _ in reserved}
    found = Ledger(products=await _products(db, products) if described else {})
    cost = await costs(db, products)

    for moment, state in zip(moments, states, strict=True):
        sold, fractional = (
            await _sold(db, moment, options.velocity_days, facility, filters)
            if options is not None
            else ({}, set())
        )
        now = moment == today and reads_reserved
        keys = set(state) | set(sold) | (set(reserved) if now else set())
        pairs = []
        for p, w in keys:
            held, last = state.get((p, w), (ZERO, None))
            pair = Pair(
                product=p,
                warehouse=w,
                facility=warehouses.get(w, (None, False))[0],
                on_hand=held,
                last=last,
                cost=cost.get(p),
                reserved=reserved.get((p, w), ZERO) if now else None,
                sold=sold.get((p, w), ZERO) if options is not None else None,
                fractional=p in fractional,
            )
            if not matches(pair, filters, found.products.get(p, ProductInfo())):
                continue
            if options is not None:
                assess(pair, moment, options)
            pairs.append(pair)
        _place_transit(pairs, transit)
        found.pairs[moment] = pairs
    return found


def _place_transit(pairs: Sequence[Pair], transit: Mapping[int, int]) -> None:
    """Mark what sits in transit warehouses, with spec 030's rule: its facility is the transit
    warehouse's, the one the transfer is going to."""
    by_product: dict[int, dict[int, Pair]] = {}
    for pair in pairs:
        by_product.setdefault(pair.product, {})[pair.warehouse] = pair
    for held in by_product.values():
        _, moving = stock_query_service.split_transit(
            {w: pair.on_hand for w, pair in held.items()}, dict(transit)
        )
        for stock in moving:
            held[stock.warehouse].in_transit = stock.on_hand
            held[stock.warehouse].facility = stock.facility


# ── The facts ─────────────────────────────────────────────────────────────────


def group(
    pairs: Iterable[Pair],
    dimensions: Sequence[DimensionKey],
    products: Mapping[int, ProductInfo],
    figures: Any,
) -> dict[tuple, tuple[list[str | None], dict[str, Any]]]:
    """Sum `figures(pair)` per group of `dimensions`, with the groups' display text."""
    groups: dict[tuple, tuple[list[str | None], dict[str, Any]]] = {}
    for pair in pairs:
        values = figures(pair)
        if not any(values.values()):
            continue
        info = products.get(pair.product, ProductInfo())
        for combo in itertools.product(*(keys_of(pair, d, info) for d in dimensions)):
            key = tuple(k for k, _ in combo)
            labels, sums = groups.setdefault(key, ([None] * len(dimensions), {}))
            for index, (_, label) in enumerate(combo):
                if label is not None and (labels[index] is None or label > labels[index]):
                    labels[index] = label
            for name, value in values.items():
                sums[name] = sums.get(name, 0) + value
    return groups


def _rows(
    moment: date,
    groups: Mapping[tuple, tuple[list[str | None], dict[str, Any]]],
    width: int,
    empty: dict[str, Any],
) -> list[dict[str, Any]]:
    if not width and not groups:
        groups = {(): ([], dict(empty))}
    rows = []
    for key, (labels, sums) in groups.items():
        row: dict[str, Any] = {'moment': moment}
        row |= {f'k{i}': value for i, value in enumerate(key)}
        row |= {f'l{i}': label for i, label in enumerate(labels)}
        rows.append(row | sums)
    return rows


async def stock(db: AsyncSession, slice_: facts.Slice) -> list[dict[str, Any]]:
    """The stock fact: on hand, transit, value and coverage at each moment; with options, the R5
    measures. One row per moment and group."""
    options = slice_.stock_options
    found = await ledger(
        db,
        sorted(slice_.moments),
        facility=slice_.facility,
        filters=slice_.filters,
        options=options,
        described=bool(DESCRIBED & {*slice_.dimensions, *slice_.filters}),
    )
    rows: list[dict[str, Any]] = []
    for moment, pairs in found.pairs.items():
        groups = group(pairs, slice_.dimensions, found.products, lambda p: columns(p, options))
        rows += _rows(moment, groups, len(slice_.dimensions), {})
    return rows


async def stock_today(db: AsyncSession, slice_: facts.Slice) -> list[dict[str, Any]]:
    """Reserved and available now (R2): rows only for today's moment. Reservations have no
    history, so a past moment has no row and its measures are null."""
    today = local_now().date()
    if today not in slice_.moments:
        return []
    found = await ledger(
        db,
        [today],
        facility=slice_.facility,
        filters=slice_.filters,
        options=None,
        reservations=True,
        described=bool(DESCRIBED & {*slice_.dimensions, *slice_.filters}),
    )

    def figures(pair: Pair) -> dict[str, Any]:
        return {'reserved': pair.reserved or ZERO, 'available': pair.available}

    groups = group(found.pairs[today], slice_.dimensions, found.products, figures)
    return _rows(today, groups, len(slice_.dimensions), {'reserved': ZERO, 'available': ZERO})
