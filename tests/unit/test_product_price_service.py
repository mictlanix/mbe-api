from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.models.product import PriceList, ProductPrice
from app.services.product_price_service import _attach_price_list


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
    must not choke on a PriceList object already injected by the first."""
    price_list = PriceList(
        price_list_id=5,
        name="Retail",
        high_profit_margin=Decimal("0.3"),
        low_profit_margin=Decimal("0.1"),
    )
    price = ProductPrice(
        product_price_id=1,
        product=1,
        price_list=5,
        price=Decimal("10.00"),
        low_profit=Decimal("1.0"),
        high_profit=Decimal("2.0"),
    )

    db = _db_returning([price_list])
    await _attach_price_list(db, [price])
    assert price.price_list is price_list

    # Second call (as happens when update_product_price re-runs the attach step)
    # must not raise ArgumentError from passing a PriceList into .in_().
    await _attach_price_list(db, [price])
    assert price.price_list is price_list
