# Research: Master Data Access Privileges

The spec's "Findings that shape this spec" section records the investigation of the current system, mbe-ui and mbe_dev. This file records the design decisions that follow from it.

## R1. How a route accepts more than one privilege

**Decision**: Add `require_any_privilege(*grants)` to `app/core/deps.py`. Each grant is a `(SystemObject, AccessRight)` pair. The dependency allows the request if the user is an administrator or holds the right on at least one of the objects. It reads every listed object in one query (`object IN (...)`) and refuses with the same 403 `Insufficient privileges` that `require_privilege` returns.

**Rationale**: The FR-005 lookups need "own Read, or Read on one of these hosts". `require_privilege` takes one object. A second factory that takes a list keeps every existing call to `require_privilege` unchanged, and it is the only new abstraction this feature needs.

**Alternatives considered**:
- *Chain several `require_privilege` dependencies*: FastAPI runs every dependency, so they combine as AND, not OR.
- *Rewrite `require_privilege` on top of the new factory*: this would change the query that existing tests fake through `scalar_one_or_none()` (`tests/api/test_print_endpoints.py`, `tests/api/test_fiscal_documents.py`) for no change in behavior. Left alone (constitution III).
- *New lookup routes gated by the host privilege*: mbe-ui would need to call them, and UI changes are out of scope.

## R2. The customer link exception (FR-006)

**Decision**: `PUT /customers/{id}` resolves its privilege inside the handler, after the body is parsed:
- If the body sets nothing but `addresses` and/or `contacts`, it accepts Customers Update, SalesOrders Update or SalesQuotes Update.
- Otherwise it requires Customers Update.

"Sets" means a non-null value: `data.model_dump(exclude_none=True)`. `customer_service.update_customer` already treats `None` as "leave unchanged", so a field sent as `null` changes nothing and does not count. The check calls the same helper that backs `require_any_privilege` (`has_any_privilege(db, user, grants)`), so there is one query path.

**Rationale**: A dependency cannot see the parsed body without parsing it twice, and the rule depends on the body. mbe-ui sends link-only bodies (`customer_repository_impl.dart:135-150` builds the body with `if (x != null)` guards).

**Alternatives considered**: A separate link route (`POST /customers/{id}/addresses`) was rejected because it needs a UI change.

## R3. Rights for the routes that are not plain CRUD

| Route | Right | Why |
|---|---|---|
| `GET /price-lists/{id}/delete/preview` | Price lists Read | It reads. Products' `GET /merge/preview` also takes Read. No host privilege is accepted (spec Edge Cases). |
| `POST /vehicle-operators/{id}/license-photo` | Vehicle operators Update | Changes the operator, the same as `POST /products/{id}/image` taking Products Update. |
| `DELETE /vehicle-operators/{id}/license-photo` | Vehicle operators Update | Removing the photo edits the operator; it does not delete it. |
| `GET /vehicle-operators/{id}/license-photo` | Vehicle operators Read | Own only. |
| `POST`/`PUT`/`DELETE /customers/{id}/discounts…` | Customers Update | FR-003: a change to a sub-panel is an edit of the parent. |
| `POST`/`PUT`/`DELETE /suppliers/{id}/agreements…` | Suppliers Update | FR-003 |
| `GET /customers/{id}/discounts`, `GET /suppliers/{id}/agreements` | Parent Read, own only | FR-003, FR-007 |

## R4. Which host objects each lookup accepts

The hosts come from mbe-ui's route gates (`app/router/app_router.dart:534-630`), checked against the calls listed in the spec. Read is the right in every case.

| Lookup routes | Hosts | Calling screens |
|---|---|---|
| `GET /customers`, `GET /customers/{id}` | SALES_ORDERS, SALES_QUOTES, POS | POS capture, sales-order editor, quote editor (planned) |
| `GET /employees`, `GET /employees/{id}` | SALES_ORDERS, SALES_QUOTES, POS, CUSTOMERS, VEHICLE_OPERATORS | salesperson picker, cash-session cashier filter, customer form, vehicle-operator form |
| `GET /taxpayer-recipients`, `GET /taxpayer-recipients/{rfc}` | SALES_ORDERS, CUSTOMERS | invoice recipient picker in the order header, customer form and POS new-customer dialog |
| `GET /price-lists`, `GET /price-lists/{id}` | CUSTOMERS, PRICING | customer form and new-customer dialog, pricing grid |
| `GET /payment-method-options`, `GET /payment-method-options/{id}` | SALES_ORDERS, POS, CUSTOMER_PAYMENTS | checkout tender grid |
| `GET /labels`, `GET /labels/{id}` | PRODUCTS, PRICING | product form and list, pricing grid |
| `GET /suppliers`, `GET /suppliers/{id}` | PRODUCTS, PRICING | product form and list, pricing grid |

The POS new-customer dialog is shown only to users with Customers Create. In mbe_dev, every one of those users also holds Customers Read except where noted in the spec (0 sellers lack Customers Read), so the dialog's price-list and recipient lookups are covered by the CUSTOMERS host.

## R5. How the tests prove every route is guarded

**Decision**: Use two layers.

1. **Route-table test** (`tests/api/test_master_data_privileges.py`): walk `app.routes` under the 11 prefixes and read the grants that the route's guard dependency carries. Assert that they equal the contract table (`contracts/route-privileges.md`), and that no route is missing from the table or extra in it. This covers SC-001 and FR-009 for every route without patching 68 services. To make the grants readable, both factories set `_check.grants` on the dependency they return. That adds one line to `require_privilege`. `PUT /customers/{id}` carries no guard dependency, so the test checks it through the link-exception tests instead.
2. **Behavior tests**:
   - `tests/unit/test_deps_privileges.py` tests `require_any_privilege` and `has_any_privilege`: administrator bypass, a missing row, a row with mask 0, the right bit held on the second grant only, the wrong bit held.
   - API tests run one representative route per decision kind end to end, with a non-admin user and a fake session (a strict write refused, a lookup allowed via a host, a host refused on a write, the delete preview, the license photo). They also cover every branch of the customer link exception.

**Alternatives considered**: Testing all 68 routes end to end with patched services. That would duplicate the existing per-router tests, and it proves less about missing routes than the route-table test does.

## R6. Existing tests

The existing per-router tests override `get_current_user` with an administrator, and `require_privilege` / `require_any_privilege` depend on `get_current_user`. Administrators bypass the query, so those tests keep passing without changes.
