# Data Model: Master Data API Gaps

Every table already exists in mbe_dev except one column, `vehicle_operator.license_photo` (migration 022). Column types below come from `SHOW COLUMNS` on mbe_dev, run 2026-10-21.

## Supplier links (existing junction tables, no change)

| Table | Columns | Key | Rows in mbe_dev |
|---|---|---|---|
| `supplier_address` | `supplier`, `address` | composite PK | 119 |
| `supplier_contact` | `supplier`, `contact` | composite PK | 154 |
| `supplier_bank_account` | `supplier`, `bank_account` | composite PK | 2 |

- Each is written replace-all per collection (research R1). `None` leaves the links alone and `[]` clears them.
- An unknown id violates the FK, the request returns 409, and nothing changes.
- A link blocks deletion of either side with a 409 through `assert_not_referenced`.

## BankAccount (`bank_account`, existing, no change)

| Field | Type | Null | Validation |
|---|---|---|---|
| `bank_account_id` | int | PK | — |
| `bank_name` | varchar(250) | no | required, max 250 |
| `account_number` | varchar(20) | no | required, max 20 |
| `reference` | varchar(20) | yes | max 20 |
| `routing_number` | varchar(18) | yes | max 18 |
| `comment` | varchar(500) | yes | max 500 |

## SupplierAgreement (`supplier_agreement`, existing, no change)

| Field | Type | Null | Validation |
|---|---|---|---|
| `supplier_agreement_id` | int | PK | — |
| `supplier` | int FK `supplier` | no | taken from the path, never the body |
| `start` | date | no | required |
| `end` | date | no | required, `end >= start` (FR-006) |
| `comment` | varchar(500) | yes | max 500 |

- On update, the rule is checked against the merged values, so sending only `end` is checked against the stored `start`.
- Agreements of one supplier may overlap, and `start == end` is allowed.
- Listed ordered by `start`, then id. mbe_dev has 18 rows, and none has `end < start`.

## CustomerDiscount (`customer_discount`, existing; the model gains its unique constraint)

| Field | Type | Null | Validation |
|---|---|---|---|
| `customer_discount_id` | int | PK | — |
| `customer` | int FK `customer` | no | taken from the path |
| `product` | int FK `product` | no | must exist (422); fixed after create |
| `discount` | decimal(9,8) | no | `0 ≤ discount ≤ 1`, up to 8 decimal places |

- Unique `(customer, product)` as `customer_discount_unique_idx`. This already exists in mbe_dev; the model adds `UniqueConstraint` so that the SQLite test schema matches. A duplicate returns 409.
- Listed ordered by product code. mbe_dev has 0 rows.
- No pricing code reads this table (clarification Q4).

## VehicleOperator (`vehicle_operator`, gains one column)

| Field | Type | Null | Notes |
|---|---|---|---|
| `license_photo` | varchar(255) | yes | **New, migration 022.** The bare filename (`<uuid4 hex>.png`) inside `settings.license_photos_dir`. NULL means there is no photo. It is never exposed; the response carries `has_license_photo: bool`. |

**State transitions:**

```
none --upload--> photo(A)
photo(A) --upload--> photo(B)    file A removed after commit
photo(A) --delete photo--> none  file A removed after commit
photo(A) --delete operator--> (row gone)  file A removed after commit
```

A failed upload (422) leaves the state and the file untouched.

## Incidence (`incidence`, existing, new writers only)

| Writer | `source` | `instance_id` | `updater` | `comment` (reason) | `content` (context) |
|---|---|---|---|---|---|
| product create | 9 PRODUCT | product id | acting employee | `Product {code} created` | product name |
| product update | 9 PRODUCT | product id | acting employee | `Product {code} updated` | `Changed: {fields}` |
| product delete | 9 PRODUCT | product id | acting employee | `Product {code} deleted` | `{code} {name}` |
| user update | 8 USER_SETTINGS | affected user's employee id | acting admin's employee | `User {user_id} updated` | `{user_id} updated; changed: {fields}` |
| user delete | 8 USER_SETTINGS | affected user's employee id | acting admin's employee | `User {user_id} deleted` | `{user_id} deleted` |
| profile applied | 8 USER_SETTINGS | affected user's employee id | acting admin's employee | `User {user_id} profile applied` | `{user_id} profile {profile_id} applied` |
| recovery token issued | 8 USER_SETTINGS | affected user's employee id | acting admin's employee | `User {user_id} recovery token issued` | `{user_id} recovery token issued` |

- `{fields}` is the sorted list of field names the request sent. It never contains values.
- Each row is staged in the same transaction as its change (FR-016).

## Product search (no schema change)

The `search` filter adds `bar_code = search.strip()` to the existing OR over code, name, model, SKU and brand. mbe_dev has 43 products with a barcode.
