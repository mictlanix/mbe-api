# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Optional free-text `search` query param on `GET /api/v1/facilities`, `GET /api/v1/warehouses`, `GET /api/v1/points-of-sale` and `GET /api/v1/cash-drawers` — case-insensitive substring match on the record's `code` or `name`, combinable with the existing facet params, matching the semantics already used by customers/employees/suppliers (#86, #87, #88, #89)
- CRUD endpoints for addresses under `/api/v1/addresses` (list with `search`/`type`/`status` filters, create, get, update, delete), gated by `SystemObject.ADDRESSES` (11); `app/services/address_service.py`. `Facility.address` (and every other address FK) is now resolvable and pickable by a client (#90)
- `AddressType` int enum (`0` other / `1` home / `2` work / `3` business / `4` fiscal) in `app/enums.py`; `Address.type` is typed with it instead of a bare `int`
- `POST /api/v1/facilities/{id}/logo` — multipart image upload mirroring `POST /api/v1/products/{id}/image`; stores the processed filename in `facility.logo` and returns the updated facility (#91)
- SQL migration script `migrations/sql/006_facility_logo_nullable.sql` (+ rollback script): makes `facility.logo` nullable and clears empty strings and legacy ASP.NET virtual paths (`~/Content/images/...`, unrenderable by any client) to `NULL` — affected facilities show no logo until one is re-uploaded through the new endpoint
- `EntityStatus`, `FacilityType` and the corrected `AddressType` sections in `docs/constants.md`

### Changed
- **Breaking**: `FacilityCreate.logo` is now optional — a facility can be created without a logo and given one later via the upload endpoint (#91)
- `FacilityResponse.logo` and `FacilitySummary.logo` now return a renderable URL (`{images_base_url}/images/{filename}`, or `/images/{filename}` when `images_base_url` is unset) instead of the bare stored filename, matching how `Product.photo` is already returned; `null` when the facility has no logo (#91)
- `docs/data-dictionary.md` now documents the `status` column on all 13 status-bearing tables instead of the `disabled`/`active`/`deactivated`/`enabled` columns that migration 005 dropped (the `employee` pair collapses to a single row), plus the nullable `facility.logo`
- `/api/v1/facilities`, `/api/v1/warehouses`, `/api/v1/points-of-sale` and `/api/v1/cash-drawers` are now gated by `require_privilege` — `Facilities` (29), `Warehouses` (4), `PointsOfSale` (9) and `CashDrawers` (10) respectively, with `READ` on list/get and `CREATE`/`UPDATE`/`DELETE` on the mutating routes; they previously required only an authenticated session. `Facilities` (29) is the former `Stores` object, renamed in the Store + ProductionSite merge; the retired `ProductionSites` (107) does not govern anything (#93)

### Changed
- **Breaking**: every boolean lifecycle flag is replaced by a single integer `status` field (`0` = active, `1` = inactive, `2` = archived, `EntityStatus` enum) across all status-bearing entities — users, customers, products, employees, facilities, warehouses, points of sale, cash drawers, payment method options, vehicles, vehicle operators (plus persistence-only addresses and taxpayer certificates). The legacy fields `disabled` (user/customer/facility/warehouse/point_sale/cash_drawer), `active` (employee/vehicle/vehicle_operator), `deactivated` (product), and `enabled` (payment_method_option) no longer exist in requests or responses; `Employee` in particular collapses its duplicate `active`+`disabled` pair into the one `status` field (#80, #81)
- **Breaking**: lifecycle list filters are now uniform — `?status=<0|1|2>` on every status-bearing list endpoint (users, customers, products (both list variants incl. `labels/facets`), employees, facilities, warehouses, points-of-sale, cash-drawers, payment-method-options, vehicles, vehicle-operators), replacing the previous `?deactivated` (products), `?disabled` (customers), and `?active` (employees) parameters
- Login is rejected for any user whose `status` is not active (`0`), preserving the former disabled-user rejection and extending it to archived users

### Added
- `EntityStatus` int enum (`0` active / `1` inactive / `2` archived) in `app/enums.py`
- SQL migration script `migrations/sql/005_unified_entity_status.sql` (+ rollback script): adds the non-nullable `status` column to 13 tables, backfills it from the legacy flag(s) (restrictive flag wins for `employee`), then drops the legacy columns

### Changed
- **Breaking**: `store` renamed to `facility` throughout — table `store` → `facility`, PK `store_id` → `facility_id`, every FK column named `store` on other tables (`cash_drawer`, `customer_payment`, `customer_refund`, `delivery_order`, `expense_voucher`, `fiscal_document`, `inventory_issue`, `inventory_receipt`, `inventory_transfer`, `payment_method_option`, `point_sale`, `sales_order`, `sales_quote`, `special_receipt`, `user_settings`, `warehouse`) → `facility`; API routes `/stores` → `/facilities`; embedded `store` JSON fields in responses → `facility`
- `facility` gains a new `type` column — `FacilityType` int enum (`0` = store, `1` = production_site, default `0`)

### Removed
- `production_site` entity removed — production sites are now `facility` rows with `type = 1` (`PRODUCTION_SITE`); the `/production-sites` endpoints and `ProductionSites` (107) `SystemObject` no longer exist

### Added
- `GET /api/v1/products/labels/facets` — returns `[{label_id, count}, ...]` for every label carried by at least one product matching the same filters as `GET /api/v1/products` (`search`, `label`, `deactivated`, `stockable`, `salable`, `purchasable`, `supplier`; no `skip`/`limit`), so clients can grey out labels that would narrow the current result set to zero (#78)
- CRUD endpoints for per-product prices under `/api/v1/product-prices` (list with `product`/`price_list` filters, create, get, update, delete), gated by `SystemObject.PRICING`; `app/schemas/product_price.py` and `app/services/product_price_service.py`

### Changed
- `settings` in `GET /api/v1/auth/me` (and `/api/v1/users/{id}`) now carries the resolved `store_code`/`store_name`, `point_sale_code`/`point_sale_name`, and `cash_drawer_code`/`cash_drawer_name` alongside the existing ids, so clients can show the caller's location context without catalog-read privileges; the `*_id` fields are unchanged, making this additive for existing clients (#79)
- `Product.unit_of_measurement` in `GET /api/v1/products` and `GET /api/v1/products/{id}` now returns the full `sat_unit_of_measurement` record (`{id, name, description, symbol}`) instead of the generic `{id, description}` shape used by other SAT catalog FKs; new `SatUnitOfMeasurementResponse` schema in `app/schemas/sat_catalog.py`
- `label` filter on `GET /api/v1/products` now accepts multiple values via repeated query params (e.g. `?label=2&label=5`); when more than one is given, only products carrying **all** requested labels are returned (a single `label` value behaves as before)

### Removed
- `ProductResponse.prices` field — product endpoints no longer return or manage pricing data; use `GET /api/v1/product-prices?product={id}` instead
- Auto-creation of a zeroed `ProductPrice` row per price list on `POST /api/v1/products`; new products now start with zero prices until explicitly created via `/api/v1/product-prices`

### Fixed
- `PUT /api/v1/products/{id}` no longer returns HTTP 500 for products with price list entries; `_attach_price_relations` in `app/services/product_service.py` was passing a stale `PriceList` ORM object (injected by the endpoint's earlier `get_product` call) into a `.in_()` clause instead of its integer FK (#75)

### Docs
- Synced speckit docs for features `001`–`004` with the implementation: all four spec statuses set to Implemented; `002` contract's `ProductListItem` gained the missing `sku` field and a note that product endpoints require the `PRODUCTS` privilege (spec assumption updated to match); `003` spec/research corrected — a missing images directory no longer fails startup, it is created on first upload (`check_dir=False`)

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
