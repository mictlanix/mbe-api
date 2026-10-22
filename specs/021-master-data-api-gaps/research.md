# Research: Master Data API Gaps

Every decision below was made by reading the code paths the feature touches and by probing mbe_dev read-only on 2026-10-21. Nothing in the Technical Context was left open.

## R1. Supplier links reuse the customer link pattern

- **Decision**: `supplier_service` gets the customer service's three link helpers:
  - `_get_links` reads the links.
  - `_attach_links` writes them into `__dict__` for the response.
  - `_set_links` replaces the links of each collection the request sent, with a delete followed by a bulk insert; a collection the request omitted is left alone.

  `SupplierCreate` and `SupplierUpdate` gain `addresses`, `contacts` and `bank_accounts` as `list[int] | None`.
- **Rationale**: This is clarification Q1 and the request's own instruction. A client already knows the customer pattern, and `customer_service.py:54-162` is the tested reference.
- **Unknown ids**: an unknown id raises an FK `IntegrityError` at insert time. The global handler (`app/main.py:67`) turns it into a 409, and since the commit never runs, nothing changes. Customers behave the same way today. No up-front existence check is added, so suppliers and customers keep the same contract.
- **Alternatives considered**: a shared generic link helper for customers and suppliers. Rejected because it refactors working customer code that is not broken (constitution III), and the copy is about 40 lines.

## R2. The detail gets its own response schema

- **Decision**: Add `SupplierDetailResponse(SupplierResponse)` with `addresses`, `contacts`, `bank_accounts` and `agreements`. `GET /suppliers/{id}`, `POST` and `PUT` return it. `GET /suppliers` and every embedding keep `SupplierResponse`.
- **Rationale**: `ProductResponse.supplier` embeds `SupplierResponse` (`app/schemas/product.py:202`). Adding the lists to `SupplierResponse` would put four empty arrays on every product and every supplier list row, which is misleading and contradicts FR-001. Customers reached the same split by a different naming (`CustomerListItem` and `CustomerResponse`), but renaming `SupplierResponse` would break both the product embed and mbe-ui's generated type for the list.
- **Alternatives considered**: renaming the list type to `SupplierListItem`. Rejected: it renames a component two clients already use.

## R3. Bank accounts become a standalone resource

- **Decision**: A new `/bank-accounts` router, `bank_account_service.py`, and `BankAccountCreate`, `BankAccountUpdate` and `BankAccountResponse` in `app/schemas/core.py`, beside the address and contact schemas. The router is gated with `require_privilege(SystemObject.BANK_ACCOUNTS, …)`, as `/addresses` and `/contacts` are gated with theirs. `SystemObject.BANK_ACCOUNTS = 13` already exists.
- **Search**: `bank_name`, `account_number`, `reference` (ILIKE), mirroring the contact search.
- **Delete**: `assert_not_referenced`. A bank account linked to a supplier returns 409.
- **Rationale**: This is clarification Q1. The `bank_account` model already exists (`app/models/core.py:195`), and mbe_dev holds 2 rows, both linked.

## R4. Agreements and discounts are nested resources with their own small services

- **Decision**: `supplier_agreement_service.py` and `customer_discount_service.py`, each with list, create, update and delete. Their routes go on the existing `suppliers.py` and `customers.py` routers:
  - `/suppliers/{supplier_id}/agreements[/{supplier_agreement_id}]`
  - `/customers/{customer_id}/discounts[/{customer_discount_id}]`

  A child that belongs to a different parent returns 404 (FR-007, FR-012).
- **Rationale**: Neither entity can exist without its parent. Separate modules keep `customer_service.py`, already 300 lines, from growing a second concern. The two services are justified in the plan's Complexity Tracking table.
- **Access**: Same as the parent router, which is `get_current_user` (FR-031). `SystemObject.SUPPLIER_AGREEMENTS = 14` exists, but gating it belongs to the Users & permissions epic, together with the rest of `/suppliers` and `/customers`.

## R5. Discount validation and uniqueness

- **Rate**: `Decimal`, `ge=0`, `le=1`, `decimal_places=8`, matching the `Numeric(9, 8)` column and the 0–1 convention in `docs/data-dictionary.md:447`.
- **Uniqueness**: mbe_dev already has `UNIQUE KEY customer_discount_unique_idx (customer, product)`, so no migration is needed. The service checks for a duplicate first and raises its own 409 with a specific message. The model gains a `UniqueConstraint` of the same name, which makes the SQLite integration schema enforce it too.
- **Unknown product**: checked up front and rejected with 422 (`Product not found`), because it is a validation error, not a conflict (spec US2, scenario 5).
- **Product is fixed**: `CustomerDiscountUpdate` has only `discount` (FR-011).
- **Response**: `CustomerDiscountResponse` embeds a new `ProductSummary` with `product_id`, `code` and `name`, read in the same query by joining `product`. `ProductListItem` was rejected because it needs the unit-of-measure expansion and photo-URL rewriting, which is two extra steps for fields no one asked for.
- **Data**: mbe_dev has 0 rows. Nothing reads the table except the merge discard (`product_service.py:517`). That is clarification Q4.

## R6. Incidences reuse `incidences.record`

- **Signature**: `record(db, *, source, instance_id, updater, reason, context)`. It is synchronous, it stages the row, and the caller's commit saves it. The change and its incidence therefore commit or roll back together (FR-016).
- **Products** (`SourceType.PRODUCT = 9`):

  | Action | `reason` → comment | `context` → content |
  |---|---|---|
  | create | `Product {code} created` | the product name |
  | update | `Product {code} updated` | `Changed: {sorted field names sent}` |
  | delete | `Product {code} deleted` | `{code} {name}`; recorded before `db.delete` |

  The product service functions gain a keyword-only `updater: int`, and the three product routes pass `current.employee_id`. The image upload route calls `update_product`, so it also produces an "updated" incidence with `Changed: photo`, which is correct.
- **Users** (`SourceType.USER_SETTINGS = 8`, clarification Q3):
  - `instance_id` is the affected user's `employee_id`, which is NOT NULL since migration 012.
  - `updater` is the acting admin's `employee_id`.
  - `reason`: `User {user_id} updated` or `User {user_id} deleted`.
  - `context`: for an update, `{user_id} updated; changed: {sorted field names}`; for a delete, `{user_id} deleted`.
  - Field names only, never values: a privilege payload or an email is not copied into the audit text.
  - The two TODO lines are removed. `update_user` and `delete_user` gain `updater: int`, and `users.py` passes the admin's employee id. The record is staged before the commit, not after it as the TODOs were placed.
- **Text length**: content is `String(1000)` and comment is `String(500)`. Product codes are at most 25 characters and names at most 250, so the longest text produced still fits.
- **Also covered** (decided after the analysis):
  - **`apply_profile`** (`POST` on the user-profiles router, `user_profiles.py:101`). It rewrites privileges, so it is an edit. Its incidence is staged before its existing commit.
  - **`initiate_recovery`** (`POST /users/{id}/recover-password`). It writes nothing today: the token is self-contained. Issuing the token lets the administrator set the user's password, so it is audited. The incidence is the only row the call writes, so `initiate_recovery` commits it.
  - Both gain `updater: int`, and their routes pass the acting admin's `employee_id`.
- **Not covered**: product merge. It is in the spec's Out of Scope.
- **Legacy parity**: legacy wrote only `Product created.` (799 of 802 product rows) and the bare user id with instance 0 for users. New rows are richer but keep the user id at the start of the content, so a search by content finds both.

## R7. Barcode search is one extra OR term

- **Decision**: In `_apply_product_filters` (`product_service.py:41`), add `Product.bar_code == search.strip()` to the existing `or_()`.
- **Exact only**: there is no ILIKE on `bar_code`, so a partial barcode matches only through the other fields (FR-018).
- **Strip**: a scanner may append a newline or a space. The existing ILIKE terms keep the raw term, so no current match changes (FR-019).
- **Reach**: `list_products`, `get_label_facets` and `get_missing_price_facets` share this filter, so all three gain the match. That is intended and is stated in FR-019.
- **Not touched**: `_find_salable`, the POS lookup in `sales_order_service.py:1289` (FR-020).

## R8. The license photo uses the proof-of-delivery storage path (clarification Q2)

- **Storage**: a new setting `license_photos_dir: str = 'license-photos'`, beside `pod_dir`. It is outside the public `/images` mount. It needs its own setting so that operations can place and back it up separately, and so that a license never lands in a directory named for delivery proofs.
- **Writing**: reuse `image_service.save_proof_image(content, settings.license_photos_dir)`. It applies the same 2 MB limit, rejects input Pillow cannot decode with 422, scales to 1024 px wide, writes PNG, and names the file with `uuid4().hex`. The UUID name means two operators never share a file, so deleting one operator's file cannot break another's. Writing a second function with identical behaviour would be rebuild over reuse (constitution V).
- **Deleting a file**: a new helper `image_service.delete_image(directory, filename)` removes a file and ignores one that is already gone. It runs through `asyncio.to_thread` (constitution VI), and it runs after the commit, so a failed commit never removes the file the row still points at. It is called on:
  - **Replace**: after the new name is committed, the old file is removed.
  - **Delete photo**: the column is set to NULL, the change is committed, and then the file is removed.
  - **Delete operator**: after the operator is deleted and committed, its file is removed.
- **Serving**: `GET /vehicle-operators/{id}/license-photo` returns `FileResponse(path, media_type='image/png')` behind `get_current_user`, mirroring `delivery_orders.py:345`. It returns 404 when the column is NULL or the file is missing from disk (spec edge case).
- **Upload route**: `POST /vehicle-operators/{id}/license-photo`, multipart `file`. It mirrors `POST /products/{id}/image` and returns `VehicleOperatorResponse`.
- **Response flag**: `VehicleOperatorResponse.has_license_photo: bool`, computed from the column. The filename itself is not exposed, because it is only meaningful to the server.

## R9. Migration 022

- `migrations/022_vehicle_operator_license_photo.sql` runs `ALTER TABLE vehicle_operator ADD COLUMN IF NOT EXISTS license_photo VARCHAR(255) NULL`. 255 matches `product.photo`. A nullable column with no default cannot fail on the 14 existing rows, and legacy, which never selects it, is unaffected.
- The rollback drops the column and warns that the stored files become orphaned. It gives the query that lists them first.
- No migration is needed for `customer_discount`, because the unique key already exists in mbe_dev (R5).

## R10. Contract documents are checked by a test

- `tests/unit/test_contract_docs.py` compares every `Name:` pseudo-schema block in `specs/*/contracts/*.md` with the live OpenAPI components, in both directions. Three blocks in `specs/002-master-data-endpoints/contracts/api.md` therefore have to change with the code:
  - `SupplierCreate / SupplierUpdate` gains the three id lists.
  - `VehicleOperatorResponse` gains `has_license_photo`.
  - A new `SupplierDetailResponse` block is added.
- This feature's own `contracts/master-data-gaps.md` uses the same notation. Its new component names, such as `BankAccountResponse` and `CustomerDiscountResponse`, will fail `test_a_documented_component_exists` until they are implemented. That is the intended test-first signal: the failing set shrinks to zero as the tasks land.
