# Feature Specification: Pilot Sales Rules

**Feature Branch**: `023-pilot-sales-rules`

**Created**: 2026-10-22

**Status**: Draft

**Input**: Implements mictlanix/mbe-roadmap#149, mictlanix/mbe-roadmap#35 and mictlanix/mbe-api#248 (epic mictlanix/mbe-roadmap#3). The scope document is `docs/specs/02-sales.md`. The request named "§2.2 (Sales Quotes)" and "§2.4 (Sales Orders)", but those numbers do not exist: Sales Quotes is §2 and Sales Orders is §4. There are three parts:
1. apply a customer's per-product discount when a sales order or quote line is added;
2. enforce two order rules on the server: back-office orders need delivery details and a delivery date before confirmation, and credit customers default to credit and cannot pay cash;
3. remove the dead `PaymentOnDelivery` model.

## Findings that shape this spec

### The current system (`mictlanix/mbe`)

- **Customer discount.** `SalesOrdersController.AddItem` (`:814-816`, `:871`) and `QuotationsController.AddItem` (`:674-676`, `:706`) look up `CustomerDiscount` by customer and product when a line is added, and set the line's discount rate to it, or 0 if there is none. That is the only place it is applied:
  - changing the customer (`SetCustomer`) leaves existing lines alone, and so does changing quantity or price;
  - a user can still edit the discount afterwards;
  - converting a quote (`CreateFromSalesQuote`, `:260`) and duplicating one (`:257`) copy each line's discount rather than looking it up again;
  - the POS inherits the sales-order behaviour.
- **Credit terms.**
  - A customer "has credit" when both credit days and credit limit are above 0 (`Customer.HasCredit`).
  - A new sales order, and a change of customer on one, set the terms to credit for such a customer and to cash otherwise (`:173`, `:403`).
  - Quotes always default to cash: the credit default is commented out in the quote's `SetCustomer` (`:327-329`).
  - **Cash is never rejected for a credit customer.** Credit is only a default, and the user can switch terms.
  - A customer with overdue credit, or over their limit, cannot change terms at all (`SetTerms`, `:717-751`). Confirmation refuses only overdue credit (`:1252`).
- **Delivery at confirmation.** Confirming a sales order checks nothing about delivery. The promise date is filled automatically: order date + `MaxDaysToDeliverStockables` on a new order (`:170`), and the order date on one converted from a quote (`:232`). Its editor is commented out. The checks for an address and a contact belong to the delivery order (`DeliveryOrdersController`, `:569-582`).
- **PaymentOnDelivery.**
  - The entity was removed from the current system in 2025 (`35bbcb7`).
  - mictlanix/mbe#84 (merged 2026-10-21) drops the table in `Schema/changes/mbe-26.10.sql`; production holds no rows.
  - mbe_dev still has the table, with 1 row from 2017. It is dropped when that schema change is applied to mbe_dev.

### This API today

- **Line discount.** Adding a line takes `discount_rate` from the request, and the request defaults it to 0. So "not sent" cannot be told apart from "sent as 0". No code reads `customer_discount` outside its own endpoints (spec 021 FR-013).
- **Credit default.**
  - A new sales order already defaults to credit (NET_D) when the customer has a credit limit above 0 and is not the walk-in customer (#207). The terms the order ends up with go through the existing credit checks.
  - A new quote always defaults to IMMEDIATE.
  - Changing the customer on an order or quote does not re-derive the terms.
- **Credit holds** stay as they are (#207, #219, #220):
  - credit is refused for the walk-in customer, for a customer with no credit limit, for a customer with overdue credit orders, and for a customer over their limit;
  - confirmation refuses any order whose customer has overdue credit, whatever its terms.
- **Delivery.**
  - `promise_date` is a required column and is always filled: the server defaults it to the order date + 7 days.
  - `fulfillment_intent` (pickup, delivery or mixed) and `ship_to` are optional, and confirmation checks neither.
  - `origin` records the screen that created the order: 0 is the POS register and 1 is the back-office sales orders screen (spec 017).

### mbe-ui (`origin/main` at `2ab245e`)

- **Origin and fulfilment intent.** The back-office sales orders screen creates every order with `origin = 1` and `fulfillment_intent = delivery`. The POS sends `origin = 0` and sets the intent from its mode selector. Quotes send no origin.
- **Delivery address and date.** No screen sets the order's `ship_to`. Delivery addresses are entered on the **delivery order**, which the UI creates *after* it confirms the sale (`delivery_controller.dart:117-148`). The promise date is an optional date picker in the order header.
- **Payment terms.** On create the UI sends no terms and lets the server derive them. When a customer is attached to an open sale, the UI sets credit for a customer with a credit limit and cash otherwise. The user can also change the terms by hand. Nothing blocks cash for a credit customer.
- **Line discount.** Lines are added without a discount (`capture_step.dart:207-213`). The discount can be edited afterwards.
- **TODO.md items behind #35:**
  - "Cuando un cliente tenga credito, que por default la forma de pago sea credito y restringir pago de contado" (2026-07-28): when a customer has credit, default the payment terms to credit and restrict cash payment;
  - under "Pedidos" (orders, 2026-08-15): "Quitar pago y agregar entrega obligatorio" (remove payment and make delivery mandatory) and "Fecha de entrega" (delivery date).

### Measured in mbe_dev (2026-10-22; orders through 2026-10-21)

**Customer discounts**: `customer_discount` has **0 rows**. Applying overrides changes no line until someone enters one.

**Credit customers.** "Active" means status active, credit limit above 0, and not the walk-in customer.

| Measure | Count |
|---|---|
| Active credit customers | **1,835** |
| … of which have credit days above 0 too (the current system's definition) | 1,695 |
| … of which bought (non-cancelled order) in the last 12 months | 380 |
| … of which had at least one **cash** (IMMEDIATE) order in those 12 months | **234** |
| … of which are on credit hold now (overdue, unpaid credit orders) | 154 |
| Orders by credit customers in the last 12 months: cash / credit | **2,405** / 5,409 (31% cash) |
| … cash orders whose customer is on credit hold now (these customers are already refused at confirmation whatever the terms, #219, so the cash rule does not change this) | 832 |
| … cash orders converted from a quote | 919 |
| Quotes by credit customers in the last 12 months: cash / credit | 1,970 / 982 |
| Open (draft) orders by credit customers: cash / credit | **37** / 20 (of 290 open orders) |
| Open quotes by credit customers: cash / credit | **86** / 19 |

**Delivery rule**: open (draft) orders by origin, with the fields that are empty.

| Origin | Open drafts | No `ship_to` | No `fulfillment_intent` | No `promise_date` |
|---|---|---|---|---|
| Not recorded (current system, API clients that send none) | 287 | 266 | 287 | 0 |
| 0 POS | 2 | 2 | 2 | 0 |
| 1 back office | **1** | **1** | 0 | 0 |

Every one of the 5 back-office orders ever created (the 1 open draft and 4 confirmed) has no `ship_to`. In the last 12 months, 5,515 of 35,221 orders have a promise date equal to the order date; every one of the 35,221 has a promise date.

## Clarifications

### Session 2026-10-22

- Q: What counts as "delivery details and a delivery date" when a back-office order is confirmed? → A: `fulfillment_intent` is recorded; `ship_to` is set when the intent is delivery or mixed; and `promise_date` is not earlier than today. mbe-ui does not yet set the order's `ship_to`, so back-office delivery orders are refused at confirmation until it does (FR-008, FR-018).
- Q: When may a credit customer use cash terms? → A: While they are under their credit limit. Credit stays the default. Once their outstanding credit debt reaches or passes the limit, cash is refused as well, so a customer cannot keep buying in cash while leaving their debt unpaid. Overdue credit already blocks confirmation whatever the terms (#219) (FR-012).
- Q: Which documents do the credit rules cover? → A: All sales orders, including POS counter sales and orders with no recorded origin, and all quotes (FR-013).
- Q: Should a quote for a credit customer on hold be refused, as an order is (#207)? → A: No. Quotes default to credit for credit customers and are never refused on credit grounds, neither for the hold nor for the cash rule. The credit and cash checks apply when the quote is converted to an order (FR-011, FR-012).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A customer's agreed discount appears on the line by itself (Priority: P1)

A salesperson adds a product to a sales order, a POS sale or a quote for a customer who has an agreed discount on that product. The line starts with that discount. The salesperson can still change it. When a quote is converted into an order, the order keeps each line's discount exactly as it was on the quote.

**Why this priority**: It is the one legacy action left in this group (mictlanix/mbe-roadmap#149). Without it, a seller has to remember and type every customer's discount by hand, and a forgotten discount means the customer is overcharged.

**Independent Test**:
- Add a 10% discount for a customer on product P.
- Add P to an order for that customer: the line's discount is 10%.
- Add P to an order for another customer: 0%.
- Add P with a discount sent explicitly as 5%: the line keeps 5%.
- Convert a quote whose P line was edited to 7%: the order line is 7%.

**Acceptance Scenarios**:

1. **Given** a customer with a discount override on a product, **When** that product is added to a sales order or quote for the customer without a discount in the request, **Then** the line's discount rate is the override.
2. **Given** no override for the customer and product, **When** the line is added without a discount, **Then** the line's discount rate is 0, as today.
3. **Given** an override, **When** the line is added with an explicit discount (including 0), **Then** the explicit value is kept.
4. **Given** a line created with the override, **When** the user changes its discount, **Then** the change is kept, and the override is not re-applied on later edits to quantity or price.
5. **Given** an order or quote with lines, **When** its customer changes, **Then** the existing lines keep their discounts. Only lines added afterwards use the new customer's overrides.
6. **Given** a quote, **When** it is converted to an order or duplicated, **Then** each line's discount is copied from the quote line, not looked up again.

---

### User Story 2 - Credit customers buy on credit by default, and cannot dodge their debt in cash (Priority: P1)

When a sales order or quote is opened for a customer with a credit line, it starts on credit terms. The customer may still choose to pay cash while they are under their credit limit. Once their credit debt reaches the limit, the server refuses cash as well as credit, so they must pay down what they owe before buying again. Customers without a credit line keep buying in cash as today, and the existing credit holds keep applying.

**Why this priority**: This rule comes from the pilot's own TODO list ("restringir pago de contado"). A third of credit customers' orders are cash today, so the rule restricts cash only where it lets a customer avoid paying their debt.

**Independent Test**:
- For a credit customer under their limit:
  - create an order and a quote without terms: both are on credit;
  - set an order to cash: accepted.
- For a credit customer whose debt is at or over their limit:
  - set an order to cash: refused;
  - confirm an open cash draft: refused.
- For a customer without a credit line: cash works.

**Acceptance Scenarios**:

1. **Given** a credit customer, **When** a sales order or quote is created without payment terms, **Then** it gets credit terms. For an order, the existing credit checks then apply. A quote is never refused, even for a customer on hold (FR-019).
2. **Given** a credit customer whose credit debt is at or over their limit, **When** an order's create or update asks for cash terms, **Then** it is refused with a message saying the customer must settle their credit first, and nothing changes. Under the limit, cash is accepted.
3. **Given** an open cash order or quote, **When** its customer changes to a credit customer and the request does not name terms, **Then** the terms become credit. For an order, the existing credit checks then apply. A quote is not checked (FR-019).
4. **Given** an open cash draft for a credit customer at or over their limit, **When** it is confirmed (order) or converted (quote), **Then** it is refused under the same rule. Of the 37 such orders and 86 such quotes in mbe_dev, only those whose customer is at or over the limit are affected (SC-005).
5. **Given** a customer without a credit line, or the walk-in customer, **When** an order or quote is created, **Then** it is on cash terms, as today, and credit is still refused.
6. **Given** any customer, **Then** the existing credit holds (overdue credit, over the limit, no limit, walk-in) behave exactly as before.

---

### User Story 3 - Back-office orders are confirmed with delivery details and a date (Priority: P2)

An order captured on the back-office sales orders screen cannot be confirmed until it says how it will be fulfilled and when. POS counter sales are confirmed as today.

**Why this priority**: These orders are delivered rather than picked up, and dispatch cannot plan without the details and the date. Today only 1 open order would be affected, because the screen is new, so the rule costs little now and prevents bad data from accumulating.

**Independent Test**: Create a back-office order (origin 1) and add lines. Confirming is refused, and the response names what is missing, in each of these cases:
- no intent;
- intent delivery with no `ship_to`;
- a `promise_date` before today.

With intent delivery, a `ship_to` and a date of today or later, it confirms. With intent pickup and no `ship_to`, it also confirms. A POS order (origin 0) and an order with no origin confirm as today.

**Acceptance Scenarios**:

1. **Given** a back-office order missing a required delivery detail or the delivery date (FR-008), **When** it is confirmed, **Then** it is refused, nothing is reserved or numbered, and the response lists the missing fields.
2. **Given** a back-office order with everything FR-008 requires, **When** it is confirmed, **Then** confirmation proceeds with today's other checks: lines, prices, stock, credit.
3. **Given** a POS order (origin 0) or an order with no recorded origin, **When** it is confirmed, **Then** no delivery requirement applies.

---

### User Story 4 - The dead PaymentOnDelivery model is gone (Priority: P3)

The API no longer maps a table that the current system has dropped, and the documentation no longer describes it.

**Why this priority**: It has no behaviour; the API should simply not depend on a table that is going away (mictlanix/mbe-api#248).

**Independent Test**: The model is gone and no document mentions it, and the full test suite passes, including the data-dictionary and schema-dump checks.

**Acceptance Scenarios**:

1. **Given** the codebase, **When** `app/`, the data dictionary and the schema dump are searched for the model or the table, **Then** nothing refers to it. Spec 019's receipt-label row is kept with a note saying it is not the table (FR-015).
2. **Given** mbe_dev before or after the current system's schema change drops the table, **Then** the API starts and behaves the same.

---

### Edge Cases

- **Override changes later:** a customer's override changes after a line was added. The line keeps the rate it was given; only new lines use the new rate.
- **Product added twice:** each line is created separately and each gets the override when added.
- **Walk-in customer:** the walk-in customer can have overrides like any customer. They apply to lines, while the credit rule never applies to it.
- **Moving onto a held customer:** moving an open cash order onto a credit customer on hold is refused at that update, with the hold message (#207), as creating one is. Before, the move succeeded and the order could not be confirmed (#219). A quote moved the same way is not refused (FR-019).
- **Credit days of 0:** a customer with a credit limit above 0 but 0 credit days (140 active customers) counts as a credit customer, as it does in this API today. The current system would not count it (see Assumptions).
- **Both fields in one request:** a request that changes the customer and sets cash terms is judged on the state the order ends in, like the existing credit checks (#219).
- **Converting a quote:** a quote is never refused on credit grounds while it is a quote. Converting it judges the resulting order's terms with every credit check and FR-012, the same as confirming an order.
- **Pickup back-office order:** a back-office order with intent pickup needs no `ship_to`. It still needs a promise date of today or later.
- **Stale default date:** an order whose defaulted promise date has passed is refused until the date is moved to today or later.
- **Origin set later:** origin cannot be changed after create, so an order's delivery requirement is fixed when it is created.

## Requirements *(mandatory)*

### Functional Requirements

**Customer discounts (#149)**

- **FR-001**: When a sales order line or a sales quote line is added without a discount rate in the request, its discount rate MUST be the customer's override for that product, or 0 if there is none. The customer is the document's customer at the moment the line is added.
- **FR-002**: An explicit discount rate in the request, including 0, MUST be kept as sent.
- **FR-003**: The override MUST NOT be re-applied when a line's quantity, price or warehouse changes, or when the document's customer changes.
- **FR-004**: Converting a quote to an order, and duplicating a quote, MUST copy each line's discount rate from the quote line, unchanged.
- **FR-005**: FR-001 to FR-003 apply to every sales order, whatever its origin (POS or back office).

**Delivery details at confirmation (#35)**

- **FR-006**: Confirming a sales order whose origin is back office (1) MUST be refused while the delivery details or the delivery date required by FR-008 are missing.
- **FR-007**: The refusal MUST name each missing item, and nothing may change: no folio, no stock reservation, no status change.
- **FR-008**: The required items are:
  - **Fulfilment intent:** `fulfillment_intent` is recorded (pickup, delivery or mixed);
  - **Delivery address:** `ship_to` is set when the intent is delivery or mixed (a pickup needs no address);
  - **Delivery date:** `promise_date` is not earlier than today's date. The server's 7-day default passes until it goes stale.
- **FR-009**: Orders with origin 0 (POS) or no recorded origin MUST be confirmed exactly as today.

**Credit terms (#35)**

- **FR-010**: A credit customer is a customer with a credit limit above 0 who is not the walk-in customer, the definition this API already uses for the order credit default. Status is not part of it.
- **FR-011**: A new sales order and a new sales quote for a credit customer MUST default to credit terms when the request names none. Changing an open order's or quote's customer to a credit customer MUST set credit terms the same way when the request names no terms. The order's resulting terms go through the existing credit checks (unchanged). A quote's do not: a quote is never refused on credit grounds (FR-019).
- **FR-012**: A sales order or quote for a credit customer MAY be on cash terms only while the customer's outstanding credit debt is below their credit limit. "Outstanding credit debt" means what the existing limit check already counts (#220). At or over the limit, cash MUST be refused with a message saying the customer must settle their credit first. Credit is refused there already, so such a customer cannot buy until they pay down. The check runs:
  - on an order's create and update (terms or customer);
  - when an order is confirmed;
  - when a quote is converted, the only point at which it applies to a quote (FR-019).

  A cash order adds nothing to the debt, so the comparison is the debt alone, not the debt plus the order.
- **FR-013**: FR-011 and FR-012 apply to every sales order, whatever its origin: POS counter sales (0), back-office orders (1) and orders with no recorded origin. They also apply to every sales quote, as limited by FR-019.
- **FR-019**: Creating or updating a sales quote MUST NOT be refused on credit grounds. That covers the walk-in customer, no credit limit, overdue credit, being over the limit, and FR-012. A quote may record credit or cash terms for any customer. A credit customer's quote defaults to credit, and every check applies when the quote is converted to an order.
- **FR-014**: The existing credit holds MUST behave exactly as before: no credit for the walk-in customer or a customer with no limit, no credit or confirmation for a customer with overdue credit, and no credit beyond the limit.

**PaymentOnDelivery (#248)**

- **FR-015**: The `PaymentOnDelivery` model MUST be removed from the API, and its descriptions MUST be removed from the data dictionary and the schema dump. Spec 019's data model keeps its `PaymentOnDelivery | Contraentrega` row, with a note. That row is the sale-receipt label for payments collected without a cash session, which the receipt still prints, not the dropped table (research R7). This departs from the literal list in #248.
- **FR-016**: No migration in this API drops the table. The current system's schema change (mictlanix/mbe#84) owns the drop.

- **FR-018**: The delivery address check reads the order's own `ship_to`, not the delivery orders' addresses. Until mbe-ui sets the order's `ship_to`, a back-office order with intent delivery or mixed is refused at confirmation. This is accepted, and mictlanix/mbe-ui gets an issue for it.

**Documentation**

- **FR-017**: `docs/specs/02-sales.md` MUST describe the rules this feature enforces, in the Sales Quotes and Sales Orders sections.

### Key Entities

- **Customer discount** (`customer_discount`, spec 021): one rate (0–1) per customer and product. Read when a line is added; no change to its shape.
- **Sales order / quote line**: `discount_rate` now has a default that comes from the override.
- **Sales order**: `origin`, `fulfillment_intent`, `ship_to`, `promise_date`, `payment_terms`. No new fields.
- **Sales quote**: `payment_terms` gets a credit default.
- **Customer**: `credit_limit`, `credit_days`, `status`. These are read to decide whether the customer is a credit customer.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A line added for a customer with an override carries the override in 100% of cases where the request did not name a discount, and an explicit discount is never overwritten.
- **SC-002**: 0 back-office orders reach confirmation without the FR-008 items, and POS orders are unaffected. Tests cover a back-office order with each item missing in turn, and a POS order.
- **SC-003**: No new order or quote for a credit customer starts on cash terms unless the request asks for it. Cash is refused on every order whose credit customer's debt is at or over their limit, and on every conversion of such a quote (FR-013, FR-019). No quote is refused on credit grounds while it is a quote.
- **SC-004**: Every existing credit-hold test passes unchanged.
- **SC-005**: The mbe_dev counts in this spec are re-run after implementation, and the spec reports how many open drafts the rules now block, before any data change: cash drafts for credit customers at or over their limit (out of 37 cash orders and 86 cash quotes for credit customers), and back-office orders missing an FR-008 item (1 today).
- **SC-006**: A search of `app/`, `docs/data-dictionary.md` and `docs/mbe_schema.sql` finds no `PaymentOnDelivery` or `payment_on_delivery`, and the full test suite passes. The spec 019 receipt-label row stays, with its note (FR-015).

## Assumptions

- **Deployment database.** mbe_dev is the deployment database, a copy of live data through 2026-10-21.
- **Credit customer definition.** It stays this API's: a credit limit above 0, ignoring credit days. The current system also requires credit days above 0, and 140 active customers differ. Changing that would change which customers already get the credit default today, and nobody asked for it.
- **Override filter.** "The customer's override" is the `customer_discount` row for the document's customer and the line's product. Spec 021 allows at most one per pair, and it has no status to filter on.
- **Explicit discount.** A request field that is absent means "not sent"; a value of 0 is an explicit 0. Clients that already send the field keep their behaviour. mbe-ui sends it only when a user edited it.
- **Quote discounts.** Quotes get the same discount default as orders, as in the current system.
- **Quotes and delivery.** Quotes have no delivery requirement: they carry no fulfilment intent, and FR-006 is about confirming an order.
- **Open drafts.** Open drafts that break the new rules are not migrated or changed. They are refused when someone confirms or converts them, and the user fixes them then.
- **Discount on existing lines.** No backfill of discounts onto existing lines.

## Out of Scope

- UI changes. **Dependency (FR-018):** mbe-ui must set the order's `ship_to` (it enters addresses only on delivery orders today) before back-office delivery or mixed orders can be confirmed.
- The quote PDF and email (mictlanix/mbe-roadmap#30).
- Commissions (mictlanix/mbe-roadmap#32, #33).
- Concrete dispatch (mictlanix/mbe-roadmap#36).
- Dropping the `payment_on_delivery` table in mbe_dev. That is mictlanix/mbe#84's schema change.
- Changing the credit-hold rules or the credit customer definition.
- A possible mbe-ui bug spotted during research: the quote expiry date picker sends a field that quotes ignore (`quote_header_panel.dart:97-114`). It belongs to mbe-ui.
