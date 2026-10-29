# Quickstart: validating Localizable Error Codes

## 1. Local

The worktree is `/Users/eddy/Projects/mictlanix/mbe-api-036`, and `.env` is linked to the main checkout's.

```bash
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

Expected:
- The full suite passes.
- During phases 0–4 the guard tests in `tests/unit/test_error_codes.py` report as xfail. From phase 5 they pass, which covers:
  - no `HTTPException(` outside `app/core/errors.py`;
  - every code tested;
  - every table named;
  - `test_contract_docs.py` matching `contracts/error-codes.md` to `ErrorCode`.

Spot checks against a local server (`uv run uvicorn app.main:app --port 8000`):

| Request | Expect |
|---|---|
| `GET /api/v1/customers/999999` with a token | 404, `application/problem+json`, `code: resource.not_found`, `params: {"resource": "customer", "id": 999999}`, `detail: "Customer not found"` |
| `GET /api/v1/customers/1` with no token | 401, `WWW-Authenticate: Bearer`, `code: auth.not_authenticated` |
| `GET /api/v1/nope` | 404, `code: request.route_not_found` |
| `/openapi.json` | `components.schemas.Problem` and `ErrorCode` (241 values); every v1 operation has a `default` response referencing `Problem` |

## 2. Contract

- [contracts/error-codes.md](contracts/error-codes.md) lists all 241 codes.
- [contracts/api.md](contracts/api.md) has the body shapes.
- [raise-map.md](raise-map.md) maps every raise site to its code.

## 3. Dev (SC-004) — mbe-ui's three dependencies

Deploy the branch to xolotl dev :8765 as `mictlanix@` (pre-authorized). Before any write to mbe_dev, ask the user. The checks below create a test customer and order and delete them afterwards, as spec 035's races did.

Each request goes through `https://…dev…/api/v1` and nginx, as mbe-ui calls it. For each one, check the status, `Content-Type: application/problem+json` and the body.

1. **Password change**:
   - Setup: a test user with `must_change_password = 1`.
   - Request: `GET /auth/me`.
   - Expect: 200, since `me` is allowed.
   - Request: `GET /customers`.
   - Expect:
     - 403;
     - `detail == "password_change_required"` exactly;
     - `code == "auth.password_change_required"`.
2. **Credit hold**:
   - Setup: a test customer with an overdue credit order.
   - Request: `PUT /sales-orders/{id}` setting credit terms.
   - Expect:
     - 422, with `detail` a string;
     - `code == "customer.credit_hold"`;
     - `params.overdue_count` an int;
     - `params.action == "raise"`.
3. **Confirm with zero price**:
   - Setup: a draft order with a zero-priced line.
   - Request: `POST /sales-orders/{id}/confirm`.
   - Expect:
     - 409, with `detail` a dict whose `message` is "Order has lines priced at zero";
     - `lines` still present;
     - `code == "sales_order.zero_priced_lines"`.

Record the three responses in the spec's verifications, then delete the test rows. Confirm that 0 rows are left and that dev logged no 500s.

## 4. After merge

Open the mbe-ui issue (FR-019). It links `contracts/error-codes.md` on main, lists the codes by area with their params, and names what phase 3 will change:
- the four dict details;
- `ItineraryResponse.warnings`;
- the four inventory and purchasing warning lists.
