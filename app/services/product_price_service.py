from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.product import PriceList, Product, ProductPrice
from app.schemas.product_price import ProductPriceCreate, ProductPriceUpdate


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


async def list_product_prices(
    db: AsyncSession,
    *,
    product: int | None = None,
    price_list: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[ProductPrice], int]:
    base = select(ProductPrice)
    count_q = select(func.count()).select_from(ProductPrice)

    if product is not None:
        base = base.where(ProductPrice.product == product)
        count_q = count_q.where(ProductPrice.product == product)
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
    if await db.get(PriceList, data.price_list) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Price list not found')

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
        low_profit=data.low_profit,
        high_profit=data.high_profit,
    )
    db.add(pp)
    await db.commit()
    await db.refresh(pp)
    await _attach_price_list(db, [pp])
    return pp


async def update_product_price(
    db: AsyncSession, pp: ProductPrice, data: ProductPriceUpdate
) -> ProductPrice:
    if data.price is not None:
        pp.price = data.price
    if data.low_profit is not None:
        pp.low_profit = data.low_profit
    if data.high_profit is not None:
        pp.high_profit = data.high_profit
    await db.commit()
    await db.refresh(pp)
    await _attach_price_list(db, [pp])
    return pp


async def delete_product_price(db: AsyncSession, pp: ProductPrice) -> None:
    await db.delete(pp)
    await db.commit()


async def delete_for_product(db: AsyncSession, product_id: int) -> None:
    await db.execute(delete(ProductPrice).where(ProductPrice.product == product_id))
