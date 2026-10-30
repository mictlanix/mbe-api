from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.core.constants import COST_PRICE_LIST_ID
from app.core.errors import DomainError
from app.models.product import PriceList, ProductPrice
from app.schemas.product_price import ProductPriceBulkItem, ProductPriceResponse
from app.services.product_price_service import (
    _assert_none_missing,
    _attach_price_list,
    bulk_upsert_product_prices,
    delete_product_price,
)


def _db_returning(price_lists: list[PriceList]) -> AsyncMock:
    result = MagicMock()
    result.scalars.return_value.all.return_value = price_lists
    db = AsyncMock()
    db.execute = AsyncMock(return_value=result)
    return db


@pytest.mark.asyncio
async def test_attach_price_list_survives_repeated_calls() -> None:
    """Reproduces GH #75: get_product_price + update_product_price both call
    _attach_price_list on the same ProductPrice instance, so the second call
    must not choke on the state left by the first.

    Since #104 the expansion lands on `price_list_detail` and the mapped column keeps
    the raw FK, which is what makes the repeat harmless rather than merely tolerated."""
    price_list = PriceList(
        price_list_id=5,
        name='Retail',
        high_profit_margin=Decimal('0.3'),
        low_profit_margin=Decimal('0.1'),
    )
    price = ProductPrice(
        product_price_id=1,
        product=1,
        price_list=5,
        price=Decimal('10.00'),
        low_profit=Decimal('1.0'),
        high_profit=Decimal('2.0'),
    )

    db = _db_returning([price_list])
    await _attach_price_list(db, [price])
    assert price.price_list == 5
    assert price.__dict__['price_list_detail'] is price_list

    # Second call (as happens when update_product_price re-runs the attach step)
    # must not raise ArgumentError from passing a PriceList into .in_().
    await _attach_price_list(db, [price])
    assert price.price_list == 5
    assert ProductPriceResponse.model_validate(price).price_list.price_list_id == 5


def test_missing_products_are_reported_all_at_once() -> None:
    with pytest.raises(DomainError) as exc:
        _assert_none_missing({1, 2, 3}, {2}, 'Product', 'product')

    assert (exc.value.status_code, exc.value.detail) == (404, 'Product not found: 1, 3')
    assert exc.value.code == 'product_price.references_not_found'
    assert exc.value.params == {'resource': 'product', 'ids': [1, 3]}


def test_missing_price_lists_are_reported_all_at_once() -> None:
    with pytest.raises(DomainError) as exc:
        _assert_none_missing({4, 5}, set(), 'Price list', 'price_list')

    assert exc.value.detail == 'Price list not found: 4, 5'
    assert exc.value.code == 'product_price.references_not_found'
    assert exc.value.params == {'resource': 'price_list', 'ids': [4, 5]}


def _item(product: int, price_list: int) -> ProductPriceBulkItem:
    return ProductPriceBulkItem(
        product=product,
        price_list=price_list,
        price=Decimal('1'),
        low_profit=Decimal('0'),
        high_profit=Decimal('0'),
    )


@pytest.mark.asyncio
async def test_a_repeated_cell_in_a_bulk_write_is_a_400() -> None:
    with pytest.raises(DomainError) as exc:
        await bulk_upsert_product_prices(AsyncMock(), [_item(7, 2), _item(7, 2)])

    assert exc.value.status_code == 400
    assert exc.value.detail == 'Duplicate entry for product 7 on price list 2'
    assert exc.value.code == 'product_price.duplicate_entry'
    assert exc.value.params == {'product_id': 7, 'price_list_id': 2}


@pytest.mark.asyncio
async def test_a_cost_list_cell_is_read_only() -> None:
    with pytest.raises(DomainError) as exc:
        await bulk_upsert_product_prices(AsyncMock(), [_item(7, COST_PRICE_LIST_ID)])

    assert (exc.value.status_code, exc.value.detail) == (400, 'The cost price list is read-only')
    assert exc.value.code == 'price_list.cost_read_only'
    assert exc.value.params == {}


@pytest.mark.asyncio
async def test_deleting_a_cost_list_price_is_read_only() -> None:
    pp = ProductPrice(product_price_id=1, product=1, price_list=COST_PRICE_LIST_ID)
    with pytest.raises(DomainError) as exc:
        await delete_product_price(AsyncMock(), pp)

    assert exc.value.code == 'price_list.cost_read_only'
