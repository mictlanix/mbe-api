# Contract: Master Data API Gaps

All routes are under `/api/v1`, and every route requires a bearer token (401 without one).

The schemas use the pseudo-schema notation checked by `tests/unit/test_contract_docs.py`. Three existing blocks in `specs/002-master-data-endpoints/contracts/api.md` change with this feature, and are edited there rather than repeated here:

- `SupplierCreate / SupplierUpdate` gains `addresses`, `contacts` and `bank_accounts`.
- `VehicleOperatorResponse` gains `has_license_photo: bool`.
- `SupplierResponse` stays unchanged. It is still the list item and the `ProductResponse.supplier` embed.

---

## 1. Suppliers (scope 01 §6)

**Prefix**: `/api/v1/suppliers`. Access: `get_current_user`, unchanged.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `` | — | `ListResponse[SupplierResponse]`, unchanged |
| POST | `` | `SupplierCreate` | 201 `SupplierDetailResponse` |
| GET | `/{supplier_id}` | — | `SupplierDetailResponse` · 404 |
| PUT | `/{supplier_id}` | `SupplierUpdate` | `SupplierDetailResponse` · 404 |
| DELETE | `/{supplier_id}` | — | 204 · 404 · 409 while links or agreements exist |

`addresses`, `contacts` and `bank_accounts` on `SupplierCreate` and `SupplierUpdate` are `[int, ...] | null`. Each list replaces that link set: `null` or omitted leaves it alone, and `[]` clears it. An unknown id returns 409, and no link changes.

```
SupplierDetailResponse:
  supplier_id: int
  code: str
  name: str
  zone: str | null
  credit_limit: Decimal
  credit_days: int
  comment: str | null
  addresses: [AddressResponse, ...]
  contacts: [ContactResponse, ...]
  bank_accounts: [BankAccountResponse, ...]
  agreements: [SupplierAgreementResponse, ...]
```

### Agreements

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `/{supplier_id}/agreements` | — | `[SupplierAgreementResponse, ...]` · 404 supplier |
| POST | `/{supplier_id}/agreements` | `SupplierAgreementCreate` | 201 `SupplierAgreementResponse` · 404 · 422 |
| PUT | `/{supplier_id}/agreements/{supplier_agreement_id}` | `SupplierAgreementUpdate` | `SupplierAgreementResponse` · 404 · 422 |
| DELETE | `/{supplier_id}/agreements/{supplier_agreement_id}` | — | 204 · 404 |

- An agreement that belongs to another supplier returns 404.
- `end < start` returns 422. On update the check uses the merged values.

```
SupplierAgreementCreate:
  start: date
  end: date
  comment: str | null

SupplierAgreementUpdate:
  start: date | null
  end: date | null
  comment: str | null

SupplierAgreementResponse:
  supplier_agreement_id: int
  supplier: int
  start: date
  end: date
  comment: str | null
```

---

## 2. Bank accounts (new)

**Prefix**: `/api/v1/bank-accounts`. Access: `require_privilege(SystemObject.BANK_ACCOUNTS, READ | CREATE | UPDATE | DELETE)`, the same way `/addresses` and `/contacts` are gated.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `` (`search`, `skip`, `limit`) | — | `ListResponse[BankAccountResponse]` |
| POST | `` | `BankAccountCreate` | 201 `BankAccountResponse` · 422 |
| GET | `/{bank_account_id}` | — | `BankAccountResponse` · 404 |
| PUT | `/{bank_account_id}` | `BankAccountUpdate` | `BankAccountResponse` · 404 |
| DELETE | `/{bank_account_id}` | — | 204 · 404 · 409 while linked to a supplier |

`search` matches `bank_name`, `account_number` and `reference` with ILIKE.

```
BankAccountCreate:
  bank_name: str
  account_number: str
  reference: str | null
  routing_number: str | null
  comment: str | null

BankAccountUpdate:
  bank_name: str | null
  account_number: str | null
  reference: str | null
  routing_number: str | null
  comment: str | null

BankAccountResponse:
  bank_account_id: int
  bank_name: str
  account_number: str
  reference: str | null
  routing_number: str | null
  comment: str | null
```

---

## 3. Customer discounts (scope 01 §3)

**Prefix**: `/api/v1/customers/{customer_id}/discounts`. Access: `get_current_user`, the same as `/customers`.

| Method | Path | Body | Response |
|---|---|---|---|
| GET | `` | — | `[CustomerDiscountResponse, ...]`, ordered by product code · 404 customer |
| POST | `` | `CustomerDiscountCreate` | 201 `CustomerDiscountResponse` · 404 · 409 duplicate · 422 |
| PUT | `/{customer_discount_id}` | `CustomerDiscountUpdate` | `CustomerDiscountResponse` · 404 · 422 |
| DELETE | `/{customer_discount_id}` | — | 204 · 404 |

- `discount` is a fraction between 0 and 1 with up to 8 decimal places.
- A product that does not exist returns 422 (`Product not found`).
- A second override for the same product returns 409 (`Customer already has a discount for this product`).
- An override that belongs to another customer returns 404.

```
CustomerDiscountCreate:
  product: int
  discount: Decimal

CustomerDiscountUpdate:
  discount: Decimal

CustomerDiscountResponse:
  customer_discount_id: int
  customer: int
  product: ProductSummary
  discount: Decimal

ProductSummary:
  product_id: int
  code: str
  name: str
```

---

## 4. Products (scope 01 §1)

There is no new route.

- **`GET /products?search=…`**: also returns products whose `bar_code` equals `search.strip()`. The other matches are unchanged, and `GET /products/labels/facets` and `GET /products/prices/missing-facets`, which share the filter, gain the same match.
- **`POST`, `PUT` and `DELETE /products…` and `POST /products/{id}/image`**: the responses are unchanged. Each also writes one incidence, described in [data-model.md](../data-model.md#incidence-incidence-existing-new-writers-only).

## 5. Users (scope 12 §1)

There is no new route and the responses are unchanged. Each of these writes one `USER_SETTINGS` incidence:

- `PUT /users/{user_id}`
- `DELETE /users/{user_id}`
- `POST /users/{user_id}/recover-password`
- the user-profiles route that applies a profile to a user

---

## 6. Vehicle operator license photo (scope 01 §16)

**Prefix**: `/api/v1/vehicle-operators/{vehicle_operator_id}/license-photo`. Access: `get_current_user`, the same as `/vehicle-operators`.

| Method | Body | Response |
|---|---|---|
| POST | multipart `file` | `VehicleOperatorResponse` with `has_license_photo: true` · 404 · 422 for a file that is not an image or is over 2 MB |
| GET | — | `image/png` bytes · 404 when there is no photo or the file is missing |
| DELETE | — | 204 · 404 when there is no photo |

- POST stores the image at up to 1024 px wide, as PNG, in private storage, never under `/images`. If a photo already exists, POST replaces it and the old file is removed.
- DELETE clears the photo and removes the file.
- Deleting the operator also removes the file.
