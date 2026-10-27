# Feature Specification: Purchase Requests, Purchase Orders and Average Cost

**Feature Branch**: `031-purchase-requests-orders`

**Created**: 2026-10-26

**Status**: Draft

**Input**: Implements two items of the purchasing epic (mictlanix/mbe-roadmap#7), within §1–4 of `docs/specs/05-purchases.md`, with the corrections below:
- mictlanix/mbe-roadmap#68, purchase requests and their approval;
- mictlanix/mbe-roadmap#70, purchase orders, the receipts they generate, average cost, and the order's PDF and email.

It builds on spec 029: receipts, their PO links, the complementary receipt, and the shared locking. Purchasing moves from the current system to the new MBE in a single cutover; the two systems never record purchases at the same time.

## Findings that shape this spec

### The current system (`mictlanix/mbe`, `origin/casa-maestra-2026`)

- **No server-side rights.** Both controllers only require a login. The rights on PurchaseRequest (86), PurchaseRequestApproval (96), PurchasesOrders (20) and PurchaseOrderApproval (95) only hide buttons.
- **Requests:**
  - States are three flags: `completed` (submitted), `approved` and `cancelled`. Submit sets `completed`, and also `approved` when approval is not required.
  - The serial is assigned on creation as the store's highest + 1, with no lock. The re-assignment on submit has the operator-precedence bug `Max() ?? 0 + 1`, which returns the existing maximum.
  - Approve has no state check and does not record who approved or when. Reject writes an incidence and returns the request to draft, but its button is commented out.
  - The minimum order quantity is a hard error, not a warning.
  - `to_purchase` defaults to marked. It filters "to purchase by supplier" only when approval is required, and "add from request" ignores it.
  - Cancel works on submitted and approved requests, whatever their lines are on.
  - The "delivery" and "pending deliveries" views under purchase requests are dead copies of sales delivery screens.
- **Purchase orders:**
  - Lines, supplier and comment stay editable after confirm. Confirm has no state guard, so posting it again repeats the cost update and the receipts. Approve works on drafts and cancelled orders.
  - The supplier of a request line is the product's supplier (`product.supplier`); there is no other supplier link.
  - "To purchase by supplier" ignores the request's state, so draft and cancelled requests' lines appear. It fails when a product has no cost row.
  - Cancel deletes the PO lines that came from requests, which frees them and loses the history.
  - **Receipts on confirm** (`GenerateInventoryEntries`): one draft receipt per destination warehouse, quantity = ordered − what non-cancelled receipts hold. It runs outside the confirm transaction, with no duplicate guard, and includes non-stockable products that the receipt later rejects.
  - **Cost on confirm:** the cost list becomes the larger of the old cost and the raw line price. It ignores discount, tax, currency and exchange rate, and takes the highest price when a product is on several lines.
  - **No price list is recalculated** when cost changes, here or anywhere. The sales minimum and maximum prices are computed on the fly from cost and the cost row's profit rates. Every other list holds stored absolute prices.
  - **Printing** a confirmed PO renders a view that does not exist, so it fails. Nothing links to it, and there is no email.

### This API today

- Models for the four tables exist; there are no routes or services.
- **Receipts (spec 029)** already carry an optional PO and PO-line link. They show the ordered quantity from the PO line, warn on over-receipt, and create complementary receipts. Spec 029 left "receipts generated from a PO" to this epic.
- **Cost** is list 0 (`COST_PRICE_LIST_ID`). Every API write to it is refused (spec 004 FR-005a, "computed by the purchases module later"). The only reader is the cost snapshot on a sales-order line. Price-list profit margins are deprecated (#185), and nothing derives a stored price from cost.
- **Shared pieces exist:** PDF rendering (spec 019), the mailer and the "emailed" incidence (spec 024, sales quotes), supplier contacts with email (spec 021), and incidence sources 4 (purchase request) and 5 (purchase order).
- **Indexing:** `purchase_order_detail.purchase_request_detail` has no index and no foreign key.

### Measured on mbe_dev (2026-10-26, last 24 months unless stated)

- **Requests:** 2,235.
  - Approved, submitted, not cancelled: 2,123. Drafts: 63. Submitted awaiting approval: 18. Cancelled: 31.
  - Every month since 2024-11 has approvals (27 to 166 a month).
  - About 6 lines per request; the largest has 156. 78 requests have no lines.
  - **Serials, all time:** 6 duplicate pairs within a facility (facilities 6, 8 and 47), 18 serials of 0 and 41 empty.
  - **Lines:** `Accepted` is 0 on every line. The per-line customer is the default customer on 13,564 of 13,565 lines. 17 lines in 3 requests go to a warehouse of another facility than the header.
  - `to_purchase` is marked on 72% of lines. 1,612 unmarked lines were ordered anyway, and 24 lines of unapproved requests were ordered.
  - **Ordered more than once:** 109 request lines are on 2+ live PO lines.
- **Purchase orders:** 3,850.
  - Confirmed: 3,512. Drafts: 195. Cancelled: 143. 129 have no lines.
  - About 4.6 lines per PO: median 1, 99th percentile 48, maximum 212.
  - **Approval** was used from 2024-11 to 2025-03 (300 POs) and never since. 202 confirmed POs have no receipt; most are from 2024-11 to 2025-01.
  - **Unused fields:** currency, exchange rate and discount are the same on every line (MXN, 1, 0), and `invoice_number` is always empty.
  - **Tax:** `tax_included` is set on every line, copied from the product's flag, which is set on 21,735 of 21,749 products. The tax rate is 16% on all but 17 lines.
  - **Destinations:** 8 POs send lines to warehouses of more than one facility.
- **Receipts from POs:** 3,349. 25 PO+warehouse pairs have two live receipts:
  - 11 are genuine complementary receipts;
  - 13 duplicate the same lines, 6 of them with both receipts confirmed, so stock was posted twice;
  - 1 covers different lines but over-receives one.
- **Cost:**
  - 20,573 products have a cost above 0.
  - In 2026, 69% of PO lines carry exactly the product's current cost, and the last PO price equals current cost for 83% of products. So the cost list and PO prices are on the same basis.
  - Sale lists stand about 1.30× cost (mode).
- **Stock for averaging:** 611 of 3,359 products ordered in 2026 went to 2 to 4 facilities. Negative on hand is negligible at either level (3 product+facility pairs, 3 products company-wide).
- **Supplier email:** 43 of the 91 suppliers on 2026 POs have a contact with an email. They cover 985 of 1,456 POs.
- **Rights held:**
  - 86: 16 users with full rights;
  - 96: 4 users with full rights;
  - 20: 9 with full rights, 5 with create/read/update.

## Clarifications

### Session 2026-10-26

- Q: Are the cost list and PO prices as typed with or without IVA? → A: Both without IVA. PO prices are net, and averaging uses them as typed. The "tax included" flag on existing PO lines is a copy of the product's sales flag and is ignored; new PO lines store it off and tax is added on top (FR-021, FR-034, FR-040).
- Q: What does cancelling a confirmed PO-linked receipt do to cost? → A: It restores the cost from before the receipt when no later change touched that product's cost; otherwise cost stays and the cancel warns (FR-037).
- Q: How does "to purchase by supplier" group pending lines? → A: By supplier and destination facility (the facility of the line's warehouse): one draft PO per supplier per facility. Only 1 of 1,797 live request-fed POs in 24 months mixed facilities (FR-024, FR-025).
- Q: May a requester approve their own request? → A: Yes. Anyone holding PurchaseRequestApproval (96) update may approve or reject any submitted request, their own included (FR-005).
- Q: Which requests does the approval queue show? → A: Submitted requests from all facilities, filterable by facility; there are 4 approvers for 7 facilities (FR-007).
- Q: Do facility limits apply to requests and POs? → A: Requests follow spec 029's rule: a non-administrator edits, submits and cancels only requests whose header warehouse is in their facility. Approving, rejecting and marking lines are not limited by facility, and POs have no facility limit (FR-011, FR-029).
- Q: Can a rejected request's header warehouse move to another facility? → A: No. Once a request has a serial, its header warehouse may change only to another warehouse of the same facility (FR-002).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A store requests goods and a supervisor approves (Priority: P1)

Store staff open a purchase request, add the products they need with quantities and destination warehouses, and submit it. A supervisor approves it or rejects it with a reason, which sends it back to draft.

**Why this priority**: Requests are how stores ask for stock, and their approval gates what purchasing may order. About 95 are approved every month.

**Independent Test**: Create a request, add two lines and submit it. It gets its facility's next serial and is submitted. A supervisor approves it, and it shows the approver and the time. Another request rejected with a reason is a draft again, with the reason on record.

**Acceptance Scenarios**:

1. **Given** a draft request with lines, **When** it is submitted, **Then**:
   - it gets the next request serial of its warehouse's facility, unless it already has one;
   - it becomes submitted; its header and lines can no longer be edited, and only the to-purchase mark (FR-008) can change.
2. **Given** a draft with no lines, or a line with quantity 0, **When** it is submitted, **Then** it is refused.
3. **Given** a line below the product's minimum order quantity, **When** it is saved, **Then** it is accepted with a warning.
4. **Given** a submitted request, **When** a supervisor approves it, **Then** it is approved, with the approver and the time recorded.
5. **Given** a submitted request, **When** a supervisor rejects it with a reason, **Then**:
   - the reason is recorded as an incidence of the request;
   - the request returns to draft, keeps its serial, and can be edited and submitted again.
6. **Given** a draft or an approved request, **When** anyone approves or rejects it, **Then** it is refused.
7. **Given** a request with a line on a live PO, **When** anyone cancels it, **Then** it is refused.
8. **Given** a user without the matching right, **When** they act on requests or approvals, **Then** it is refused.

---

### User Story 2 - Purchasing turns approved requests into purchase orders (Priority: P1)

The purchasing agent reviews approved requests and marks the lines to buy. They then either let the system build one draft PO per supplier and destination facility from the pending marked lines, or add request lines to a PO they opened. Each request line can be on only one live PO.

**Why this priority**: 1,809 POs in two years came from requests, and 109 request lines were ordered twice.

**Independent Test**: Two approved requests have marked lines whose products come from suppliers A and B, plus one unmarked line; supplier A's lines go to warehouses in two facilities. "To purchase by supplier" lists A twice (once per facility) and B once, with their pending lines and estimated totals at cost. Creating all three gives three draft POs priced from the cost list. The unmarked line is on neither. Creating again adds nothing.

**Acceptance Scenarios**:

1. **Given** approved requests with marked lines not on a live PO, **When** "to purchase by supplier" is read, **Then**:
   - those lines are grouped by their product's supplier and destination facility, with line count and estimated total at cost;
   - lines of draft, submitted or cancelled requests, unmarked lines, and lines already on a live PO are excluded.
2. **Given** that list, **When** a PO is created for a supplier and facility, **Then** a draft PO for that supplier holds one line per pending line of that group, with:
   - the line's product, quantity and destination warehouse;
   - the price from the cost list, or 0 with a warning when the product has no cost.
3. **Given** two agents creating POs from the same request lines at the same moment, **When** both finish, **Then** each line is on exactly one live PO.
4. **Given** a draft PO, **When** the agent adds a specific approved request's lines, **Then** only its marked lines not on a live PO are added.
5. **Given** a PO with request lines, **When** the PO is cancelled, **Then**:
   - its lines stay on it as history;
   - the request lines become pending again and can be ordered on another PO.
6. **Given** a line already on a live PO, **When** the agent unmarks it, **Then** it is refused.

---

### User Story 3 - A buyer issues a purchase order and the stores receive it (Priority: P1)

A buyer prepares a PO for a supplier, by hand or from requests, and confirms it. Confirming locks the PO and creates one draft receipt for each destination warehouse, ready for the store to count and confirm.

**Why this priority**: The PO is the commitment to the supplier, and the generated receipts are how the goods enter stock. Today 202 confirmed POs have no receipt, and 6 POs posted stock twice.

**Independent Test**: Confirm a PO with lines to warehouses W1 (two products) and W2 (one product). Two draft receipts exist: W1 with two lines and W2 with one, linked to the PO and its lines, with the ordered quantity as both ordered and pending quantity. Confirming again is refused, and there are still two receipts.

**Acceptance Scenarios**:

1. **Given** a draft PO, **When** the buyer edits it, **Then** they can:
   - change the supplier, the estimated receipt date and the comment;
   - add, change and remove lines (product, destination warehouse, quantity, unit price, tax rate).
2. **Given** a confirmed or cancelled PO, **When** anyone edits its header or lines, **Then** it is refused.
3. **Given** a draft PO with lines, **When** it is confirmed, **Then**:
   - it becomes confirmed;
   - for each destination warehouse with stockable lines, one draft receipt is created in that warehouse's facility, linked to the PO;
   - each receipt has one line per stockable PO line, linked to it, with the ordered quantity and the quantity still pending.
4. **Given** two confirms of the same PO at the same moment, **When** both finish, **Then** one succeeds and one is refused, and each warehouse has exactly one generated receipt.
5. **Given** a confirmed PO, **When** anyone cancels it, **Then** it is refused. **Given** a draft PO, **When** it is cancelled, **Then** it is cancelled and its request lines are released.
6. **Given** a PO whose receipts were partly confirmed, **When** a store asks for a complementary receipt, **Then** spec 029's complementary receipt covers the remainder.

---

### User Story 4 - Receiving goods updates average cost (Priority: P1)

When a receipt linked to a PO is confirmed, each received product's cost becomes the weighted average of the stock on hand at its old cost and the goods received at the PO's unit cost. Every change is recorded against its receipt.

**Why this priority**: Cost feeds margins, the cost snapshot on every sales line, and inventory valuation. Today's rule only ever raises cost and ignores tax and discount.

**Independent Test**: A product has 10 on hand company-wide at cost 100. A receipt linked to a PO line whose unit cost (FR-034) is 120 receives 10. After the receipt is confirmed, cost is (10×100 + 10×120) / 20 = 110, and a cost change record links the receipt, the old and new cost, the on-hand and received quantities and the unit cost.

**Acceptance Scenarios**:

1. **Given** a product with on hand Q at cost C, **When** a PO-linked receipt line of quantity R at unit cost U is confirmed, **Then** cost becomes (Q×C + R×U) / (Q + R), with Q taken as 0 when it is negative.
2. **Given** a receipt without a PO, or a receipt line without a PO line, **When** it is confirmed, **Then** cost does not change.
3. **Given** two lines of the same product on one receipt, **When** it is confirmed, **Then** both are averaged in, as if received one after the other.
4. **Given** two receipts of the same product confirmed at the same moment in different facilities, **When** both finish, **Then** cost reflects both, and neither change is lost.
5. **Given** a confirmed PO-linked receipt, **When** it is cancelled, **Then** cost follows FR-037.

---

### User Story 5 - The buyer prints and emails the order to the supplier (Priority: P2)

The buyer downloads the confirmed PO as a PDF, or emails it to one of the supplier's contacts or to an address they type. Each send is recorded.

**Why this priority**: Suppliers need the order in writing. Printing is broken today and there is no email, but orders are still placed by phone and in other ways.

**Independent Test**: Print a confirmed PO and check the supplier, lines, destination warehouses and totals. Email it to a contact of the supplier: the message carries the PDF, and the PO's incidences show the send with its recipient.

**Acceptance Scenarios**:

1. **Given** a confirmed PO, **When** the PDF is requested, **Then** it shows:
   - the PO number and date, the issuing company, and the supplier;
   - per line: code, name, destination warehouse, quantity, unit price and amount;
   - subtotal, tax and total, plus the estimated receipt date and comment.
2. **Given** a confirmed PO, **When** it is emailed to an address, **Then** the PDF is attached, and an incidence of the PO records the recipients and the sender.
3. **Given** a draft or cancelled PO, **When** it is printed or emailed, **Then** it is refused.
4. **Given** mail is not configured, or the mail server rejects the message, **When** it is emailed, **Then** the user is told, and nothing is recorded as sent.

### Edge Cases

- **Concurrency.** Submitting, approving, confirming and cancelling are each applied once, including two at the same moment. Request serials are never repeated within a facility, and a failed submit consumes no number.
- **One live PO per request line.** This holds under concurrent "create by supplier", "add from request", PO line removal and PO cancel. A request line on a cancelled PO is pending again.
- **Resubmitting** a rejected request keeps its serial; its header warehouse cannot have moved to another facility (FR-002).
- **Cross-facility lines.** A request line or a PO line may go to a warehouse of another facility. The request's serial follows the header warehouse's facility.
- **Products.** Requests and POs accept only purchasable products. Non-stockable PO lines get no receipt line. A warehouse with only non-stockable lines gets no receipt.
- **Warehouses.** In-transit warehouses cannot be a request or PO destination.
- **No supplier.** Pending lines whose product has no supplier are listed apart under "to purchase by supplier". They cannot be ordered by supplier, only added to a PO by hand.
- **No cost.** A product with no cost row is priced at 0 with a warning when ordered from requests.
- **Zero price.** A PO line with unit price 0 may be confirmed, with a warning. Its received quantity does not change cost (FR-035).
- **Over-receipt.** Receiving more than ordered is a warning (spec 029). The whole received quantity is averaged in.
- **First cost row.** When two receipts of a product that has no cost yet are confirmed at the same moment, one may be asked to retry; the retry averages on top of the other (FR-033).
- **Empty PO.** A PO with no lines, or a line with quantity 0, cannot be confirmed.
- **Supplier mismatch.** Adding request lines whose product's supplier differs from the PO's supplier is allowed, with a warning.
- **Generated receipts** follow spec 029. They can be edited, confirmed or cancelled there; cancelling a draft receipt does not touch the PO. A later complementary receipt covers what is still pending.

## Requirements *(mandatory)*

### Functional Requirements

**Purchase requests (#68)**

- **FR-001**: Users MUST be able to create a draft request. It has:
  - a destination warehouse, defaulting to the caller's point-of-sale warehouse;
  - an optional comment of up to 500 characters;
  - its date, creator, updater and times.
- **FR-002**: While a request is a draft, users MUST be able to:
  - add, change and remove lines (product, product name up to 250 characters, quantity above 0 with up to 2 decimals, destination warehouse);
  - change the header warehouse and comment. Once the request has a serial, the header warehouse MUST stay within the same facility; a change to another facility's warehouse is refused.

  The product name defaults to the product's name and can be overwritten. The line warehouse defaults to the header's. A new line's quantity defaults to the product's minimum order quantity, or 1 when that minimum is 0.
- **FR-003**: Saving a line below the product's minimum order quantity MUST be accepted with a warning naming the minimum.
- **FR-004**: Submitting MUST:
  - be refused for a request that is not a draft, has no lines, or has a line with quantity 0;
  - give the request its facility's next request serial, safely under concurrent submits, unless it already has one. The facility is the header warehouse's facility, and a failed submit consumes no number;
  - make it submitted.
- **FR-005**: Approving MUST:
  - be refused unless the request is submitted;
  - be allowed to any holder of the approval right, including the request's creator;
  - make it approved, recording the approving employee and the time.

  Several requests MAY be approved in one call, each reported as approved or refused with its reason.
- **FR-006**: Rejecting MUST:
  - be refused unless the request is submitted, and require a reason of up to 500 characters;
  - record the reason as an incidence of the request;
  - return the request to draft, keeping its serial.
- **FR-007**: The approval queue MUST list submitted requests of every facility, oldest first, filterable by facility, with requester, facility, serial, date and line count, and the detail of each.
- **FR-008**: Each request line MUST carry a "to purchase" mark, set on new lines. The purchasing agent MUST be able to mark or unmark lines of submitted and approved requests. Unmarking a line on a live PO MUST be refused.
- **FR-009**: Cancelling MUST be possible from draft, submitted or approved, and MUST be refused once any of the request's lines is on a live PO. A cancelled request is read-only.
- **FR-010**: Each request line MUST show the live PO line it is on, if any, and that PO's state.
- **FR-011**: The rights:
  - **PurchaseRequest (86):** read to list and view, create to create, update to edit and submit, delete to cancel;
  - **PurchaseRequestApproval (96):** read for the queue, update to approve or reject;
  - **PurchasesOrders (20) update:** to mark lines to purchase;
  - **Facility:** a non-administrator MUST be refused (403) editing, submitting or cancelling a request whose header warehouse belongs to another facility, as in spec 029. Approving, rejecting and marking lines are not limited by facility.
- **FR-012**: Request lists MUST show, by default, the requests the caller created or updated, excluding cancelled. They MUST be filterable by state, warehouse, facility, creator, serial or id and date range, newest first, and readers MAY list all.
- **FR-013**: The per-line customer and the `Accepted` flag MUST NOT be offered. New lines store the default customer and `Accepted` = false.

**Purchase orders (#70)**

- **FR-020**: Users MUST be able to create a draft PO for a supplier, with an optional estimated receipt date (not in the past) and comment up to 500 characters. POs are numbered by their id, as today (Assumptions).
- **FR-021**: While a PO is a draft, users MUST be able to change its supplier, estimated receipt date and comment, and add, change and remove lines. Each line has:
  - a purchasable product, with its code and name snapshotted;
  - a destination warehouse that is not in transit, defaulting to the caller's point-of-sale warehouse;
  - a quantity above 0 with up to 4 decimals;
  - a unit price before tax, 0 or more with up to 7 decimals, defaulting to the product's cost (0 when it has none);
  - a tax rate, defaulting to the product's.

  Currency, exchange rate and discount MUST be stored as MXN, 1 and 0, "tax included" as off, and the invoice number left empty, with no way to edit them.
- **FR-022**: Every change to a PO's header or lines MUST be refused unless the PO is a draft.
- **FR-023**: Users MUST be able to add to a draft PO the pending lines of a given approved request. Pending lines are marked and not on a live PO. Each becomes a PO line linked to the request line, with its quantity and warehouse, priced from cost. A line whose product's supplier differs from the PO's is added with a warning.
- **FR-024**: "To purchase by supplier" MUST list the pending lines of approved, non-cancelled requests, grouped by their product's supplier and destination facility (the facility of the line's warehouse), with line count and estimated total at cost. The list MAY be filtered by facility. Lines whose product has no supplier MUST be listed in a separate group per facility.
- **FR-025**: Users MUST be able to create a draft PO for one supplier and facility from all pending lines of that group. A PO created this way never mixes facilities. Each line is priced from the cost list; a product with no cost gets 0 and a warning.
- **FR-026**: A request line MUST be on at most one PO line of a non-cancelled PO, safely under concurrent PO creation and line additions. A request line that loses that race is left out and reported, not doubled.
- **FR-027**: Confirming a PO MUST:
  - be refused unless it is a draft with a supplier, at least one line, and every quantity above 0;
  - warn about lines with unit price 0;
  - for each destination warehouse with stockable lines, create one draft receipt in that warehouse's facility, linked to the PO, with one line per stockable PO line linked to it. Each line holds the ordered quantity, and as quantity the quantity still pending: ordered minus what non-cancelled receipts already hold for that PO line. Lines with nothing pending are left out;
  - make the PO confirmed;
  - be all or nothing, and run once: a second or concurrent confirm is refused and creates no receipt.
- **FR-028**: Cancelling MUST be possible only for a draft PO. Its lines stay on it, and its request lines become pending again.
- **FR-029**: The rights on PurchasesOrders (20): read to list and view, print and to-purchase; create to create; update to edit and confirm, and to email; delete to cancel. PO approval (95) MUST NOT be built. POs are not limited by facility.
- **FR-030**: PO lists MUST show, by default, the POs the caller created or updated, excluding cancelled. They MUST be filterable by state, supplier, creator, id and date range, newest first, and readers MAY list all. Each PO shows its receiving progress per line: ordered, received on confirmed receipts, and pending.

**Average cost (#70)**

- **FR-031**: Confirming a receipt linked to a PO MUST update the cost of each product on a line linked to a PO line. The new cost is (Q×C + R×U) / (Q + R), where:
  - Q is the product's company-wide on hand before this receipt, across all warehouses including in transit, taken as 0 when negative;
  - C is the current cost, 0 when the product has none;
  - R is the line's received quantity;
  - U is the PO line's unit cost (FR-034).

  It MUST run in the same transaction as the receipt's confirm.
- **FR-032**: Receipts without a PO, and receipt lines without a PO line, MUST NOT change cost.
- **FR-033**: Several lines of one product on a receipt MUST be averaged in one after the other. Concurrent receipts of the same product MUST each be applied, in order, without losing either.
- **FR-034**: The unit cost U MUST be the PO line's price × (1 − discount). PO prices and cost are both before tax. The line's "tax included" flag MUST be ignored: on pre-cutover lines it is a copy of the product's sales flag, not a statement about the price.
- **FR-035**: A PO line with unit price 0 MUST NOT change cost, and the receipt confirm MUST report it.
- **FR-036**: Each cost change MUST be recorded with the product, the receipt, the PO line, the previous and new cost, Q, R, U and the time. A product's cost history MUST be listable.
- **FR-037**: Cancelling a confirmed PO-linked receipt (spec 029) MUST, for each product whose cost the receipt changed:
  - restore the cost from before the receipt when the receipt's change is still that product's latest cost change, and record the restore as a cost change;
  - otherwise leave cost as it is, and warn, naming the product and the later change.

  Sales and other movements do not change average cost, so restoring is exact in the first case.
- **FR-038**: Cost MUST be written only through FR-031 and FR-037. The cost list stays read-only to every other API write (spec 004 FR-005a). No stored sale price is recalculated when cost changes; sale lists stay as stored, as today (Assumptions).
- **FR-039**: Cost MUST be rounded half up to 4 decimals, the precision of the cost list and of the cost snapshot on sales lines. The cost history keeps the unit cost U at the PO line's 7 decimals.

**PDF and email (#70)**

- **FR-040**: Users MUST be able to download a confirmed PO as a PDF, through the document printing of spec 019. It shows:
  - the PO number and date, the company, the supplier and the issuing employee;
  - per line: code, name, destination warehouse, quantity, unit price and amount;
  - subtotal before tax, tax added at each line's rate, and total, plus the estimated receipt date and the comment.
- **FR-041**: Users MUST be able to email a confirmed PO's PDF, through the mailer of spec 024. The recipients are one or more of the supplier's contact emails (spec 021) or addresses the user types, with an optional message.
- **FR-042**: Each successful send MUST be recorded as an incidence of the PO, with recipients and sender. A send that fails MUST be reported to the user and not recorded as sent.
- **FR-043**: Printing or emailing a draft or cancelled PO MUST be refused.

### Key Entities

- **Purchase request**: a store's request for goods.
  - Header: warehouse (whose facility numbers it), serial, date, comment, creator, updater, times, and state (draft, submitted, approved, cancelled).
  - **New:** the approving employee and the approval time. This needs a migration.
  - Lines: product, name snapshot, quantity, destination warehouse, and the "to purchase" mark.
- **Purchase order**: an order to one supplier.
  - Header: id as its number, supplier, estimated receipt date, comment, creator, updater, times, and state (draft, confirmed, cancelled).
  - Lines: product, code and name snapshots, destination warehouse, quantity, unit price before tax, tax rate, and an optional request-line link. Currency, exchange rate, discount and "tax included" are fixed defaults.
  - The request-line link needs an index.
- **Generated receipt**: a spec 029 receipt linked to the PO, with lines linked to PO lines.
- **Cost change**: one averaging step.
  - Fields: product, receipt, PO line, previous cost, new cost, on hand Q, received R, unit cost U, time, and the employee.
  - It is a new table.
- **Cost**: the product's row on the cost list (list 0).
- **Incidence**: request rejections (source 4) and PO sends (source 5).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: No request line is on more than one live PO line, including after 20 concurrent attempts to order the same lines.
- **SC-002**: Request serials are unique within each facility after 20 concurrent submits, with no gaps from failed submits.
- **SC-003**: Every confirmed PO with stockable lines has exactly one generated receipt per destination warehouse, including after two simultaneous confirms.
- **SC-004**: After any PO-linked receipt is confirmed, each affected product's cost equals the formula in FR-031 rounded to 4 decimals (FR-039), and the cost history reproduces every change from its recorded inputs.
- **SC-005**: A 212-line PO (the largest measured) confirms, generating its receipts, in under 5 seconds on the test deployment. A 212-line receipt confirms with its cost updates in under 5 seconds.
- **SC-006**: A confirmed PO prints, and emails to a supplier contact, with the PDF attached and the send recorded.
- **SC-007**: Every action is refused to users without the matching right, every edit of a non-draft is refused, and unknown ids answer "not found". None of them errors.

## Assumptions

- **No PO approval.** It was used from 2024-11 to 2025-03 and stranded confirmed POs without receipts. PurchaseOrderApproval (95) and the `approved` and `approver` columns of `purchase_order` stay as they are, unused.
- **PO numbering stays the id.** A serial would need a facility, and the PO header has none; 8 POs span facilities. Suppliers and staff have used the id for years.
- **Company-wide on hand for averaging.** The cost list holds one cost per product for the whole company. Averaging against a single facility's stock would weigh a receipt against the wrong stock: 611 of the products ordered in 2026 went to 2 to 4 facilities, and cost would swing with whichever facility received last. In-transit stock is the company's and counts. Negative stock is negligible at either level.
- **Prices before tax.** Cost and PO prices are net of IVA (clarified). Every existing PO line says "tax included" only because the flag is copied from the product, whose flag is about sale prices. New lines store it off, and cost ignores it, so complementary receipts of pre-cutover POs average correctly.
- **Receipts generated at confirm hold the pending quantity.** On a first confirm that is the ordered quantity. The receipt is a draft for the store to correct before it confirms.
- **No recalculation of sale prices.** The request asked for derived lists to recalculate "as they do today", but nothing derives today. The current system computes sales minimum and maximum prices on the fly from cost and profit rates, which this API deprecated (#185). Sale lists hold stored prices, about 1.30× cost. Repricing from cost is out of scope.
- **Marking defaults to marked.** New lines are marked to purchase, as today, and the agent unmarks what they won't buy. Only approved requests' marked lines feed POs, as the request says, although 1,612 unmarked lines were ordered in the current system.
- **Request lines keep their warehouse** even when it belongs to another facility than the header's (17 lines today); cross-facility destinations are normal.
- **Request serials on submit.** Existing requests keep their serials, including the duplicates, zeros and empties. New requests are numbered on first submit from the facility's highest serial, safely under concurrency, following spec 029's locking. The facility comes from the header warehouse.
- **Rights.** Approval uses update on 96, and marking uses update on 20 because marking is the purchasing agent's job. Emailing uses update on 20.
- **Requests have no PDF.** The current system's request ticket is not in scope.
- **Cutover.** Only the new MBE writes these tables after cutover. Existing drafts and submitted requests remain usable. Existing confirmed POs keep their receipts; the duplicates remain as history.

## Out of Scope

- PO approval (95), supplier returns, expense vouchers and supplier payments (§5–6).
- Currency, exchange rate, discount and invoice number editing.
- Repricing sale lists from cost, and valuing stock at cost.
- A PDF for requests.
- Cleaning up past duplicates: request serials, request lines on two POs, duplicate receipts.
- The UI (mbe-ui).
