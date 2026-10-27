# Feature Specification: Expense Vouchers and Supplier Payments

**Feature Branch**: `032-expense-supplier-payments`

**Created**: 2026-10-27

**Status**: Draft

**Input**: Implements two items of the purchasing epic (mictlanix/mbe-roadmap#7), within §5–6 of `docs/specs/05-purchases.md`, with the corrections below:
- mictlanix/mbe-roadmap#72, expense vouchers against the cash session;
- mictlanix/mbe-roadmap#74, supplier payments applied to purchase orders, with balances per order and per supplier.

It builds on spec 031's purchase orders and on the cash sessions and cash cut of the Sales specs. Supplier payments follow customer payments (spec 011) for structure, except where noted below. Purchasing moves from the current system to the new MBE in a single cutover; the two systems never record purchases or supplier payments at the same time.

## Findings that shape this spec

### The current system (`mictlanix/mbe`, `origin/casa-maestra-2026`)

- **No server-side rights.** Both controllers only require a login. ExpenseTicket (82) and SupplierPayment (21) only hide menus and buttons.
- **Expense vouchers:**
  - States are two flags, `completed` and `cancelled`. There is no serial: the printed number is the id.
  - A voucher is created only with an open session on the terminal's drawer. Its creator is set to the session's cashier, not the logged-in user.
  - A draft's lines (expense category, amount, note) are edited freely. An amount of 0 or less is silently ignored.
  - Since 2025-08, confirm requires an open session and a total above 0, and moves the voucher to the session open at that moment. Before that it did neither: 21 confirmed vouchers total $0, and 10 were confirmed after their session closed.
  - **Cancel works on drafts only.** A confirmed voucher can never be voided.
  - **Set cashier** overwrote the creator with any employee. Its button was removed in 2025-08.
  - The print is a ticket: the header, the cashier, the date, one row per line (category, note, amount), no total, and "Entregó" and "Recibió" signature lines.
- **Supplier payments:**
  - A payment is a standalone row: supplier, amount, SAT payment method, date, reference and comment. It has no facility, serial, cancel or cash session, and is not linked to any PO. Edit and delete do nothing.
  - **Accounts payable** computes, per supplier, payments + supplier returns − completed POs. It also counts the 31 POs that are both completed and cancelled.

### This API today

- **Models exist** for expense vouchers, their lines and the category catalog. The catalog already has its own routes (Expenses, 81). There are no voucher routes.
- **The cash cut** (`cut_figures`) subtracts every completed, non-cancelled voucher of the session as cash: in drawer = starting + cash sales − expenses − cash refunds.
- **Cash sessions are found by cashier.** Customer refunds require the caller's open session (409 without one). Customer payments attach it when there is one, whatever the method.
- **Customer payments**, the reference for supplier payments:
  - applications to orders are recorded per order, are never deleted, and are reversed one by one with a reason and an incidence;
  - the unapplied amount is computed, never stored;
  - the balance per order is exposed, but there is no balance per customer;
  - **not to copy:** new payments get serial 0, nothing is locked, an application is not checked against the order's balance, and a payment as a whole cannot be cancelled.
- **Bank accounts (spec 021)** are suppliers' accounts, the ones a supplier is paid into. The company has no bank accounts in MBE.
- **Purchase orders (spec 031)** have a total with tax added, no facility and no paid amount. A confirmed PO cannot be cancelled.
- **Numbering:** `assign_folio` numbers a document per facility under a lock on the facility row (spec 027).

### Measured on mbe_dev

The copy's last voucher is from 2026-09-29; voucher figures are for the 24 months before it.

- **Vouchers:** 9,555.
  - 9,445 confirmed, totalling $20.36M; 49 cancelled, of which 43 were empty drafts; 61 drafts left in sessions that have since closed.
  - 4.1 per session on average, at most 46. 88% have one line, 662 have more than one, and the largest has 12. The average line is $1,779 and the largest is $50,000.
  - 45 of the 49 categories are used. The catalog has no active flag, and "AGUA" appears twice.
  - Line notes are filled on 96% of lines and carry the description; the header comment is empty on 97%.
  - The voucher's facility always equals its drawer's.
  - Creator differs from the session's cashier on 49 vouchers, all before 2025-08. 44 are one employee who set herself as cashier inside another cashier's open session for 10 days.
  - The top categories are VARIOS (35% of the amount), ARENA ANDESITA, TEPOJAL, PROVEEDORES and REPOSICION DE CAJA: many vouchers pay suppliers in cash.
- **Supplier payments:** 5 ever, totalling $62,773. Two are in 2026: a $1,000 test, and $25,000 in cash "SE TOMAN DE CAJA DE CMZ", taken from the CMZ drawer by an employee with no open session.
- **Supplier returns:** none ever recorded.
- **Purchase orders:** confirmed POs total about $335M with tax, against $62.7k ever paid. 3,513 were confirmed in the last 24 months, to 120 suppliers. All are in MXN.
- **Rights held:** 82 by 15 users (9 full); 21 by 10 users; Accounts payable (19) by 13 users.

## Clarifications

### Session 2026-10-27

- Q: How do purchase orders confirmed before the cutover count in PO and supplier balances? → A: Only POs confirmed on or after a cutoff date set at go-live count. Older POs show no balance and accept no applications, so supplier balances start clean (FR-020).
- Q: Which cash session does a cash supplier payment come out of? → A: The caller's open session, as for vouchers. Whoever records a cash payment must have an open session, and the cash leaves their drawer and their cut (FR-012).
- Q: Who may edit, confirm and void an expense voucher? → A: Only the voucher's creator (its cashier) edits and confirms it, into their own open session. Any user with delete on 82 in the voucher's facility may void a confirmed voucher with a reason while its session is open (FR-002, FR-003, FR-004).
- Q: Is a PO's balance based on what was ordered or what was received? → A: What was ordered. The balance is the PO's total (spec 031, tax added) minus live applications, fixed once confirmed; receipts do not change it. Shortfalls are for accounts payable (#79) (FR-018).
- Q: How is the balance cutoff date set? → A: In a numbered database migration at go-live. There is no setting or route to change it; changing it means another migration (FR-020).
- Q: Which right do voiding a voucher, reversing an application and cancelling a payment require? → A: Delete: on 82 for voiding a voucher, on 21 for reversing an application or cancelling a payment. Cancelling one's own draft voucher needs only update on 82 (FR-004, FR-015, FR-016).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Record cash expenses during a session (Priority: P1)

A cashier pays a small expense out of the drawer: fuel, sand for a delivery, office supplies. They open a voucher, add one or more lines (category, amount, note) and confirm it. The voucher belongs to their open session and lowers the cash expected at the cut.

**Why this priority**: About 4 vouchers a day per session move $20M a year out of the drawers; without them the cash cut is wrong.

**Independent Test**: With an open session, create a voucher with two lines, confirm it, and check that the session's cut shows the expense and the expected cash drops by its total.

**Acceptance Scenarios**:

1. **Given** a cashier with an open session, **When** they create a voucher, **Then** it is a draft in their facility, and they can add, change and remove lines.
2. **Given** a draft with lines totalling more than 0, **When** it is confirmed, **Then** it belongs to the caller's session open at that moment, and that session's expected cash drops by its total.
3. **Given** a caller with no open session, **When** they create or confirm a voucher, **Then** it is refused.
4. **Given** a draft totalling 0 or with no lines, **When** it is confirmed, **Then** it is refused.
5. **Given** a draft created in a session that has since closed, **When** its cashier confirms it in their new session, **Then** it belongs to the new session.
7. **Given** another user's draft, **When** a user edits or confirms it, **Then** it is refused.
6. **Given** a line with an amount of 0 or less, **When** it is added or changed, **Then** it is refused with a message (not silently ignored).

---

### User Story 2 - Void a confirmed voucher while the session is open (Priority: P1)

A cashier confirmed a voucher by mistake. While the session is still open, a user with the right voids it with a reason. The expense leaves the cut. After the session closes, the voucher is read-only.

**Why this priority**: Today a wrong voucher cannot be corrected at all; the cut stays wrong.

**Independent Test**: Confirm a voucher, void it with a reason, check the cut no longer counts it and the reason is recorded; close the session and check another void is refused.

**Acceptance Scenarios**:

1. **Given** a confirmed voucher whose session is open, **When** it is voided with a reason, **Then** it is cancelled, the reason is recorded as an incidence, and the cut no longer subtracts it.
2. **Given** a confirmed voucher whose session is closed, **When** a void is attempted, **Then** it is refused, and the voucher cannot be changed in any way.
3. **Given** a draft, **When** it is cancelled, **Then** it is cancelled without a reason.
4. **Given** a void and the session's close happening at the same moment, **Then** either the void lands before the close and the close counts without it, or the void is refused; the closed cut never changes.

---

### User Story 3 - Print a voucher (Priority: P2)

The cashier prints the voucher as a ticket for the person who receives the cash to sign.

**Why this priority**: The signed ticket is the drawer's proof of the outflow.

**Independent Test**: Print a confirmed voucher and check the header, cashier, date, lines, total and signature lines.

**Acceptance Scenarios**:

1. **Given** a confirmed voucher, **When** it is printed, **Then** the ticket shows the facility header, the voucher number, the cashier, the date, each line's category, note and amount, the total, and "Entregó" and "Recibió" signature lines.
2. **Given** a draft or voided voucher, **When** it is printed, **Then** it is refused.

---

### User Story 4 - Pay a supplier and apply the payment to purchase orders (Priority: P1)

The purchasing or accounting staff records a payment to a supplier (amount, method, date, reference, note) and applies it to one or more of that supplier's confirmed POs, partially or in full. What is not applied stays as the supplier's credit, to apply later. A cash payment comes out of a drawer and lowers that session's expected cash like a voucher.

**Why this priority**: Without applications there is no balance per PO or per supplier, which accounts payable (#79) needs.

**Independent Test**: Record a $10,000 payment to a supplier with two confirmed POs of $6,000 and $8,000; apply $6,000 to the first and $3,000 to the second; check the POs' balances (0 and 5,000) and the payment's credit (1,000).

**Acceptance Scenarios**:

1. **Given** a supplier, **When** a payment is recorded, **Then** it gets the next serial of its facility, and its whole amount is unapplied credit.
2. **Given** a payment with credit and a confirmed PO of the same supplier, **When** an amount is applied, **Then** the PO's balance and the payment's credit both drop by it.
3. **Given** an application larger than the payment's credit or the PO's balance, **Then** it is refused.
4. **Given** a draft or cancelled PO, or another supplier's PO, **Then** an application to it is refused.
5. **Given** a cash payment, **When** it is recorded, **Then** it belongs to the caller's open session, and that session's cut shows it as a separate outflow. Without an open session, a cash payment is refused.
6. **Given** two applications of the same payment, or to the same PO, made at the same moment, **Then** neither the payment's credit nor the PO's balance goes below 0.

---

### User Story 5 - Correct payments: reverse an application, cancel a payment (Priority: P2)

An application went to the wrong PO: it is reversed with a reason and the amount returns to the payment's credit. A payment was recorded in error: it is cancelled with a reason, which reverses all its applications.

**Why this priority**: Corrections must leave a trail; payments are money.

**Independent Test**: Reverse one application and check both balances; cancel the payment and check every PO it touched is back to its previous balance.

**Acceptance Scenarios**:

1. **Given** a live application, **When** it is reversed with a reason, **Then** it is kept as reversed, the PO's balance and the payment's credit rise by its amount, and an incidence records the reason.
2. **Given** a payment, **When** it is cancelled with a reason, **Then** all its live applications are reversed, its credit is no longer available, and an incidence records the reason.
3. **Given** a cash payment whose session is closed, **When** a cancel is attempted, **Then** it is refused.
4. **Given** a cancelled payment, **Then** nothing can be applied from it, and it cannot be cancelled again.

---

### User Story 6 - Balances per purchase order and per supplier (Priority: P2)

Staff see, for a supplier, each open PO with its total, applied amount and balance, the payments with their unapplied credit, and the totals: owed, credit, and net.

**Why this priority**: This is the data accounts payable (#79) builds on.

**Independent Test**: For a supplier with three POs and two payments, check each PO's balance, each payment's credit, and the supplier totals.

**Acceptance Scenarios**:

1. **Given** a confirmed PO, **Then** its balance is its total minus its live applications.
2. **Given** a supplier, **Then** its summary lists the POs with a balance above 0, the payments with credit above 0, and the totals owed and credit.
3. **Given** a PO confirmed before the cutoff date, **Then** it shows no balance, is left out of the supplier's summary, and an application to it is refused.

### Edge Cases

- A voucher's draft from a closed session: only its creator can confirm it, into their current open session, or cancel it.
- A caller with two open sessions (one cashier has two today): the newest is used, as for customer refunds.
- A stale session (opened on an earlier day) counts as open, as it does for customer refunds: vouchers and cash payments go into it until it is closed.
- A PO whose receipts were cancelled or partly received: its total is still what was ordered; receipts do not change the balance.
- A payment applied in full to a PO and then reversed: the PO is open again.
- A payment amount of 0 or less, or an application of 0 or less: refused.
- A payment to a supplier with no confirmed POs: allowed; all of it is credit.
- Concurrent payments in one facility: serials stay distinct and consecutive.

## Requirements *(mandatory)*

### Functional Requirements

**Expense vouchers (ExpenseTicket, 82)**

- **FR-001**: Users with create on 82 MUST be able to create a voucher only while they have an open cash session. The voucher is a draft in that session's facility, with the caller as creator and cashier.
- **FR-002**: On a draft, only its creator (its cashier), holding update on 82, MUST be able to change the comment and add, change and remove lines. A line has an expense category from the catalog, an amount above 0 (two decimals), and an optional note of up to 500 characters. The note defaults to the category's comment.
- **FR-003**: Only the voucher's creator MAY confirm it. Confirming MUST require the caller's open cash session and a total above 0. The voucher then belongs to the caller's session open at that moment, and is read-only except for void.
- **FR-004**: A confirmed voucher MUST be voidable by any user with delete on 82 in its facility (not only its cashier), with a required reason, only while its session is open. The reason is recorded as an incidence of the voucher. A draft is cancelled by its creator, with update on 82, without a reason.
- **FR-005**: Once its session is closed, a voucher MUST NOT change. Voiding and confirming MUST be serialized with closing the session, so a closed session's figures never change.
- **FR-006**: The cash cut MUST subtract the session's confirmed, non-voided vouchers, as it does today.
- **FR-007**: "Set cashier" MUST NOT be built: the cashier is always the caller whose session the voucher is in.
- **FR-008**: Users with read on 82 MUST be able to list vouchers filtered by facility, cash session, date range and state, and see one with its lines and total. A non-administrator sees and acts on their own facility's vouchers only.
- **FR-009**: A confirmed voucher MUST print as a ticket (spec 019): facility header, voucher number (its id), cashier, date, lines with category, note and amount, the total, and two signature lines.

**Supplier payments (SupplierPayment, 21)**

- **FR-010**: Users with create on 21 MUST be able to record a payment: supplier, amount above 0, payment method (the SAT methods already used by customer payments), date (default now, not in the future; always now for a cash payment), reference (up to 50 characters) and note (up to 500).
- **FR-011**: A cash payment MUST come out of a cash session, as in FR-012. Other methods MUST NOT be tied to a session.
- **FR-012**: A cash payment MUST require the caller's open cash session and belongs to it; without one it is refused. The session's cut MUST subtract its cash payments as a separate figure, shown on the cut and its print. Voiding and cancelling are serialized with closing the session, as in FR-005.
- **FR-013**: A payment MUST get the next serial of its facility under spec 027's lock, distinct and consecutive under concurrency. Its facility is its session's for a cash payment, and the caller's otherwise.
- **FR-014**: Users with update on 21 MUST be able to apply a payment's credit to one or more confirmed, non-cancelled POs of the same supplier, at recording or later. Each application is above 0 and at most both the payment's credit and the PO's balance.
- **FR-015**: Applications MUST be kept, never deleted. Reversing one needs delete on 21 and a reason, recorded as an incidence of the payment; its amount returns to the payment's credit and the PO's balance.
- **FR-016**: Cancelling a payment MUST need delete on 21 and a reason. It reverses every live application, and is refused for a cash payment whose session is closed. A cancelled payment accepts nothing further.
- **FR-017**: Applications and cancels MUST NOT let a payment's credit or a PO's balance go below 0, even when made at the same moment.
- **FR-018**: A PO's balance MUST be its ordered total (spec 031, tax added) minus its live applications; confirming or cancelling receipts does not change it. A payment's credit is its amount minus its live applications, 0 once cancelled.
- **FR-019**: Users with read on 21 MUST be able to see a PO's balance and its applications; list payments filtered by supplier, facility, date range, method and state; and see a supplier's summary: the POs with a balance, the payments with credit, and the totals owed and credit. Which POs count is set by FR-020.
- **FR-020**: Only POs confirmed on or after the balance cutoff date MUST count: they have a balance and accept applications. The cutoff is one company-wide date, written by a numbered migration at go-live; no route sets or changes it. Older POs show no balance, are left out of supplier summaries, and refuse applications. Until that migration runs, no PO accepts applications, and payments are all credit.
- **FR-021**: A payment MUST NOT be edited. A wrong payment is cancelled and recorded again.
- **FR-022**: Supplier payments have no facility limit on reading or applying, as POs have none (spec 031). Recording and cancelling a cash payment follow its session.

### Key Entities

- **Expense voucher**: a cash outflow from a session's drawer. Facility, cash session, cashier, date, comment, state (draft, confirmed, voided). Numbered by id, as today.
- **Expense voucher line**: expense category, amount, note.
- **Expense category**: the existing catalog (Expenses, 81).
- **Supplier payment**: supplier, facility, serial, amount, method, date, reference, note, cash session when paid in cash, state (live, cancelled).
- **Payment application**: a payment, a PO, an amount, who applied it and when, and whether it was reversed.
- **Incidence**: the reasons for voids, reversals and cancels.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A session's expected cash equals starting cash plus cash sales, minus its confirmed vouchers, its cash supplier payments and its cash refunds, to the cent, for every session in a sample of 50.
- **SC-002**: No voucher or cash payment changes after its session closes: 20 voids or cancels racing a close leave the closed cut unchanged.
- **SC-003**: 20 applications made at the same moment against one payment and one PO never take either below 0.
- **SC-004**: 20 payments recorded at the same moment in one facility get 20 distinct, consecutive serials.
- **SC-005**: For any PO and payment, balance and credit equal total or amount minus live applications, after any sequence of applications, reversals and cancels.
- **SC-006**: A cashier records a one-line voucher, from opening it to confirming it, in under 30 seconds of API time per step.

## Assumptions

- **Vouchers keep their scope.** Many vouchers pay suppliers in cash, but they stay vouchers; turning them into supplier payments is a business decision outside this spec.
- **Voucher numbers stay the id**, global, as today. Vouchers have no serial column, and the user did not ask for one.
- **The 61 stranded drafts** remain drafts; their cashier can confirm them in a new session or cancel them.
- **Payments are in MXN.** All POs are MXN, and neither currency nor exchange rate is built.
- **No company bank accounts.** Spec 021's bank accounts are the supplier's; a payment's source is a cash session or nothing. Recording which company account paid is out of scope.
- **Supplier returns** are not modelled; none exist. Balances ignore them.
- **Incidences** for vouchers and supplier payments use two new incidence sources.
- **The cutoff date** is a single company-wide value, not per supplier or facility, held in the database and written by migration. A PO's confirmation time decides which side of it the PO falls.
- **Facility of a non-cash payment** is the caller's. An administrator without a facility names one.
- **The 5 legacy payments** keep their rows and get no serial. They are history, not credit: nothing can be applied from them.
- **Cutover.** Only the new MBE records vouchers and supplier payments after cutover.

## Out of Scope

- Accounts payable reports, statements, aging and credit limits (#79).
- Supplier returns.
- Company bank accounts and bank reconciliation.
- A printed supplier payment receipt.
- Turning cash vouchers into supplier payments.
- Cleaning up legacy data: zero-total vouchers, stranded drafts, the duplicate "AGUA" category.
- The UI (mbe-ui).
