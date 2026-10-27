# Feature Specification: Credit Note Balances and the Facility Rule

**Feature Branch**: `034-credit-notes-facility`

**Created**: 2026-10-27

**Status**: Draft

**Input**: Fixes for the sales pilot. They block mbe-ui's credit notes (mictlanix/mbe-roadmap#28).
- mictlanix/mbe-api#277: credit note balances on legacy data;
- mictlanix/mbe-api#278: open-only paging;
- mictlanix/mbe-roadmap#156: the facility rule on every customer payment and sales order path, left over from mictlanix/mbe-api#271 (PR #273).

Builds on spec 011 (refunds, credit notes, payments), spec 033 (a payment's unapplied amount counts change handed back) and #271 (payment reads scoped to the caller's facility).

## Findings that shape this spec

### The current system (`mictlanix/mbe`, `origin/casa-maestra-2026`)

- **A credit note has no amount of its own.** Confirming a refund whose total covers the order's balance writes two rows (`CustomerRefundsController.cs:343-378`):
  - a `customer_payment`: amount = the surplus, `PaymentType.CreditNote` (4), method NA, the cashier's cash session, the store, the next serial;
  - a `credit_note`: `Refunded = 0`, `CashSession` not set (NULL), linked to that payment.
- **Its balance is the backing payment's** (`CustomerPayment.cs:135-142`): `Balance = Amount − Σ(Amount + Change)` over the payment's allocations. Spec 033 calls this the payment's unapplied amount.
- **Spending a note is applying its backing payment** to an order (`PaymentsController.ApplyCreditNote(s)`, `:1490-1620`). The note row never changes.
- **The spendable pool** (`ModelHelpers.GetCreditNotes`, `:125-134`):
  - the customer's notes with `cash_session IS NULL`;
  - nothing for the default (walk-in) customer;
  - no balance filter and no store filter.

  `CreditNote.IsRefundedToCustomer` (`CreditNote.cs:53`) is `CashSession != null`: a note paid back in cash.
- **Nothing in the current code sets `credit_note.cash_session` or `refunded`.** Only notes 158 and 462 carry them, from an earlier path. On both, `refunded` equals exactly what the payment still held when it was paid back (1,025.00; 3,510.00 − 1,965.00 applied = 1,545.00).
- **The verification screen** (`PaymentsController.PaymentsValidation`, `:234-268`, PaymentsVerification 108):
  - lists payments of every store;
  - filters only by the payment methods that require verification;
  - has no store filter.
- **Sales orders:**
  - the default list shows the user's own orders, in any store;
  - a numeric search finds any store's order;
  - a text search is limited to the user's store, unless an administrator searches with the wildcard;
  - no privilege grants seeing every store: `SearchAllSalesOrderFromAllStores` (105) is commented out (`SystemObjects.cs:242-245`).
- **Customer payments:** the editor's list (PaymentsEditor 100) has no store filter.

### This API today

- **`GET /credit-notes` and `/{id}`** compute `remaining = credit_note.refunded − Σ live applications.amount`, floored at 0 (`credit_note_service.remaining_balance`). Legacy notes have `refunded = 0`, so 982 of 984 report 0 issued and 0 remaining.
  - Note 990: payment 300743, 27.00 with 24.00 applied, has 3.00 left. The API says 0.
- **`open_only=true` filters after offset/limit, and `total` ignores it** (`credit_note_service.py:97-106`). `limit=100` returns 0 items with `total` 984, while note 158 reports 1,025.00 open. No other list endpoint filters after paging: the survey checked every paged list.
- **The API's refund (spec 011) writes a note unlike the current system's** (`_pay_customer_back`): `refunded` = the refund total, and `cash_session` = the session. Read by the current system's rule, every note the API issues looks paid back in cash, and its cashiers can't spend it. mbe_dev holds no API-issued note today.
- **Credit notes have no facility check.** The routes require CreditPayments (83) read. A note has no facility column; its backing payment has one.
- **Customer payments** (#271): the list, the detail, the ticket and the applications list answer 404 for another facility's payment, unless the caller is an administrator or holds PaymentsEditor (100) read. These don't:
  - `POST /{id}/applications`, `POST …/reverse`, `POST /{id}/verify` and `POST /{id}/reject` fetch the payment without a facility check;
  - `GET /unverified?facility=` takes any facility, without a check.
- **Sales orders:**
  - `GET /sales-orders?facility=` takes any facility, and defaults to the caller's;
  - no single-order route checks facility (`GET`, `PUT`, `confirm`, `cancel`, lines, ticket, document, payments);
  - no "every facility" privilege is used for sales orders. SearchCreditsFromAllStores (101) scopes accounts receivable (spec 033).

### Measured on mbe_dev (2026-10-27, read-only)

- **Credit notes:**
  - 984 notes, every one backed by a type-4 payment with a cash session; 11.27M issued.
  - By the backing payment's unapplied amount: **330 open, 1,778,475.54**.
  - Paid back in cash (`cash_session` set): 2 notes, 2,570.00, both open by that figure.
  - Walk-in customer: 512 notes; 191 open, 48,147.45.
  - **Spendable pool** (open, not paid back, not walk-in): **137 notes, 1,727,758.09**.

    | Facility | Notes | Spendable | Amount |
    |---|---|---|---|
    | 8 | 410 | 31 | 35,945.66 |
    | 47 | 64 | 13 | 263,232.80 |
    | 50 | 264 | 33 | 113,861.00 |
    | 51 | 103 | 10 | 187,164.10 |
    | 52 | 143 | 50 | 1,127,554.53 |
- **Notes spent at another facility:** 5 of 647 credit-note applications in 2026, 2 of 241 in 2025.
- **Payments applied by someone at another facility:** of 28,270 live applications in 2026, 742 were applied by someone other than the payment's creator. In 613 of those, the person applying took payments only at another facility that day. That's about 2% of applications, all by non-editors, that the facility rule would refuse.
- **Verification:** no payment dated 2026 has a verifier. The screen is effectively unused.
- **Privileges among the 36 non-administrators:**

  | Privilege (read) | Holders | Of whom without the cross-facility privilege |
  |---|---|---|
  | CustomerPayments (8) | 22 | 15 without PaymentsEditor |
  | PaymentsVerification (108) | 3 | 1 without PaymentsEditor |
  | SalesOrders (7) | 26 | 20 without SearchCreditsFromAllStores (101) |

  PaymentsEditor (100) has 7 holders; 101 has 6.

## Clarifications

### Session 2026-10-27

- Q: Which facility rule do verify and reject follow? → A: Company-wide for holders of PaymentsVerification (108), as the current system's verification screen lists every store (FR-010, FR-012).
- Q: Which privilege lets a clerk see other facilities' sales orders? → A: SearchCreditsFromAllStores (101), which already scopes accounts receivable's orders (FR-011).
- Q: Do single sales-order routes get the facility rule? → A: Writes only. `PUT`, lines, `confirm` and `cancel` answer 404 for another facility's order without 101; reads (`GET`, ticket, document, payments) stay open by id, as in the current system (FR-011).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A credit note shows what it can still pay (Priority: P1)

A cashier looks up a customer's credit notes at checkout. Each note shows the amount issued and what is left to spend, the same figures the current system and the payment's ticket show. Notes paid back in cash, and the walk-in customer's notes, are not offered for spending.

**Why this priority**: mbe-ui's credit notes (roadmap#28) are blocked on it. Today 982 of 984 notes report 0.

**Independent Test**: For every note in mbe_dev, compare its reported amount and remaining with its backing payment's amount and unapplied amount. Expect 0 mismatches, with notes paid back in cash at 0.

**Acceptance Scenarios**:

1. **Given** note 990, backed by payment 300743 (27.00, 24.00 applied), **When** it is read, **Then** it reports 27.00 issued and 3.00 remaining.
2. **Given** a note whose backing payment handed back change on an application, **When** it is read, **Then** the change counts as used, as on the payment.
3. **Given** note 158, paid back in cash, **When** it is read, **Then** it reports 0 remaining and that it was refunded in cash. It is never listed as open.
4. **Given** a walk-in customer's note with something left, **When** open notes are listed, **Then** it is not among them.
5. **Given** a refund confirmed through this API with a credit-note payout, **When** the note is read here or by the current system, **Then** it reads like a note the current system issued: spendable, with its full amount remaining.

---

### User Story 2 - Open notes page correctly (Priority: P1)

A cashier lists a customer's open notes, or a supervisor lists every open note, and pages through them. Every page is full until the last, and the total counts only open notes.

**Why this priority**: today the first page of 100 comes back empty with a total of 984.

**Independent Test**: List open notes with `limit=100`; expect 100 items (or all of them, if fewer) and a total equal to the open count measured in SQL. Page through; no note is repeated or skipped.

**Acceptance Scenarios**:

1. **Given** 137 spendable notes, **When** open notes are listed with `limit=100`, **Then** 100 come back, the total is 137, and the next page has 37.
2. **Given** a customer filter, **When** open notes are listed, **Then** the total counts only that customer's open notes.

---

### User Story 3 - Payment and order writes respect the caller's facility (Priority: P1)

A clerk can apply or reverse only payments of their own facility, and edit, confirm or cancel only orders of their own facility, unless they hold the cross-facility privilege. A record of another facility answers as if it didn't exist, as reading a payment already does since #271. Verifiers keep working company-wide, as the current system's verification screen does.

**Why this priority**: #271 closed the reads; the writes are still open by payment id.

**Independent Test**: As a clerk of facility A without the privileges, call each write on a payment and an order of facility B. Expect 404 and no change. With the privilege, expect the call to proceed.

**Acceptance Scenarios**:

1. **Given** a clerk without PaymentsEditor read, **When** they apply or reverse a payment of another facility, **Then** the answer is 404 and nothing changes.
2. **Given** an administrator or a holder of PaymentsEditor read, **When** they do the same, **Then** it proceeds under the existing rules.
3. **Given** a clerk applying a payment of their own facility to an order of another facility, **When** they apply it, **Then** it proceeds. The rule is about the payment's facility, not the order's (spec 033 FR-021).
4. **Given** a holder of PaymentsVerification update, **When** they verify or reject a payment of any facility, **Then** it proceeds, as in the current system.
5. **Given** a clerk without SearchCreditsFromAllStores read, **When** they edit, add or change lines on, confirm or cancel another facility's order, **Then** the answer is 404 and nothing changes. **And** reading that order, its ticket, document or payments still works.

---

### User Story 4 - Lists honour the caller's facility (Priority: P2)

A clerk's sales orders, unverified payments and credit notes come from their own facility. Naming another facility is refused unless they hold the cross-facility privilege. Another facility's credit note answers 404.

**Why this priority**: `GET /sales-orders?facility=` and `/unverified?facility=` accept any facility today (seen live on 2026-08-24).

**Independent Test**: As a clerk without the privilege, request each list with another facility, and read another facility's note. Expect 403 for the lists and 404 for the note. With the privilege, expect the other facility's rows.

**Acceptance Scenarios**:

1. **Given** a clerk without the cross-facility privilege, **When** they request `GET /sales-orders?facility=<other>`, **Then** the answer is 403, as `GET /customer-payments?facility=` already answers.
2. **Given** the same clerk, **When** they list credit notes, **Then** only notes whose backing payment is of their facility come back. Reading another facility's note answers 404.
3. **Given** a holder of PaymentsVerification read, **When** they list unverified payments, **Then** they see the facilities the current system's verification screen shows, as decided in FR-012.

### Edge Cases

- **A note whose backing payment is applied beyond its amount** (legacy rows): its remaining is floored at 0, never negative. None exist today.
- **A note paid back in cash with something applied first** (note 462): remaining is 0. `refunded` (1,545.00) is what was handed back.
- **A refund to the walk-in customer:** the note is issued and readable, but never offered for spending. The current system does the same.
- **An administrator** passes every facility check, as everywhere in the API.
- **A clerk with no facility** (an administrator without one): falls under the administrator rule. A non-administrator always has a facility in their token.
- **Payments on facility-less legacy rows:** customer payments always carry a facility; none is NULL in mbe_dev.

## Requirements *(mandatory)*

### Functional Requirements

#### Credit note balances

- **FR-001**: A credit note's amount issued MUST be its backing payment's amount.
- **FR-002**: A credit note's remaining MUST be its backing payment's unapplied amount (spec 033 FR-005a: amount less applied and change over live applications), floored at 0. A note paid back in cash (`cash_session` set) MUST report 0 remaining. This keeps FR-070's rule of no separately maintained balance; spec 011's FR-070 is corrected from "the amount issued less applications" to this.
- **FR-003**: A note paid back in cash MUST be reported as such (refunded to the customer, with the amount handed back).
- **FR-004**: A note is open when its remaining is above 0.01, it was not paid back in cash, and its customer is not the walk-in customer. This is the current system's spendable pool (`GetCreditNotes`), with a balance filter added.
- **FR-005**: A credit note issued by this API's refund MUST be written as the current system writes one: `refunded = 0` and `cash_session` NULL on the note, with the cash session on the backing payment. Notes from either system then read alike, and the current system can spend this API's notes while both run.
- **FR-006**: Each credit note MUST report its facility, which is its backing payment's facility.

#### Open-only listing

- **FR-007**: `GET /credit-notes?open_only=true` MUST filter on FR-004 in the query, before counting and paging. `total` MUST count the notes the filter keeps.
- **FR-008**: No other list endpoint may filter after paging. The survey found none; the plan records the check.

#### The facility rule

- **FR-009**: A record's facility, or a `facility` the caller names, MUST be the caller's own unless the caller is an administrator or holds the path's cross-facility privilege.
  - A record of another facility answers 404, so its existence isn't revealed.
  - A named facility of another facility answers 403, as `GET /customer-payments?facility=` does since #271.
- **FR-010**: Applying a payment (`POST /{id}/applications`) and reversing an application MUST apply FR-009 to the payment's facility, with PaymentsEditor (100) read as the cross-facility privilege.
  - The order's facility stays free, as spec 033 decided: a clerk may apply their facility's payment to another facility's order.
  - Verify and reject stay company-wide for holders of PaymentsVerification (108) update. The current system's verification screen lists every store, and these are its actions (clarified).
- **FR-011**: Sales orders MUST apply FR-009, with SearchCreditsFromAllStores (101) read as the cross-facility privilege (clarified). Neither system defines another one for sales orders: 105 is commented out in the current system.
  - `GET /sales-orders?facility=` naming another facility answers 403.
  - The writes on a single order answer 404 for another facility's order: `PUT /{id}`, adding, changing or removing lines, `confirm` and `cancel`.
  - The reads stay open by id (`GET /{id}`, ticket, document, payments), as in the current system, so lookups and cross-facility collections keep working (clarified).
- **FR-012**: `GET /customer-payments/unverified` MUST show what the current system's verification screen shows: payments of every facility, of the methods that require verification. A named `facility` narrows it to that facility for any holder of PaymentsVerification read.
- **FR-013**: `GET /credit-notes` and `GET /credit-notes/{id}` MUST apply FR-009 to the note's facility (FR-006), with PaymentsEditor (100) read as the cross-facility privilege. A note is a customer payment, and it follows the payments rule.
- **FR-014**: Administrators MUST keep their usual short-circuit on every check above.

### Key Entities

- **Credit note**: a refund's credit, a view over its backing customer payment. It has a source refund and order, a customer, an issue date, and optionally a cash session and an amount handed back when it was paid back in cash.
- **Backing payment**: a type-4 customer payment carrying the note's amount, facility and cash session; its applications spend the note.
- **Cross-facility privilege**: PaymentsEditor (100) for payments and credit notes; SearchCreditsFromAllStores (101) for sales orders; verification is company-wide for PaymentsVerification (108) holders.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For all 984 notes in mbe_dev, the reported amount and remaining equal the backing payment's amount and unapplied amount, with the 2 notes paid back in cash at 0. 0 mismatches.
- **SC-002**: Open notes on mbe_dev are 137, 1,727,758.09, as measured on 2026-10-27, give or take the day's activity. Paging through them with any page size returns each exactly once, with `total` equal to the count.
- **SC-003**: A credit note issued by this API's refund reads, in this API and by the current system's `GetCreditNotes` rule, as a spendable note with its full amount remaining.
- **SC-004**: As a clerk without the cross-facility privilege, every path named in FR-010, FR-011 and FR-013 refuses another facility's record or a named other facility: 0 leaks. As an administrator or a holder, the same calls proceed. Verify, reject and the single-order reads proceed for any facility, as clarified.
- **SC-005**: The verification list on mbe_dev shows the same payments as the current system's verification screen for the same filters.

## Assumptions

- **Volume affected by FR-010:** about 2% of 2026 applications (613 of 28,270) were made by someone working at another facility that day. Under the rule they need PaymentsEditor read or an administrator. Notes spent at another facility (5 in 2026) are among them.
- **Facility of a note:** its backing payment's facility, which is the refund's facility. The `credit_note` table has no facility column, and none is added.
- **`refunded`** keeps the current system's meaning, the amount paid back in cash. The response's issued amount comes from the backing payment (FR-001).
- **No data correction:** mbe_dev holds no note issued by this API, so FR-005 changes only future writes.
- **No new privilege objects.** The cross-facility privileges are existing ones.

## Out of Scope

- Paying a credit note back in cash. The current system's code no longer does it; the 2 legacy notes are read, not written.
- Redeeming a note through a new route: it stays an ordinary application of its backing payment (FR-070a).
- Facility rules on other modules (inventory, purchasing, fiscal documents).
- mbe-ui's credit-note screens (roadmap#28).
