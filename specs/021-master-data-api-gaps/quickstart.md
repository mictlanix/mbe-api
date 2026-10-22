# Quickstart: validating Master Data API Gaps

The route and schema details are in [contracts/master-data-gaps.md](contracts/master-data-gaps.md). The tables and incidence wording are in [data-model.md](data-model.md).

## Automated gate

```bash
uv run ruff check app/ migrations/ tests/
uv run pytest
```

Expected: zero ruff violations and a green suite. This includes `tests/unit/test_contract_docs.py`, which proves that the pseudo-schemas in this feature's contract and in spec 002's `api.md` match the live OpenAPI document (research R10).

| Requirement | Covered by |
|---|---|
| FR-001–FR-007 | `tests/api/test_supplier_sub_panels.py`, `tests/api/test_bank_accounts.py`, `tests/unit/test_supplier_agreement_service.py`, `tests/integration/test_supplier_links_flow.py` (unknown id gives 409 with no change; delete is blocked by links) |
| FR-008–FR-013 | `tests/api/test_customer_discounts.py`, `tests/unit/test_customer_discount_service.py`, `tests/integration/test_customer_discount_flow.py` (unique key) |
| FR-014–FR-017 | `tests/unit/test_product_service.py`, `tests/unit/test_user_service.py`, `tests/api/test_products.py`, `tests/api/test_auth.py` and `tests/api/test_user_profiles.py` (the routes pass the acting employee), `tests/integration/test_master_data_audit.py` (rollback leaves no incidence) |
| FR-018–FR-020 | `tests/unit/test_product_service.py` (filter), `tests/integration/test_product_flag_filters.py` (barcode exact versus partial) |
| FR-022–FR-027 | `tests/api/test_vehicle_operator_license_photo.py` |
| SC-002, unique (customer, product) | `tests/integration/test_customer_discount_flow.py`, which uses the SQLite schema with the model's `UniqueConstraint` |

## Migration (mbe_dev only)

```bash
# Read-only check first: the column must not exist yet.
SHOW COLUMNS FROM vehicle_operator LIKE 'license_photo';
# Apply migrations/022_vehicle_operator_license_photo.sql, then re-run the check.
```

Expected: the column is `varchar(255)`, `NULL` is allowed, and all 14 rows are NULL. Running the migration a second time does nothing (`IF NOT EXISTS`).

## Manual checks against mbe_dev (test deployment)

Run each check with an authenticated token against `test.api.mbe.mictlanix.com`, after deploying the branch and applying migration 022.

1. **SC-001, supplier detail.** Pick a supplier that has a link:

   ```sql
   SELECT supplier, COUNT(*) FROM supplier_contact GROUP BY supplier ORDER BY 2 DESC LIMIT 1;
   ```

   `GET /suppliers/{id}` lists exactly that many contacts, plus its addresses, bank accounts and agreements. `GET /suppliers` rows have no lists. Summed over every supplier, the totals are 119 addresses, 154 contacts, 2 bank accounts and 18 agreements.
2. **SC-002, discounts.**
   - `POST /customers/{id}/discounts` with `{"product": P, "discount": "0.15"}` returns 201.
   - Repeating the same request returns 409.
   - `PUT` with `"discount": "0.2"` returns 200.
   - `DELETE` returns 204.
   - The table is back to 0 rows.
3. **SC-003, incidences.** Edit a test product, then run:

   ```sql
   SELECT * FROM incidence WHERE source = 9 ORDER BY incidence_id DESC LIMIT 1;
   ```

   Its `instance_id` is the product id, `updater` is your employee id, and `content` is `Changed: …`.
4. **SC-004, barcode.**
   - Find a barcode with `SELECT bar_code FROM product WHERE bar_code IS NOT NULL LIMIT 1;`.
   - `GET /products?search=<that barcode>` returns the product.
   - The same request with only the first 6 digits does not return it through the barcode.
5. **SC-005, license photo.**
   - `POST /vehicle-operators/{id}/license-photo` with a JPEG returns `has_license_photo: true`.
   - `GET` with a token returns a PNG. `GET` without a token returns 401.
   - Upload again: the previous file is gone from `LICENSE_PHOTOS_DIR`.
   - `DELETE` returns 204, a following `GET` returns 404, and the directory no longer has the file.
   - Nothing is reachable under `/images/`.

## Results: 2026-10-22, test deployment (branch `021-master-data-api-gaps` at `1079252`)

These checks ran on xolotl against `mbe_dev` with a 30-minute admin token, minted on the box from the instance's own `.env`.

- **Migration**: the runner applied `021_user_must_change_password` and `022_vehicle_operator_license_photo`.
  - 021's column already existed but had never been recorded as applied. Its `IF NOT EXISTS` made it a no-op apart from recording it.
  - The new column is `license_photo varchar(255)`, nullable, and NULL on all 14 operators.
- **1. SC-001 ✅**:
  - Supplier 15 shows 4 contacts (4 expected), 1 address, 0 bank accounts and 1 agreement.
  - List items carry no sub-panel keys.
  - Across all 265 suppliers the detail totals are 119 addresses, 154 contacts, 2 bank accounts and 18 agreements, exactly what the database holds.
- **2. SC-002 ✅**: POST returned 201, the duplicate 409, PUT 200 and DELETE 204. `customer_discount` is back to 0 rows.
- **3. SC-003 ✅**: product 6524 was re-saved with its existing comment and returned 200. The new incidence row is `source 9, instance 6524, updater 1` (the acting admin's employee), with `comment = "Product ESTALBRON1545 updated"` and `content = "Changed: comment"`.
- **4. SC-004 ✅**: the full barcode returns product 22052. Its first 6 digits do not, because neither code `12100` nor the name contains them.
- **5. SC-005 ✅**:
  - Upload returned `has_license_photo: true` and no filename in the response.
  - GET with the token returned 200 `image/png`, scaled from 1600 px to 1024 px wide. Without the token it returned 401, and the same file under public `/images/` returned 404.
  - Replacing the photo removed the first file and kept the new one.
  - DELETE returned 204, a GET afterwards returned 404, and the directory is empty again.

## Operations note

Create `LICENSE_PHOTOS_DIR` (default `license-photos`, relative to the working directory) on the server and include it in backups. It must not be served by nginx, and it must be outside `IMAGES_DIR`.
