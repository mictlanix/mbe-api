# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- CRUD endpoints for per-product prices under `/api/v1/product-prices` (list with `product`/`price_list` filters, create, get, update, delete), gated by `SystemObject.PRICING`; `app/schemas/product_price.py` and `app/services/product_price_service.py`

### Changed
- `Product.unit_of_measurement` in `GET /api/v1/products` and `GET /api/v1/products/{id}` now returns the full `sat_unit_of_measurement` record (`{id, name, description, symbol}`) instead of the generic `{id, description}` shape used by other SAT catalog FKs; new `SatUnitOfMeasurementResponse` schema in `app/schemas/sat_catalog.py`

### Removed
- `ProductResponse.prices` field — product endpoints no longer return or manage pricing data; use `GET /api/v1/product-prices?product={id}` instead
- Auto-creation of a zeroed `ProductPrice` row per price list on `POST /api/v1/products`; new products now start with zero prices until explicitly created via `/api/v1/product-prices`

### Fixed
- `PUT /api/v1/products/{id}` no longer returns HTTP 500 for products with price list entries; `_attach_price_relations` in `app/services/product_service.py` was passing a stale `PriceList` ORM object (injected by the endpoint's earlier `get_product` call) into a `.in_()` clause instead of its integer FK (#75)

## [0.2.0] - 2026-07-04

### Added
- `POST /api/v1/products/{product_id}/image` — upload a product image (JPEG/PNG/GIF/WEBP); resized to ≤150 px wide, saved as PNG named by SHA-256 content hash; duplicate uploads reuse the existing file
- `GET /images/{filename}` — public static endpoint serving stored product images (no authentication required)
- `images_dir` setting in `app/core/config.py` for configuring the image storage directory (default: `"images"`, override with `IMAGES_DIR` env var)
- `images_base_url` setting in `app/core/config.py` for constructing full image URLs in API responses (default: `""` → relative `/images/{filename}`, override with `IMAGES_BASE_URL` env var)
- `app/services/image_service.py` — image processing service (resize, convert, hash, dedup)
- `ProductResponse.photo` now returns the full public URL of the image (e.g. `/images/{hash}.png` or `https://host/images/{hash}.png`) instead of the bare filename; existing bare filenames in the DB are automatically upgraded at read time
- REST CRUD endpoints for 17 master data resources: Products, Price Lists, Customers, Labels, Taxpayer Recipients, Suppliers, Employees, Stores, Warehouses, Points of Sale, Cash Drawers, Exchange Rates, Expenses, Payment Method Options, Vehicles, Vehicle Operators, Production Sites
- FK filter query parameters on 5 list endpoints: `supplier` on `GET /api/v1/products`, `price_list`/`salesperson` on `GET /api/v1/customers`, `store`/`warehouse` on `GET /api/v1/points-of-sale`, `store` on `GET /api/v1/cash-drawers`, `employee` on `GET /api/v1/vehicle-operators`
- Read-only SAT catalog endpoints under `/api/v1/sat/` for 8 reference catalogs: `cfdi-usages`, `countries`, `currencies`, `postal-codes`, `product-services`, `reason-cancellations`, `tax-regimes`, `units-of-measurement`; each exposes paginated list and get-by-id; write operations return `405`
- `app/schemas/sat_catalog.py` — `SatCatalogResponse` schema used by all 8 SAT catalog endpoints
- `app/services/sat_catalog_service.py` — generic list/get service for SAT catalog models
- `SatCatalogResponse.description` — human-readable text now returned on all SAT catalog endpoints, mapped from each table's existing `description`/`name` column (`sat_unit_of_measurement.name` for units-of-measurement, `description` for the rest); `sat_postal_code` has no description text in the source schema, so it stays `null` (#73)
- `search` query parameter on `GET /api/v1/sat/{catalog}`, matching (case-insensitive, substring) against every varchar column on the catalog's table — the code, the description/name column, and any remaining text columns (`keywords` for `product-services`; `state`/`borough`/`locality` for `postal-codes`; `description`/`symbol` for `units-of-measurement`) (#73)
- `GET /api/v1/products/merge` endpoint for merging duplicate products
- `app/schemas/product.py` — Pydantic schemas for products and price lists
- `app/schemas/customer.py` — Pydantic schemas for customers and taxpayer recipients
- `app/schemas/supplier.py` — Pydantic schemas for suppliers
- `app/schemas/core.py` — Pydantic schemas for all remaining catalog resources
- `app/schemas/__init__.py` — generic `ListResponse[T]` model for paginated list responses
- 17 service modules under `app/services/` for all catalog resources
- 17 endpoint modules under `app/api/v1/endpoints/` for all catalog resources
- `default_vat`, `is_tax_included`, `default_price_type`, `default_photo_file`, `default_customer_id` settings to `app/core/config.py`
- `docs/constants.md` — full enum reference extracted from `Model/Constants/` with integer values and descriptions
- VS Code debug configuration (`.vscode/launch.json`) for F5 launch via debugpy + uvicorn
- README with setup, environment variables, run, migration, test, and lint instructions

### Docs
- Updated specs: `02-sales`, `03-production`, `04-inventory`, `05-purchases`, `07-administration`, `08-technical-service`, `09-front-desk`, `10-fiscal-documents`, `11-reports`
- `docs/README.md` index updated to reference `constants.md`

### Fixed
- `GET /api/v1/products`, `POST /api/v1/products`, `GET /api/v1/products/{id}`, `PUT /api/v1/products/{id}`, and `DELETE /api/v1/products/{id}` now enforce `require_privilege(SystemObject.PRODUCTS, ...)`; previously any authenticated user could call them regardless of their `products` privilege (#70)
- `PUT /api/v1/products/{id}` with `{"photo": null}` now correctly clears the photo field; previously the null value was silently ignored due to `if data.photo is not None` guard in `update_product`
- BIT(1) columns now correctly map to Python `bool`; previously aiomysql returned raw bytes and `b'\x00'` (false) was incorrectly evaluated as `True` in boolean contexts
- `GET /api/v1/products/{id}` now returns `labels` (populated from the `product_label` junction table), satisfying FR-003/Acceptance Scenario 1 of `specs/002-master-data-endpoints/spec.md`; `POST /api/v1/products` and `PUT /api/v1/products/{id}` now accept a `labels: list[int]` field to assign/replace a product's labels (#74)

### Changed
- FK properties in master data list/detail responses now return the full referenced object instead of only its ID, one level deep (request bodies still accept plain IDs): `Product.supplier`/`unit_of_measurement`/`key`, `ProductPrice.price_list`, `Customer.price_list`/`salesperson`, `TaxpayerRecipient.postal_code`/`regime`, `Store.location`, `Warehouse.store`, `PointSale.store`/`warehouse`, `CashDrawer.store`, `PaymentMethodOption.store`/`warehouse`, `VehicleOperator.driver`/`creator`/`updater`, `ProductionSite.store`; new `StoreSummary`/`WarehouseSummary` schemas represent the flat (non-expanded) shape when embedded a second level deep (e.g. a `PointSale`'s embedded `warehouse` keeps its own `store` as a plain ID); `Store.address`/`Store.taxpayer` remain plain IDs since `Address`/`TaxpayerIssuer` have no read endpoint in this feature
- Password hashing simplified to SHA1-only; `verify_password` now compares hashes case-insensitively
- `currency` columns in all affected models now use `Mapped[CurrencyCode]` instead of `Mapped[int]`
  (models: `core.ExchangeRate`, `product.Product`, `sales.*`, `supplier.SupplierReturnDetail`,
  `purchases.PurchaseOrderDetail`, `fiscal.FiscalDocument`, `fiscal.FiscalDocumentDetail`)
- `ExchangeRate.base` and `ExchangeRate.target` now typed as `Mapped[CurrencyCode]`

### Removed
- `password_scheme` column from `User` model (not present in the real DB schema)
- bcrypt hashing and `passlib` dependency; `bcrypt_hash`, `verify_bcrypt`, `verify_sha1` removed
- SHA1→bcrypt migration logic on login

### Fixed
- All ruff rule violations across the codebase
  - E501: wrapped long lines (> 100 chars) in `mapped_column` calls, function signatures, and `raise` statements
  - F401: removed unused imports (`SmallInteger` in `technical_service.py`, `UTC` and `random_password` in `user_service.py`)
  - I001: fixed unsorted import block in `migrations/env.py`

## [0.1.0] - 2026-06-13

### Added
- FastAPI project bootstrap with `uv`, async SQLAlchemy 2.0, MariaDB via `aiomysql`
- JWT authentication with `session_version` invalidation pattern and SHA1→bcrypt migration on login
- User management module (spec §12): `User`, `AccessPrivilege`, `UserSettings` models with full CRUD API
- `CurrencyCode`, `AccessRight`, and `SystemObject` enums in `app/enums.py`
- All 98 database models from the data dictionary across 14 domain files:
  `sat_catalog`, `core`, `product`, `customer`, `supplier`, `sales`, `inventory`,
  `purchases`, `logistics`, `fiscal`, `technical_service`, `front_desk`, `commission`, `incidence`
- Async Alembic migration environment wired to application settings
- OpenAPI/Swagger UI available at `/docs` (ReDoc at `/redoc`)
