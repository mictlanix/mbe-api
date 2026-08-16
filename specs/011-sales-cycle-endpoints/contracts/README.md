# Phase 1 Contracts: Sales Cycle Endpoints

Every route below sits under `/api/v1/`, requires an authenticated session, and is gated by
`require_privilege(<SystemObject>, <AccessRight>)`. There is **no** point-of-sale route set — a
counter sale is composed by the client from sales orders, payments and cash sessions (FR-054).

Conventions inherited unchanged from the existing API:

- List responses are `ListResponse[T]` — `{items: [...], total: n}` — with `skip` / `limit`
  (`limit` ≤ 100).
- Foreign keys are expanded onto a `<column>_detail` key and read through `AliasChoices`, per the
  fix in `app/services/fk_expansion.py`. Never overwrite the mapped FK column.
- Lists apply **no implicit scoping** beyond the caller's facility; narrowing is by explicit
  parameter (FR-009). There is no `*` wildcard.
- Documents are never deleted; there is no `DELETE` on any document root (FR-006).

Shared error semantics:

| Status | Meaning here |
|---|---|
| 401 | No or invalid session |
| 403 | Authenticated but lacking the governing privilege |
| 404 | Document or referenced row not found |
| 409 | Lifecycle conflict — editing a confirmed document, paying a draft, cancelling a paid order, refunding an unpaid one, a second session on a drawer |
| 422 | Validation — quantity below minimum, price outside margin, reversal without a reason, cross-currency application |

Two error shapes carry extra detail rather than a bare message: confirming an order with zero-priced
lines names those lines (FR-017), and cancelling an order with live applications names them
(FR-019b).

---

## `/sales-orders` — SystemObject `SALES_ORDERS` (7)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/sales-orders` | READ | Filters: `mine`, `customer`, `salesperson`, `status`, `date_from`, `date_to`, `facility` (needs privilege 101), `point_sale` (#136), `search` |
| POST | `/sales-orders` | CREATE | Body may omit everything; defaults per FR-010. Optional `fulfillment_intent` records how the cashier said the goods reach the customer — `0` pickup, `1` delivery, `2` mixed (`FulfillmentType`). Pickup leads because it is the ordinary counter sale: 92.5% of sales orders never produce a delivery order. **`delivery_order.fulfillment_type` uses this same scale** as of migration 018, which renumbered it from the old `0` delivery / `1` pickup — so a value read from either column means the same thing. `2` is valid on a sale only; a delivery order is one kind or the other. Omitted means **not recorded**, and the response then answers `null`: nothing is inferred from `ship_to`, because that address can express the first two and not the third, which is the whole of #170. Distinct from the legacy `partial_deliveries`, which the system writes after a delivery order exists to record how fulfilment turned out. 422 when the caller has no employee (FR-002) or no point of sale (FR-004a), distinguishably |
| GET | `/sales-orders/{id}` | READ | Includes computed subtotal, tax, total, balance |
| PUT | `/sales-orders/{id}` | UPDATE | 409 once completed or cancelled, except `priority` (FR-011). Changing `customer` re-prices every line against the new customer's price list, unconditionally (FR-013a, #131). `fulfillment_intent` is editable while the sale is a draft, and an explicit `null` clears it back to not-recorded (#170) |
| POST | `/sales-orders/{id}/confirm` | UPDATE | Assigns folio, posts stock, marks completed (FR-017) |
| POST | `/sales-orders/{id}/cancel` | UPDATE | 409 if paid → "refund it instead"; 409 if live applications exist, naming them |
| GET | `/sales-orders/{id}/payments` | READ¹ | The applications standing against this order, each with its payment's method, reference, date and verification state. **Includes cancelled** (FR-041a, #134) |
| GET | `/sales-orders/{id}/lines` | READ | |
| POST | `/sales-orders/{id}/lines` | UPDATE | Snapshots code/name/cost; price from customer's list and tax rate from the product, both overridable by an explicit `price` / `tax_rate` (#135) |
| PUT | `/sales-orders/{id}/lines/{line_id}` | UPDATE | Mutable: `quantity`, `price`, `discount_rate`, `tax_rate` (#135), `warehouse`, `comment` |
| DELETE | `/sales-orders/{id}/lines/{line_id}` | UPDATE | Permitted only while editable |
| GET | `/sales-orders/product-lookup` | READ | `pattern`, `customer`, `warehouse`. A 13-digit numeric pattern matches barcode (FR-021). Each row carries the product's `unit_of_measurement` and `photo`, as every line response does (FR-021a/FR-021b, #145, #157) |
| GET | `/sales-orders/outstanding` | READ | Unpaid confirmed orders with balances (FR-046) |

¹ `/sales-orders/{id}/payments` is gated by `CUSTOMER_PAYMENTS` (8) READ, not `SALES_ORDERS` (7): it returns payment data, so a caller who may read orders but not payments is refused.

## `/sales-quotes` — SystemObject `SALES_QUOTES` (30)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/sales-quotes` | READ | Same filter set as orders |
| POST | `/sales-quotes` | CREATE | Defaults per FR-030 |
| GET | `/sales-quotes/{id}` | READ | |
| PUT | `/sales-quotes/{id}` | UPDATE | 409 once completed or cancelled |
| POST | `/sales-quotes/{id}/confirm` | UPDATE | Assigns folio (FR-032) |
| POST | `/sales-quotes/{id}/cancel` | UPDATE | |
| POST | `/sales-quotes/{id}/duplicate` | CREATE | New editable quote, prices re-fetched (FR-033) |
| POST | `/sales-quotes/{id}/convert` | CREATE | → new draft order; 409 if not confirmed, cancelled, or expired (FR-034). Requires CREATE on Sales Orders too |
| GET/POST | `/sales-quotes/{id}/lines` | READ / UPDATE | |
| PUT/DELETE | `/sales-quotes/{id}/lines/{line_id}` | UPDATE | |

## `/customer-payments` — SystemObject `CUSTOMER_PAYMENTS` (8)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/customer-payments` | READ | Filters: `customer`, `cash_session`, `facility`, `date_from`, `date_to`, `method`, `verified`. No implicit session scoping (FR-009a) |
| POST | `/customer-payments` | CREATE | Attaches the caller's open session when one exists |
| GET | `/customer-payments/{id}` | READ | Includes derived unapplied amount |
| GET | `/customer-payments/{id}/applications` | READ | **Includes cancelled** (FR-073) |
| POST | `/customer-payments/{id}/applications` | CREATE | Body: `sales_order`, `amount`, optional `amount_change`. 409 if the order is not completed or is cancelled; 422 on cross-currency or over-application |
| POST | `/customer-payments/{id}/applications/{app_id}/reverse` | UPDATE | Body requires `reason`; 422 without it. Writes an incidence entry (FR-045a) |
| POST | `/customer-payments/{id}/verify` | UPDATE | Governed by `PAYMENTS_VERIFICATION` (108) |
| POST | `/customer-payments/{id}/reject` | UPDATE | Body requires `reason`; writes an incidence entry (FR-072). Governed by (108) |
| GET | `/customer-payments/unverified` | READ | Queue where `verifier IS NULL`; filters per FR-071. Governed by (108) |

The payments editor (US8) needs no new route: it is the applications listing plus reverse and
re-apply, gated additionally by `PAYMENTS_EDITOR` (100) for cross-facility search.

## `/cash-sessions` — SystemObject `POS`-adjacent; close gated by `CASH_SESSION_CLOSE` (111)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/cash-sessions/current` | READ | Three states: none, open-today, open-stale (FR-053) |
| GET | `/cash-sessions` | READ | Filters: `cash_drawer`, `cashier`, `facility`, `status` = `open` \| `stale` \| `closed`, `date_from` / `date_to` over `start`. `sort` = `-id` \| `start` \| `-start`, omitted means `-id` (FR-051b); the default is applied server-side and deliberately **not** declared in the OpenAPI schema (#144). Not scoped to the caller's facility |
| POST | `/cash-sessions` | CREATE | Body: `cash_drawer`, `opening_amount`. 409 if the drawer or the cashier already has one open |
| GET | `/cash-sessions/{id}` | READ | Opening amount plus payments summarised by method (FR-051) |
| POST | `/cash-sessions/{id}/close` | UPDATE | Body: denomination counts. Gated by (111) |

Every cash-session response expands `cash_drawer`, `cashier` and `cash_supervisor` as objects
rather than ids (FR-051a) — `cash_supervisor` stays `null` until the session is closed.

## `/customer-refunds` — SystemObject `CUSTOMER_REFUNDS` (22); confirm gated by (110)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/customer-refunds` | READ | Filters: `customer`, `sales_order`, `status`, date range |
| POST | `/customer-refunds` | CREATE | Body: `sales_order`. 409 when not completed **or not paid**, distinguishably; 409 when no refundable lines (FR-060) |
| GET | `/customer-refunds/{id}` | READ | |
| PUT | `/customer-refunds/{id}/lines/{line_id}` | UPDATE | Return quantity and warehouse; 422 above refundable (FR-062) |
| POST | `/customer-refunds/{id}/confirm` | UPDATE | Body: `payout` = `cash` \| `credit_note`. Requires an open session; re-validates under lock; posts inbound stock; returns full total (FR-063, FR-065). Gated by (110) |
| POST | `/customer-refunds/{id}/cancel` | UPDATE | 409 once completed (FR-066) |

## `/credit-notes` — SystemObject `CREDIT_PAYMENTS` (83)

| Method | Path | Right | Notes |
|---|---|---|---|
| GET | `/credit-notes` | READ | Filter by `customer`; each row carries amount issued, originating refund and source order, and derived remaining balance (FR-070) |
| GET | `/credit-notes/{id}` | READ | |

Redemption deliberately has **no route of its own**: it is `POST /customer-payments/{backing_payment_id}/applications` (FR-070a), so it is bounded, reversible and correctable by the same code paths as any other application.

---

## Request/response shape notes

- **Money** is serialised as a JSON number with 2 decimals; internally `Decimal` throughout (R5).
- **Computed figures** (`subtotal`, `tax_total`, `total`, `balance`, `unapplied`, `remaining`) are
  response-only and rejected if sent in a request body.
- **`serial`** is `null` on a draft and populated at confirmation — clients must not treat it as an
  identifier before then.
- **Status** on a document response is derived from the `completed` / `cancelled` / `paid` flags
  rather than exposing three raw booleans, so the client sees one lifecycle state.
