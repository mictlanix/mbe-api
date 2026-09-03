# Phase 0 Research: Sales Cycle Endpoints

Ten decisions taken before design. Each was verified against the codebase or `docs/mbe_schema.sql`
rather than assumed — two of them overturned what the spec's Dependencies section anticipated, and
both are flagged.

---

## R1 — Folio uniqueness under concurrent confirmation (SC-005)

**Finding**: `sales_order`, `sales_quote` and `customer_refund` each have `serial int(11) DEFAULT
NULL` with **no unique index on `(facility, serial)`**. Nothing in the database stopped two
concurrent confirmations from taking the same folio.

**Decision**: Both layers, because they do different jobs.

1. **A unique index on `(facility, serial)`** for all three tables
   (`migrations/007_document_serial_unique.sql`). This is the guarantee: a regression in the
   application surfaces as a constraint violation instead of a silently duplicated folio. Draft
   documents carry `NULL` and MySQL permits any number of those in a unique index.
2. **A `FOR UPDATE` lock on the owning `facility` row**, taken inside the confirmation transaction
   before reading `MAX(serial)`. This is the ergonomics: concurrent confirmations queue rather than
   one of them failing on a duplicate key. It also covers any future code path that assigns a folio
   without going through `documents.assign_folio()`.

**The data audit this originally deferred was carried out**, against the live database, and it
found real violations — which is why the migration is not a bare `CREATE UNIQUE INDEX`:

| Table | `serial = 0` placeholders | Genuine duplicate folios | Clean? |
|---|---|---|---|
| `sales_order` | 3 | 0 groups | already clean |
| `sales_quote` | 4,065 (3,974 completed) | 9 groups / 24 rows | needed both fixes |
| `customer_refund` | 172 | 4 groups / 10 rows | needed both fixes |

The two classes are different problems and the migration treats them differently:

- **`serial = 0` is not folio zero.** It is the legacy application's placeholder for "not numbered",
  written on 4,240 rows from 2024 onward. These become `NULL`, which is what the new model means by
  "no folio yet". No real folio changes.
- **Thirty-four rows genuinely collide**, dating 2018–2023. The earliest document in each group
  keeps its folio; the 21 later ones move to the next free numbers for their facility. This was
  authorised explicitly, with the consequence understood: a reassigned `customer_refund` folio will
  no longer match a receipt printed years ago.

Order matters and the migration says so — the `0 → NULL` step must run first, or the 4,240
placeholder rows would be treated as duplicates and issued real folios.

**Verified before shipping**: the data steps were executed against the live database inside a
transaction that was rolled back. 4,240 rows nulled, 21 renumbered, and zero duplicate groups
remaining on all three tables — so the index is creatable. Nothing was written.

**Alternatives considered**:
- *Row lock alone* (the original decision): no migration and no data risk, but the guarantee lives
  only in application code and a regression is invisible to the database.
- *Unique index alone, with retry on duplicate key*: turns every concurrent confirm into a
  user-visible failure and a retry loop, for a guarantee the lock already provides quietly.
- *Renumbering the earliest document instead of the latest*: would move the folio of the document
  most likely to have been circulated.

---

## R2 — Preventing over-refund under concurrency (SC-006)

**Decision**: Confirmation of a refund locks the source `sales_order` row (`FOR UPDATE`), then
recomputes each line's refundable quantity from `sales_order_detail` minus the sum over completed,
uncancelled `customer_refund_detail` rows, and adjusts or drops lines to fit before writing
anything. Two clerks refunding the same order therefore serialize on the order row.

**Rationale**: The quantity check must happen against state that cannot change between the check and
the write. Locking the parent order — rather than individual detail rows — also covers a refund of a
line that a concurrent refund is adding, and matches FR-063's requirement to re-validate at confirm.

**Alternatives considered**:
- *Optimistic check at edit time only*: what the field-level validation does (FR-062); insufficient
  alone, which is exactly why FR-063 mandates re-validation.
- *`SELECT ... FOR UPDATE` on each `sales_order_detail`*: finer-grained, but multi-row locks in
  varying order invite deadlocks for no practical gain at this contention level.

---

## R3 — Where cost comes from at line add (FR-012)

**Decision**: Read the product's cost from `product_price` for the price list identified by a new
`cost_price_list_id` setting, defaulting to `0` as the source document states. Resolve it through
the existing `product_price_service`; if no row exists for that product and list, the cost is
recorded as zero rather than failing the line add.

**Rationale**: The source document names "cost price list, id=0" but nothing in the codebase
encodes it, and a hard-coded `0` in service logic would be invisible. A setting makes it
configurable per deployment, matching how `default_customer_id` is already handled.

**Alternatives considered**: a `cost` column on `product` (does not exist); failing the line add
when no cost row exists (would make a product unsellable because of a missing *reporting* figure).

**Superseded in part by #194**: the id is now the constant `COST_PRICE_LIST_ID`, not a setting.
This decision's *rationale* was half right and half wrong. Naming the id rather than open-coding
`0` was the point and still is. "A setting makes it configurable per deployment" was not: the
monolith computes those averages and writes them to that id, so a deployment pointing the API
elsewhere does not reconfigure anything — every line silently snapshots `cost` from a sale list
and every margin booked after is wrong with nothing marking when it started. A setting whose only
correct value is fixed by another application is configuration in name only. `default_customer_id`,
the comparison drawn here, is genuinely a deployment's choice; this was never that.

The rest of R3 stands unchanged: cost still resolves through the price list, and a missing row
still records zero rather than failing the line add.

---

## R4 — Available stock (FR-018)

**Decision**: On-hand for a product in a warehouse is `SUM(lot_serial_tracking.quantity)` filtered
by product and warehouse. There is no stock-balance table; the ledger is the only source of truth,
with positive rows inbound and negative outbound.

Stock validation at confirm aggregates the order's own lines first, so a product appearing on three
lines is checked once against the total — as FR-018 requires — not three times independently.

**Rationale**: Confirmed from `docs/data-dictionary.md` (`quantity`: "Positive=in, Negative=out")
and the absence of any balance table in the schema.

---

## R5 — Money computation and rounding (SC-004, FR-007)

**Decision**: All money is `Decimal`, never `float`. One helper (`app/services/totals.py`) computes
every figure, so orders, quotes, refunds and credit notes cannot round differently:

- Line subtotal = `quantity × price × (1 − discount_rate)`
- When `tax_included` is false, tax = `subtotal × tax_rate`; when true, the price already contains
  tax and the subtotal is back-derived as `subtotal ÷ (1 + tax_rate)`
- Document totals sum line figures, then quantize to 2 decimal places **once, at the end**
- Order balance = total − sum of non-cancelled applications

**Rationale**: `product.tax_included` exists per product and per line, so both conventions occur in
the same document and must be handled per line. Quantizing per line then summing produces
cent-level drift that would break SC-004's exactness requirement.

**Alternatives considered**: storing computed totals on the document — rejected by spec Assumption
7, and no column exists.

---

## R6 — Transaction boundaries

**Decision**: `get_db` yields a session and never commits; services commit, matching every existing
service. Each state transition is **one** `await db.commit()` at the end of the service function.
Confirming an order writes the folio, the completed flag and every ledger row in a single
transaction; a refund confirm additionally writes the payout. Nothing is committed midway.

**Rationale**: FR-054's atomicity language was removed with the POS endpoint, but the underlying
requirement survives per transition: a confirm that half-succeeds would leave stock decremented for
an order with no folio. The row locks from R1/R2 are only meaningful within one transaction anyway.

---

## R7 — Enums to add (spec Assumption 6)

**Decision**: Add to `app/enums.py`, values taken verbatim from `docs/constants.md`:

| Enum | Values | Backing column |
|---|---|---|
| `PaymentTerms` | 0 Immediate, 1 NetD | `sales_order.payment_terms`, `sales_quote.payment_terms` |
| `PaymentMethod` | 0 NA, 1 Cash, 2 Check, 3 EFT, … (SAT-aligned) | `customer_payment.method` |
| `PaymentType` | 0 NA, 1 Immediate, 2 CreditPayment, 3 PaymentInAdvance, … | `customer_payment.payment_type` |
| `Priority` | 0 Low, 1 Normal, 2 High, 3 Critical | `sales_order.priority` |
| `TransactionType` | 1 SalesOrder, 2 CustomerRefund, 3 InventoryIssue, 4 InventoryReceipt | `lot_serial_tracking.source` |
| `SourceType` | 1 DeliveryOrder, 2 CustomerPayment, 3 SalesOrder, … | `incidence.source` |

Two naming traps confirmed against the repository, both already recorded in the spec's Divergences:
`customer_payment.payment_type` (the document says `type`), and `lot_serial_tracking.source` carries
`TransactionType` (the document says `transaction_type`).

---

## R8 — Configuration settings to add (spec Assumption 5)

**Decision**: Extend `app/core/config.py`, which already absorbed the legacy `WebConfig` product
defaults:

| Setting | Default | Replaces |
|---|---|---|
| `default_currency` | `CurrencyCode.MXN` | `WebConfig.DefaultCurrency` |
| `default_quotation_due_days` | `30` | `WebConfig.DefaultQuotationDueDays` |
| `max_days_to_deliver_stockables` | `7` | `WebConfig.MaxDaysToDeliverStockables` |
| `price_validation_in_range_required` | `True` | `WebConfig.PriceValidationInRangeRequired` |
| `cost_price_list_id` | `0` | the "cost price list, id=0" convention (R3). **Superseded by #194:** now the constant `COST_PRICE_LIST_ID`, not a setting |

`default_customer_id` already exists and is reused unchanged.

---

## R9 — Surfacing the caller's employee and point of sale (FR-002, FR-004a)

**Finding that simplified the approach**: `get_current_user` already loads the `User` row, and
`User.settings` is `lazy='selectin'` with `facility`, `point_sale` and `cash_drawer` eagerly joined.
Everything needed is already in memory and simply is not exposed on `CurrentUser`.

**Decision**: Extend the `CurrentUser` dataclass with `employee_id`, `point_sale_id` and
`cash_drawer_id`, populated from the already-loaded `User` and its settings. **No JWT change** — the
token keeps its current claims, so no live session is invalidated and no re-login is forced.

Services then guard explicitly: a missing `employee_id` fails FR-002 with a distinguishable error,
and a missing `point_sale_id` fails FR-004a with a different one.

**Rationale**: Reading from the token would require re-issuing every token; re-querying inside each
service would repeat a read already done per request.

**Note on blast radius**: `CurrentUser` is constructed in `app/core/deps.py` and in test fixtures.
Adding fields with defaults keeps every existing call site and test valid — verified against the
`_auth()` helper pattern in `tests/api/test_facilities.py`.

---

## R10 — Testing approach (Constitution v1.1.0)

**Decision**: Follow the established `tests/api/` pattern exactly — FastAPI `dependency_overrides`
replacing `get_current_user` and `get_db`, with services patched via `unittest.mock.AsyncMock`. No
live database, matching every existing API test in this repository.

Per router, cover: happy path, 404, 401 (no auth override), 403 (privilege denied), and the
resource-specific failures — 409 for lifecycle conflicts (paying a draft, cancelling a paid order,
refunding an unpaid one, opening a second session on a drawer) and 422 for validation (quantity
below minimum, price outside margin, reversal with no reason).

State-transition and money logic get `tests/unit/` service tests, where the locking and re-validation
from R1/R2 can be exercised directly rather than through HTTP.

**Rationale**: The constitution makes tests mandatory for new endpoints and prescribes the minimum
matrix. The no-database pattern is what the repository already does; introducing a test database
would be a larger change than this feature warrants.

---

## Resolved unknowns

Every `NEEDS CLARIFICATION` from Technical Context is closed:

| Unknown | Resolved by |
|---|---|
| Folio uniqueness mechanism | R1 — facility row lock |
| Refund race prevention | R2 — source order row lock |
| Source of line cost | R3 — `COST_PRICE_LIST_ID` (a setting until #194) |
| Stock balance source | R4 — `lot_serial_tracking` aggregate |
| Rounding rules | R5 — quantize once at document level |
| Transaction granularity | R6 — one commit per transition |
| Enum values | R7 — `docs/constants.md`, verified against columns |
| Configuration defaults | R8 — extend existing settings |
| Employee / point-of-sale resolution | R9 — extend `CurrentUser`, no token change |
| Test strategy | R10 — existing `dependency_overrides` pattern |

**Performance targets** remain deliberately unset: the spec defines none, and inventing latency
budgets would contradict Principle I. The one operational constraint carried into design is
avoiding N+1 queries on list endpoints, handled by the existing `fk_expansion` helper.
