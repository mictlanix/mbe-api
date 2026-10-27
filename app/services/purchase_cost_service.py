"""Average cost on the confirm of a purchase order's receipt, and its restore on cancel (spec 031,
US4; research R5, R6).

`inventory_documents.confirm_simple` and `cancel` call this only for a receipt linked to a
purchase order. Each step runs in two parts:

1. **Lock**, right after the receipt and warehouse locks and before any ordinary read: the
   receipt's products by a locking read, then their cost rows (list 0) `FOR UPDATE`, ascending. A
   receipt of the same product in any facility waits here, and once it has the lock its snapshot
   sees the first receipt's ledger entries and new cost together (FR-033).
2. **Apply** (or restore) after the receipt's checks, before its ledger entries are staged, so the
   on-hand read is the stock before this receipt.

Cost is written nowhere else (FR-038). It is rounded half up to 4 decimals, the column's precision
(FR-039); the history keeps the unit cost at the PO line's 7.
"""

from collections.abc import Iterable, Sequence
from decimal import ROUND_HALF_UP, Decimal
from types import SimpleNamespace
from typing import Any

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.constants import COST_PRICE_LIST_ID
from app.core.deps import CurrentUser
from app.models.inventory import InventoryReceipt, InventoryReceiptDetail, LotSerialTracking
from app.models.product import PriceList, Product, ProductPrice
from app.models.purchases import ProductCostChange, PurchaseOrderDetail

COST_PLACES = Decimal('0.0001')

# ── Pure rules ────────────────────────────────────────────────────────────────


def unit_cost(price: Decimal, discount: Decimal) -> Decimal:
    """U: the PO line's price net of discount. Prices are before tax; `tax_included` is ignored,
    being a copy of the product's sales flag on pre-cutover lines (FR-034)."""
    return price * (Decimal(1) - discount)


def average(q: Decimal, c: Decimal, r: Decimal, u: Decimal) -> Decimal:
    """(Q×C + R×U) / (Q + R), with negative on hand as none, half up to 4 decimals (FR-031).

    R is always above 0 (receipt lines are), so the divisor is too.
    """
    q = max(q, Decimal(0))
    return ((q * c + r * u) / (q + r)).quantize(COST_PLACES, ROUND_HALF_UP)


def apply_lines(
    lines: Iterable[Any], *, on_hand: dict[int, Decimal], costs: dict[int, Decimal]
) -> tuple[list[dict[str, Any]], dict[int, Decimal], list[str]]:
    """Average a receipt's lines in line-id order: `(changes, final cost per product, warnings)`.

    Each line carries `receipt_detail_id`, `product`, `product_code`, `quantity`,
    `purchase_order_detail`, and its PO line's `price` and `discount`. A line without a PO line
    is ignored (FR-032). A line priced at 0 changes no cost (FR-035), but its goods are on hand at
    the old cost for the next line of the product, so Q advances by them.
    """
    running_q = dict(on_hand)
    running_c = dict(costs)
    changes: list[dict[str, Any]] = []
    final: dict[int, Decimal] = {}
    warnings: list[str] = []
    for line in sorted(lines, key=lambda ln: ln.receipt_detail_id):
        if line.purchase_order_detail is None:
            continue
        product = line.product
        q = running_q.get(product, Decimal(0))
        c = running_c.get(product, Decimal(0))
        u = unit_cost(line.price, line.discount)
        running_q[product] = q + line.quantity
        if u == 0:
            warnings.append(f'{line.product_code} is priced at 0; its cost was not changed')
            continue
        new = average(q, c, line.quantity, u)
        changes.append(
            {
                'product': product,
                'purchase_order_detail': line.purchase_order_detail,
                'previous_cost': c,
                'new_cost': new,
                'quantity_on_hand': max(q, Decimal(0)),
                'quantity_received': line.quantity,
                'unit_cost': u,
            }
        )
        running_c[product] = new
        final[product] = new
    return changes, final, warnings


def restore_plan(
    receipt_changes: Sequence[Any], latest_by_product: dict[int, Any]
) -> tuple[list[dict[str, Any]], list[tuple[int, Any]]]:
    """`(restores, warnings)` for a cancelled receipt's own changes, in id order (FR-037, R6).

    A product is restored to the previous cost of the receipt's first change of it when the
    receipt's change is still the product's latest; otherwise the warning names the later change.
    Sales and other movements never change average cost, so the restore is exact.
    """
    first: dict[int, Any] = {}
    for change in sorted(receipt_changes, key=lambda ch: ch.product_cost_change_id):
        first.setdefault(change.product, change)
    restores: list[dict[str, Any]] = []
    warnings: list[tuple[int, Any]] = []
    for product, change in first.items():
        latest = latest_by_product[product]
        if latest.receipt == change.receipt and latest.reverted_change is None:
            restores.append(
                {
                    'product': product,
                    'cost': change.previous_cost,
                    'reverted_change': change.product_cost_change_id,
                }
            )
        else:
            warnings.append((product, latest))
    return restores, warnings


# ── Locks ─────────────────────────────────────────────────────────────────────


async def _lock_costs(db: AsyncSession, products: Iterable[int]) -> None:
    ordered = sorted(set(products))
    if ordered:
        await db.execute(
            select(ProductPrice.product_price_id)
            .where(ProductPrice.price_list == COST_PRICE_LIST_ID, ProductPrice.product.in_(ordered))
            .order_by(ProductPrice.product)
            .with_for_update()
        )


async def lock(db: AsyncSession, receipt_id: int) -> None:
    """The cost rows of the receipt's PO-linked products, before any ordinary read (R5).

    The products come from a locking read: the receipt row lock already stops line edits, so this
    only keeps the snapshot from opening before the cost locks. A product with no cost row yet
    locks nothing; two first receipts of it at once end with one answering 409 "busy, retry".
    """
    detail = InventoryReceiptDetail
    products = (
        (
            await db.execute(
                select(detail.product)
                .where(detail.receipt == receipt_id, detail.purchase_order_detail.is_not(None))
                .order_by(detail.product)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    await _lock_costs(db, products)


async def lock_restore(db: AsyncSession, receipt_id: int) -> None:
    """The cost rows of the products the receipt changed, before any ordinary read (R6)."""
    products = (
        (
            await db.execute(
                select(ProductCostChange.product)
                .where(
                    ProductCostChange.receipt == receipt_id,
                    ProductCostChange.reverted_change.is_(None),
                )
                .order_by(ProductCostChange.product)
                .with_for_update()
            )
        )
        .scalars()
        .all()
    )
    await _lock_costs(db, products)


# ── Apply and restore ─────────────────────────────────────────────────────────


async def _cost_rows(db: AsyncSession, products: Iterable[int]) -> dict[int, ProductPrice]:
    rows = await db.execute(
        select(ProductPrice)
        .where(
            ProductPrice.price_list == COST_PRICE_LIST_ID,
            ProductPrice.product.in_(set(products)),
        )
        .execution_options(populate_existing=True)
    )
    return {row.product: row for row in rows.scalars().all()}


async def _write_costs(
    db: AsyncSession, rows: dict[int, ProductPrice], final: dict[int, Decimal]
) -> None:
    """Update each product's cost row, or create it with the cost list's default profits."""
    cost_list = None
    for product, cost in final.items():
        row = rows.get(product)
        if row is not None:
            row.price = cost
            continue
        if cost_list is None:
            cost_list = await db.get(PriceList, COST_PRICE_LIST_ID)
        db.add(
            ProductPrice(
                product=product,
                price_list=COST_PRICE_LIST_ID,
                price=cost,
                low_profit=cost_list.low_profit_margin,
                high_profit=cost_list.high_profit_margin,
            )
        )


async def apply(
    db: AsyncSession, receipt: InventoryReceipt, lines: Sequence[Any], *, current: CurrentUser
) -> list[str]:
    """Average the receipt's PO-linked lines into cost and stage their history (FR-031 to
    FR-036). Call it after `lock`, and before the receipt's ledger entries are staged."""
    linked = [line for line in lines if line.purchase_order_detail is not None]
    if not linked:
        return []
    products = {line.product for line in linked}
    on_hand = dict(
        (
            await db.execute(
                select(LotSerialTracking.product, func.sum(LotSerialTracking.quantity))
                .where(LotSerialTracking.product.in_(products))
                .group_by(LotSerialTracking.product)
            )
        ).all()
    )
    rows = await _cost_rows(db, products)
    prices = {
        line_id: (price, discount)
        for line_id, price, discount in (
            await db.execute(
                select(
                    PurchaseOrderDetail.purchase_order_detail_id,
                    PurchaseOrderDetail.price,
                    PurchaseOrderDetail.discount,
                ).where(
                    PurchaseOrderDetail.purchase_order_detail_id.in_(
                        {line.purchase_order_detail for line in linked}
                    )
                )
            )
        ).all()
    }
    priced = []
    for line in linked:
        price, discount = prices[line.purchase_order_detail]
        priced.append(
            SimpleNamespace(
                receipt_detail_id=line.receipt_detail_id,
                product=line.product,
                product_code=line.product_code,
                quantity=line.quantity,
                purchase_order_detail=line.purchase_order_detail,
                price=price,
                discount=discount,
            )
        )
    changes, final, warnings = apply_lines(
        priced,
        on_hand={product: total or Decimal(0) for product, total in on_hand.items()},
        costs={product: row.price for product, row in rows.items()},
    )
    await _write_costs(db, rows, final)
    now = local_now()
    for change in changes:
        db.add(
            ProductCostChange(
                receipt=receipt.inventory_receipt_id,
                creation_time=now,
                creator=current.employee_id,
                **change,
            )
        )
    return warnings


async def restore(
    db: AsyncSession, receipt: InventoryReceipt, *, current: CurrentUser
) -> list[str]:
    """Undo the receipt's cost changes where still possible, else warn (FR-037, R6). Call it
    after `lock_restore`."""
    receipt_id = receipt.inventory_receipt_id
    mine = list(
        (
            await db.execute(
                select(ProductCostChange)
                .where(
                    ProductCostChange.receipt == receipt_id,
                    ProductCostChange.reverted_change.is_(None),
                )
                .order_by(ProductCostChange.product_cost_change_id)
            )
        )
        .scalars()
        .all()
    )
    if not mine:
        return []
    products = {change.product for change in mine}
    latest_ids = (
        select(func.max(ProductCostChange.product_cost_change_id))
        .where(ProductCostChange.product.in_(products))
        .group_by(ProductCostChange.product)
    )
    latest = {
        change.product: change
        for change in (
            await db.execute(
                select(ProductCostChange).where(
                    ProductCostChange.product_cost_change_id.in_(latest_ids)
                )
            )
        )
        .scalars()
        .all()
    }
    restores, later = restore_plan(mine, latest)

    rows = await _cost_rows(db, products)
    now = local_now()
    for item in restores:
        row = rows[item['product']]
        db.add(
            ProductCostChange(
                product=item['product'],
                receipt=receipt_id,
                purchase_order_detail=None,
                previous_cost=row.price,
                new_cost=item['cost'],
                reverted_change=item['reverted_change'],
                creation_time=now,
                creator=current.employee_id,
            )
        )
        row.price = item['cost']

    if not later:
        return []
    codes = dict(
        (
            await db.execute(
                select(Product.product_id, Product.code).where(
                    Product.product_id.in_({product for product, _ in later})
                )
            )
        ).all()
    )
    receipts = {
        row.inventory_receipt_id: row
        for row in (
            await db.execute(
                select(InventoryReceipt).where(
                    InventoryReceipt.inventory_receipt_id.in_({c.receipt for _, c in later})
                )
            )
        )
        .scalars()
        .all()
    }
    warnings = []
    for product, change in later:
        other = receipts[change.receipt]
        label = other.serial if other.serial is not None else other.inventory_receipt_id
        warnings.append(f'Cost of {codes[product]} not restored: changed since by receipt {label}')
    return warnings


# ── History (FR-036) ──────────────────────────────────────────────────────────


async def history(
    db: AsyncSession, product: int, *, skip: int = 0, limit: int = 20
) -> tuple[list[ProductCostChange], int]:
    """A product's cost changes, newest first."""
    if await db.get(Product, product) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Product not found')
    where = ProductCostChange.product == product
    total: int = (
        await db.execute(select(func.count()).select_from(ProductCostChange).where(where))
    ).scalar_one()
    items = list(
        (
            await db.execute(
                select(ProductCostChange)
                .where(where)
                .order_by(ProductCostChange.product_cost_change_id.desc())
                .offset(skip)
                .limit(limit)
            )
        )
        .scalars()
        .all()
    )
    return items, total
