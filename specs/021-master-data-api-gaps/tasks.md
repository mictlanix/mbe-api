# Tasks: Master Data API Gaps

**Input**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/master-data-gaps.md](./contracts/master-data-gaps.md), [quickstart.md](./quickstart.md)

> **Scale note**: 55 tasks across five independent slices.
> - US3 (incidences) and US4 (barcode search) both edit `app/services/product_service.py` and `tests/unit/test_product_service.py`. Run those two phases one after the other.
> - Every other story touches its own files, apart from additive edits to `docs/specs/01-master-data.md` and `specs/002-master-data-endpoints/contracts/api.md`.

**Tests**: required by the constitution. In every story the test tasks come first and must fail before the implementation. The template's "tests are optional" line does not apply to this project.

**One commit per test-and-code pair**: the constitution requires tests to be committed in the same task as the endpoints they cover. Each test task is marked **(commit with T0xx)**. Write its tests first, watch them fail, then implement T0xx and commit both together. When several test tasks name the same T0xx, they all go into that one commit.

**Contract docs**: `tests/unit/test_contract_docs.py` fails for every component named in [contracts/master-data-gaps.md](./contracts/master-data-gaps.md) until that component exists (research R10). Each story's last code task makes its own blocks pass. By T053 nothing may fail.

## Format: `- [ ] T### [P?] [US#] Description · path`

- `[P]`: independent of the other open tasks in its phase (different file, no incomplete dependency).
- `[US#]`: the user story from the spec.

---

## Phase 1: Setup

- [X] T001 [P] Write migration 022 and its rollback.
  - **Forward**: `ALTER TABLE vehicle_operator ADD COLUMN IF NOT EXISTS license_photo VARCHAR(255) NULL COMMENT '...'`.
  - **Rollback**: `DROP COLUMN IF EXISTS`. It must warn that the stored files become orphaned and give the `SELECT vehicle_operator_id, license_photo … WHERE license_photo IS NOT NULL` to run first.
  - Write the header comments in the style of `021_user_must_change_password.sql`: why, default, idempotency, MariaDB 10.11 (research R9).

  Files: `migrations/022_vehicle_operator_license_photo.sql`, `migrations/022_vehicle_operator_license_photo_rollback.sql`
- [X] T002 [P] Add `license_photos_dir: str = 'license-photos'` beside `pod_dir`. Add a comment saying it is private, outside `images_dir`, and never mounted (research R8) · `app/core/config.py`

## Phase 2: Foundational

- [X] T003 Run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q` on the branch before any code change. Record which tests fail before the work starts, so that later failures can be attributed. Expected: everything passes except `tests/unit/test_contract_docs.py` for the new contract's components, as research R10 predicts. List any other failure in this task before continuing · `specs/021-master-data-api-gaps/tasks.md`
  - **Baseline (2026-10-21)**: ruff clean; pytest 2861 passed, 11 failed. All 11 failures are `test_a_documented_component_exists` for this feature's not-yet-built components: `SupplierDetailResponse`, `SupplierAgreementCreate/Update/Response`, `BankAccountCreate/Update/Response`, `CustomerDiscountCreate/Update/Response`, `ProductSummary`. There were no other failures.
  - T046 (data dictionary) was done here alongside T001. The migration adds the column, and `test_data_dictionary.py` would otherwise fail from this commit on.

**Checkpoint**: the baseline is known. The user stories can start.

---

## Phase 3: User Story 1 — Supplier sub-panels (P1) 🎯 MVP

**Goal**: The supplier detail shows its addresses, contacts, bank accounts and agreements. The three link sets are replace-all id lists, bank accounts have their own resource, and agreements are nested under the supplier.

**Independent test**: `GET /suppliers/{id}` returns all four lists. Replacing the contacts and adding an agreement changes exactly those, and `GET /suppliers` rows carry no lists.

### Tests (write first, must fail)

- [ ] T004 [P] [US1] **(commit with T012)** API tests for `/bank-accounts`, mocking the service the way `tests/api/test_contacts.py` does:
  - list with `search`, `skip` and `limit`;
  - create returns 201, and a create without `bank_name` or `account_number` returns 422;
  - get and update, each returning 404 for an unknown id;
  - delete returns 204, and 409 when the service raises the not-referenced conflict;
  - 401 without a token, and 403 for a non-admin user lacking `SystemObject.BANK_ACCOUNTS`.

  File: `tests/api/test_bank_accounts.py`
- [ ] T005 [P] [US1] **(commit with T015)** Unit tests for the agreement service:
  - Create rejects `end < start` with 422 and accepts `start == end`.
  - Update validates the *merged* values: sending only `end` earlier than the stored `start` returns 422.
  - Get, update and delete return 404 when the agreement's `supplier` differs from the path supplier.
  - The list is ordered by `start`, then id.

  File: `tests/unit/test_supplier_agreement_service.py`
- [ ] T006 [P] [US1] **(commit with T016)** API tests for the supplier sub-panels:
  - **Detail**: `GET /suppliers/{id}` includes `addresses`, `contacts`, `bank_accounts` and `agreements`.
  - **List**: `GET /suppliers` items have none of those keys.
  - **Responses**: POST and PUT return the detail shape.
  - **Links**: POST and PUT pass `addresses`, `contacts` and `bank_accounts` through to the service. Omitted means `None` and `[]` means `[]`.
  - **Agreements**: GET list (200, 404 for an unknown supplier), POST (201, 422 when `end < start`), PUT (200, 404), DELETE (204, 404).
  - **Auth**: 401 without a token on each new route.

  File: `tests/api/test_supplier_sub_panels.py`
- [ ] T007 [P] [US1] **(commit with T016)** Integration tests on the SQLite schema:
  - Create a supplier with address, contact and bank account ids and read back the detail.
  - Replace the contacts with `[]` and confirm the address links are untouched.
  - An unknown address id returns 409, and the supplier's existing links are unchanged.
  - `DELETE /suppliers/{id}` returns 409 while links or agreements exist and 204 once they are cleared.
  - `DELETE /bank-accounts/{id}` returns 409 while the account is linked.

  File: `tests/integration/test_supplier_links_flow.py`
- [ ] T008 [P] [US1] **(commit with T014)** Unit tests for supplier `_get_links` and `_set_links`, modelled on `tests/unit/test_customer_links.py`:
  - An omitted list (`None`) issues no statement for that collection.
  - An empty list (`[]`) deletes that collection's links and inserts nothing.
  - A list with ids deletes, then bulk-inserts `{'supplier': id, 'address' | 'contact' | 'bank_account': x}`.
  - The asserted column names are taken from the `Table()` definitions in `app/models/supplier.py`, not retyped (the #154 lesson).
  - Agreements are read ordered by `start`, then id.

  File: `tests/unit/test_supplier_links.py`
- [ ] T009 [P] [US1] **(commit with T011)** Unit test: `list_bank_accounts` with `search` filters with ILIKE on `bank_name`, `account_number` and `reference`; without `search` it applies no filter; `skip` and `limit` are applied · `tests/unit/test_bank_account_service.py`

### Implementation

- [ ] T010 [P] [US1] Add `BankAccountCreate`, `BankAccountUpdate` and `BankAccountResponse` beside the contact schemas, with `max_length` 250/20/20/18/500 per data-model.md · `app/schemas/core.py`
- [ ] T011 [US1] Create `bank_account_service.py` with `list_bank_accounts` (ILIKE on `bank_name`, `account_number` and `reference`), `get`, `create`, `update` and `delete` (`assert_not_referenced`). Mirror `contact_service.py`, including a module docstring explaining why the resource exists (#13) · `app/services/bank_account_service.py`
- [ ] T012 [US1] Create the `/bank-accounts` router. Gate each route with `require_privilege(SystemObject.BANK_ACCOUNTS, READ|CREATE|UPDATE|DELETE)` the same way `addresses.py` is gated, then register it in `router.py` with tag `bank-accounts`. Files: `app/api/v1/endpoints/bank_accounts.py`, `app/api/v1/router.py`
- [ ] T013 [US1] Supplier schemas:
  - `SupplierCreate` and `SupplierUpdate` gain `addresses`, `contacts` and `bank_accounts` as `list[int] | None = None`, with the replace-all `#:` comment copied from `CustomerCreate`.
  - Add `SupplierDetailResponse(SupplierResponse)` with the four lists, each defaulting to `[]`.
  - Add `SupplierAgreementCreate` (with a `model_validator` rejecting `end < start`), `SupplierAgreementUpdate` (all optional) and `SupplierAgreementResponse`.
  - Leave `SupplierResponse` unchanged (research R2).

  File: `app/schemas/supplier.py`
- [ ] T014 [US1] Supplier links in `supplier_service`:
  - Add `_get_links`, `_attach_links` and `_set_links`, modelled on `customer_service.py:54-162`, over `supplier_address`, `supplier_contact`, `supplier_bank_account` and `SupplierAgreement`. Agreements are read only, ordered by `start`, then id.
  - `get_supplier_detail` returns the supplier with links attached.
  - `create_supplier` flushes, sets the links, commits and attaches.
  - `update_supplier` sets the links before committing and then attaches.
  - `get_supplier` stays as it is for the list and for existence checks.

  File: `app/services/supplier_service.py`
- [ ] T015 [US1] Create `supplier_agreement_service.py` with:
  - `list_agreements(db, supplier_id)`;
  - `get_agreement(db, supplier_id, agreement_id)`, which returns `None` when the agreement belongs to another supplier;
  - `create_agreement`;
  - `update_agreement`, which checks the merged `end >= start` and raises 422;
  - `delete_agreement`.

  File: `app/services/supplier_agreement_service.py`
- [ ] T016 [US1] Supplier routes:
  - GET `/{id}`, POST and PUT return `SupplierDetailResponse` through `get_supplier_detail`.
  - Add `GET` and `POST /{supplier_id}/agreements`, and `PUT` and `DELETE /{supplier_id}/agreements/{supplier_agreement_id}`. Each returns 404 for an unknown supplier and for an agreement belonging to another supplier.
  - Access stays `get_current_user` (FR-031).

  File: `app/api/v1/endpoints/suppliers.py`
- [ ] T017 [US1] Update the existing supplier tests for the detail response, which now carries the four lists and is mocked through `get_supplier_detail` · `tests/api/test_suppliers_employees.py`
- [ ] T018 [P] [US1] Add `addresses`, `contacts` and `bank_accounts` (`[int, ...] | null`) to the `SupplierCreate / SupplierUpdate` block, and list the new routes in the suppliers section · `specs/002-master-data-endpoints/contracts/api.md`
- [ ] T019 [P] [US1] Scope 01 §6: describe the four sub-panels as delivered. Give the link semantics (replace-all id lists), the `/bank-accounts` resource, the agreement routes, the `end >= start` rule, and the fact that links block a supplier's deletion · `docs/specs/01-master-data.md`

**Checkpoint**: T004–T009 pass, and so do the contract-doc blocks for the supplier and bank account components. US1 is shippable alone.

---

## Phase 4: User Story 2 — Customer discounts (P1)

**Goal**: A customer's product discount overrides can be listed, added, changed and removed. There is at most one per product, and none of it affects pricing.

**Independent test**: add an override, list it with the product's code and name, change the rate, check that a duplicate returns 409, then remove it.

### Tests (write first, must fail)

- [ ] T020 [P] [US2] **(commit with T025)** Unit tests for the discount service:
  - Create raises 422 `Product not found` for an unknown product, and 409 `Customer already has a discount for this product` for a duplicate.
  - Get, update and delete return `None` or 404 for an override belonging to another customer.
  - Update changes only `discount`.
  - The list is ordered by product code and carries `product_id`, `code` and `name`.

  File: `tests/unit/test_customer_discount_service.py`
- [ ] T021 [P] [US2] **(commit with T026)** API tests for `/customers/{customer_id}/discounts`:
  - list: 200, and 404 for an unknown customer;
  - POST: 201, 404, 409, and 422 for a rate of `-0.01`, a rate of `1.01`, 9 decimal places, or a missing `product`;
  - PUT: 200 and 404. A body that also sends `product` still returns 200, and `product` is not passed to the service, because the update schema has no such field and extra fields are ignored (FR-011);
  - DELETE: 204 and 404;
  - 401 on each route.

  File: `tests/api/test_customer_discounts.py`
- [ ] T022 [P] [US2] **(commit with T026)** Integration test on SQLite:
  - Add an override and list it.
  - A second override for the same customer and product returns 409 and leaves one row.
  - The same product for a different customer is allowed.
  - Deleting the override leaves 0 rows.

  File: `tests/integration/test_customer_discount_flow.py`

### Implementation

- [ ] T023 [P] [US2] Add `__table_args__ = (UniqueConstraint('customer', 'product', name='customer_discount_unique_idx'),)` to `CustomerDiscount`, with a comment that the key already exists in mbe_dev and in `docs/mbe_schema.sql`, so no migration is needed (research R5) · `app/models/customer.py`
- [ ] T024 [P] [US2] Schemas:
  - `ProductSummary` (`product_id`, `code`, `name`), defined in `app/schemas/product.py` beside `ProductListItem` and imported here.
  - `CustomerDiscountCreate` (`product: int`, `discount: Decimal = Field(ge=0, le=1, decimal_places=8)`).
  - `CustomerDiscountUpdate` (`discount` only).
  - `CustomerDiscountResponse` (`customer_discount_id`, `customer: int`, `product: ProductSummary`, `discount`).

  Files: `app/schemas/product.py`, `app/schemas/customer.py`
- [ ] T025 [US2] Create `customer_discount_service.py` with `list_discounts` (a single join to `product`, ordered by `Product.code`), `get_discount` (scoped to the customer), `create_discount` (product-existence check first, then the duplicate check, then insert), `update_discount` and `delete_discount`. Attach the `ProductSummary` the same way the other services attach expansions. The module docstring states that nothing applies these to prices (clarification Q4) · `app/services/customer_discount_service.py`
- [ ] T026 [US2] Add `GET` and `POST /{customer_id}/discounts`, and `PUT` and `DELETE /{customer_id}/discounts/{customer_discount_id}`. Each returns 404 for an unknown customer or for an override of another customer. Access stays `get_current_user` · `app/api/v1/endpoints/customers.py`
- [ ] T027 [P] [US2] Scope 01 §3: describe the discount sub-panel as delivered (routes, the 0–1 rate, unique per product, product fixed after create). Say plainly that sales orders and quotes do not apply these yet · `docs/specs/01-master-data.md`

**Checkpoint**: T020–T022 pass, and so do the contract-doc blocks for `CustomerDiscount*` and `ProductSummary`.

---

## Phase 5: User Story 3 — Incidences for products and users (P2)

**Goal**: Product create, update and delete, and user update, delete, profile application and administrator-issued recovery token, each write one incidence in the same transaction as the change.

**Independent test**: each of the five actions leaves exactly one incidence with the right source, instance, updater and wording, and a failed change leaves none.

### Tests (write first, must fail)

- [ ] T028 [P] [US3] **(commit with T033)** Unit tests in `test_product_service.py`, patching `app.services.product_service.incidences.record` and asserting the exact keyword arguments from data-model.md:
  - **create**: `source=SourceType.PRODUCT`, `instance_id=<new id>`, `updater=<given>`, `reason='Product {code} created'`, `context=<name>`.
  - **update**: `reason='Product {code} updated'`, `context='Changed: <sorted model_fields_set>'`.
  - **delete**: `reason='Product {code} deleted'`, `context='{code} {name}'`, and the record is staged before `db.delete` (check the call order on the mock).

  File: `tests/unit/test_product_service.py`
- [ ] T029 [P] [US3] **(commit with T035)** Unit tests in `test_user_service.py`:
  - **update**: `source=SourceType.USER_SETTINGS`, `instance_id=user.employee_id`, `updater=<acting>`, `reason='User {user_id} updated'`, `context` starting with `'{user_id} updated; changed: '`, followed by sorted field names and no values (send a `password`-like or privileges payload and assert that no value appears).
  - **delete**: `reason='User {user_id} deleted'`, `context='{user_id} deleted'`.
  - **apply_profile**: `reason='User {user_id} profile applied'`, `context='{user_id} profile {profile_id} applied'`.
  - **initiate_recovery**: `reason='User {user_id} recovery token issued'`, `context='{user_id} recovery token issued'`, and the incidence is committed. Today the function writes nothing, so this commit is new.
  - In every case the record is staged before `commit`.
  - Fix the existing `apply_profile` calls (lines 134–155) and the update/delete calls to pass `updater`.

  File: `tests/unit/test_user_service.py`
- [ ] T030 [P] [US3] **(commit with T034)** API tests asserting that the product routes pass `updater=7`, the `employee_id` of the overridden `CurrentUser`, to `create_product`, `update_product` and `delete_product`. Cover `POST /products/{id}/image`'s call to `update_product` too · `tests/api/test_products.py`
- [ ] T031 [P] [US3] **(commit with T036)** API tests asserting that the user routes pass `updater=<CurrentUser.employee_id>`:
  - `PUT /users/{id}`, `DELETE /users/{id}` and `POST /users/{id}/recover-password`, in the existing users route tests in `tests/api/test_auth.py`;
  - the profile-apply route, extending the patch at `tests/api/test_user_profiles.py:223`.

  Files: `tests/api/test_auth.py`, `tests/api/test_user_profiles.py`
- [ ] T032 [P] [US3] **(commit with T036)** Integration test on SQLite:
  - Create, update and delete a product, then update and delete a user. Read `incidence` rows and assert one per action with the expected `source`, `instance_id`, `updater`, `comment` and `content`.
  - Apply a profile to a user, then call `POST /users/{id}/recover-password`. Assert one `USER_SETTINGS` row for each, with `instance_id` set to the user's employee id and the reason and content from data-model.md. For the recovery token, this also proves that `initiate_recovery`'s new commit saves the row.
  - A create with a duplicate code (409) leaves no new incidence.

  File: `tests/integration/test_master_data_audit.py`

### Implementation

- [ ] T033 [US3] Product incidences:
  - `create_product`, `update_product` and `delete_product` gain a keyword-only `updater: int`.
  - Stage `incidences.record(...)` with the wording from data-model.md. For create, record after the flush, so that `product_id` exists, and before the commit. For update, record before the commit, using `sorted(data.model_fields_set)`. For delete, record before `db.delete`.
  - Import `incidences` the way `facility_service.py` does.

  File: `app/services/product_service.py`
- [ ] T034 [US3] The product routes rename `_` to `current` and pass `updater=current.employee_id` to create, update, delete and the image upload's `update_product` · `app/api/v1/endpoints/products.py`
- [ ] T035 [US3] User incidences:
  - `update_user` and `delete_user` gain a keyword-only `updater: int`.
  - Replace both `# TODO` lines with `incidences.record(...)`, staged **before** `commit`, with `instance_id=user.employee_id` and the wording from data-model.md (clarification Q3).
  - In `delete_user`, capture `user_id` and `employee_id` before `db.delete`.
  - `apply_profile` gains `updater` and stages its incidence before its existing commit.
  - `initiate_recovery` gains `updater`, stages its incidence and commits.

  File: `app/services/user_service.py`
- [ ] T036 [US3] `PUT` and `DELETE /users/{user_id}`, `POST /users/{user_id}/recover-password`, and the user-profiles apply route (`user_profiles.py:101`) rename `_` to `current` and pass `updater=current.employee_id`. Files: `app/api/v1/endpoints/users.py`, `app/api/v1/endpoints/user_profiles.py`
- [ ] T037 [P] [US3] Docs:
  - Scope 01 §1: replace "Logs an `Incidence` record (`SourceType.Product`)" with the delivered wording.
  - Scope 12 §1: list the four actions that each write one `UserSettings` incidence (edit, delete, profile application and an administrator-issued recovery token). Incidences are keyed by the affected user's employee id, and the content starts with the user id and lists field names only.

  Files: `docs/specs/01-master-data.md`, `docs/specs/12-users.md`

**Checkpoint**: T028–T032 pass. `grep -n "TODO: write incidence" app/services/user_service.py` returns nothing.

---

## Phase 6: User Story 4 — Barcode search (P2)

**Goal**: The product list search also matches `bar_code` exactly. It runs after US3, because both edit `product_service.py` and its unit test.

**Independent test**: a full 13-digit barcode finds its product, a 6-digit prefix does not match through the barcode, and every earlier match still holds.

### Tests (write first, must fail)

- [ ] T038 [US4] **(commit with T040)** Unit test: `_apply_product_filters` with `search=' 7501234567890\n'` compiles to SQL containing an equality on `product.bar_code` with the stripped value, and the five ILIKE terms are unchanged · `tests/unit/test_product_service.py`
- [ ] T039 [P] [US4] **(commit with T040)** Integration tests, added to the existing catalog fixture:
  - A product with `bar_code='7501234567890'`, whose code and name do not contain those digits, is returned for the full barcode and not for `750123`.
  - `/products/labels/facets` and `/products/prices/missing-facets` count it for the full barcode.

  File: `tests/integration/test_product_flag_filters.py`

### Implementation

- [ ] T040 [US4] Add `Product.bar_code == search.strip()` to the `or_()` in `_apply_product_filters`, applied only when `search.strip()` is non-empty. Leave `_find_salable` in `sales_order_service.py` untouched (FR-020) · `app/services/product_service.py`
- [ ] T041 [P] [US4] Scope 01 §1: change "Search by: **name, code, model, SKU, brand** (not barcode; not supplier)" to include **barcode (exact match)**, keep "not supplier", and note that the same search drives the label and missing-price facets · `docs/specs/01-master-data.md`

**Checkpoint**: T038–T039 pass, and `tests/integration/test_product_flag_filters.py` is fully green.

---

## Phase 7: User Story 5 — Vehicle operator license photo (P3)

**Goal**: An operator's license photo can be uploaded, replaced, viewed (authenticated only) and deleted. It is stored privately, and its file is removed when it is no longer referenced.

**Independent test**: upload, fetch, replace (the old file is gone), delete (the file is gone, then 404), and a fetch without a token returns 401.

### Tests (write first, must fail)

- [ ] T042 [P] [US5] **(commit with T047)** Unit tests for `delete_image(directory, filename)`: it removes an existing file, a missing file is a no-op that does not raise, and a `filename` containing `/` or `..` is refused with `ValueError`. The last case keeps the helper from ever removing anything outside `directory` · `tests/unit/test_image_service.py`
- [ ] T043 [P] [US5] **(commit with T048)** Unit tests for the vehicle operator service:
  - `set_license_photo(db, vo, filename)` stores the name, commits, and returns the previous name, or `None` when there was none.
  - `clear_license_photo(db, vo)` sets NULL, commits, and returns the previous name.
  - `delete_vehicle_operator` returns the operator's photo name for the caller to remove.
  - `VehicleOperator.has_license_photo` is `True` only when `license_photo` is set.

  File: `tests/unit/test_vehicle_operator_service.py`
- [ ] T044 [P] [US5] **(commit with T049)** API tests, using `tmp_path` as `settings.license_photos_dir`:
  - POST with a PNG returns 200 with `has_license_photo: true`; 404 for an unknown operator; 422 for non-image bytes and for more than 2 MB, with the existing photo kept and no file added.
  - A second POST removes the first file from disk.
  - GET returns `image/png` bytes; 401 without a token; 404 when there is no photo; 404 when the column is set but the file is missing.
  - DELETE returns 204 and removes the file; 404 when there is no photo.
  - `DELETE /vehicle-operators/{id}` removes the photo file.
  - The response never contains the filename.
  - Nothing is written under `settings.images_dir`.

  File: `tests/api/test_vehicle_operator_license_photo.py`

### Implementation

- [ ] T045 [P] [US5] Model:
  - Add `license_photo: Mapped[str | None] = mapped_column(String(255))` to `VehicleOperator`.
  - Add a read-only `has_license_photo` property returning `self.license_photo is not None`, so `from_attributes` picks it up without the filename leaving the server.

  File: `app/models/core.py`
- [X] T046 [P] [US5] Document `vehicle_operator.license_photo`: a private filename under `LICENSE_PHOTOS_DIR`, NULL meaning no photo, migration 022, #20 · `docs/data-dictionary.md`
- [ ] T047 [P] [US5] Add `delete_image(directory, filename)`. It refuses a name containing `/` or `..`, and removes the file through `asyncio.to_thread` while ignoring `FileNotFoundError`. Reuse `save_proof_image` unchanged for writing (research R8) · `app/services/image_service.py`
- [ ] T048 [US5] Service:
  - `set_license_photo` and `clear_license_photo` each return the previous filename.
  - `delete_vehicle_operator` returns `vo.license_photo`, captured before the delete.
  - Add `has_license_photo: bool` to `VehicleOperatorResponse`.

  Files: `app/services/vehicle_operator_service.py`, `app/schemas/core.py`
- [ ] T049 [US5] Routes:
  - `POST /{vehicle_operator_id}/license-photo` (multipart `file`) calls `image_service.save_proof_image(content, settings.license_photos_dir)` and maps `ValueError` to 422. It then calls `set_license_photo`, and then `delete_image` on the previous name.
  - `GET` returns `FileResponse(..., media_type='image/png')`, with a 404 when there is no photo or the file is missing. Its docstring explains why the route is authenticated, mirroring `delivery_orders.py:345`.
  - `DELETE` calls `clear_license_photo` and then `delete_image`.
  - `DELETE /{vehicle_operator_id}` calls `delete_image` after the service commits.
  - Access stays `get_current_user`.

  File: `app/api/v1/endpoints/vehicle_operators.py`
- [ ] T050 [P] [US5] Add `has_license_photo: bool` to the `VehicleOperatorResponse` block and list the three license-photo routes · `specs/002-master-data-endpoints/contracts/api.md`
- [ ] T051 [P] [US5] Scope 01 §16: add the license photo to the field table and describe its routes, private storage, the 2 MB limit, the 1024 px PNG, and file removal on replace, clear and operator delete. The expired-license check stays advisory and unchanged · `docs/specs/01-master-data.md`

**Checkpoint**: T042–T044 pass. `tests/unit/test_contract_docs.py`, `test_model_schema.py` and `test_data_dictionary.py` are all green.

---

## Phase 8: Polish & cross-cutting

- [ ] T052 Add to the CHANGELOG under `[Unreleased]`:
  - **Added**: `/bank-accounts`, the supplier sub-panels and agreements, customer discounts (stored, not yet applied), product barcode search, and the operator license photo.
  - **Changed**: `GET`, `POST` and `PUT /suppliers/{id}` return `SupplierDetailResponse`; product and user changes write incidences.
  - **Operations notes**: apply migration 022, and create `LICENSE_PHOTOS_DIR` outside `IMAGES_DIR`, not served by nginx, and included in backups.

  File: `CHANGELOG.md`
- [ ] T053 Run the full gate: `uv run ruff check app/ migrations/ tests/` returns zero violations and `uv run pytest -q` is green, including all contract-doc, model-schema, data-dictionary and list-query-count tests. Compare the results against the baseline recorded in T003 · repository root
- [ ] T054 **Ask the user before running.** Apply `migrations/022_vehicle_operator_license_photo.sql` to mbe_dev, then verify with `SHOW COLUMNS FROM vehicle_operator LIKE 'license_photo'` (quickstart.md, Migration) · mbe_dev
- [ ] T055 **Ask the user before deploying.** On the test deployment (`test.api.mbe.mictlanix.com`), create `LICENSE_PHOTOS_DIR`, deploy the branch, and run quickstart.md's manual checks 1–5 (SC-001–SC-005). Record the results under each check · `specs/021-master-data-api-gaps/quickstart.md`

---

## Dependencies & execution order

```
Phase 1 (T001, T002) ─┐
Phase 2 (T003) ───────┴─> US1 (T004–T019) ─┐
                         US2 (T020–T027) ─┤ independent of each other
                         US3 (T028–T037) ─┤
                         US4 (T038–T041) ─┤ after US3 (shares product_service.py + its unit test)
                         US5 (T042–T051) ─┘ needs T001 (migration text) and T002 (setting)
                                        └─> Polish (T052–T055)
```

- **Within each story**: the tests come first, then the model and schemas, then the services, then the routes, then the docs.
- **Shared files**: `docs/specs/01-master-data.md` (T019, T027, T037, T041, T051) and `specs/002…/api.md` (T018, T050) are edited by several stories. Each edit touches a different section and only adds text, so merge sequentially if the stories run in parallel.
- **T054 and T055 change shared state**: mbe_dev and the test deployment. Both need explicit user confirmation.

## Parallel examples

- **US1**: T004–T009 together; then T010 alongside T013; then T011 → T012, and T014 → T015 → T016.
- **US2**: T020, T021 and T022 together; then T023 alongside T024; then T025 → T026.
- **US3**: T028–T032 together; then T033 → T034 alongside T035 → T036.
- **US5**: T042, T043 and T044 together; then T045, T046 and T047 together; then T048 → T049.
- **Across stories**: US1, US2, US3 and US5 can be developed concurrently by different agents. US4 waits for US3.

## Implementation strategy

1. **MVP**: Phases 1 and 2, then US1 (supplier sub-panels). That exposes the largest body of existing, invisible data: 293 links and agreements.
2. **Then US2.** US1 and US2 together close both P1 roadmap items (#13 and #15).
3. **Then US3 → US4** in that order, closing #17 and #18.
4. **Then US5**, closing #20, followed by the polish phase.
5. **One PR** closes mictlanix/mbe-roadmap#13, #15, #17, #18 and #20, with a `Closes` line for each under "Roadmap".
