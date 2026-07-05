from collections.abc import Sequence
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import delete, insert, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.models.core import Label
from app.models.product import PriceList, Product, ProductPrice, product_label
from app.models.sat_catalog import SatProductService, SatUnitOfMeasurement
from app.models.supplier import Supplier
from app.schemas.product import ProductCreate, ProductMergeRequest, ProductUpdate
from app.schemas.sat_catalog import SatUnitOfMeasurementResponse
from app.services.sat_catalog_service import SAT_CATALOG_MAP, to_response


async def list_products(
    db: AsyncSession,
    *,
    search: str | None = None,
    label: int | None = None,
    deactivated: bool | None = None,
    stockable: bool | None = None,
    salable: bool | None = None,
    purchasable: bool | None = None,
    supplier: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[Product], int]:
    from sqlalchemy import func

    base = select(Product)
    count_q = select(func.count()).select_from(Product)

    if search:
        term = f"%{search}%"
        condition = or_(
            Product.code.ilike(term),
            Product.name.ilike(term),
            Product.model.ilike(term),
            Product.sku.ilike(term),
            Product.brand.ilike(term),
        )
        base = base.where(condition)
        count_q = count_q.where(condition)

    if label is not None:
        base = base.where(
            Product.product_id.in_(
                select(product_label.c["product"]).where(product_label.c["label"] == label)
            )
        )
        count_q = count_q.where(
            Product.product_id.in_(
                select(product_label.c["product"]).where(product_label.c["label"] == label)
            )
        )

    if deactivated is not None:
        base = base.where(Product.deactivated == deactivated)
        count_q = count_q.where(Product.deactivated == deactivated)
    if stockable is not None:
        base = base.where(Product.stockable == stockable)
        count_q = count_q.where(Product.stockable == stockable)
    if salable is not None:
        base = base.where(Product.salable == salable)
        count_q = count_q.where(Product.salable == salable)
    if purchasable is not None:
        base = base.where(Product.purchasable == purchasable)
        count_q = count_q.where(Product.purchasable == purchasable)
    if supplier is not None:
        base = base.where(Product.supplier == supplier)
        count_q = count_q.where(Product.supplier == supplier)

    total: int = (await db.execute(count_q)).scalar_one()
    products = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    await _attach_unit_of_measurement(db, products)
    return products, total


async def _get_labels(db: AsyncSession, product_id: int) -> list[Label]:
    rows = (
        await db.execute(
            select(Label)
            .join(product_label, product_label.c["label"] == Label.label_id)
            .where(product_label.c["product"] == product_id)
        )
    ).scalars().all()
    return list(rows)


async def _set_labels(db: AsyncSession, product_id: int, label_ids: list[int]) -> None:
    await db.execute(delete(product_label).where(product_label.c["product"] == product_id))
    if label_ids:
        await db.execute(
            insert(product_label),
            [{"product": product_id, "label": label_id} for label_id in label_ids],
        )


def _price_list_id(pp: ProductPrice) -> int:
    """Read the FK id even if a previous call already injected the PriceList object."""
    value = pp.price_list
    return value.price_list_id if isinstance(value, PriceList) else value


async def _attach_price_relations(db: AsyncSession, prices: Sequence[ProductPrice]) -> None:
    list_ids = {_price_list_id(pp) for pp in prices}
    if not list_ids:
        return
    price_lists = (
        await db.execute(select(PriceList).where(PriceList.price_list_id.in_(list_ids)))
    ).scalars().all()
    by_id = {pl.price_list_id: pl for pl in price_lists}
    for pp in prices:
        pp.__dict__["price_list"] = by_id.get(_price_list_id(pp))


async def _attach_unit_of_measurement(db: AsyncSession, products: Sequence[Product]) -> None:
    """Attach only `unit_of_measurement` — the single FK field ProductListItem exposes.

    Returns the full sat_unit_of_measurement record (id, name, description, symbol), not just
    the generic id/description shape used by the standalone SAT catalog endpoints.
    """
    if not products:
        return
    unit_ids = {p.unit_of_measurement for p in products}
    units = (
        await db.execute(
            select(SatUnitOfMeasurement).where(
                SatUnitOfMeasurement.sat_unit_of_measurement_id.in_(unit_ids)
            )
        )
    ).scalars().all()
    units_by_id = {u.sat_unit_of_measurement_id: u for u in units}
    for p in products:
        unit_row = units_by_id.get(p.unit_of_measurement)
        p.__dict__["unit_of_measurement"] = (
            SatUnitOfMeasurementResponse(
                id=unit_row.sat_unit_of_measurement_id,
                name=unit_row.name,
                description=unit_row.description,
                symbol=unit_row.symbol,
            )
            if unit_row
            else None
        )


async def _attach_product_relations(db: AsyncSession, products: Sequence[Product]) -> None:
    """Attach unit_of_measurement, key, and supplier — full FK set for ProductResponse."""
    if not products:
        return
    await _attach_unit_of_measurement(db, products)
    key_config = SAT_CATALOG_MAP["product-services"]

    key_ids = {p.key for p in products if p.key is not None}
    keys_by_id: dict[str, SatProductService] = {}
    if key_ids:
        keys = (
            await db.execute(
                select(SatProductService).where(
                    SatProductService.sat_product_service_id.in_(key_ids)
                )
            )
        ).scalars().all()
        keys_by_id = {k.sat_product_service_id: k for k in keys}

    supplier_ids = {p.supplier for p in products if p.supplier is not None}
    suppliers_by_id: dict[int, Supplier] = {}
    if supplier_ids:
        suppliers = (
            await db.execute(select(Supplier).where(Supplier.supplier_id.in_(supplier_ids)))
        ).scalars().all()
        suppliers_by_id = {s.supplier_id: s for s in suppliers}

    for p in products:
        key_row = keys_by_id.get(p.key) if p.key is not None else None
        p.__dict__["key"] = to_response(key_row, key_config) if key_row else None
        p.__dict__["supplier"] = suppliers_by_id.get(p.supplier) if p.supplier is not None else None


async def get_product(db: AsyncSession, product_id: int) -> Product | None:
    product = await db.get(Product, product_id)
    if product is None:
        return None
    prices = (
        await db.execute(select(ProductPrice).where(ProductPrice.product == product_id))
    ).scalars().all()
    product.__dict__["prices"] = list(prices)
    product.__dict__["labels"] = await _get_labels(db, product_id)
    await _attach_price_relations(db, prices)
    await _attach_product_relations(db, [product])
    return product


async def _check_code_unique(db: AsyncSession, code: str, exclude_id: int | None = None) -> None:
    q = select(Product).where(Product.code == code)
    if exclude_id is not None:
        q = q.where(Product.product_id != exclude_id)
    existing = (await db.execute(q)).scalar_one_or_none()
    if existing is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail="Product code already exists"
        )


async def create_product(db: AsyncSession, data: ProductCreate, settings: Settings) -> Product:
    await _check_code_unique(db, data.code)

    product = Product(
        code=data.code,
        name=data.name,
        photo=data.photo or settings.default_photo_file,
        sku=data.sku,
        brand=data.brand,
        model=data.model,
        bar_code=data.bar_code or None,
        location=data.location,
        unit_of_measurement=data.unit_of_measurement,
        key=data.key,
        tax_rate=data.tax_rate if data.tax_rate is not None else settings.default_vat,
        tax_included=(
            data.tax_included if data.tax_included is not None else settings.is_tax_included
        ),
        price_type=data.price_type if data.price_type is not None else settings.default_price_type,
        currency=data.currency,
        min_order_qty=1,
        supplier=data.supplier,
        stockable=data.stockable,
        perishable=data.perishable,
        seriable=data.seriable,
        purchasable=data.purchasable,
        salable=data.salable,
        invoiceable=data.invoiceable,
        stock_verification=data.stock_required if data.stock_required is not None else True,
        deactivated=False,
        comment=data.comment,
    )
    db.add(product)
    await db.flush()  # get product_id before creating prices

    price_lists = (await db.execute(select(PriceList))).scalars().all()
    for pl in price_lists:
        db.add(ProductPrice(
            product=product.product_id,
            price_list=pl.price_list_id,
            price=Decimal("0"),
            low_profit=Decimal("0"),
            high_profit=Decimal("0"),
        ))

    if data.labels is not None:
        await _set_labels(db, product.product_id, data.labels)

    await db.commit()
    await db.refresh(product)
    prices = (
        await db.execute(select(ProductPrice).where(ProductPrice.product == product.product_id))
    ).scalars().all()
    product.__dict__["prices"] = list(prices)
    product.__dict__["labels"] = await _get_labels(db, product.product_id)
    await _attach_price_relations(db, prices)
    await _attach_product_relations(db, [product])
    return product


async def update_product(db: AsyncSession, product: Product, data: ProductUpdate) -> Product:
    if data.code is not None and data.code != product.code:
        await _check_code_unique(db, data.code, exclude_id=product.product_id)
        product.code = data.code
    if data.name is not None:
        product.name = data.name
    if "photo" in data.model_fields_set:
        product.photo = data.photo
    if data.sku is not None:
        product.sku = data.sku
    if data.brand is not None:
        product.brand = data.brand
    if data.model is not None:
        product.model = data.model
    if data.bar_code is not None:
        product.bar_code = data.bar_code or None
    if data.location is not None:
        product.location = data.location
    if data.unit_of_measurement is not None:
        product.unit_of_measurement = data.unit_of_measurement
    if data.key is not None:
        product.key = data.key
    if data.tax_rate is not None:
        product.tax_rate = data.tax_rate
    if data.tax_included is not None:
        product.tax_included = data.tax_included
    if data.price_type is not None:
        product.price_type = data.price_type
    if data.currency is not None:
        product.currency = data.currency
    if data.min_order_qty is not None:
        product.min_order_qty = data.min_order_qty
    if data.supplier is not None:
        product.supplier = data.supplier
    if data.stockable is not None:
        product.stockable = data.stockable
    if data.perishable is not None:
        product.perishable = data.perishable
    if data.seriable is not None:
        product.seriable = data.seriable
    if data.purchasable is not None:
        product.purchasable = data.purchasable
    if data.salable is not None:
        product.salable = data.salable
    if data.invoiceable is not None:
        product.invoiceable = data.invoiceable
    if data.stock_required is not None:
        product.stock_verification = data.stock_required
    if data.deactivated is not None:
        product.deactivated = data.deactivated
    if data.comment is not None:
        product.comment = data.comment
    if data.labels is not None:
        await _set_labels(db, product.product_id, data.labels)

    await db.commit()
    await db.refresh(product)
    rows = await db.execute(select(ProductPrice).where(ProductPrice.product == product.product_id))
    prices = list(rows.scalars().all())
    product.__dict__["prices"] = prices
    product.__dict__["labels"] = await _get_labels(db, product.product_id)
    await _attach_price_relations(db, prices)
    await _attach_product_relations(db, [product])
    return product


async def delete_product(db: AsyncSession, product: Product) -> None:
    await db.execute(delete(ProductPrice).where(ProductPrice.product == product.product_id))
    await db.delete(product)
    await db.commit()


async def merge_products(db: AsyncSession, req: ProductMergeRequest) -> None:
    if req.product_id == req.duplicate_id:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Cannot merge a product with itself",
        )

    # Verify both products exist
    canonical = await db.get(Product, req.product_id)
    duplicate = await db.get(Product, req.duplicate_id)
    if canonical is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Canonical product not found"
        )
    if duplicate is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Duplicate product not found"
        )

    from sqlalchemy import text

    # Remap FK references across all transactional tables using raw UPDATE statements.
    # We use text() because these tables may not all have ORM models defined yet.
    tables_and_columns: list[tuple[str, str]] = [
        ("sales_order_detail", "product"),
        ("purchase_order_detail", "product"),
        ("inventory_receipt_detail", "product"),
        ("inventory_issue_detail", "product"),
        ("inventory_transfer_detail", "product"),
        ("lot_serial_tracking", "product"),
    ]
    for table, col in tables_and_columns:
        await db.execute(
            text(f"UPDATE {table} SET {col} = :canonical WHERE {col} = :duplicate"),
            {"canonical": req.product_id, "duplicate": req.duplicate_id},
        )

    # product_price: remove duplicate's prices (canonical already has its own rows)
    await db.execute(delete(ProductPrice).where(ProductPrice.product == req.duplicate_id))

    # product_label junction: remap, ignoring duplicates (ON DUPLICATE KEY approach via text)
    await db.execute(
        text(
            "UPDATE IGNORE product_label SET product = :canonical WHERE product = :duplicate"
        ),
        {"canonical": req.product_id, "duplicate": req.duplicate_id},
    )
    # delete any remaining (already existed on canonical side)
    await db.execute(
        text("DELETE FROM product_label WHERE product = :duplicate"),
        {"duplicate": req.duplicate_id},
    )

    await db.delete(duplicate)
    await db.commit()
