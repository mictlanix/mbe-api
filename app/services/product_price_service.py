from collections.abc import Sequence
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import PriceList, Product, ProductPrice
from app.schemas.product_price import (
    ProductPriceBulkItem,
    ProductPriceCreate,
    ProductPriceUpdate,
)
from app.services.price_list_service import COST_LIST_READ_ONLY, assert_not_cost_list

# The cost list is readable here and written by nothing: cost is a computed average, owned by the
# monolith today and by the purchases module later (#194). Reads stay open because the grid's
# "copy from the cost list" action reads cost and writes the sale column.
#
# `delete_for_product` is exempt. It is the product-deletion cascade, so guarding it would make
# any product with a cost row undeletable, and skipping the row would orphan it behind an FK.


async def _attach_price_list(db: AsyncSession, prices: Sequence[ProductPrice]) -> None:
    list_ids = {pp.price_list for pp in prices}
    if not list_ids:
        return
    price_lists = (
        (await db.execute(select(PriceList).where(PriceList.price_list_id.in_(list_ids))))
        .scalars()
        .all()
    )
    by_id = {pl.price_list_id: pl for pl in price_lists}
    for pp in prices:
        # Written under a separate key: the mapped column is shared through the session
        # identity map, so overwriting it corrupts every reader of the raw FK (#95, #104).
        pp.__dict__['price_list_detail'] = by_id.get(pp.price_list)


def _margin_defaults(
    data: ProductPriceCreate | ProductPriceBulkItem, pl: PriceList
) -> dict[str, Decimal]:
    """The `low_profit` / `high_profit` a *newly created* row gets when the client omits them.

    Both columns are `NOT NULL` with no server default, so a create has to name them — but since
    #185 nothing reads them, and asking a pricing screen that edits one number to invent two more
    is exactly what #183 objected to. The price list's own `low_profit_margin` /
    `high_profit_margin` are the values the data already treats as the default for its members, so
    a row created without margins inherits the list's rather than a number this service invented.
    Both are deprecated; this is the last thing that reads either.
    """
    # Read through `model_dump` rather than by attribute: the fields are marked deprecated, so
    # Pydantic warns on attribute access, and this service *is* the intended reader of what the
    # client sent. The warning is for callers, not for the code honouring the value.
    sent = data.model_dump()
    return {
        'low_profit': (
            sent['low_profit'] if sent['low_profit'] is not None else pl.low_profit_margin
        ),
        'high_profit': (
            sent['high_profit'] if sent['high_profit'] is not None else pl.high_profit_margin
        ),
    }


async def list_product_prices(
    db: AsyncSession,
    *,
    product: list[int] | None = None,
    price_list: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[ProductPrice], int]:
    """List prices, optionally narrowed to several products at once (#182).

    `product` repeats rather than taking one id, which is the shape `GET /products?label=`
    already uses. A pricing grid asks for a page of products against every price list, and one
    id per request made a twenty-row page cost twenty-one round trips — paid again on every
    scroll and every filter change. Repeated, it is one, and it still composes with `price_list`
    for the single-column case.
    """
    base = select(ProductPrice)
    count_q = select(func.count()).select_from(ProductPrice)

    if product:
        base = base.where(ProductPrice.product.in_(product))
        count_q = count_q.where(ProductPrice.product.in_(product))
    if price_list is not None:
        base = base.where(ProductPrice.price_list == price_list)
        count_q = count_q.where(ProductPrice.price_list == price_list)

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_price_list(db, items)
    return items, total


async def get_product_price(db: AsyncSession, product_price_id: int) -> ProductPrice | None:
    pp = await db.get(ProductPrice, product_price_id)
    if pp is None:
        return None
    await _attach_price_list(db, [pp])
    return pp


async def create_product_price(db: AsyncSession, data: ProductPriceCreate) -> ProductPrice:
    if await db.get(Product, data.product) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Product not found')
    pl = await db.get(PriceList, data.price_list)
    if pl is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Price list not found')
    assert_not_cost_list(data.price_list, detail=COST_LIST_READ_ONLY)

    existing = (
        await db.execute(
            select(ProductPrice).where(
                ProductPrice.product == data.product,
                ProductPrice.price_list == data.price_list,
            )
        )
    ).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='Price already exists for this product and price list',
        )

    pp = ProductPrice(
        product=data.product,
        price_list=data.price_list,
        price=data.price,
        **_margin_defaults(data, pl),
    )
    db.add(pp)
    await db.commit()
    await db.refresh(pp)
    await _attach_price_list(db, [pp])
    return pp


async def update_product_price(
    db: AsyncSession, pp: ProductPrice, data: ProductPriceUpdate
) -> ProductPrice:
    # `ProductPriceUpdate` has no `price_list`, so the row's list is what makes this a cost write.
    assert_not_cost_list(pp.price_list, detail=COST_LIST_READ_ONLY)
    if data.price is not None:
        pp.price = data.price
    sent = data.model_dump()
    if sent['low_profit'] is not None:
        pp.low_profit = sent['low_profit']
    if sent['high_profit'] is not None:
        pp.high_profit = sent['high_profit']
    await db.commit()
    await db.refresh(pp)
    await _attach_price_list(db, [pp])
    return pp


def _assert_none_missing(wanted: set[int], found: set[int], what: str) -> None:
    missing = sorted(wanted - found)
    if missing:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f'{what} not found: {", ".join(str(i) for i in missing)}',
        )


async def _assert_products_exist(db: AsyncSession, ids: set[int]) -> None:
    found = (
        await db.execute(select(Product.product_id).where(Product.product_id.in_(ids)))
    ).scalars()
    _assert_none_missing(ids, set(found.all()), 'Product')


async def _price_lists_by_id(db: AsyncSession, ids: set[int]) -> dict[int, PriceList]:
    rows = (
        (await db.execute(select(PriceList).where(PriceList.price_list_id.in_(ids))))
        .scalars()
        .all()
    )
    by_id = {pl.price_list_id: pl for pl in rows}
    _assert_none_missing(ids, set(by_id), 'Price list')
    return by_id


async def bulk_upsert_product_prices(
    db: AsyncSession, items: Sequence[ProductPriceBulkItem]
) -> Sequence[ProductPrice]:
    """Apply a page of price edits in one transaction, keyed on `(product, price_list)` (#183).

    The grid's column actions — fill down, copy from the cost list, adjust every shown row by a
    percentage — each rewrite a whole visible page at once, and no existing write expresses that.
    Doing it with the per-row endpoints was one request per cell, each independently committed, so
    a run that failed halfway left a column of prices in a state nobody asked for. Here it is one
    commit: either every cell in the body lands or none does.

    Upsert rather than create-or-update, because `(product, list)` is unique and the client cannot
    know which it needs without a read that is stale by the time it writes. A row that exists is
    updated, one that does not is created with the price list's margins (`_margin_defaults`).

    Every product and every price list named in the body is checked up front, so a body naming one
    bad id is refused whole rather than applying its good rows first. A repeated `(product,
    price_list)` is a 400: two cells for one cell is a client bug, and silently letting the last
    one win would hide it. So is any cell naming the cost list, which is read-only here (#194).
    """
    seen: set[tuple[int, int]] = set()
    for item in items:
        key = (item.product, item.price_list)
        if key in seen:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=(
                    f'Duplicate entry for product {item.product} on price list {item.price_list}'
                ),
            )
        seen.add(key)
        # Here rather than in the write loop, so one cost cell refuses the whole body.
        assert_not_cost_list(item.price_list, detail=COST_LIST_READ_ONLY)

    await _assert_products_exist(db, {i.product for i in items})
    lists = await _price_lists_by_id(db, {i.price_list for i in items})

    # Fetched by the two id sets rather than by the pairs themselves: a row-value `IN` over
    # tuples is not something every backend renders the same way, and the over-fetch is bounded
    # by the body — at most `len(items)` products times the handful of lists it names.
    existing = {
        (pp.product, pp.price_list): pp
        for pp in (
            await db.execute(
                select(ProductPrice).where(
                    ProductPrice.product.in_({i.product for i in items}),
                    ProductPrice.price_list.in_({i.price_list for i in items}),
                )
            )
        )
        .scalars()
        .all()
    }

    result: list[ProductPrice] = []
    for item in items:
        pp = existing.get((item.product, item.price_list))
        if pp is None:
            pp = ProductPrice(
                product=item.product,
                price_list=item.price_list,
                price=item.price,
                **_margin_defaults(item, lists[item.price_list]),
            )
            db.add(pp)
        else:
            pp.price = item.price
            # Left alone when omitted, deliberately: on an update there is no "default" to fall
            # back to, and overwriting a stored band with the list's would change rows the client
            # never mentioned.
            sent = item.model_dump()
            if sent['low_profit'] is not None:
                pp.low_profit = sent['low_profit']
            if sent['high_profit'] is not None:
                pp.high_profit = sent['high_profit']
        result.append(pp)

    await db.commit()
    await _attach_price_list(db, result)
    return result


async def delete_product_price(db: AsyncSession, pp: ProductPrice) -> None:
    assert_not_cost_list(pp.price_list, detail=COST_LIST_READ_ONLY)
    await db.delete(pp)
    await db.commit()


async def delete_for_product(db: AsyncSession, product_id: int) -> None:
    await db.execute(delete(ProductPrice).where(ProductPrice.product == product_id))
