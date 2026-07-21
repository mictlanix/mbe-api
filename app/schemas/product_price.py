from decimal import Decimal

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.schemas.product import PriceListResponse


class ProductPriceCreate(BaseModel):
    product: int
    price_list: int
    price: Decimal = Field(ge=0)
    low_profit: Decimal = Field(ge=0)
    high_profit: Decimal = Field(ge=0)


class ProductPriceUpdate(BaseModel):
    price: Decimal | None = Field(default=None, ge=0)
    low_profit: Decimal | None = Field(default=None, ge=0)
    high_profit: Decimal | None = Field(default=None, ge=0)


class ProductPriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    product_price_id: int
    product: int
    price_list: PriceListResponse = Field(
        validation_alias=AliasChoices('price_list_detail', 'price_list')
    )
    price: Decimal
    low_profit: Decimal
    high_profit: Decimal
