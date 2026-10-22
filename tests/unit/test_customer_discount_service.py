"""A customer's product discounts: the product and duplicate checks, and the parent check (#15).

`customer_discount` is a child of a customer, so an override that belongs to a different customer
than the path names must read as absent rather than be touched.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.models.product import Product
from app.schemas.customer import CustomerDiscountCreate, CustomerDiscountUpdate
from app.services import customer_discount_service as service


def _discount(customer: int = 1, **overrides) -> SimpleNamespace:
    base = dict(
        customer_discount_id=5, customer=customer, product=9, discount=Decimal('0.10000000')
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _product() -> SimpleNamespace:
    return SimpleNamespace(product_id=9, code='P9', name='Producto Nueve')


def _db(found: SimpleNamespace | None = None, product: SimpleNamespace | None = None) -> AsyncMock:
    db = AsyncMock()

    async def _get(model, _id):
        return product if model is Product else found

    db.get = AsyncMock(side_effect=_get)
    db.add = lambda _: None
    return db


def _executing(*, first=None, rows=()) -> MagicMock:
    result = MagicMock()
    result.first.return_value = first
    result.all.return_value = list(rows)
    return AsyncMock(return_value=result)


class TestCreate:
    @pytest.mark.asyncio
    async def test_an_unknown_product_is_refused_with_422(self) -> None:
        db = _db(product=None)

        with pytest.raises(HTTPException) as refused:
            await service.create_discount(
                db, 1, CustomerDiscountCreate(product=9, discount=Decimal('0.1'))
            )

        assert refused.value.status_code == 422
        assert refused.value.detail == 'Product not found'
        assert db.commit.await_count == 0

    @pytest.mark.asyncio
    async def test_a_second_override_for_the_product_is_refused_with_409(self) -> None:
        db = _db(product=_product())
        db.execute = _executing(first=(5,))

        with pytest.raises(HTTPException) as refused:
            await service.create_discount(
                db, 1, CustomerDiscountCreate(product=9, discount=Decimal('0.1'))
            )

        assert refused.value.status_code == 409
        assert refused.value.detail == 'Customer already has a discount for this product'
        assert db.commit.await_count == 0

    @pytest.mark.asyncio
    async def test_a_new_override_is_saved_with_its_product_summary(self) -> None:
        db = _db(product=_product())
        db.execute = _executing(first=None)

        created = await service.create_discount(
            db, 7, CustomerDiscountCreate(product=9, discount=Decimal('0.25'))
        )

        assert created.customer == 7
        assert created.product == 9
        assert created.product_detail.code == 'P9'
        assert db.commit.await_count == 1


class TestUpdate:
    @pytest.mark.asyncio
    async def test_only_the_discount_changes(self) -> None:
        discount = _discount()
        db = _db(product=_product())

        await service.update_discount(db, discount, CustomerDiscountUpdate(discount=Decimal('0.5')))

        assert discount.discount == Decimal('0.5')
        assert discount.product == 9
        assert discount.customer == 1
        assert db.commit.await_count == 1


class TestParentCheck:
    @pytest.mark.asyncio
    async def test_an_override_of_another_customer_is_absent(self) -> None:
        assert await service.get_discount(_db(_discount(customer=2)), 1, 5) is None

    @pytest.mark.asyncio
    async def test_an_override_of_the_same_customer_is_found(self) -> None:
        found = _discount(customer=1)

        assert await service.get_discount(_db(found, _product()), 1, 5) is found
        assert found.product_detail.name == 'Producto Nueve'

    @pytest.mark.asyncio
    async def test_an_unknown_override_is_absent(self) -> None:
        assert await service.get_discount(_db(None), 1, 5) is None


class TestList:
    @pytest.mark.asyncio
    async def test_rows_carry_their_product_summary(self) -> None:
        db = _db()
        db.execute = _executing(rows=[(_discount(), _product())])

        listed = await service.list_discounts(db, 1)

        assert [(d.product_detail.product_id, d.product_detail.code) for d in listed] == [(9, 'P9')]
        assert listed[0].product_detail.name == 'Producto Nueve'

    @pytest.mark.asyncio
    async def test_the_query_is_one_join_ordered_by_product_code(self) -> None:
        db = _db()
        db.execute = _executing()

        await service.list_discounts(db, 1)

        sql = str(db.execute.await_args.args[0]).lower()
        assert db.execute.await_count == 1
        assert 'join product' in sql
        assert 'order by product.code' in sql
