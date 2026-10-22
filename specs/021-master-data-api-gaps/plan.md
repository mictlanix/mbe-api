# Implementation Plan: Master Data API Gaps

**Branch**: `021-master-data-api-gaps` | **Date**: 2026-10-21 | **Spec**: [spec.md](./spec.md)

> **Scale note**: about 30 files across five independent slices, each shippable alone:
> - supplier sub-panels (US1);
> - customer discounts (US2);
> - incidences (US3);
> - barcode search (US4);
> - license photo (US5).
>
> Most of the work is CRUD written to existing patterns. Watch three things:
> - `SupplierResponse` must stay unchanged, because `ProductResponse` embeds it (R2).
> - The license photo must never touch the public `/images` path (R8).
> - Each incidence must be staged before its change's commit, not after it, which is where the old TODOs sat (R6).

## Summary

The work covers five pieces of pending [API] work in the Master data epic. All of them use tables that already exist, except for one new column.

1. **Supplier detail.** It gains addresses, contacts, bank accounts and agreements. The three link sets use the customer replace-all id-list pattern, bank accounts get a standalone `/bank-accounts` resource, and agreements are nested under `/suppliers/{id}/agreements`.
2. **Customer discounts.** They become a nested `/customers/{id}/discounts` resource, unique per product. They are maintained only and never applied to prices.
3. **Incidences.** Product create, update and delete, and user update, delete, profile application and administrator-issued recovery token, write an incidence through the existing `incidences.record`.
4. **Product search.** It also matches `bar_code` exactly.
5. **License photo.** Vehicle operators get one, stored privately in the same way as proof-of-delivery images, behind an authenticated route, with migration 022 adding `vehicle_operator.license_photo`.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- FastAPI, SQLAlchemy 2.0 async, Pydantic v2 and Pillow, all of which are already used by `image_service`.
- No new dependency.

**Storage**:
- MariaDB `mbe_dev`.
- One new nullable column, added by migration 022. No other DDL: the `customer_discount` unique key already exists (R5).
- Files go in a new private directory, `settings.license_photos_dir`.

**Testing**:
- pytest, pytest-asyncio and httpx `ASGITransport`.
- `tests/api/` for routes, `tests/unit/` for services, and `tests/integration/` (SQLite) for the unique key, FK failures and transactional incidences.

**Target Platform**: Linux server (xolotl): test on systemd :8766, dev in tmux :8765.

**Project Type**: web service (REST API)

**Performance Goals**:
- Supplier detail: one query per collection, so 4 extra queries on the detail only. The list stays flat (`tests/unit/test_list_query_counts.py`).
- Discount list: a single join.

**Constraints**:
- `SupplierResponse`, the list and embed schema, is unchanged.
- License photos are reachable only through an authenticated route.
- Each incidence commits atomically with its change.
- Pseudo-schemas in the contracts match the live OpenAPI (R10).
- Every column in the deployed schema is described in `docs/data-dictionary.md` (`tests/unit/test_data_dictionary.py`).

**Scale/Scope**:
- mbe_dev data: 265 suppliers, with 119 address links, 154 contact links, 2 bank account links and 18 agreements; 0 customer discounts; 43 products with a barcode; 14 operators.
- 16 new routes:
  - `/bank-accounts`: 5
  - supplier agreements: 4
  - customer discounts: 4
  - license photo: 3
  - the rest change the behaviour of existing routes.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | Pass | No generic link helper. The supplier link functions copy the customer ones (about 40 lines), which is cheaper than refactoring working code. The license photo reuses `save_proof_image` rather than a parallel writer. The only new helper is `delete_image`, used on three paths. |
| II. Think Before Coding | Pass | Four departures from or corrections to the request were surfaced and decided in clarify (Q1–Q4). Further findings are recorded in research: the `SupplierResponse` embed (R2), the contract-doc test (R10), and the vehicle operator `creator_id=0` default, which is noted below and not touched. |
| III. Surgical Changes | Pass | The existing routes change only where an FR requires: suppliers' detail schema, product and user service signatures (`updater`), and the product search filter. Nothing else in `customer_service`, `delivery_itinerary_service` or `_find_salable` changes. |
| IV. Goal-Driven Execution | Pass | Each FR maps to a test file in quickstart.md. SC-001 and SC-005 also have manual checks against mbe_dev. |
| V. Reuse Over Rebuild | Pass | Reused: the `_set_links` pattern, `assert_not_referenced`, the IntegrityError backstop, `incidences.record`, `SourceType.PRODUCT/USER_SETTINGS`, `SystemObject.BANK_ACCOUNTS`, `save_proof_image`, `FileResponse` serving, `ListResponse`, and `AddressResponse`/`ContactResponse`. The new modules are justified below. |
| VI. Async-First | Pass | Every handler is `async def`. Image work and file removal run in `asyncio.to_thread`. |
| VII. Security by Default | Pass | Every new route requires `get_current_user`, and `/bank-accounts` also requires its privilege. License photos stay out of the static mount (FR-024). Incidence text holds field names, never values (R6). Gating the customer, supplier and operator routers is out of scope by request (FR-031). |
| VIII. Ruff Compliance | Pass | The gate runs before every commit. |
| Testing | Pass | Tests first, for every route (happy path, 401, 404, 409 and 422 where they apply) and every service branch: replace-all semantics, merged-date validation, duplicate and unknown product, incidence wording, file removal ordering. |
| Changelog | Pass | `[Unreleased]` gets the Added entries: bank accounts, supplier sub-panels, agreements, discounts, license photo and barcode search. The Changed entry covers product and user incidences and the supplier detail schema. It carries operations notes for migration 022 and `LICENSE_PHOTOS_DIR`. |

**Post-design re-check**: still passes. The design added one column, one setting and no dependency.

## Project Structure

### Documentation (this feature)

```text
specs/021-master-data-api-gaps/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R10
├── data-model.md
├── quickstart.md
├── contracts/
│   └── master-data-gaps.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
app/
├── api/v1/
│   ├── router.py                         # + bank-accounts router
│   └── endpoints/
│       ├── bank_accounts.py              # NEW (R3)
│       ├── suppliers.py                  # detail schema; + agreements routes (R2, R4)
│       ├── customers.py                  # + discounts routes (R4)
│       ├── products.py                   # pass current.employee_id to create/update/delete/image (R6)
│       ├── users.py                      # pass current.employee_id to update/delete/recover-password (R6)
│       ├── user_profiles.py              # pass current.employee_id to apply_profile (R6)
│       └── vehicle_operators.py          # + license-photo POST/GET/DELETE; delete removes file (R8)
├── core/config.py                        # + license_photos_dir
├── models/
│   ├── customer.py                       # CustomerDiscount UniqueConstraint (R5)
│   └── core.py                           # VehicleOperator.license_photo
├── schemas/
│   ├── core.py                           # BankAccount*, VehicleOperatorResponse.has_license_photo
│   ├── supplier.py                       # link lists, SupplierDetailResponse, SupplierAgreement*
│   ├── product.py                        # + ProductSummary (beside ProductListItem)
│   └── customer.py                       # CustomerDiscount*
└── services/
    ├── bank_account_service.py           # NEW
    ├── supplier_agreement_service.py     # NEW
    ├── customer_discount_service.py      # NEW
    ├── supplier_service.py               # _get_links/_attach_links/_set_links (R1)
    ├── product_service.py                # bar_code term (R7); incidences (R6)
    ├── user_service.py                   # incidences: the two TODOs, apply_profile, initiate_recovery (R6)
    ├── vehicle_operator_service.py       # set/clear photo; delete returns the filename to remove
    └── image_service.py                  # + delete_image (R8)

migrations/
├── 022_vehicle_operator_license_photo.sql
└── 022_vehicle_operator_license_photo_rollback.sql

docs/
├── data-dictionary.md                    # vehicle_operator.license_photo
└── specs/
    ├── 01-master-data.md                 # §1 barcode search, §3 discounts note, §6 routes, §16 photo
    └── 12-users.md                       # §1 incidence keying

specs/002-master-data-endpoints/contracts/api.md   # SupplierCreate/Update, VehicleOperatorResponse (R10)
CHANGELOG.md

tests/
├── api/
│   ├── test_bank_accounts.py             # NEW
│   ├── test_supplier_sub_panels.py       # NEW (detail, links, agreements)
│   ├── test_customer_discounts.py        # NEW
│   ├── test_vehicle_operator_license_photo.py   # NEW
│   ├── test_products.py                  # updater passed; barcode search param
│   └── test_suppliers_employees.py       # existing supplier tests follow the new detail schema
├── unit/
│   ├── test_supplier_agreement_service.py   # NEW
│   ├── test_customer_discount_service.py    # NEW
│   ├── test_product_service.py           # filter term, incidence per action
│   ├── test_user_service.py              # incidence per action, keyed by employee id
│   ├── test_supplier_links.py            # NEW: _get_links/_set_links branches (None / [] / ids)
│   ├── test_bank_account_service.py      # NEW: search branch
│   ├── test_image_service.py             # delete_image, missing file tolerated
│   └── test_vehicle_operator_service.py  # photo set/replace/clear return the old name
└── integration/
    ├── test_supplier_links_flow.py       # NEW: unknown link id gives 409 with no change; delete blocked by links
    ├── test_customer_discount_flow.py    # NEW: unique (customer, product) on a real schema
    ├── test_master_data_audit.py         # NEW: incidences committed with the change; rollback leaves none
    └── test_product_flag_filters.py      # barcode exact versus partial
```

**Structure Decision**: This is the existing single-project layout, unchanged. Endpoints, services and schemas go in the same modules as their parent resource, and the three new services are standalone modules (see Complexity Tracking).

## Implementation Notes

- **The order of file operations decides correctness** (R8). Write the new file, then commit the row, and only then remove the old file. The service functions return the filename to remove, and the endpoint calls `delete_image` after the service has committed. That keeps the service free of filesystem side effects, which makes it unit-testable.
- **Product service signature change.** `create_product`, `update_product` and `delete_product` gain a keyword-only `updater: int`. Every caller must be updated: the endpoints, the image upload route, and any test or service that calls them. `grep -rn "create_product\|update_product\|delete_product" app tests` before changing them.
- **Pre-existing and not touched**: the vehicle operator routes never pass the current user, so `creator` and `updater` are always 0 (`vehicle_operator_service.py:74,98`). That is outside this feature; it is reported here and not fixed (constitution III).
- **Supplier delete.** With links now writable, `DELETE /suppliers/{id}` returns 409 for any supplier that has links or agreements. This is existing behaviour, the same as customers, and is tested so that it stays explicit.

## Complexity Tracking

| New element | Why Needed | Simpler Alternative Rejected Because |
|-------------|------------|-------------------------------------|
| `bank_account_service.py` and its router | Clarification Q1: bank account records need create, read, update and delete before they can be linked. | Inline creation inside the supplier body was option C, which the user rejected. |
| `supplier_agreement_service.py` | Agreements are owned rows with their own validation (end ≥ start, merged on update) and their own ownership check. | Folding them into `supplier_service.py` mixes the link replace-all logic with child CRUD in one module that tests mock differently. |
| `customer_discount_service.py` | Discounts are owned rows with a duplicate check, a product-existence check and an ownership check. | Adding them to `customer_service.py` (300 lines, three concerns already) makes it harder to review. The new module is about 80 lines. |
| `SupplierDetailResponse` | The detail needs four collections that the list and the product embed must not carry (R2). | Adding the lists to `SupplierResponse` puts empty arrays in every product response. Renaming the list schema breaks existing clients. |
| `ProductSummary` | The discount list needs the product's code and name without the unit-of-measure expansion. | `ProductListItem` requires relation attachment and photo-URL rewriting for fields nobody asked for. |
| `license_photos_dir` setting | A separate private directory for operations and backups. | Reusing `pod_dir` mixes personal identity documents into a directory named and retained for delivery proofs. |
| `image_service.delete_image` | File removal on replace, clear and operator delete. | An inline `os.remove` in three places, each needing the same missing-file tolerance and thread offload. |
