"""A customer's product discounts — per-product rate overrides (#15).

`customer_discount` is a child of `customer` and means nothing without it, so every lookup is
scoped to the customer in the path: an override belonging to another customer is reported as
absent, never touched. The product is fixed once the override exists.

A line added to a sales order or quote without a discount starts at the customer's rate for its
product (`rate_for`, spec 023). That is the only read: the price grid does not use these rates,
and a line's rate is its own once it exists.
"""

from collections.abc import Sequence
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.models.customer import CustomerDiscount
from app.models.product import Product
from app.schemas.customer import CustomerDiscountCreate, CustomerDiscountUpdate
from app.schemas.product import ProductSummary


def _summary(product: Product) -> ProductSummary:
    return ProductSummary.model_validate(product)


async def _attach_product(db: AsyncSession, discount: CustomerDiscount) -> CustomerDiscount:
    product = await db.get(Product, discount.product)
    discount.__dict__['product_detail'] = _summary(product) if product is not None else None
    return discount


async def rate_for(db: AsyncSession, customer_id: int, product_id: int) -> Decimal | None:
    """The customer's override for the product, if any; the pair is unique (spec 021)."""
    return (
        await db.execute(
            select(CustomerDiscount.discount).where(
                CustomerDiscount.customer == customer_id,
                CustomerDiscount.product == product_id,
            )
        )
    ).scalar_one_or_none()


async def list_discounts(db: AsyncSession, customer_id: int) -> Sequence[CustomerDiscount]:
    rows = (
        await db.execute(
            select(CustomerDiscount, Product)
            .join(Product, Product.product_id == CustomerDiscount.product)
            .where(CustomerDiscount.customer == customer_id)
            .order_by(Product.code, CustomerDiscount.customer_discount_id)
        )
    ).all()
    for discount, product in rows:
        discount.__dict__['product_detail'] = _summary(product)
    return [discount for discount, _ in rows]


async def get_discount(
    db: AsyncSession, customer_id: int, customer_discount_id: int
) -> CustomerDiscount | None:
    discount = await db.get(CustomerDiscount, customer_discount_id)
    if discount is None or discount.customer != customer_id:
        return None
    return await _attach_product(db, discount)


async def create_discount(
    db: AsyncSession, customer_id: int, data: CustomerDiscountCreate
) -> CustomerDiscount:
    # An unknown product is a validation error (422); a repeat is a conflict (409). Both are
    # checked up front so the client gets a specific message rather than an integrity error.
    if await db.get(Product, data.product) is None:
        raise DomainError(
            ErrorCode.CUSTOMER_DISCOUNT_PRODUCT_NOT_FOUND,
            'Product not found',
            {'product_id': data.product},
        )
    duplicate = (
        await db.execute(
            select(CustomerDiscount.customer_discount_id).where(
                CustomerDiscount.customer == customer_id,
                CustomerDiscount.product == data.product,
            )
        )
    ).first()
    if duplicate is not None:
        raise DomainError(
            ErrorCode.RESOURCE_ALREADY_EXISTS,
            'Customer already has a discount for this product',
            {'resource': 'customer_discount', 'field': 'product'},
        )
    discount = CustomerDiscount(customer=customer_id, product=data.product, discount=data.discount)
    db.add(discount)
    await db.commit()
    await db.refresh(discount)
    return await _attach_product(db, discount)


async def update_discount(
    db: AsyncSession, discount: CustomerDiscount, data: CustomerDiscountUpdate
) -> CustomerDiscount:
    discount.discount = data.discount
    await db.commit()
    await db.refresh(discount)
    return await _attach_product(db, discount)


async def delete_discount(db: AsyncSession, discount: CustomerDiscount) -> None:
    await db.delete(discount)
    await db.commit()
