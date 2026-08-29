from decimal import Decimal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.schemas.product import PriceListResponse

#: The largest body `PUT /product-prices` accepts, and the largest page `GET /product-prices`
#: returns. One number for both, because the grid's column actions rewrite exactly the page it
#: last read: a cap on the read that the write cannot match would make a full page unwritable.
BULK_LIMIT = 500

# `low_profit` / `high_profit` are deprecated (#185). They were the per-product, per-list profit
# band `assert_margin_in_range` enforced on sales-order lines; that validation is gone, so nothing
# reads them any more. They stay accepted and returned — a client still sending them is not
# refused, and a generated client keeps the field it compiles against — and the columns stay until
# the legacy monolith is retired. Omitted on a create, they take the price list's own
# `low_profit_margin` / `high_profit_margin`, which is the only remaining use of those two.
_PROFIT_DEPRECATED = (
    'Deprecated (#185): the sales-order margin validation that read this has been retired. '
    'Omit it; a created price takes the price list margins.'
)


class ProductPriceCreate(BaseModel):
    product: int
    price_list: int
    price: Decimal = Field(ge=0)
    low_profit: Decimal | None = Field(default=None, ge=0, deprecated=_PROFIT_DEPRECATED)
    high_profit: Decimal | None = Field(default=None, ge=0, deprecated=_PROFIT_DEPRECATED)


class ProductPriceUpdate(BaseModel):
    price: Decimal | None = Field(default=None, ge=0)
    low_profit: Decimal | None = Field(default=None, ge=0, deprecated=_PROFIT_DEPRECATED)
    high_profit: Decimal | None = Field(default=None, ge=0, deprecated=_PROFIT_DEPRECATED)


class ProductPriceBulkItem(BaseModel):
    """One cell of the pricing grid, keyed on `(product, price_list)` rather than on a row id.

    That key is the `UNIQUE (product, list)` the table already carries, which is what lets one
    body express both halves of an upsert. A client editing a cell no longer has to pick `POST`
    against `PUT /{id}` from what its last read said, and no longer loses the race — and a 409 —
    when someone else priced that product in between (#183).
    """

    product: int
    price_list: int
    price: Decimal = Field(ge=0)
    low_profit: Decimal | None = Field(default=None, ge=0, deprecated=_PROFIT_DEPRECATED)
    high_profit: Decimal | None = Field(default=None, ge=0, deprecated=_PROFIT_DEPRECATED)


class ProductPriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_price_id: int
    product: int
    price_list: PriceListResponse = Field(
        validation_alias=AliasChoices('price_list_detail', 'price_list')
    )
    price: Decimal
    low_profit: Decimal = Field(deprecated=_PROFIT_DEPRECATED)
    high_profit: Decimal = Field(deprecated=_PROFIT_DEPRECATED)
