"""Physical counts (spec 030, US1 and US2): what only a count has, on `inventory_documents`.

Draft, header, list, lock, numbering and cancel rules are the shared ones, run for `COUNT`
(research R1). What is a count's own:

- **lines whose quantity is replaced**, never added to, and may be zero, set one by one or in
  batches of up to 2,000 that are validated whole before anything is applied (R3);
- **the blind draft:** no on-hand quantity or difference until confirm (R4);
- **confirm**, which reads on hand under the warehouse lock, posts one type-12 entry per
  difference in bulk, and reports two warnings (R2).
"""

from collections.abc import Iterable, Mapping, Sequence
from datetime import datetime
from decimal import Decimal
from typing import Any, NamedTuple

from sqlalchemy import func, insert, select
from sqlalchemy import update as update_rows  # `update` is this module's header edit
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import set_committed_value

from app.core.clock import local_now
from app.core.deps import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.enums import TransactionType
from app.models.inventory import InventoryCount, InventoryCountDetail, LotSerialTracking
from app.models.product import Product
from app.schemas.inventory import CountCreate, CountLineSet, CountUpdate
from app.services import documents, stock_ledger
from app.services import inventory_documents as inv

#: Rows per `IN` list and per bulk statement (R2): keeps lists and packets small.
CHUNK = 1000

EXACTLY_ONE = 'give exactly one of product and product_code'
UNKNOWN = 'unknown product'
NOT_STOCKABLE = 'not stockable'
REPEATED = 'repeated in the batch'


class BatchRefused(Exception):
    """A batch with bad entries; the route answers 422 with every one of them (R3)."""

    def __init__(self, errors: list[dict[str, Any]]) -> None:
        self.errors = errors
        super().__init__(f'{len(errors)} invalid entries; nothing was applied')


# ── Pure helpers ──────────────────────────────────────────────────────────────


def code_key(code: str) -> str:
    """How a product code is matched: case-insensitively, as MariaDB's collation finds it."""
    return code.casefold()


def _named(item: Any) -> dict[str, Any]:
    return {
        key: value
        for key in ('product', 'product_code')
        if (value := getattr(item, key)) is not None
    }


def _resolve(item: Any, products_by_id: Mapping[int, Any], products_by_code: Mapping[str, Any]):  # noqa: ANN202
    if item.product is not None:
        return products_by_id.get(item.product)
    return products_by_code.get(code_key(item.product_code))


def validate_batch(
    items: Sequence[Any],
    products_by_id: Mapping[int, Any],
    products_by_code: Mapping[str, Any],
) -> list[dict[str, Any]]:
    """Every bad entry, by index, with its `inventory_count_line.*` code: unknown, not stockable,
    repeated, or naming its product other than by exactly one of id and code. A repeat is each
    entry after the first naming a product, whether by id or by code. Empty when the batch is
    clean (FR-002, R3)."""
    errors: list[dict[str, Any]] = []
    seen: set[int] = set()
    for index, item in enumerate(items):
        named = _named(item)
        if len(named) != 1:
            errors.append(
                {
                    'index': index,
                    **named,
                    'reason': EXACTLY_ONE,
                    'code': ErrorCode.INVENTORY_COUNT_LINE_PRODUCT_REFERENCE,
                }
            )
            continue
        product = _resolve(item, products_by_id, products_by_code)
        reason = code = None
        if product is None:
            reason, code = UNKNOWN, ErrorCode.INVENTORY_COUNT_LINE_PRODUCT_UNKNOWN
        elif not product.stockable:
            reason, code = NOT_STOCKABLE, ErrorCode.INVENTORY_COUNT_LINE_NOT_STOCKABLE
        elif product.product_id in seen:
            reason, code = REPEATED, ErrorCode.INVENTORY_COUNT_LINE_REPEATED
        if product is not None:
            seen.add(product.product_id)
        if reason is not None:
            errors.append({'index': index, **named, 'reason': reason, 'code': code})
    return errors


def _new_line(product: Any, quantity: Decimal) -> dict[str, Any]:
    return {
        'product': product.product_id,
        'product_code': product.code,
        'product_name': product.name,
        'quantity': quantity,
    }


def apply_batch(
    existing_lines: Iterable[Any], items: Iterable[tuple[Any, Decimal]]
) -> list[dict[str, Any]]:
    """Replace the quantity of each product already counted; return the lines to create.

    `items` are `(product, quantity)` pairs of a validated batch. Applying the same batch twice
    changes nothing the second time (US2 scenario 2).
    """
    by_product = {line.product: line for line in existing_lines}
    new = []
    for product, quantity in items:
        line = by_product.get(product.product_id)
        if line is None:
            new.append(_new_line(product, quantity))
        elif line.quantity != quantity:
            line.quantity = quantity
    return new


class Difference(NamedTuple):
    line_id: int
    product: int
    on_hand: Decimal
    #: `counted − on_hand`; posted when non-zero.
    difference: Decimal


def differences(lines: Iterable[Any], on_hand: Mapping[int, Decimal]) -> list[Difference]:
    """Each line's on hand at confirm and its difference; a product never moved has 0."""
    result = []
    for line in lines:
        have = on_hand.get(line.product, Decimal(0))
        result.append(Difference(line.count_detail_id, line.product, have, line.quantity - have))
    return result


def entry_rows(
    count_id: int, warehouse: int, differences: Iterable[Difference], now: datetime
) -> list[dict[str, Any]]:
    """One type-12 ledger row per non-zero difference, signed as `post_movement` signs it:
    inbound positive, outbound negative, with no lot, serial or expiry (FR-004)."""
    return [
        {
            'source': int(TransactionType.PHYSICAL_COUNT),
            'reference': count_id,
            'date': now,
            'warehouse': warehouse,
            'product': item.product,
            'quantity': item.difference,
            'lot_number': None,
            'expiration_date': None,
            'serial_number': None,
        }
        for item in differences
        if item.difference != 0
    ]


def uncounted_lines(
    on_hand_by_product: Mapping[int, Decimal],
    counted_products: set[int],
    stockable: Mapping[int, Any],
) -> list[dict[str, Any]]:
    """Lines counted at 0 for each stockable product with a non-zero balance, negative too, and
    no line yet (FR-005). `stockable` maps product id to its row, stockable products only."""
    return [
        _new_line(stockable[product], Decimal(0))
        for product in sorted(on_hand_by_product)
        if on_hand_by_product[product] != 0
        and product not in counted_products
        and product in stockable
    ]


def negative_available(
    lines: Iterable[Any], reserved: Mapping[int, Decimal]
) -> list[dict[str, Any]]:
    """Products whose available stock, counted less reserved, is negative after the count
    (FR-010). The count still confirms: reality wins."""
    result = []
    for line in lines:
        held = reserved.get(line.product, Decimal(0))
        if line.quantity - held < 0:
            result.append(
                {
                    'product': line.product,
                    'product_code': line.product_code,
                    'counted': line.quantity,
                    'reserved': held,
                    'available': line.quantity - held,
                }
            )
    return result


class OtherCountLine(NamedTuple):
    """A product on another completed count of the same warehouse."""

    inventory_count_id: int
    serial: int | None
    #: Its `modification_time`, which is its confirm time: a confirmed count is read-only.
    confirmed: datetime
    product: int


def overlaps(
    this_products: Mapping[int, str | None],
    other_counts: Iterable[OtherCountLine],
    *,
    since: datetime,
) -> list[dict[str, Any]]:
    """This count's products that another count confirmed at or after `since`, this count's
    creation time (FR-010a). `>=`: times are stored to the second, so a confirm in the same second
    is reported, at the cost of a rare extra warning (R2)."""
    found = sorted(
        (other.product, other.inventory_count_id, other.serial)
        for other in other_counts
        if other.confirmed >= since and other.product in this_products
    )
    return [
        {
            'product': product,
            'product_code': this_products[product],
            'inventory_count_id': count_id,
            'serial': serial,
        }
        for product, count_id, serial in found
    ]


def _chunks(items: Sequence[Any], size: int = CHUNK) -> Iterable[Sequence[Any]]:
    for start in range(0, len(items), size):
        yield items[start : start + size]


# ── Responses ─────────────────────────────────────────────────────────────────


async def attach_derived(db: AsyncSession, count: InventoryCount) -> InventoryCount:
    """Status and lines. A count not confirmed is blind: no on hand, no difference (R4)."""
    await inv.attach(db, inv.COUNT, count)
    if not count.completed:
        for line in count.__dict__['lines']:
            # The response's figures only: no history, so nothing writes them back to the row.
            set_committed_value(line, 'quantity_on_hand', None)
            set_committed_value(line, 'difference', None)
    return count


# ── Drafts, header, list, cancel (shared, R1) ─────────────────────────────────


async def create(db: AsyncSession, data: CountCreate, *, current: CurrentUser) -> InventoryCount:
    count = await inv.create_draft(
        db, inv.COUNT, warehouse=data.warehouse, comment=data.comment, current=current
    )
    return await attach_derived(db, count)


async def update(
    db: AsyncSession, count_id: int, data: CountUpdate, *, current: CurrentUser
) -> InventoryCount:
    count = await inv.update_header(db, inv.COUNT, count_id, data, current=current)
    return await attach_derived(db, count)


async def list_counts(
    db: AsyncSession,
    *,
    current: CurrentUser,
    status: str | None,
    warehouse: int | None,
    skip: int,
    limit: int,
) -> tuple[Sequence[InventoryCount], int]:
    """Spec 029's scope, newest first (FR-009)."""
    return await inv.list_documents(
        db,
        inv.COUNT,
        current=current,
        status=status,
        warehouse=warehouse,
        skip=skip,
        limit=limit,
    )


async def cancel(db: AsyncSession, count_id: int, *, current: CurrentUser) -> InventoryCount:
    """A draft only; a confirmed count answers 409 (FR-006, FR-007)."""
    count = await inv.cancel(db, inv.COUNT, count_id, current=current)
    return await attach_derived(db, count)


# ── Lines (FR-002, R3) ────────────────────────────────────────────────────────


_PRODUCT_COLUMNS = (Product.product_id, Product.code, Product.name, Product.stockable)


async def _products_named(
    db: AsyncSession, items: Sequence[CountLineSet]
) -> tuple[dict[int, Any], dict[str, Any]]:
    """The batch's products, by id and by code, with one `IN` query each."""
    ids = {item.product for item in items if item.product is not None}
    codes = {item.product_code for item in items if item.product_code is not None}
    by_id: dict[int, Any] = {}
    by_code: dict[str, Any] = {}
    if ids:
        rows = await db.execute(select(*_PRODUCT_COLUMNS).where(Product.product_id.in_(ids)))
        by_id = {row.product_id: row for row in rows.all()}
    if codes:
        rows = await db.execute(select(*_PRODUCT_COLUMNS).where(Product.code.in_(codes)))
        by_code = {code_key(row.code): row for row in rows.all()}
    return by_id, by_code


async def _lines_of(db: AsyncSession, count_id: int, products: Sequence[int]) -> list[Any]:
    """The count's lines of those products, re-read from their rows."""
    lines: list[Any] = []
    for chunk in _chunks(products):
        rows = await db.execute(
            select(InventoryCountDetail)
            .where(
                InventoryCountDetail.count_id == count_id,
                InventoryCountDetail.product.in_(chunk),
            )
            .execution_options(populate_existing=True)
        )
        lines.extend(rows.scalars().all())
    return lines


async def _insert_lines(db: AsyncSession, count_id: int, values: Sequence[dict]) -> None:
    for chunk in _chunks(values):
        await db.execute(
            insert(InventoryCountDetail), [{**line, 'count_id': count_id} for line in chunk]
        )


async def set_lines(
    db: AsyncSession, count_id: int, items: Sequence[CountLineSet], *, current: CurrentUser
) -> InventoryCount:
    """Apply a batch whole, or nothing of it (R3)."""
    async with inv.busy_as_conflict(db):
        count = await inv.begin_locked(db, inv.COUNT, count_id)
        documents.assert_editable(count)

        by_id, by_code = await _products_named(db, items)
        errors = validate_batch(items, by_id, by_code)
        if errors:
            raise BatchRefused(errors)

        resolved = [(_resolve(item, by_id, by_code), item.quantity) for item in items]
        existing = await _lines_of(db, count_id, [product.product_id for product, _ in resolved])
        await _insert_lines(db, count_id, apply_batch(existing, resolved))

        inv._touch(count, current)
        await db.commit()
    await db.refresh(count)
    return await attach_derived(db, count)


async def update_line(
    db: AsyncSession, count_id: int, line_id: int, quantity: Decimal, *, current: CurrentUser
) -> InventoryCount:
    """Replace one line's counted quantity."""
    count = await inv.update_line(
        db, inv.COUNT, count_id, line_id, quantity=quantity, current=current
    )
    return await attach_derived(db, count)


async def remove_line(
    db: AsyncSession, count_id: int, line_id: int, *, current: CurrentUser
) -> InventoryCount:
    count = await inv.remove_line(db, inv.COUNT, count_id, line_id, current=current)
    return await attach_derived(db, count)


# ── Confirm (FR-004, FR-005, FR-010, FR-010a, R2) ─────────────────────────────


async def _line_rows(db: AsyncSession, count_id: int) -> list[Any]:
    """The count's lines as plain rows: no ORM objects to track for 15,000 lines."""
    rows = await db.execute(
        select(
            InventoryCountDetail.count_detail_id,
            InventoryCountDetail.product,
            InventoryCountDetail.product_code,
            InventoryCountDetail.quantity,
        )
        .where(InventoryCountDetail.count_id == count_id)
        .order_by(InventoryCountDetail.count_detail_id)
    )
    return list(rows.all())


async def _on_hand(
    db: AsyncSession, warehouse: int, products: Sequence[int] | None
) -> dict[int, Decimal]:
    """On hand per product in the warehouse: of `products`, in chunks, or of every product."""
    query = (
        select(LotSerialTracking.product, func.sum(LotSerialTracking.quantity))
        .where(LotSerialTracking.warehouse == warehouse)
        .group_by(LotSerialTracking.product)
    )
    if products is None:
        return dict((await db.execute(query)).all())
    result: dict[int, Decimal] = {}
    for chunk in _chunks(products):
        rows = await db.execute(query.where(LotSerialTracking.product.in_(chunk)))
        result.update(rows.all())
    return result


async def _stockable(db: AsyncSession, products: Sequence[int]) -> dict[int, Any]:
    result: dict[int, Any] = {}
    for chunk in _chunks(products):
        rows = await db.execute(
            select(*_PRODUCT_COLUMNS).where(
                Product.product_id.in_(chunk), Product.stockable.is_(True)
            )
        )
        result.update((row.product_id, row) for row in rows.all())
    return result


async def _insert_entries(db: AsyncSession, rows: Sequence[dict]) -> None:
    """One bulk `INSERT` executemany of ledger rows."""
    await db.execute(insert(LotSerialTracking), list(rows))


async def _reserved(
    db: AsyncSession, warehouse: int, products: Sequence[int]
) -> dict[int, Decimal]:
    result: dict[int, Decimal] = {}
    for chunk in _chunks(products):
        held = await stock_ledger.reserved_by_warehouse(db, products=set(chunk))
        result.update(
            (product, quantity) for (product, where), quantity in held.items() if where == warehouse
        )
    return result


async def _other_counts(db: AsyncSession, count: InventoryCount) -> list[OtherCountLine]:
    """Products on the warehouse's other completed counts confirmed since this one's creation."""
    rows = await db.execute(
        select(
            InventoryCount.inventory_count_id,
            InventoryCount.serial,
            InventoryCount.modification_time,
            InventoryCountDetail.product,
        )
        .join(
            InventoryCountDetail, InventoryCountDetail.count_id == InventoryCount.inventory_count_id
        )
        .where(
            InventoryCount.warehouse == count.warehouse,
            InventoryCount.inventory_count_id != count.inventory_count_id,
            InventoryCount.completed.is_(True),
            InventoryCount.cancelled.is_(False),
            InventoryCount.modification_time >= count.creation_time,
        )
    )
    return [OtherCountLine(*row) for row in rows.all()]


async def confirm(
    db: AsyncSession, count_id: int, *, include_uncounted: bool, current: CurrentUser
) -> InventoryCount:
    """Number the count, set each counted product to its count, and warn (R2).

    All or nothing: any refusal or failure rolls back, so no serial is consumed and no entry is
    left behind. While it runs the count holds the warehouse's lock; a large count is confirmed
    with the warehouse idle (R2, Idle warehouse).
    """
    async with inv.busy_as_conflict(db):
        # 1. Locks: the count, then its warehouse and the facility's transit (numbering) lock.
        count = await inv.begin_locked(db, inv.COUNT, count_id, stock=True)
        # 2. Checks. Another facility learns nothing about the count's state or lines.
        inv.assert_facility(current, count.facility, resource='inventory_count')
        documents.assert_editable(count)
        # 3. Serial.
        serial = await inv.next_serial(db, InventoryCount, count.facility)

        # 4. On hand at this moment, then the lines check (after any uncounted lines are added).
        lines = await _line_rows(db, count_id)
        if include_uncounted:
            on_hand = await _on_hand(db, count.warehouse, None)
            counted = {line.product for line in lines}
            candidates = [p for p, q in on_hand.items() if q != 0 and p not in counted]
            added = uncounted_lines(on_hand, counted, await _stockable(db, candidates))
            if added:
                await _insert_lines(db, count_id, added)
                lines = await _line_rows(db, count_id)
        else:
            on_hand = await _on_hand(db, count.warehouse, [line.product for line in lines])
        if not lines:
            raise DomainError(
                ErrorCode.INVENTORY_DOCUMENT_NO_LINES,
                'Count has no lines',
                {'resource': 'inventory_count'},
            )

        # 5. Post the differences, in bulk.
        found = differences(lines, on_hand)
        for chunk in _chunks(entry_rows(count_id, count.warehouse, found, local_now())):
            await _insert_entries(db, chunk)

        # 6. Record on hand and difference on each line.
        for chunk in _chunks(found):
            await db.execute(
                update_rows(InventoryCountDetail),
                [
                    {
                        'count_detail_id': item.line_id,
                        'quantity_on_hand': item.on_hand,
                        'difference': item.difference,
                    }
                    for item in chunk
                ],
            )

        # 7. Warnings, read before commit.
        products = [line.product for line in lines]
        short = negative_available(lines, await _reserved(db, count.warehouse, products))
        overlapping = overlaps(
            {line.product: line.product_code for line in lines},
            await _other_counts(db, count),
            since=count.creation_time,
        )

        # 8. Last, after every read, so autoflush cannot write it before the checks pass.
        count.serial = serial
        count.completed = True
        inv._touch(count, current)
        await db.commit()
    await db.refresh(count)
    await attach_derived(db, count)
    count.__dict__['negative_available'] = short
    count.__dict__['corrected_by_other_counts'] = overlapping
    return count
