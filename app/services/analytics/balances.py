"""Balance facts (spec 038, research R1–R5, R7–R10): stock as of the end of any day since the
history start, and receivables and payables (at the end of this file).

On hand at the end of day D is Σ `lot_serial_tracking.quantity` dated before D + 1, the sum spec
030's `stock_ledger.on_hand` and kardex opening take, so the two can't diverge (R2). Every moment
is read in two queries: the opening at the first moment, then the movements between moments,
added on in Python. The opening starts from a per-process checkpoint of yesterday's sums, so it
reads only the entries between the checkpoint and the moment (`checkpoint`, `_opening`).

Everything is computed per product × warehouse pair and then grouped in Python, because velocity,
cover, stock-outs, dead and slow stock are pair-level rules (R5) that no group sum can reproduce.
The facility of a pair is its warehouse's; stock in a transit warehouse belongs to the facility the
transfer is going to, as spec 030's `split_transit` places it.

Value is today's list-0 cost without VAT (R4). A cost of 0 or less, or at least
`settings.placeholder_cost`, is uncosted: such pairs carry no value.
"""

import asyncio
import itertools
import weakref
from collections.abc import Hashable, Iterable, Mapping, Sequence
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
from app.models.analytics import ReceivableSnapshot
from app.models.core import Warehouse
from app.models.customer import Customer
from app.models.inventory import LotSerialTracking
from app.models.product import Product, ProductPrice, product_label
from app.models.purchases import PurchaseOrderDetail
from app.schemas.analytics import DimensionKey, StockOptions
from app.services import accounts_service, sales_order_service, stock_ledger, stock_query_service
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


Pairs = tuple[int, int]
Balances = dict[Pairs, tuple[Decimal, datetime | None]]
#: A bucket's movements: `(pair, Σ quantity, last entry)` of each pair that moved in it.
Moved = list[tuple[Pairs, Decimal, datetime | None]]


def _opening_query(
    until: datetime, facility: int | None, filters: Mapping[DimensionKey, Sequence[Any]]
):  # noqa: ANN202
    """`(product, warehouse, Σ quantity, last date)` of the entries dated before `until`."""
    entry = LotSerialTracking
    return _scoped(
        select(entry.product, entry.warehouse, func.sum(entry.quantity), func.max(entry.date))
        .where(entry.date < until)
        .group_by(entry.product, entry.warehouse),
        facility,
        filters,
    )


def _movements_query(
    moments: Sequence[date], facility: int | None, filters: Mapping[DimensionKey, Sequence[Any]]
):  # noqa: ANN202
    """The entries after the first moment up to the last, grouped by pair and by the moment that
    ends their bucket (1 for the second moment, and so on)."""
    entry = LotSerialTracking
    bounds = [day_end(m) for m in moments]
    bucket = case(*((entry.date < bound, i) for i, bound in enumerate(bounds[1:], start=1)))
    return _scoped(
        select(
            entry.product, entry.warehouse, bucket.label('bucket'), func.sum(entry.quantity),
            func.max(entry.date),
        )
        .where(entry.date >= bounds[0], entry.date < bounds[-1])
        .group_by(entry.product, entry.warehouse, bucket),
        facility,
        filters,
    )  # fmt: skip


def _delta_query(
    condition: Any, facility: int | None, filters: Mapping[DimensionKey, Sequence[Any]]
):  # noqa: ANN202
    """`(product, warehouse, Σ quantity, last date)` of the entries matching `condition`."""
    entry = LotSerialTracking
    return _scoped(
        select(entry.product, entry.warehouse, func.sum(entry.quantity), func.max(entry.date))
        .where(condition)
        .group_by(entry.product, entry.warehouse),
        facility,
        filters,
    )


# ── The daily checkpoint (research R2, R18) ───────────────────────────────────


@dataclass(frozen=True)
class Checkpoint:
    """Every pair's ledger sum and last entry over the entries with an id up to `max_id` dated
    before the end of `day` (yesterday, business time), in every warehouse. A read adds what came
    after: the entries dated after `day`, and those with a higher id dated on or before it (a
    back-dated entry), so the checkpoint can't go stale. The ledger is append-only (spec 030)."""

    day: date
    max_id: int
    sums: Balances


#: One checkpoint per database engine, per process; rebuilt on the first read of each day.
_checkpoints: weakref.WeakKeyDictionary[Any, Checkpoint] = weakref.WeakKeyDictionary()
_building: weakref.WeakKeyDictionary[Any, asyncio.Lock] = weakref.WeakKeyDictionary()


async def checkpoint(db: AsyncSession) -> Checkpoint | None:
    """The engine's checkpoint at the end of yesterday, built when missing or a day old."""
    bind = getattr(db, 'bind', None)
    engine = getattr(bind, 'sync_engine', None)
    if engine is None:
        return None
    day = local_now().date() - timedelta(days=1)
    found = _checkpoints.get(engine)
    if found is not None and found.day == day:
        return found
    lock = _building.setdefault(engine, asyncio.Lock())
    async with lock:
        found = _checkpoints.get(engine)
        if found is None or found.day != day:
            entry = LotSerialTracking
            max_id = (
                await db.execute(select(func.coalesce(func.max(entry.lot_serial_tracking_id), 0)))
            ).scalar_one()
            covered = (entry.lot_serial_tracking_id <= max_id) & (entry.date < day_end(day))
            rows = (await db.execute(_delta_query(covered, None, {}))).all()
            sums = {(p, w): (Decimal(q or 0), _datetime(last)) for p, w, q, last in rows}
            found = Checkpoint(day, int(max_id), sums)
            _checkpoints[engine] = found
    return found


def _kept(
    key: Pairs,
    facility: int | None,
    filters: Mapping[DimensionKey, Sequence[Any]],
    warehouses: Mapping[int, tuple[int, bool]],
) -> bool:
    """What `_scoped` keeps in SQL, applied to a checkpoint's pairs."""
    product, warehouse = key
    if facility is not None and warehouses.get(warehouse, (None, False))[0] != facility:
        return False
    for dimension, value in ((DimensionKey.PRODUCT, product), (DimensionKey.WAREHOUSE, warehouse)):
        values = filters.get(dimension)
        if values is not None and None not in values and value not in values:
            return False
    return True


async def _opening(
    db: AsyncSession,
    moment: date,
    facility: int | None,
    filters: Mapping[DimensionKey, Sequence[Any]],
    warehouses: Mapping[int, tuple[int, bool]],
    *,
    last: bool,
) -> Balances:
    """`(on hand, last entry)` per pair at the end of `moment` (R2): from the checkpoint plus or
    minus the entries between it and the moment, else one sum over the whole ledger.

    A last entry can't be taken back: before the checkpoint's day, the pairs that moved since the
    moment get theirs read again, through their products (`_last_entries`); a read that doesn't
    need it leaves it unset there."""
    until = day_end(moment)
    found = await checkpoint(db)
    if found is None:
        return await _whole(db, until, facility, filters)

    entry = LotSerialTracking
    covered_end = day_end(found.day)
    later = entry.lot_serial_tracking_id > found.max_id
    keeps_last = last or moment >= found.day
    state: Balances = {
        key: (held, at if keeps_last else None)
        for key, (held, at) in found.sums.items()
        if _kept(key, facility, filters, warehouses)
    }
    changes = [(1, later & (entry.date < min(covered_end, until)))]
    if moment > found.day:
        changes.append((1, (entry.date >= covered_end) & (entry.date < until)))
    elif moment < found.day:
        covered = entry.lot_serial_tracking_id <= found.max_id
        changes.append((-1, covered & (entry.date >= until) & (entry.date < covered_end)))
    since: set[Pairs] = set()  # the pairs with an entry after the moment
    for sign, condition in changes:
        for p, w, q, at in (await db.execute(_delta_query(condition, facility, filters))).all():
            held, before = state.get((p, w), (ZERO, None))
            moved = _datetime(at) if sign > 0 and keeps_last else None
            state[(p, w)] = (held + sign * Decimal(q or 0), _latest(before, moved))
            if sign < 0:
                since.add((p, w))
    if last and since:
        products = {p for p, _ in since}
        if len(products) > BULK:
            return await _whole(db, until, facility, filters)
        lasts = await _last_entries(db, products, until)
        for key in since:
            if key in lasts:
                state[key] = (state[key][0], lasts[key])
            else:
                del state[key]  # no entry up to the moment: the pair didn't exist yet
    return state


async def _whole(
    db: AsyncSession,
    until: datetime,
    facility: int | None,
    filters: Mapping[DimensionKey, Sequence[Any]],
) -> Balances:
    """One sum over the whole ledger before `until`."""
    rows = (await db.execute(_opening_query(until, facility, filters))).all()
    return {(p, w): (Decimal(q or 0), _datetime(at)) for p, w, q, at in rows}


async def _last_entries(
    db: AsyncSession, products: set[int], until: datetime
) -> dict[Pairs, datetime | None]:
    """Each pair's last entry before `until`, for a few products: read through the product's index
    rather than the date range."""
    entry = LotSerialTracking
    rows = await db.execute(
        select(entry.product, entry.warehouse, func.max(entry.date))
        .where(entry.product.in_(sorted(products)), entry.date < until)
        .group_by(entry.product, entry.warehouse)
    )
    return {(p, w): _datetime(at) for p, w, at in rows.all()}


def _latest(*moments: datetime | None) -> datetime | None:
    return max((m for m in moments if m is not None), default=None)


async def _read(
    db: AsyncSession,
    moments: Sequence[date],
    facility: int | None,
    filters: Mapping[DimensionKey, Sequence[Any]],
    warehouses: Mapping[int, tuple[int, bool]],
    *,
    last: bool,
) -> tuple[Balances, dict[int, Moved]]:
    """The balances at the first moment, and each later moment's movements since the one before
    (R2): two reads whatever the number of moments."""
    opening = await _opening(db, moments[0], facility, filters, warehouses, last=last)
    moved: dict[int, Moved] = {}
    if len(moments) > 1:
        rows = (await db.execute(_movements_query(moments, facility, filters))).all()
        for p, w, index, q, at in rows:
            moved.setdefault(int(index), []).append(((p, w), Decimal(q or 0), _datetime(at)))
    return opening, moved


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


#: Above this many products a read takes the whole table and keeps what it needs: one statement
#: instead of a round trip per 500 ids (spec 038, R18).
BULK = 500


def _among(query, column, ids: set[int]):  # noqa: ANN001, ANN202
    """`query` narrowed to `ids` when they are few; all of it otherwise (filtered in Python)."""
    return query.where(column.in_(sorted(ids))) if len(ids) <= BULK else query


async def _products(db: AsyncSession, ids: Iterable[int]) -> dict[int, ProductInfo]:
    ids = set(ids)
    if not ids:
        return {}
    labels: dict[int, list[int]] = {}
    found_labels = await db.execute(
        _among(select(product_label.c.product, product_label.c.label), product_label.c.product, ids)
    )
    for p, label in found_labels.all():
        if p in ids:
            labels.setdefault(p, []).append(label)
    rows = await db.execute(
        _among(
            select(
                Product.product_id, Product.code, Product.name, Product.brand, Product.model,
                Product.supplier,
            ),
            Product.product_id,
            ids,
        )
    )  # fmt: skip
    return {
        p: ProductInfo(code, name, brand, model, supplier, tuple(sorted(labels.get(p, []))))
        for p, code, name, brand, model, supplier in rows.all()
        if p in ids
    }


async def costs(db: AsyncSession, ids: Iterable[int]) -> dict[int, Decimal]:
    """Today's costed list-0 cost per product (R4); uncosted products are absent."""
    ids = set(ids)
    if not ids:
        return {}
    rows = await db.execute(
        _among(
            select(ProductPrice.product, ProductPrice.price).where(
                ProductPrice.price_list == COST_PRICE_LIST_ID
            ),
            ProductPrice.product,
            ids,
        )
    )
    return {
        p: Decimal(price)
        for p, price in rows.all()
        if p in ids and price is not None and 0 < price < settings.placeholder_cost
    }


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
    state, moved = await _read(db, moments, facility, filters, warehouses, last=options is not None)
    held = set(state) | {key for bucket in moved.values() for key, _, _ in bucket}
    reserved: dict[tuple[int, int], Decimal] = {}
    reads_reserved = today in moments and (options is not None or reservations)
    if reads_reserved:
        reserved = {
            key: Decimal(q)
            for key, q in (
                await stock_ledger.reserved_by_warehouse(db, products={p for p, _ in held})
            ).items()
            if facility is None or warehouses.get(key[1], (None,))[0] == facility
        }
    products = {p for p, _ in held} | {p for p, _ in reserved}
    found = Ledger(products=await _products(db, products) if described else {})
    cost = await costs(db, products)

    for index, moment in enumerate(moments):
        for key, q, at in moved.get(index, []):
            before, last = state.get(key, (ZERO, None))
            state[key] = (before + q, _latest(last, at))
        sold, fractional = (
            await _sold(db, moment, options.velocity_days, facility, filters)
            if options is not None
            else ({}, set())
        )
        now = moment == today and reads_reserved
        keys = set(state) | set(sold) | (set(reserved) if now else set())
        pairs = []
        for p, w in keys:
            on_hand, last = state.get((p, w), (ZERO, None))
            if not on_hand and (p, w) not in sold and not (now and (p, w) in reserved):
                continue  # nothing held, sold or reserved: no figure to give
            pair = Pair(
                product=p,
                warehouse=w,
                facility=warehouses.get(w, (None, False))[0],
                on_hand=on_hand,
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
    figures: Iterable[tuple[Pair, dict[str, Any]]],
    dimensions: Sequence[DimensionKey],
    products: Mapping[int, ProductInfo],
) -> dict[tuple, tuple[list[str | None], dict[str, Any]]]:
    """Sum each pair's figures per group of `dimensions`, with the groups' display text."""
    groups: dict[tuple, tuple[list[str | None], dict[str, Any]]] = {}
    for pair, values in figures:
        if any(values.values()):
            _accumulate(groups, pair, values, dimensions, products, 1)
    return groups


def _accumulate(
    groups: dict[tuple, tuple[list[str | None], dict[str, Any]]],
    pair: Pair,
    values: Mapping[str, Any],
    dimensions: Sequence[DimensionKey],
    products: Mapping[int, ProductInfo],
    sign: int,
) -> None:
    """Add `sign` × `values` to each group of `dimensions` the pair falls in."""
    for sums in _groups_of(groups, pair, dimensions, products):
        for name, value in values.items():
            sums[name] = sums.get(name, 0) + sign * value


def _groups_of(
    groups: dict[tuple, tuple[list[str | None], dict[str, Any]]],
    pair: Pair,
    dimensions: Sequence[DimensionKey],
    products: Mapping[int, ProductInfo],
) -> list[dict[str, Any]]:
    """The sums of each group of `dimensions` the pair falls in, made when new, with their
    display text."""
    info = products.get(pair.product, ProductInfo())
    found = []
    for combo in itertools.product(*(keys_of(pair, d, info) for d in dimensions)):
        labels, sums = groups.setdefault(tuple(k for k, _ in combo), ([None] * len(dimensions), {}))
        for index, (_, label) in enumerate(combo):
            if label is not None and (labels[index] is None or label > labels[index]):
                labels[index] = label
        found.append(sums)
    return found


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
    return (await stock_many(db, [slice_]))[0]


def share_key(slice_: facts.Slice) -> Hashable:
    """Stock slices with the same key read the ledger once (`stock_many`): they differ only in
    their dimensions and moments."""
    filters = tuple(sorted((d.value, tuple(v)) for d, v in slice_.filters.items()))
    return (slice_.facility, filters, slice_.stock_options)


async def stock_many(db: AsyncSession, slices: Sequence[facts.Slice]) -> list[list[dict[str, Any]]]:
    """The stock fact for several slices of one `share_key`, from one read of the ledger over all
    their moments, grouped by each one's dimensions (spec 038, R18): the dashboard's stock value
    now and in the comparison period, and its value by facility and in total.

    R5's rules depend on the moment itself (the windows move), so with options every pair is
    assessed at every moment asked, which is one moment on the dashboard and the detail. Without
    them a pair's columns depend only on its on hand, so each later moment updates only the pairs
    that moved and moves their groups' sums by the difference (`Groups`)."""
    first = slices[0]
    options = first.stock_options
    moments = sorted({m for s in slices for m in s.moments})
    shapes = list(dict.fromkeys(s.dimensions for s in slices))
    asked = {*(d for shape in shapes for d in shape), *first.filters}
    described = bool(DESCRIBED & asked)
    rows: dict[tuple[DimensionKey, ...], list[dict[str, Any]]] = {shape: [] for shape in shapes}

    if options is not None:
        found = await ledger(
            db, moments, facility=first.facility, filters=first.filters, options=options,
            described=described,
        )  # fmt: skip
        for moment, pairs in found.pairs.items():
            figures = [(pair, columns(pair, options)) for pair in pairs]
            for shape in shapes:
                groups = group(figures, shape, found.products)
                rows[shape] += _rows(moment, groups, len(shape), {})
    else:
        await _incremental(db, first, moments, shapes, described, rows)
    return [[row for row in rows[s.dimensions] if row['moment'] in s.moments] for s in slices]


async def _incremental(
    db: AsyncSession,
    first: facts.Slice,
    moments: Sequence[date],
    shapes: Sequence[tuple[DimensionKey, ...]],
    described: bool,
    rows: dict[tuple[DimensionKey, ...], list[dict[str, Any]]],
) -> None:
    """Without options: every pair at the first moment, then only the pairs that moved."""
    warehouses = await _warehouses(db)
    transit = {w: f for w, (f, moving) in warehouses.items() if moving}
    opening, moved = await _read(db, moments, first.facility, first.filters, warehouses, last=False)
    keys = set(opening) | {key for bucket in moved.values() for key, _, _ in bucket}
    products = {p for p, _ in keys}
    info = await _products(db, products) if described else {}
    cost = await costs(db, products)
    groups = Groups(shapes, info)
    pairs: dict[Pairs, Pair | None] = {}

    def pair_of(key: Pairs) -> Pair | None:
        """The pair, made at its first appearance; None when the filters leave it out."""
        if key not in pairs:
            p, w = key
            made = Pair(p, w, warehouses.get(w, (None, False))[0], cost=cost.get(p))
            found = info.get(p, ProductInfo())
            pairs[key] = made if matches(made, first.filters, found) else None
        return pairs[key]

    def place(pair: Pair, on_hand: Decimal) -> None:
        pair.on_hand = on_hand
        if pair.warehouse in transit:
            pair.in_transit = ZERO
            _place_transit([pair], transit)

    for key, (on_hand, _) in opening.items():
        # A pair at zero adds nothing; it is made when it first moves.
        if on_hand and (pair := pair_of(key)) is not None:
            place(pair, on_hand)
            groups.move(pair)
    groups.emit(moments[0], rows)
    for index, moment in enumerate(moments[1:], start=1):
        for key, q, _ in moved.get(index, []):
            if (pair := pair_of(key)) is not None:
                place(pair, pair.on_hand + q)
                groups.move(pair)
        groups.emit(moment, rows)


class Groups:
    """Running sums of `columns` per group, for each set of dimensions asked, moved pair by pair: a
    pair that moves moves its groups by the difference between its new figures and its last, so
    the non-linear rules (no value for negative stock or an uncosted product, the pair counts)
    hold exactly. Each pair's groups are found once."""

    def __init__(
        self, shapes: Sequence[tuple[DimensionKey, ...]], products: Mapping[int, ProductInfo]
    ) -> None:
        self.products = products
        self.sums: dict[tuple[DimensionKey, ...], dict] = {shape: {} for shape in shapes}
        self.figures: dict[Pairs, dict[str, Any]] = {}
        self.targets: dict[Pairs, list[dict[str, Any]]] = {}

    def move(self, pair: Pair) -> None:
        """Bring the pair's groups to its figures now."""
        key = (pair.product, pair.warehouse)
        after = columns(pair, None)
        before = self.figures.get(key)
        self.figures[key] = after
        delta = after if before is None else {k: v - before[k] for k, v in after.items()}
        if not any(delta.values()):
            return
        for sums in self._targets(pair, key):
            for name, value in delta.items():
                sums[name] = sums.get(name, 0) + value

    def _targets(self, pair: Pair, key: Pairs) -> list[dict[str, Any]]:
        """The sums of every group the pair falls in, made (with their labels) the first time."""
        if key not in self.targets:
            found = []
            for shape, groups in self.sums.items():
                found += _groups_of(groups, pair, shape, self.products)
            self.targets[key] = found
        return self.targets[key]

    def emit(self, moment: date, rows: dict[tuple[DimensionKey, ...], list]) -> None:
        """The groups that hold anything at `moment`, as the stock fact's rows."""
        for shape, sums in self.sums.items():
            held = {key: group for key, group in sums.items() if any(group[1].values())}
            rows[shape] += _rows(moment, held, len(shape), {})


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

    pairs = [(pair, figures(pair)) for pair in found.pairs[today]]
    groups = group(pairs, slice_.dimensions, found.products)
    return _rows(today, groups, len(slice_.dimensions), {'reserved': ZERO, 'available': ZERO})


# ── Receivables and payables (research R7–R10) ────────────────────────────────


@dataclass(frozen=True)
class Open:
    """An open document at a moment: a credit order or a purchase order, as the receivable and
    payable facts group it. `balance` is in the document's currency."""

    id: int
    party: int | None
    facility: int | None
    salesperson: int | None
    sales_agent: int | None
    due_date: datetime | None
    exchange_rate: Decimal
    balance: Decimal
    credit_limit: Decimal | None = None

    @property
    def base(self) -> Decimal:
        """The balance in base currency, as spec 033's `summarize` sums it."""
        return self.balance * self.exchange_rate


def read_day(moment: date, today: date) -> date:
    """The day a receivable at the end of `moment` is read at: today live, else the stored
    month-end on or before it (R8)."""
    return moment if moment == today else facts.month_end_on_or_before(moment)


def due_bucket(due: datetime | None, day: date) -> str | None:
    """The forecast's buckets (R9): overdue, or the days until the due date."""
    if due is None:
        return None
    days = (due.date() - day).days
    if days < 0:
        return 'overdue'
    if days <= 7:
        return '1-7'
    if days <= 30:
        return '8-30'
    if days <= 60:
        return '31-60'
    return '60+'


def document_key(document: Open, dimension: DimensionKey, day: date) -> Any:
    """The group of `document` by `dimension`, aged at the end of `day`."""
    match dimension:
        case DimensionKey.FACILITY:
            return document.facility
        case DimensionKey.CUSTOMER | DimensionKey.SUPPLIER:
            return document.party
        case DimensionKey.SALESPERSON:
            return document.salesperson
        case DimensionKey.SALES_AGENT:
            return document.sales_agent
        case DimensionKey.PAYMENT_TERMS:
            return facts.CREDIT  # open credit orders are all credit-terms orders
        case DimensionKey.AGING_BUCKET:
            days = accounts_service.days_past_due(document.due_date, day)
            return accounts_service.bucket(days)
        case DimensionKey.DUE_BUCKET:
            return due_bucket(document.due_date, day)
    return None


def selected(
    documents: Iterable[Open], filters: Mapping[DimensionKey, Sequence[Any]], day: date
) -> list[Open]:
    return [
        document
        for document in documents
        if all(document_key(document, d, day) in values for d, values in filters.items())
    ]


async def receivables_at(
    db: AsyncSession, days: Iterable[date], facility: int | None
) -> dict[date, list[Open]]:
    """The open credit orders at the end of each of `days` (each a `read_day`): today's from spec
    033's rule, a month-end's from its stored snapshot (none when it isn't written)."""
    today = local_now().date()
    days = set(days)
    found: dict[date, list[Open]] = {day: [] for day in days}
    stored = sorted(days - {today})
    if stored:
        query = select(ReceivableSnapshot).where(ReceivableSnapshot.month_end.in_(stored))
        if facility is not None:
            query = query.where(ReceivableSnapshot.facility == facility)
        for row in (await db.execute(query)).scalars():
            found[row.month_end].append(
                Open(
                    row.sales_order, row.customer, row.facility, row.salesperson, row.sales_agent,
                    row.due_date, row.exchange_rate, row.balance, row.credit_limit,
                )
            )  # fmt: skip
    if today in days:
        orders = await sales_order_service.open_credit_orders(db, facility=facility)
        customers = {order.customer for order, _ in orders}
        people: dict[int, tuple[int | None, Decimal | None]] = {}
        if customers:
            rows = await db.execute(
                select(Customer.customer_id, Customer.salesperson, Customer.credit_limit).where(
                    Customer.customer_id.in_(customers)
                )
            )
            people = {c: (agent, limit) for c, agent, limit in rows.all()}
        for order, balance in orders:
            agent, limit = people.get(order.customer, (None, None))
            found[today].append(
                Open(
                    order.sales_order_id, order.customer, order.facility, order.salesperson,
                    agent, order.due_date, order.exchange_rate, balance.balance, limit,
                )
            )  # fmt: skip
    return found


def receivable_columns(documents: Sequence[Open], day: date) -> dict[str, Any]:
    """The receivable fact's columns for a group, in cents of base currency summed before rounding,
    as `summarize` does. A customer's limit counts once per group; utilization's parts cover only
    the customers with one (R9)."""
    balance = current = overdue = limited = ZERO
    limits: dict[int | None, Decimal] = {}
    for document in documents:
        base = document.base
        balance += base
        if accounts_service.days_past_due(document.due_date, day) > 0:
            overdue += base
        else:
            current += base
        if document.credit_limit is not None and document.credit_limit > 0:
            limits[document.party] = document.credit_limit
            limited += base
    return {
        'balance_c': cents(balance),
        'current_c': cents(current),
        'overdue_c': cents(overdue),
        'orders': len(documents),
        'limit_c': cents(sum(limits.values(), ZERO)),
        'limited_c': cents(limited),
    }


def payable_columns(documents: Sequence[Open], day: date) -> dict[str, Any]:
    late = [d for d in documents if accounts_service.days_past_due(d.due_date, day) > 0]
    balance = sum((d.base for d in documents), ZERO)
    overdue = sum((d.base for d in late), ZERO)
    return {
        'payable_c': cents(balance),
        'current_c': cents(balance - overdue),
        'overdue_c': cents(overdue),
        'orders': len(documents),
        'overdue_orders': len(late),
    }


def document_rows(
    moment: date,
    documents: Sequence[Open],
    slice_: facts.Slice,
    day: date,
    figures: Any,
) -> list[dict[str, Any]]:
    """One row per group of `slice_.dimensions` at `moment`, of `figures(documents, day)`."""
    groups: dict[tuple, list[Open]] = {}
    for document in selected(documents, slice_.filters, day):
        key = tuple(document_key(document, d, day) for d in slice_.dimensions)
        groups.setdefault(key, []).append(document)
    width = len(slice_.dimensions)
    summed = {key: ([None] * width, figures(group, day)) for key, group in groups.items()}
    return _rows(moment, summed, width, figures([], day))


async def receivable(db: AsyncSession, slice_: facts.Slice) -> list[dict[str, Any]]:
    """The receivable fact (R7–R9): open credit orders at each moment, today live and earlier at
    the stored month-end on or before the moment. One row per moment and group."""
    today = local_now().date()
    days = {moment: read_day(moment, today) for moment in slice_.moments}
    found = await receivables_at(db, days.values(), slice_.facility)
    rows: list[dict[str, Any]] = []
    for moment, day in days.items():
        rows += document_rows(moment, found[day], slice_, day, receivable_columns)
    return rows


async def payables_now(db: AsyncSession, facility: int | None) -> list[Open]:
    """Spec 033's open purchase orders (R10), none while the balance cutoff is unset. A PO's
    facility is its lines' warehouses' (research R17): the lines carry the request's warehouse."""
    documents = await accounts_service.SUPPLIERS.documents(db, facility=None)
    places: dict[int, int] = {}
    if documents:
        rows = await db.execute(
            select(PurchaseOrderDetail.purchase_order, func.min(Warehouse.facility))
            .join(Warehouse, Warehouse.warehouse_id == PurchaseOrderDetail.warehouse)
            .where(PurchaseOrderDetail.purchase_order.in_([d.id for d in documents]))
            .group_by(PurchaseOrderDetail.purchase_order)
        )
        places = dict(rows.all())
    found = [
        Open(d.id, d.party, places.get(d.id), None, None, d.due_date, d.exchange_rate, d.balance)
        for d in documents
    ]
    return [d for d in found if facility is None or d.facility == facility]


async def payable(db: AsyncSession, slice_: facts.Slice) -> list[dict[str, Any]]:
    """The payable fact (R10): today only, so a past moment has no row and its measures are null.
    Zero while the cutoff is unset."""
    today = local_now().date()
    if today not in slice_.moments:
        return []
    documents = await payables_now(db, slice_.facility)
    return document_rows(today, documents, slice_, today, payable_columns)
