# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- **Sales cycle endpoints** — the first transactional capability in this API, covering `docs/specs/02-sales.md`. Six routers under `/api/v1/`: `/sales-orders` (7), `/sales-quotes` (30), `/customer-payments` (8), `/customer-refunds` (22), `/cash-sessions` (44, close gated by 111), `/credit-notes` (83). No new model and no column change — every table was already mapped
- Sales orders: create from configured defaults, line management snapshotting product code/name/tax/cost, minimum-quantity and profit-margin validation (bypassed by privilege 102), confirm assigning the folio and posting outbound stock, cancel posting a compensating inbound entry, currency change, and a barcode-aware product lookup (a 13-digit numeric pattern is a scan, not a search term)
- Customer payments: record, apply to an order, reverse with a **mandatory** reason, verify, reject, and the outstanding-orders search. An application is never deleted — reversing flips `cancelled` and writes an `incidence` entry naming who, when and why, so the payments editor sees the whole history rather than the live subset
- Customer refunds: open against a **paid** order pre-populated with refundable lines at quantity zero, per-line quantity capped at what is still refundable, confirm re-validating every quantity under a `FOR UPDATE` lock on the source order, and payout as cash from the open session or as a credit note at the cashier's choice
- Cash sessions: open with an opening amount, close with denomination counts, and a current-session response distinguishing **three** states — none, open-today, open-stale. A shift left open overnight needs a different remedy from having no shift, and a single falsy answer cannot express that
- Credit notes: listed with a remaining balance derived from the backing payment's non-cancelled applications. `refunded` is the amount issued and is never decremented, so there is no second balance to drift. Redemption has **no route of its own** — it is an ordinary payment application, which keeps it bounded, reversible and correctable by the code that already does those things
- `PaymentTerms`, `PaymentMethod`, `PaymentType`, `Priority`, `CashCountType`, `TransactionType` and `SourceType` enums in `app/enums.py`, values taken from `docs/constants.md`; five sales settings in `app/core/config.py` replacing legacy `WebConfig` values
- `CurrentUser` now carries `employee_id`, `point_sale_id` and `cash_drawer_id`, read from the already-loaded `User` row and its eager-loaded settings rather than the JWT — so no live session is invalidated and no re-login is forced. A user with no employee, or no point of sale, is refused up front with **distinguishable** errors instead of failing on a NOT NULL constraint
- Four shared service helpers: `totals.py` (money, quantized once at document level — rounding per line then summing drifts and would break the balance arithmetic), `documents.py` (folio assignment under a facility row lock, editability guard), `stock_ledger.py` (append-only `lot_serial_tracking`), `incidences.py` (audit entries)

> **Deployment note**: the lifecycle rules are enforced as clarified and are mutually exclusive by design. Only a completed, uncancelled order can be paid; only a completed **and paid** order can be refunded; a paid order is refused cancellation and directed to refund; and an order holding live payment applications cannot be cancelled until they are reversed. Because paying requires an uncancelled order, a paid order is necessarily uncancelled, so the refund path needs no separate cancellation check. A refundable order's balance is therefore always zero, which makes the legacy "apply the refund against the remaining balance" path unreachable — it is deliberately **not** implemented, and a refund never alters the order's `paid` flag or writes `balance_zeroed_time`.

> **Verified against a live database** (`mbe_demo`, every created row deleted afterwards): the order lifecycle end to end; the cash session lifecycle including the stale-session path; both refund payout forms, with the source order correctly staying paid; quote confirm/duplicate/convert with expired and cancelled conversions refused; credit-note redemption and its reversal restoring both balances exactly; stock on-hand falling 40 → 37 on confirm and returning to 40 on cancel with the original ledger entry intact and a reversal appended; and both concurrency invariants — two simultaneous folio assignments yielding distinct serials, and a second refund seeing only what the first left refundable.

> **Note**: all six list endpoints batch their derived figures per page rather than per row — a page of 50 costs the same query count as a page of 1, asserted by `tests/unit/test_list_query_counts.py` so a reintroduced loop fails the suite. `SystemObject.POS` (44) governs only the cash-session routes — no point-of-sale endpoints exist, by design.

- SQL migration `007_document_serial_unique.sql` (+ rollback): a unique index on `(facility, serial)` for `sales_order`, `sales_quote` and `customer_refund`, so a duplicate folio is refused by the database rather than only by application code. Two classes of pre-existing violation are corrected first, in order. `serial = 0` is the legacy application's placeholder for "not numbered", not folio zero — 4,240 rows carry it (4,065 sales quotes, 3,974 of them completed; 172 refunds; 3 orders, all from 2024 onward) and become `NULL`, which a MySQL unique index permits any number of. Then 34 rows dating 2018-2023 that genuinely share a folio are resolved by keeping the earliest document's number and moving the 21 later ones to the next free serials for their facility. **A reassigned `customer_refund` folio will no longer match a receipt printed years ago.** The `0 -> NULL` step must run before the renumber, or the 4,240 placeholder rows would be treated as duplicates and issued real folios. Verified against the live database inside a rolled-back transaction before shipping: zero duplicate groups remain on all three tables. The rollback drops the indexes only — the data corrections are not reversible
- `GET /api/v1/products/merge/preview?product_id=&duplicate_id=` — how much history rides on the duplicate, so an operator sees the blast radius before committing the irreversible merge (#111). Returns `{items: [{category, count}], total}`, counted per referencing `table.column` and sorted largest first, gated by `PRODUCTS_MERGE` (73) with `AllowRead`. The counts come from the same metadata-driven scan the delete guards use, so a new foreign key to `product` appears in the preview as soon as its model exists. It rejects the same pairs a merge would (`400` self-merge, `404` per missing side) and, being a read, touches nothing. Preview and merge enumerate the referencing relations through one shared helper, so the counts shown are exactly the rows the merge acts on — they cannot drift apart. They are what the merge *touches*, not what it reassigns: the four configuration relations it deletes are counted too, so a client labelling the whole total "will be reassigned" would overstate it
- SQL migration runner (`uv run python -m app.db.migrate`): applies every migration the target database has not yet received, in numeric-prefix order, and records each one in a `schema_migrations` ledger table inside that database — so what a database has received is a property of the database, not of whoever last ran something by hand. `status` reports applied vs pending; `mark` records a migration as applied without executing it, for changes made before this tooling existed. Statements are split client-side, so a failure names the exact statement, not just the file — which matters because MariaDB does not roll back DDL and a half-applied file needs manual resolution

### Changed
- **Constitution v1.2.0 — tests are no longer optional for any work.** The v1.1.0 carve-out that made tests OPTIONAL for "non-endpoint work (services, utilities, config)" is removed: a service, helper or utility carrying branching logic, state transitions or arithmetic now requires a `tests/unit/` file exercising those branches directly, not only through an endpoint. Test-first ordering becomes unconditional rather than applying only "when tests are included". A narrow exemption survives for work with no observable behaviour — renames, comments, docs, formatting — and it must be stated in the change rather than assumed silently. Note that `.specify/templates/tasks-template.md` still carries upstream Spec Kit boilerplate reading "Tests are OPTIONAL"; it ships with the toolchain, so it is flagged in the constitution's Sync Impact Report rather than edited, and generated task lists must override it
- SQL migrations now live in a single flat `migrations/` directory. `scripts/facility_rename.sql` became `migrations/004_facility_rename.sql` — numbered *before* 005, not chronologically, because 005 does `ALTER TABLE facility` and that table is only named `facility` because 004 renames it from `store`; the old numbering would have broken every fresh-database bootstrap. `migrations/sql/` and `scripts/` are gone

### Removed
- Alembic: `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/README`, and the dependency. It had been wired to application settings since the first release but never produced a single versioned migration — every real schema change was already hand-written SQL, so there was no history to convert

### Docs
- README documents the SQL migration workflow: where files live, the `NNN_name.sql` convention, the three commands, how to roll back by hand, and the `DELIMITER` limitation of the statement splitter

### Fixed
- **Profit-margin validation compared a price against a rate.** `product_price.low_profit` and `high_profit` are profit *rates* — every one of the 61,855 rows in `mbe_demo` has both between 0 and 1, typically `0.000000` and `1.000000`. The new line-add check compared the raw price against them, so a ₱23.00 price failed `23 > 1`: with `price_validation_in_range_required` on by default, only 734 of 61,855 product-price rows would have passed and 98.8% of the catalogue would have been unsellable. The check is now on the derived margin `(price - cost) / price` against those rates, which is what the columns mean. A zero price is not judged there — confirmation refuses it outright and dividing by it has no meaning. Found by driving a real order through `mbe_demo`; no unit test with invented fixtures would have caught it, because the invented bounds looked like prices
- **Reading a cashier's open cash session raised on legacy data.** `cash_session.end IS NULL` is not unique in practice — two cashiers in `mbe_demo` have three and four sessions open simultaneously — and the lookups used `scalar_one_or_none()`, which raises `MultipleResultsFound`. Recording a payment as either cashier returned a 500. The three affected queries now order by `start` and take one; `open_session` still refuses to *create* a second, so the tolerance only ever applies to pre-existing rows
- `POST /api/v1/products/merge` now moves every reference the duplicate carries, not the six a hand-written list happened to name (#112). Eleven mapped foreign keys were left behind — `sales_quote_detail`, `delivery_order_detail`, `fiscal_document_detail`, `customer_refund_detail`, `purchase_request_detail`, `customer_discount`, `lot_serial_rqmt`, `supplier_return_detail`, `service_order_detail.spare_part`, `commission_product` and `commissions_history` — so deleting the duplicate hit an enforced foreign key and the whole merge rolled back with a generic conflict. In `mbe_demo` that was 13,248 of 21,542 products: any product ever quoted, delivered, invoiced, refunded or requested could not be merged at all. `commission_product` and `commissions_history` are worse — they declare a `ForeignKey` in the model but have no constraint in the database, so those merges *succeeded* and left commission rows pointing at a deleted product (1,008 and 248 products carry such rows). The remap set now comes from the mapped metadata, the same source the delete guards and the merge preview read, so a new foreign key to `product` is covered as soon as its model exists. Verified against `mbe_demo` by merging the product with the most fiscal history (83,488 rows across 13 relations) inside a rolled-back transaction: the delete succeeds and nothing is left pointing at it, where the old path fails on `customer_refund_detail`
- A merge follows fiscal history rather than refusing to touch it: `fiscal_document_detail` rows are remapped like any other reference. A stamped CFDI keeps its own `product_code` / `product_name` snapshot, so what the document says was invoiced is unchanged — only the catalog row it points at moves, and that row is about to stop existing
- A merge moves the duplicate's *history* and discards its *configuration*. The four relations that describe how a catalog row is set up rather than what happened to it — `product_price`, `product_label`, `commission_product`, `customer_discount` — are deleted for the duplicate instead of being reassigned: the canonical is the row being kept, so its own prices, labels, commission assignment and per-customer discounts are the ones that survive. Each of the four has a unique key covering the product column, so the duplicate's rows could never all have landed on the canonical anyway; deleting them outright makes the outcome identical for every row rather than depending on which ones happened to collide. **A label or per-customer discount that existed only on the duplicate is not carried over** — set it on the canonical before merging if it should survive
- Database constraint violations return `409` instead of `500` (#107). Deleting a record that something still references is refused with the blocking `table.column` and row counts named in the error, so a client knows what to clear; the referencing tables are derived from the mapped metadata, so a new foreign key is covered as soon as its model exists. Creating or updating a warehouse, point of sale, cash drawer or vehicle with a duplicate `code` / `license_plate` is likewise `409` rather than `500`. An `IntegrityError` handler backstops anything not checked up front, returning a generic conflict — the driver message is logged, never returned, since it names tables and indexes
- `price_list` delete now reports every blocker, not only customers — it previously missed `product_price`

### Notes
- Deletes remain hard deletes and are never silently cascaded: the client removes references itself, and archiving stays an explicit `status` change. The two pre-existing owned-child cascades (a product's `product_price` rows, a user's `user_settings` / `access_privilege`) are a deliberate, closed exemption

### Fixed
- CSD upload stores `valid_from`/`valid_to` as Mexico City local time, matching the rows the legacy system wrote, instead of the UTC read off the certificate. Uploaded certificates were landing 6 hours ahead of every existing row in the same columns, so a certificate would have read as expiring 6 hours late. Found by running the parser against the real certificates in `mbe_demo`: certificate number, RFC and key/password validation reproduced the stored values exactly, the validity window did not
- `tzdata` added as a dependency, so the timezone lookup does not depend on the host carrying a system tz database
- FK expansion no longer overwrites the mapped column it expands, in the seven services that still did — cash drawers, payment method options, customers, vehicle operators, products, product prices and taxpayer recipients. The resolved object now lands on a `<column>_detail` key and the response field reads it through `AliasChoices`, so an instance shared through the session identity map keeps its raw FK for every other reader (#104, completing the fix #95 started). Response payloads are unchanged — verified by diffing the generated OpenAPI spec
- `product_price_service._price_list_id`, a workaround that read the FK id back off an already-clobbered attribute (#75), is removed as no longer needed

### Added
- CRUD endpoints for taxpayer issuers under `/api/v1/taxpayer-issuers` (list with `search` on RFC or name, create, get, update, delete), gated by `SystemObject.TAXPAYERS` (24) — previously unused; `regime` and `postal_code` are expanded to SAT catalog objects, so `Facility.taxpayer` is now resolvable and pickable by a client (#100)
- `DELETE /api/v1/taxpayer-issuers/{rfc}` returns `409` when the issuer is still referenced by a facility, certificate, fiscal batch or fiscal document, instead of letting the FK violation surface as a `500`
- Endpoints for taxpayer certificates under `/api/v1/taxpayer-certificates` (list with `taxpayer`/`status` filters, get by certificate number, and CSD upload), gated by `SystemObject.TAXPAYERS` (24). Responses carry metadata only — `taxpayer_certificate_id`, `taxpayer`, `valid_from`, `valid_to`, `status`. The `certificate_data`/`key_data` CSD binaries and the raw `key_password` are excluded from the queries themselves, not just from the response schema, so they are never read out of the database
- `POST /api/v1/taxpayer-certificates` — multipart upload of a CSD pair (`taxpayer`, `certificate` `.cer`, `key` `.key`, `key_password`). The pair is validated before anything is stored: the password must open the key, the key must match the certificate's public key, and the RFC in the certificate subject must match the issuer it is being attached to. The certificate number and the `valid_from`/`valid_to` window are read out of the certificate rather than taken from the request. Returns `422` for an unreadable or mismatched CSD and `409` when the certificate number is already registered; `app/services/csd_service.py`
- `cryptography` promoted to a direct dependency — it was already resolved transitively through `python-jose`

> **Deployment note**: `SystemObject.TAXPAYERS` (24) existed in the enum but governed no endpoint before this release, so no `access_privilege` row grants it. Until it is granted, the taxpayer issuer and certificate endpoints are reachable only by administrators, who bypass privilege checks.
- `FiscalCertificationProvider` int enum (`0` none / `1` diverza / `2` fiscoclic / `3` servisim / `4` profact) in `app/enums.py`, ported from `Model/Constants/FiscalCertificationProvider.cs`; `TaxpayerIssuer.provider` is typed with it instead of a bare `int` and is exposed on the API
- Cross-FK validation on `POST`/`PUT /api/v1/points-of-sale`: a point of sale is rejected (`422`) when the referenced `warehouse` belongs to a different `facility`, including when only `facility` changes on update, and `404` when the warehouse does not exist (#102)
- Optional free-text `search` query param on `GET /api/v1/facilities`, `GET /api/v1/warehouses`, `GET /api/v1/points-of-sale` and `GET /api/v1/cash-drawers` — case-insensitive substring match on the record's `code` or `name`, combinable with the existing facet params, matching the semantics already used by customers/employees/suppliers (#86, #87, #88, #89)
- CRUD endpoints for addresses under `/api/v1/addresses` (list with `search`/`type`/`status` filters, create, get, update, delete), gated by `SystemObject.ADDRESSES` (11); `app/services/address_service.py`. `Facility.address` (and every other address FK) is now resolvable and pickable by a client (#90)
- `AddressType` int enum (`0` other / `1` home / `2` work / `3` business / `4` fiscal) in `app/enums.py`; `Address.type` is typed with it instead of a bare `int`
- `POST /api/v1/facilities/{id}/logo` — multipart image upload mirroring `POST /api/v1/products/{id}/image`; stores the processed filename in `facility.logo` and returns the updated facility (#91)
- SQL migration script `migrations/sql/006_facility_logo_nullable.sql` (+ rollback script): makes `facility.logo` nullable and clears empty strings and legacy ASP.NET virtual paths (`~/Content/images/...`, unrenderable by any client) to `NULL` — affected facilities show no logo until one is re-uploaded through the new endpoint
- `EntityStatus`, `FacilityType` and the corrected `AddressType` sections in `docs/constants.md`

### Changed
- **Breaking**: `FacilityResponse.address` now returns the expanded address object instead of the bare `int` FK, matching how `location` is already expanded on the same model; `FacilitySummary.address` stays a raw `int`, keeping the embedded summary flat (#101)
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
