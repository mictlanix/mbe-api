# Feature Specification: Sales Cycle Endpoints

**Feature Branch**: `011-sales-cycle-endpoints`

**Created**: 2026-07-25

**Status**: Draft

**Input**: User description: "implement endpoints for @docs/specs/02-sales.md — the current state of this repo takes precedence over the document (it's older)"

## Context

`docs/specs/02-sales.md` is a reverse-engineered description of the legacy ASP.NET MVC system,
written before this API existed. It describes nine screens: Pricing, Sales Quotes, POS, Sales
Orders, Customer Payments, Customer Refunds, Credit Payments, Payments Editor, and Payments
Verification.

Today this API serves master data and configuration only — products, pricing, customers,
employees, facilities, warehouses, points of sale, cash drawers, taxpayers, exchange rates,
payment method options. **No transactional sales capability exists.** This feature closes that
gap: it is the first feature to create business documents rather than catalog records.

Where the legacy document and the current repository disagree, the repository wins. The
substantive divergences are recorded in [Divergences from the source document](#divergences-from-the-source-document).

### Source document coverage

| Source section | Disposition |
|---|---|
| §1 Pricing | **Already delivered** — price maintenance is served by the existing product-price and price-list capabilities. The legacy bulk-edit grid and CSV import/export are presentation concerns and stay out of scope. |
| §2 Sales Quotes | In scope — User Story 4 |
| §3 Point of Sale | **Partly out of scope** — the legacy `POSController` was a UI helper over the sales-order controller and is not reproduced. Its cash-session management is in scope (User Story 3); the counter-sale screen itself is composed client-side from sales orders, payments and cash sessions. |
| §4 Sales Orders | In scope — User Stories 1 and 2 |
| §5 Customer Payments | In scope — User Story 2 |
| §6 Customer Refunds | In scope — User Story 5 |
| §7 Credit Payments | In scope — User Story 6 |
| §8 Payments Editor | In scope — User Story 8 |
| §9 Payments Verification | In scope — User Story 7 |

## Clarifications

### Session 2026-07-25

- Q: How should document list endpoints scope their results? → A: Explicit filters only — no implicit default scoping, no `*` wildcard
- Q: Should point of sale be one atomic checkout, or composed from the sales-order and payment capabilities? → A: Neither — the legacy `POSController` was a UI helper and is ignored. No point-of-sale endpoints are built; `mbe-ui` composes the counter sale from the sales-order, payment and cash-session capabilities.
- Q: What should cancelling a confirmed sales order do to the stock its confirmation decremented? → A: Post a compensating reversing entry per line, restoring on-hand; the ledger stays append-only
- Q: How should a credit note's remaining balance and redemption work? → A: As a view over its backing customer payment — remaining is the issued amount less that payment's non-cancelled applications, and redemption is an ordinary payment application
- Q: When should a sales quote be assigned its folio — on create as the legacy document says, or on confirm? → A: On confirm, the same rule as sales orders and customer refunds, so abandoned drafts leave no gaps
- Q: Should folio uniqueness be guaranteed by the database as well as the application? → A: Yes — a unique index on `(facility, serial)` for all three document tables, added by migration. The legacy data blocking it is corrected first: `serial = 0` placeholders become `NULL`, and where two documents genuinely share a folio the earliest keeps it while later ones are renumbered
- Q: What is the binding lifecycle for paying, cancelling and refunding an order? → A: Given directly as four rules — only completed, uncancelled orders can be paid; only completed **and paid** orders can be refunded; a completed, paid order cannot be cancelled and must be refunded instead; a payment application can be undone, marking the order unpaid again, but must leave evidence rather than be silently deleted
- Q: A refundable order is now always fully paid, so every refund returns the full amount. In what form? → A: The cashier chooses at confirmation — cash out of the open session, or a credit note
- Q: Where should the evidence for a reversed payment application live, given no column records who reversed it? → A: The incidence log — employee, timestamp and a required reason; no schema change
- Q: What happens to money already applied when a partially-paid completed order is cancelled? → A: Refuse the cancellation until every live application has been reversed

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Build and confirm a sales order (Priority: P1)

As a salesperson, I need to open a sales order for a customer, add and adjust the products being
sold, and confirm it, so the sale becomes a committed, numbered document that reserves stock and
can be paid, delivered and invoiced.

**Why this priority**: The sales order is the spine of the entire sales cycle — payments,
refunds, deliveries and fiscal documents all hang off it. Nothing else in this feature has value
without it.

**Independent Test**: Can be fully tested by opening an order for a credit customer, adding lines
for stocked products, adjusting quantity and discount, confirming, and observing that the order
receives a folio, becomes read-only, and records an outbound stock movement per stocked line.

**Acceptance Scenarios**:

1. **Given** an authenticated salesperson whose user account is linked to an employee, **When**
   they open a new sales order without supplying a customer, salesperson, currency or dates,
   **Then** an editable order is created carrying the configured default customer, the user's own
   employee as salesperson, the user's facility and point of sale, the default currency at today's
   exchange rate, and payment terms derived from the customer's credit standing.
2. **Given** an editable order, **When** a line is added for a product, **Then** the line is stored
   with the price from the customer's price list, the product's tax rate and tax-inclusion flag,
   the cost recorded at time of sale, a quantity of at least the product's minimum order quantity,
   and a snapshot of the product's code and name. *(Both defaults are now overridable at add-time:
   an explicit `price` was always honoured, and an explicit `tax_rate` is since #135. The listed
   price is also no longer permanent — see scenario 3a.)*
3. **Given** an editable order, **When** a line's quantity, price, discount, tax rate, warehouse or
   comment is changed, **Then** the change is persisted and the order's computed subtotal, taxes and
   total reflect it. *(Corrected: the `delivery` flag this scenario originally named was dropped by
   migration 009 — see spec 012's Removed note — and `tax_rate` became editable in #135.)*
3a. **Given** an editable order carrying lines, **When** its customer is changed, **Then** every
   line is re-priced against the new customer's price list, unconditionally — including a line whose
   price was typed in by hand. `tax_rate` and `cost` are not touched: tax follows the product and
   cost comes from the cost price list, so neither depends on who is buying (#131).
4. **Given** an editable order with at least one line, **When** it is confirmed, **Then** it is
   assigned the next folio for its facility, is marked completed, records an outbound inventory
   movement for every stocked line that names a warehouse, and rejects any further edit.
5. **Given** an order containing a line priced at zero, **When** confirmation is attempted, **Then**
   confirmation is refused and the offending lines are identified.
6. **Given** a confirmed order that has not been paid, **When** it is cancelled, **Then** it is
   marked cancelled, no longer counts as outstanding, and the stock its confirmation removed is
   returned by a compensating ledger entry, leaving the original outbound entry intact.
7. **Given** a confirmed order that has been paid, **When** cancellation is attempted, **Then** it
   is refused and the caller is told to refund it instead.
8. **Given** a confirmed order carrying a partial payment, **When** cancellation is attempted,
   **Then** it is refused until that application has been reversed, and the blocking applications
   are named.

---

### User Story 2 - Take a customer payment and settle an order (Priority: P1)

As a cashier, I need to record money received from a customer and apply it against one or more of
that customer's outstanding orders, so the order's balance reflects reality and the customer's
account is accurate.

**Why this priority**: An order that cannot be paid is not a sale. Payment is the other half of
the minimum viable revenue path, and refunds, credit notes, verification and the payments editor
all build on the payment record.

**Independent Test**: Can be fully tested by confirming an order, recording a payment for the
customer, applying it to that order, and observing that the order's balance falls to zero and the
order is marked paid.

**Acceptance Scenarios**:

1. **Given** a customer, **When** a payment is recorded with an amount, currency, payment method
   and optional reference, **Then** it is stored against the customer, the recording employee, the
   facility and — when one is open — the cashier's cash session.
2. **Given** an unapplied payment and a confirmed, unpaid order for the same customer, **When** the
   payment is applied to the order, **Then** the applied amount reduces the order's outstanding
   balance and the remaining unapplied amount of the payment falls by the same amount.
3. **Given** an order whose applications now cover its total, **When** the last application is
   made, **Then** the order is marked paid.
4. **Given** a payment, **When** an application is attempted for more than the payment's unapplied
   amount, **Then** it is refused.
5. **Given** an applied payment, **When** the application is reversed with a reason, **Then** the
   application is marked cancelled but still visible, the order's balance is restored, the order
   stops being marked paid, the payment's unapplied amount is returned, and an incidence entry
   records who reversed it, when and why.
6. **Given** a reversal attempted with no reason given, **When** it is submitted, **Then** it is
   refused — no reversal is anonymous or unexplained.
7. **Given** an order that is not completed, or is cancelled, **When** a payment application is
   attempted against it, **Then** it is refused.
8. **Given** a list of outstanding orders, **When** a cashier searches by folio, order number,
   customer name or salesperson nickname, **Then** the matching unpaid confirmed orders are
   returned with their balances.

---

### User Story 3 - Open and close a cash session (Priority: P2)

As a cashier, I need to open a session on my cash drawer with an opening cash amount and close it
at the end of my shift by counting denominations, so cash handled at the counter is accounted for
and every counter payment is tied to the shift that took it.

**Why this priority**: An open session is a hard prerequisite for confirming a refund and is what
ties counter payments to a cashier's shift. It is not P1 only because credit selling (User Story 1)
works without it.

**Independent Test**: Can be fully tested by opening a session on a drawer, observing that a
second concurrent session on the same drawer is refused, entering denomination counts, and closing
the session.

**Acceptance Scenarios**:

1. **Given** a cashier with a cash drawer configured, **When** they open a session with an opening
   cash amount, **Then** an open session is created for that drawer and cashier with a start time.
2. **Given** an open session on a drawer, **When** another session is opened on the same drawer,
   **Then** it is refused.
3. **Given** an open session, **When** the cashier asks for its current state, **Then** the session
   reports its opening amount and the payments taken during it, grouped by payment method.
4. **Given** an open session, **When** the cashier submits denomination counts and closes it,
   **Then** the counts are stored, the session records an end time, and it stops being the
   cashier's open session.
5. **Given** a session opened on a previous day, **When** the cashier asks for their session state,
   **Then** the session is reported as stale, distinguishably from having no session at all, so the
   client can route the cashier to close it before taking further payments.

---

### User Story 4 - Quote a customer and convert the quote to an order (Priority: P3)

As a salesperson, I need to prepare a priced quotation for a customer, confirm it, and later turn
it into a sales order, so presales work is captured and does not have to be re-keyed when the
customer accepts.

**Why this priority**: Valuable for credit and project selling, but every quote can be re-keyed as
an order, so the sales cycle functions without it.

**Independent Test**: Can be fully tested by creating a quote, adding lines, confirming it,
duplicating it, and converting it to a sales order that carries the quote's customer and lines.

**Acceptance Scenarios**:

1. **Given** an authenticated salesperson, **When** they open a new quote, **Then** it is created
   with the default customer, the customer's assigned salesperson where one is set (otherwise the
   current user's employee), immediate terms, the default currency, and an expiry date of today
   plus the configured quotation validity period.
2. **Given** an editable quote, **When** lines are added and adjusted, **Then** each line carries
   the price from the customer's price list, an optional absolute price adjustment, a discount
   rate, and the product's tax rate.
3. **Given** an editable quote, **When** it is confirmed, **Then** it becomes read-only and is
   assigned its folio.
4. **Given** a confirmed quote, **When** it is duplicated, **Then** a new editable quote is created
   dated today with prices re-fetched from the customer's current price list.
5. **Given** a confirmed, unexpired quote, **When** it is converted to an order, **Then** a new
   editable sales order is created carrying the quote's customer, contact, ship-to, currency and
   lines, and referencing the quote as its origin.
6. **Given** a quote whose expiry date has passed, or one that is not confirmed, or one that is
   cancelled, **When** conversion is attempted, **Then** it is refused.

---

### User Story 5 - Refund a customer's returned goods (Priority: P3)

As a returns clerk, I need to take back items from a paid sales order, restock them, and hand the
customer their money back as cash or a credit note, so returns are processed without hand-adjusting
orders or stock.

**Why this priority**: Returns are a real and recurring need but are lower volume than selling,
and the sales cycle delivers value before returns exist.

**Independent Test**: Can be fully tested by confirming an order, paying it in full, opening a
refund against it, returning part of one line, confirming the refund, and observing stock restored
and the customer paid back — then attempting a second refund of the same units and seeing it capped.

**Acceptance Scenarios**:

1. **Given** a completed, fully paid order with refundable lines, **When** a refund is opened
   against it, **Then** the refund is pre-populated with every line that still has refundable
   quantity, each at a return quantity of zero, and lines with nothing left to refund are omitted.
2. **Given** an order whose lines have all been fully refunded, **When** a refund is opened,
   **Then** it is refused because no refundable items exist.
3. **Given** an order that is not completed, or is unpaid, or is only partly paid, **When** a refund
   is opened against it, **Then** it is refused, and the reason distinguishes "not completed" from
   "not paid" so the clerk knows whether to cancel the order instead.
4. **Given** an open refund, **When** a return quantity above the line's remaining refundable
   quantity is entered, **Then** it is refused.
5. **Given** an open refund with return quantities entered and an open cash session, **When** it is
   confirmed, **Then** lines with zero quantity are dropped, quantities are re-validated against
   what is still refundable, an inbound stock movement is recorded for every stocked line, the
   refund receives its folio, and it becomes read-only.
6. **Given** a confirmed refund and a cashier who chooses cash, **When** it is confirmed, **Then**
   the full refund total is paid out of the open cash session and appears in that session's close.
7. **Given** a confirmed refund and a cashier who chooses store credit, **When** it is confirmed,
   **Then** a credit note for the full refund total is issued to the customer, backed by a payment
   record classified as a credit note.
8. **Given** any confirmed refund, **When** it completes, **Then** the source order's paid flag is
   untouched — it was paid before the refund and stays paid afterwards.
9. **Given** no open cash session, **When** a refund is confirmed, **Then** it is refused.

---

### User Story 6 - Spend a customer's credit note (Priority: P4)

As a cashier, I need to see the credit notes a customer holds and apply their remaining balance
toward that customer's outstanding orders, so refunded value is redeemed rather than stranded.

**Why this priority**: Credit notes only exist once refunds do, and a small volume of them can be
handled manually in the interim.

**Independent Test**: Can be fully tested by producing a credit note through an over-refund,
listing the customer's open credit notes, applying one to another outstanding order, and observing
both the credit note's remaining balance and the order's balance fall.

**Acceptance Scenarios**:

1. **Given** a customer holding credit notes, **When** their open credit notes are listed, **Then**
   each is returned with the amount issued, the refund and source order that produced it, and a
   remaining balance derived from its backing payment's non-cancelled applications.
2. **Given** an open credit note and an outstanding order for the same customer, **When** the
   credit note is applied to the order, **Then** the order's balance falls and the credit note's
   remaining balance falls by the same amount.
3. **Given** a credit note, **When** an application exceeding its remaining balance is attempted,
   **Then** it is refused.
4. **Given** a redeemed credit note, **When** the redemption is reversed, **Then** the credit note's
   remaining balance returns to what it was and the order's balance is restored — the redemption
   behaves as the ordinary payment application it is.

---

### User Story 7 - Verify payments received off the counter (Priority: P4)

As a supervisor, I need a queue of payments nobody has confirmed yet — typically bank transfers —
so I can check them against the bank statement and mark them verified or flag them.

**Why this priority**: A control function that improves trust in the data but blocks no selling.

**Independent Test**: Can be fully tested by recording a transfer payment, seeing it appear in the
unverified queue, verifying it, and seeing it leave the queue.

**Acceptance Scenarios**:

1. **Given** payments with and without a verifier, **When** the unverified queue is listed, **Then**
   only payments without a verifier are returned, filterable by facility, date range, payment
   method and amount range.
2. **Given** an unverified payment, **When** a supervisor verifies it, **Then** the supervisor's
   employee is recorded as its verifier and it leaves the queue.
3. **Given** a payment, **When** a supervisor rejects it, **Then** an incidence entry is recorded
   against the payment describing why.

---

### User Story 8 - Correct a misapplied payment (Priority: P5)

As a supervisor, I need to see every order a payment was applied to and move that money to the
right order, so a cashier's mistake can be corrected without deleting and re-keying the payment.

**Why this priority**: A rare corrective tool. Every case it handles can otherwise be resolved by
reversing an application (User Story 2) and re-applying.

**Independent Test**: Can be fully tested by applying a payment to the wrong order, viewing the
payment's applications, cancelling the wrong one, and applying the freed amount to the correct
order.

**Acceptance Scenarios**:

1. **Given** a payment, **When** its applications are listed, **Then** every application is
   returned including cancelled ones, each naming its order, amount and the employee who applied
   it.
2. **Given** a payment applied to the wrong order, **When** a supervisor cancels the application
   and applies the freed amount to another order, **Then** both orders' balances and paid flags
   are corrected.
3. **Given** payments across facilities, **When** a supervisor searches by customer, reference or
   date, **Then** matching payments are returned.

---

### Edge Cases

- **A user with no linked employee.** Every sales document records a creator, updater and
  salesperson as an employee. A user account with no employee cannot author one; the attempt is
  refused with an explanatory error rather than a null reference.
- **A user with no point of sale configured.** A sales order requires one and a user's settings may
  omit it. Refused up front, distinguishably from the missing-employee case.
- **Cancelling a confirmed order twice, or cancelling a draft.** A second cancellation is refused;
  cancelling a never-confirmed draft posts no stock reversal, because nothing was decremented.
- **Unwinding a sale: cancel or refund.** The two paths never overlap. An unpaid order is cancelled;
  a paid order is refunded. A partly-paid order is neither until its applications are reversed,
  which returns it to unpaid and therefore cancellable.
- **A refund whose order was paid by credit note.** Permitted — the source order is paid however it
  was paid — and the refund returns value as cash or a fresh credit note like any other.
- **Reversing a payment on an order that has already been refunded.** The order stops being paid,
  but the refund already happened and is not undone; the reversal's required reason is what makes
  this visible to whoever audits it.
- **Credit terms for a customer without credit.** Credit terms are refused for a customer with no
  credit limit, with expired outstanding credit, already over their credit limit, or for the
  configured walk-in default customer.
- **Selling below the allowed margin.** A price outside the product's low/high profit margin for
  the applicable price list is refused, unless the user holds the privilege that exempts them from
  price-range validation.
- **Selling stock that is not there.** For a product that requires stock and is stockable, a line
  with no warehouse, or with a warehouse whose available balance cannot cover every line of the
  same product in the order, blocks confirmation.
- **Two cashiers confirming the same facility's document at once.** Folio assignment must not
  produce a duplicate folio for a facility.
- **A refund raced against another refund of the same order line.** Quantities are re-validated at
  confirmation, so two clerks cannot together refund more than was sold.
- **Changing the currency on an order that already has lines.** The exchange rate and every line's
  currency are brought into agreement rather than left mixed.
- **Editing a confirmed or cancelled document.** Refused; confirmation is the point of no return
  for everything except priority and the corrective supervisor tools.
- **Applying a payment across currencies.** An application in a currency other than the order's is
  refused rather than silently converted.
- **A quote converted twice.** Permitted — the legacy system does not block it — but each
  conversion produces an independent order; the origin quote reference makes duplicates visible.

## Requirements *(mandatory)*

### Functional Requirements

#### Shared behaviour

- **FR-001**: Every endpoint in this feature MUST require an authenticated session and MUST be
  gated by the access right matching the operation on the system object governing the resource:
  Sales Quotes (30), Sales Orders (7), Customer Payments (8), Customer Refunds (22), Credit
  Payments (83), Payments Editor (100), Payments Verification (108), Cash Session Close (111),
  Customer Refund Confirm (110). POS (44) governs nothing in this feature, because no
  point-of-sale-specific endpoint exists (FR-054); a cashier is authorized through Sales Orders (7)
  and Customer Payments (8) like any other seller.
- **FR-002**: The system MUST resolve the authoring employee from the authenticated user and MUST
  refuse, with a distinguishable error, any document-authoring operation by a user with no linked
  employee.
- **FR-003**: The system MUST stamp creator, updater, creation time and modification time on every
  document it creates or changes.
- **FR-004**: Every document MUST take its facility from the authenticated user's context rather
  than from the request body.
- **FR-004a**: A sales order requires a point of sale, which is optional on a user's settings. The
  system MUST refuse to create one for a user with no point of sale configured, with an error
  distinguishable from the missing-employee refusal (FR-002), rather than failing on a constraint.
- **FR-005**: Every list endpoint MUST support pagination and MUST return the total count alongside
  the page, matching the shape already used by existing list endpoints.
- **FR-006**: Documents MUST NOT be deletable. Cancellation is the only way to retire a document.
  Individual lines of an editable document MUST be removable.
- **FR-007**: Every monetary document MUST report its computed subtotal, tax total and grand total,
  and every order MUST additionally report its outstanding balance, all derived rather than stored.
- **FR-008**: The system MUST record a document's currency and exchange rate, defaulting the rate
  to the rate registered for the document's date.
- **FR-009**: Document list endpoints MUST apply no implicit scoping beyond the caller's facility.
  Narrowing MUST be expressed through explicit filter parameters — at minimum `mine` (documents the
  caller created, updated or is the salesperson for), `customer`, `salesperson`, `status` and a date
  range, plus `point_sale` on the sales-order list since #136 — and a list given no filters MUST
  return every document for the caller's facility. The legacy `*` wildcard MUST NOT be reproduced;
  widening beyond the caller's facility MUST be an explicit `facility` parameter gated by the
  cross-facility search privilege (101).
- **FR-009a**: The payments list MUST NOT be implicitly scoped to the caller's open cash session.
  Session scoping MUST be requested through an explicit `cash_session` filter.

#### Sales orders

- **FR-010**: Users MUST be able to create an editable sales order, defaulted from configuration
  and user context: default customer, the current user's employee as salesperson, the user's
  facility and point of sale, default currency at today's rate, date of now, promise date of now
  plus the configured stockable delivery window, and payment terms of credit when the customer
  qualifies for credit and immediate otherwise.
- **FR-011**: Users MUST be able to read, update and list sales orders. Updates MUST be refused
  once the order is completed or cancelled, except for its priority, which MUST remain editable
  after completion.
- **FR-011a**: A sales-order list row MUST carry the customer's own name, and the free-text list
  search MUST match it. Added by #172.

  > Both halves were the same missing join. `sales_order.customer_name` is the per-document
  > override — the data dictionary defines it as "Override customer name on docs" and nothing
  > derives it from the customer row — so a list rendering it showed a dash on every ordinary sale,
  > and a search matching only it compared a term against a column that is `NULL` on exactly the
  > rows a cashier is looking for. Searching the walk-in customer matched 10 sales out of 32,488:
  > not an empty result that reads as a gap, but a plausible handful that hides the rest.
- **FR-011b**: Users MUST be able to state, when creating a sale and while it remains a draft, how
  the goods will reach the customer — collected at the counter, delivered, or **mixed**, part
  collected and the rest shipped. The value MUST be optional, and absent MUST mean *not recorded*
  rather than any of the three. Added by #170.

  > **Three values, and the address carries one bit.** The point of sale encoded this into
  > `ship_to` — the facility's own address for a pickup, the customer's otherwise — which makes
  > delivery and mixed identical on the wire, so a sale reopened in a new session came back as plain
  > delivery and the units meant for the counter read as an unassigned remainder.
  >
  > **Absent means absent.** Nothing infers the value from the address, and migration 017 ships the
  > column empty across all 335,763 existing sales: not one of them has a `ship_to` pointing at a
  > facility address, so deriving it would stamp every row `delivery` — a confident wrong answer in
  > place of "unknown", indistinguishable afterwards from one a cashier actually gave.
- **FR-012**: Users MUST be able to add, update and remove lines on an editable order. Adding a
  line MUST snapshot the product's code and name, its tax rate and tax-inclusion flag, its cost
  from the cost price list, and its price from the customer's assigned price list, and MUST default
  the quantity to the product's minimum order quantity.
- **FR-012a**: The snapshotted tax rate MUST be a default rather than a fixed value: users MUST be
  able to state a `tax_rate` when adding a line and change it while the order is editable, as they
  already can for `price`. The tax-inclusion flag stays derived from the product. Added by #135;
  filed as a question (is a product's rate the single source of truth for every line that sells it?)
  and answered no.
- **FR-013**: The system MUST refuse a line quantity below the product's minimum order quantity.
- **FR-013a**: Changing an editable order's customer MUST re-price every existing line against the
  new customer's price list. Re-pricing is **unconditional** — a line tracks whichever customer is
  on the order, including one whose price was entered by hand. `tax_rate` and `cost` MUST NOT be
  touched: tax follows the product and cost comes from the cost price list, so neither depends on who
  is buying. A product absent from the new price list MUST price at zero, as FR-012 would for that
  customer, where FR-017's zero-price refusal catches it. Re-pricing MUST NOT occur when the request
  names the customer already on the order — an update that changed nothing is not a change. Added by
  #131, which also records the rejected alternative: preserving a hand-entered price has no stored
  marker to read, so it could only have been a guess at what the previous customer's list charged.
- **FR-014**: The system MUST refuse a line price outside the product's low and high profit margins
  for the applicable price list when margin validation is enabled, unless the user holds the
  price-range exemption privilege (102).
- **FR-015**: The system MUST derive the due date from the payment terms: the order date for
  immediate terms, and the order date plus the customer's credit days for credit terms.
- **FR-016**: The system MUST refuse credit terms when the customer has no credit limit, has
  expired outstanding credit, is over their credit limit, or is the configured default walk-in
  customer.
- **FR-017**: Users MUST be able to confirm an order. Confirmation MUST refuse an order that is
  already completed or cancelled, MUST refuse an order carrying any line priced at zero and MUST
  identify those lines, MUST validate stock for every line whose product requires stock and is
  stockable, MUST assign the next folio for the order's facility, MUST record an outbound inventory
  ledger entry classified as a sales order for every stocked line naming a warehouse, and MUST mark
  the order completed.
- **FR-018**: Stock validation MUST require a warehouse on every line whose product requires stock
  and is stockable, and MUST require that the warehouse's available balance covers the total
  quantity of that product across the whole order.
- **FR-019**: Users MUST be able to cancel an order that is not already cancelled and not paid. A
  completed, paid order MUST NOT be cancellable — it is unwound by refunding it (FR-060), and the
  refusal MUST say so rather than reporting a bare conflict.
- **FR-019b**: Cancellation MUST be refused while any non-cancelled payment application exists
  against the order, naming them. The applications are reversed first (FR-045), each leaving its own
  evidence, and the cancellation then proceeds — so a cancelled order never holds a customer's
  money, and money never moves as a side effect of cancelling.
- **FR-019a**: Cancelling an order whose confirmation decremented stock MUST post a compensating
  inbound inventory ledger entry for every line that was decremented, restoring on-hand to its
  pre-confirmation level. The original outbound entry MUST NOT be deleted or edited — the ledger is
  append-only, so the sale and its reversal are both visible. Cancelling an order that was never
  confirmed MUST post nothing.
- **FR-020**: Changing an order's currency MUST update the order's exchange rate and bring every
  line into the new currency.
- **FR-021**: The system MUST provide a sales product lookup returning, for a search pattern, the
  matching salable products with the price for a given customer and the available stock per
  warehouse. A 13-digit numeric pattern MUST be matched against the product's barcode instead of
  the free-text fields.
- **FR-021a**: The product lookup **and** a sales order's line responses MUST each report the
  product's unit of measurement, in the same full SAT-record shape the product endpoints return.
  Added by #145.

  > **Both, not either.** A capture grid shows a unit per line, and the lookup alone would let a
  > client cache one per product at scan time — but a resumed sale re-reads its lines and never
  > re-runs the lookup, so the rows already captured, exactly the ones a resume exists to show, would
  > be the blank ones. `sales_order_detail` snapshots the product's code and name and nothing else,
  > so the line's unit is read through the product; a product whose unit has no SAT catalog row
  > reports `null` rather than a fabricated value. Batched into one query per line set and per lookup
  > page, never one per row.

- **FR-021b**: The product lookup **and** a sales order's line responses MUST each report the
  product's photo, as the same resolved URL the product endpoints return. Added by #157.

  > Both shapes, for the reason FR-021a needed both: a resumed sale re-reads its lines without
  > re-running the lookup, so the rows already captured would be the ones with an empty thumbnail.
  > The photo is read through the product — `sales_order_detail` stores no image — and batched into
  > one query per line set; a lookup page needs no query at all, since it already holds the product
  > rows. A product with no photo reports `null`. `products` already resolved one, but reading it
  > from a point of sale asks a cashier to hold the products privilege and costs a call per line on
  > the one screen whose premise is speed.

- **FR-022**: Folio uniqueness MUST be enforced by a unique database constraint on
  `(facility, serial)` for sales orders, quotes and refunds, in addition to the application-level
  serialisation that assigns them. Draft documents carry no folio and MUST NOT collide with one
  another. Existing rows that violate the constraint MUST be corrected before it is applied: a
  `serial` of `0` is the legacy application's placeholder for "not numbered" and becomes absent,
  and where two documents genuinely share a folio the earliest keeps it while later ones are
  reassigned to the next free numbers for that facility.

#### Sales quotes

- **FR-030**: Users MUST be able to create an editable quote defaulted to the default customer, the
  customer's assigned salesperson where set and otherwise the current user's employee, immediate
  terms, the default currency, today's date, and an expiry date of today plus the configured
  quotation validity period.
- **FR-031**: Users MUST be able to read, update and list quotes, and to add, update and remove
  lines while the quote is editable. A quote line MUST carry a price, an absolute price adjustment,
  a discount rate, the product's tax rate and tax-inclusion flag, and a snapshot of the product's
  code and name.
- **FR-032**: Users MUST be able to confirm a quote, which MUST assign the next folio for the
  quote's facility and make it read-only, and to cancel a quote. A quote MUST NOT be assigned a
  folio before confirmation, so an abandoned draft leaves no gap in the facility's sequence — the
  same rule as sales orders (FR-017) and customer refunds (FR-063).
- **FR-033**: Users MUST be able to duplicate a quote into a new editable quote dated today, with
  every line's price re-fetched from the customer's current price list.
- **FR-034**: Users MUST be able to convert a confirmed, uncancelled, unexpired quote into a new
  editable sales order carrying the quote's customer, contact, ship-to, currency, exchange rate and
  lines, and referencing the quote as the order's origin. Conversion MUST be refused for a quote
  that is not confirmed, is cancelled, or whose expiry date has passed.

#### Customer payments

- **FR-040**: Users MUST be able to record a customer payment carrying customer, amount, currency,
  payment method, optional payment method option, optional reference, date, facility, payment
  classification, and the recording cashier's open cash session where one exists.
- **FR-041**: Users MUST be able to list and read payments, filterable by customer, cash session,
  facility, date range, payment method and verification state.
- **FR-041a**: Users MUST be able to list the payments standing against one sales order, the reverse
  of FR-045's payment-to-applications direction. Each row MUST carry enough of its payment's own
  fields — method, reference, date, classification and verification state — to render a list without
  a follow-up request per application, and MUST include cancelled applications for the same reason
  FR-045 does. Added by #134; the order's `balance` was always correct regardless, so what was
  missing was the itemisation, never the gate.
- **FR-042**: Users MUST be able to apply a payment to a confirmed, uncancelled order for the same
  customer, for an amount not exceeding the payment's unapplied amount, recording the applying
  employee and the application date. An order that is not completed, or that is cancelled, MUST NOT
  be payable — so a paid order is necessarily an uncancelled, completed one.
- **FR-042a**: An application MUST record the change given back alongside the amount applied, so a
  cash tender above the order's balance settles the order and returns the difference as change
  rather than over-applying. Change MUST NOT reduce the payment's unapplied amount available to
  other orders.
- **FR-043**: The system MUST refuse an application whose currency differs from the order's
  currency.
- **FR-044**: The system MUST mark an order paid once its non-cancelled applications cover its
  total, and MUST clear that flag when they no longer do.
- **FR-045**: Users MUST be able to reverse an application by marking it cancelled, which MUST
  restore the order's balance, clear the order's paid flag when the remaining applications no longer
  cover its total, and return the amount to the payment's unapplied total. A cancelled application
  MUST remain visible rather than be deleted — applications are never removed, only cancelled.
- **FR-045a**: A reversal MUST record evidence in the incidence log: the employee who reversed it,
  the time, the application and order concerned, and a reason. The reason MUST be required, so no
  reversal is anonymous or unexplained. A reversal MUST be refused if no reason is given.
- **FR-046**: The system MUST provide a search over outstanding orders returning unpaid confirmed
  orders with their balances, matching a numeric term against order number or folio and a text term
  against customer name, the customer's salesperson nickname, the order's salesperson nickname or
  the order's customer-name override. Each row MUST carry the customer's **own** name, not only the
  per-document override. Display half added by #174.

  > **The two halves disagreed.** The search matched the customer's name from the start; the row it
  > returned projected `sales_order.customer_name`, the per-document override, which is `NULL` on
  > every order that did not set one — all 1,840 outstanding orders in the deployment. So a cashier
  > could find an order by typing a customer's name and the row that came back would not say that
  > name. The override keeps its meaning and stays writable; the customer's name is reported beside
  > it rather than in place of it, because a read side that fell back would let a client read an
  > order and put it back unchanged, writing the customer's name *into* the override.

#### Cash sessions

- **FR-050**: Users MUST be able to open a cash session for a cash drawer with an opening cash
  amount, recording the cashier and start time. Opening MUST be refused when that drawer already
  has an open session, or when the cashier already has one open on another drawer.
- **FR-051**: Users MUST be able to read their current open session, including its opening amount
  and the payments taken during it summarised by payment method.
- **FR-051a**: Every cash-session response MUST expand its cash drawer, cashier and cash supervisor
  rather than returning bare foreign keys, so a client can render a shift list without resolving
  three ids per row. The employees list has no fetch-many-by-id filter and is capped at 100 rows,
  so a client-side map is not an equivalent.
- **FR-051b**: Users MUST be able to list sessions filtered by cash drawer, cashier, facility, a
  date range over the start time, and status — open, stale or closed, derived exactly as FR-053
  derives it — and MUST be able to order by start time in either direction as well as by the
  default newest-id-first. Client-side filtering is not an equivalent: it is wrong across page
  boundaries, and it cannot reach the extra open sessions FR-053's most-recent-only rule hides.
- **FR-051c**: The session list MUST NOT be implicitly scoped to the caller's facility. Reconciling
  a day is a cross-facility task, so facility narrowing MUST be requested through an explicit
  `facility` filter — which resolves through the drawer, as `cash_session` stores no facility.
- **FR-052**: Users MUST be able to close a session by submitting denomination counts, which MUST
  be stored and MUST set the session's end time. Closing MUST be gated by the cash session close
  system object (111).
- **FR-053**: The current-session response MUST distinguish three states — no open session, an open
  session started today, and an open session started on an earlier day — so a client can require a
  stale session to be closed before more payments are taken against it.
- **FR-054**: The system MUST NOT expose any point-of-sale-specific endpoint. A counter sale is
  composed by the client from the sales-order, customer-payment and cash-session capabilities
  above; the legacy `POSController` was a presentation-layer helper over the sales-order controller
  and has no server-side counterpart here.

#### Customer refunds

- **FR-060**: Users MUST be able to open a refund against a completed **and paid** order,
  pre-populated with every line whose refundable quantity is greater than zero at a return quantity
  of zero, carrying the original line's price, discount, tax rate, currency and product snapshot.
  Opening MUST be refused when the order has no refundable lines, and MUST be refused with a
  distinguishable reason when the order is not completed or is not fully paid. Goods are returnable
  only once they have been paid for; an unpaid or partly-paid order is unwound by cancelling it
  (FR-019b), not by refunding it. Because payment already requires an uncancelled order (FR-042), a
  paid order cannot also be cancelled, so no separate cancellation check is needed.
- **FR-061**: A line's refundable quantity MUST be the quantity sold on that order line less the
  quantity already returned on completed, uncancelled refunds of the same line.
- **FR-062**: Users MUST be able to set each refund line's return quantity and return-to warehouse
  while the refund is editable, and MUST be refused a quantity above that line's refundable
  quantity.
- **FR-063**: Users MUST be able to confirm a refund, which MUST require an open cash session,
  MUST re-validate every line's quantity against what is currently refundable and adjust or drop
  lines accordingly, MUST drop lines with zero quantity, MUST record an inbound inventory ledger
  entry classified as a customer refund for every stocked line, MUST assign the next folio for the
  refund's facility, and MUST mark the refund completed. Confirmation MUST be gated by the customer
  refund confirm system object (110).
- **FR-064**: Because a refundable order is fully paid (FR-060), its outstanding balance is zero and
  the whole of a confirmed refund's total is owed back to the customer. The legacy behaviour of
  applying a refund against a remaining balance therefore cannot arise and MUST NOT be implemented;
  a refund never alters the source order's paid flag.
- **FR-065**: Confirming a refund MUST return its full total to the customer in the form the cashier
  chooses at confirmation: cash paid out of the open cash session, or a credit note backed by a
  payment record classified as a credit note. The choice MUST be recorded on the refund, and a cash
  payout MUST be attributed to the open session so it appears in that session's close.
- **FR-065a**: `balance_zeroed_time` MUST NOT be written by the refund path. It records a supervisor
  manually zeroing a remaining balance, which is a separate act on an unpaid order.
- **FR-066**: Users MUST be able to cancel a refund that is not completed.

#### Credit notes, verification and corrections

- **FR-070**: Users MUST be able to list a customer's credit notes, each reporting the amount
  issued, the refund and source order that produced it, and its remaining balance. A credit note's
  remaining balance MUST be derived as the amount issued less the non-cancelled applications of its
  backing customer payment — it MUST NOT be stored as a second, separately-maintained figure.
- **FR-070a**: Redeeming a credit note MUST be the ordinary application of its backing payment to
  an outstanding order for the same customer (FR-042), so it is bounded by that payment's unapplied
  amount, is reversible (FR-045), and is correctable through the payments editor (FR-073) like any
  other application. No separate redemption record is introduced.
- **FR-071**: Supervisors MUST be able to list payments with no verifier, filterable by facility,
  date range, payment method and amount range, and to verify one, which MUST record the
  supervisor's employee as its verifier.
- **FR-072**: Supervisors MUST be able to reject a payment, which MUST record an incidence entry
  against it carrying the reason.
- **FR-073**: Supervisors MUST be able to list every application of a payment — including cancelled
  ones — and to search payments by customer, reference and date across facilities.

### Key Entities *(include if feature involves data)*

- **Sales Quote** (+ **Quote Line**): a priced, expiring offer to a customer. Carries facility,
  folio, date, salesperson, customer, contact, ship-to, terms, expiry, currency and rate, and its
  completed/cancelled state. Each line snapshots product code and name alongside quantity, price,
  price adjustment, discount, tax rate and currency.
- **Sales Order** (+ **Order Line**): the committed sale. Adds point of sale, origin quote,
  promise and due dates, fiscal recipient, priority, and paid/delivered state to the quote's shape.
  Each line adds cost at time of sale, fulfilment warehouse and a delivery flag.
- **Customer Payment**: money received from a customer, classified by method and payment type, tied
  to a facility and — for counter payments — a cash session, and optionally verified by a
  supervisor. Exists independently of any order.
- **Sales Order Payment**: the application of one payment to one order for an amount, recording who
  applied it and whether it has since been cancelled. Reversible without destroying history.
- **Customer Refund** (+ **Refund Line**): goods returned against a specific order. Each line
  points at the original order line it reverses and names the warehouse the stock returns to.
- **Credit Note**: value owed back to a customer, produced when a refund is settled as store credit
  rather than cash, spendable against that customer's other orders.
- **Cash Session** (+ **Cash Count**): a cashier's shift on a cash drawer, from opening amount to
  the denomination counts recorded at close. Scopes counter payments.
- **Incidence Entry**: the audit record written when money is unwound or questioned — a reversed
  payment application or a rejected payment — naming the entity it concerns, the employee, the time
  and a stated reason. It is what makes a reversal evidenced rather than silent.
- **Inventory Ledger Entry**: the stock movement a state change writes — negative when a sales
  order is confirmed, positive when a customer refund is confirmed or a confirmed order is
  cancelled — naming the source document, warehouse, product and quantity. Append-only: entries are
  never edited or removed, so a reversal is its own entry.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A client can complete a walk-in counter sale end to end — find the product by barcode
  or text, build the order, confirm it, take payment and record the change due — using only the
  capabilities in this feature, with no point-of-sale-specific endpoint.
- **SC-002**: 100% of the nine capability areas in the source document are either delivered by this
  feature or explicitly recorded as already delivered or out of scope, with no silent omissions.
- **SC-003**: Stock on hand after a confirmed sale falls by exactly the quantity sold, after a
  confirmed refund rises by exactly the quantity returned, and after a cancelled order returns to
  exactly its pre-confirmation level — all verifiable from the inventory ledger alone, which is
  only ever appended to.
- **SC-004**: The sum of a customer's outstanding order balances always equals what they owe: an
  order's balance is its total less every non-cancelled application against it, and reversing an
  application restores it exactly.
- **SC-005**: No two documents of the same type share a folio within a facility, including when
  two users confirm at the same moment. Guaranteed by a unique database constraint on
  `(facility, serial)`, not by application code alone — a regression cannot pass unnoticed.
- **SC-006**: No customer can be refunded more units of an order line than were sold on it, under
  any interleaving of concurrent refunds.
- **SC-007**: No committed document can be silently altered: after confirmation, every content
  edit is refused, and every subsequent state change (cancellation, payment, refund) is recorded as
  its own auditable record.
- **SC-009**: Money is never moved or unwound anonymously. Every payment application and every
  reversal names the employee responsible and the time, no application is ever deleted, and every
  reversal carries a stated reason — so the full history of what was paid, unpaid and why is
  reconstructable from the record alone.
- **SC-010**: A sale is unwound by exactly one route and never both: an order is cancellable only
  while no money stands against it, and refundable only once it is fully paid. No order can be both
  cancelled and refunded.
- **SC-008**: Every endpoint refuses unauthenticated callers and callers lacking the governing
  privilege, verified by test for each.

## Assumptions

Recorded because the source document is a description of a legacy UI, not a specification of this
API. Each is a judgement made where the document has no API-level answer.

1. **Screens are not resources.** The legacy document describes MVC controllers and screens. This
   feature exposes the underlying business documents as resources; the redirect behaviour it
   describes (POS confirm redirecting to `PayOrder`, PDF redirecting to `Payments/Print`) has no
   API meaning and is replaced by the outcomes those redirects existed to produce. The clearest
   case is `POSController`, which extended `SalesOrdersController` purely to change what the
   browser did next — it becomes no endpoint at all, and `mbe-ui` composes the counter sale from
   sales orders, payments and cash sessions.
2. **Document rendering is out of scope.** Print, PDF and send-by-email actions on quotes, orders,
   payments and receipts are not implemented. The repository carries no rendering or mail
   dependency, and adding one is a feature in its own right. The API returns the data a client
   needs to render.
3. **Cross-domain creation is out of scope.** "Create Delivery Order" (`docs/specs/06-logistics.md`)
   and "Create Fiscal Document" (`docs/specs/10-fiscal-documents.md`) belong to features that do not
   exist yet. The order's line-level delivery flag and fiscal recipient fields are stored so those
   features can consume them.
4. **Pricing (§1) is already delivered.** Per-product, per-price-list price maintenance already
   exists. The bulk grid, margin colouring and CSV import/export described in §1 are client
   concerns over data the API already serves, and are not re-implemented.
5. **Legacy `WebConfig` values become application settings.** The document's `DefaultCustomer`,
   `DefaultCurrency`, `DefaultQuotationDueDays`, `MaxDaysToDeliverStockables` and
   `PriceValidationInRangeRequired` join the configuration that already absorbed the legacy
   product defaults.
6. **The enums this feature needs are added from the constants reference.** Payment terms, payment
   method, payment type, priority and inventory transaction type are documented in
   `docs/constants.md` but absent from the codebase; this feature adds them rather than using bare
   integers.
7. **Totals are computed, never stored.** The legacy system computes subtotal, tax, total and
   balance as model properties. No column exists for them and none is added.
8. **Folios are assigned at confirmation** as the next value for the document's facility,
   preserving legacy numbering. The uniqueness required by SC-005 is an implementation concern for
   the plan.
9. **Existing conventions are reused, not reinvented**: paginated list envelope, privilege
   dependency, foreign-key expansion, reference guards on delete, and the 409-on-constraint
   handling all already exist and apply here.
10. **Priority values follow `docs/constants.md`** (Low / Normal / High / Critical), not the source
    document's "Low/Medium/High".
11. **A quote may be converted more than once**, matching legacy behaviour. The origin-quote
    reference on the order makes it detectable.

## Divergences from the source document

Recorded because the user directed that the repository takes precedence. Each is a case where
`docs/specs/02-sales.md` describes something the current schema contradicts.

Two further departures are **directed policy changes**, not schema conflicts — the legacy behaviour
is well-defined and is being deliberately replaced (see Clarifications):

| Source document behaviour | This feature |
|---|---|
| A refund may be opened against any completed, uncancelled order, and its total is applied against the order's remaining balance — settling it, and issuing a credit note only for the excess (§6 Confirm steps 5–6) | A refund requires a **fully paid** order. The balance is therefore always zero, the settlement step cannot arise, and the full refund total is returned as cash or a credit note at the cashier's choice (FR-060, FR-064, FR-065) |
| Cancelling a completed order is blocked only when it is paid (§4 Cancel Action) | Also blocked while any non-cancelled payment application stands against it, so a partly-paid order must have its applications reversed first (FR-019b) |

| Source document says | Repository has | Resolution |
|---|---|---|
| Quote line has `price_increment` and `price_increment_rate` | `sales_quote_detail.price_adjustment` only | One absolute price adjustment. A percentage markup is a client-side calculation. |
| Quote header field `sales_quote.terms` | `sales_quote.payment_terms` | Repository naming. |
| `sales_order.recipient` is a foreign key to `taxpayer_recipient` | `recipient` is a 13-character RFC string, with a separate `recipient_address` | Store the RFC as given. |
| Refund line field `discount_rate` | `customer_refund_detail.discount` | Repository naming. |
| Payment classification column `customer_payment.type` | `customer_payment.payment_type` | Repository naming. |
| `PaymentType` values `Normal`, `CreditNote`, `COD` | `docs/constants.md`: `NA`, `Immediate`, `CreditPayment`, `PaymentInAdvance` and beyond | Use the constants reference. |
| Priority values Low / Medium / High | `docs/constants.md`: Low / Normal / High / Critical | Use the constants reference. |
| Inventory ledger classified by `transaction_type` | `lot_serial_tracking.source` and `reference` | Write the transaction type into `source` and the document id into `reference`. |
| Quote header carries a salesperson defaulted from `customer.salesperson` | Same, and `Customer.salesperson` is nullable | Fall back to the current user's employee when unset. |
| Stock validation keys off `product.StockRequired` | `product.stock_verification`, already exposed on the API as `stock_required` | Same flag, repository spelling; "requires stock" throughout this spec means `stock_verification`. |
| A quote's folio is auto-generated on create | Column is nullable, so the schema permits either | Assigned on confirm, matching orders and refunds (FR-032). |

## Dependencies

- **Existing capabilities consumed**: customers (credit limit, credit days, assigned price list,
  assigned salesperson), products (min order quantity, tax rate, tax inclusion, stockable flag,
  barcode, cost), product prices and price lists (customer price, profit margins), employees,
  facilities, warehouses, points of sale, cash drawers, exchange rates, payment method options,
  taxpayer recipients.
- **Existing gap this feature must close**: the authenticated user's context currently carries the
  facility but not the linked employee, point of sale or cash drawer, all of which every document
  in this feature needs.
- **One schema migration.** Every table this feature writes is already mapped, and no column is
  added or changed. The single migration adds a unique index on `(facility, serial)` to
  `sales_order`, `sales_quote` and `customer_refund`, and corrects the legacy rows that would
  otherwise block it (FR-022).
- **Blocked on nothing else.**

## Out of Scope

- A dedicated point-of-sale endpoint set, including any atomic "checkout" operation. The counter
  sale is a client composition (FR-054).
- Printing, PDF generation and emailing of any document.
- Delivery orders and fiscal documents (CFDI) created from a sales order.
- The bulk pricing grid and its CSV import/export.
- Lot and serial number capture at line level. The ledger entries this feature writes carry no lot
  or serial number; lot/serial handling is `docs/specs/04-inventory.md`.
- Sales reporting (`docs/specs/11-reports.md`) and commissions.
- Advances and payments in advance beyond storing the payment classification.
