# Feature Specification: Supplier Sub-Panels by Privilege, Race-Safe Serials and Stock

**Feature Branch**: `035-serials-stock-bank-access`

**Created**: 2026-10-27

**Status**: Draft

**Input**: Two hardening fixes before the Reports epic (Cross-cutting):
- mictlanix/mbe-roadmap#152: supplier bank accounts only for callers allowed to see them. The PR says `Closes mictlanix/mbe-roadmap#152` and describes the change neutrally.
- mictlanix/mbe-roadmap#153: serials and stock availability that hold under concurrent confirmations, and the ledger index for cancelling inventory documents.

Out of scope: the error format (mictlanix/mbe-roadmap#147) and the current system's own serial bugs (mictlanix/mbe#45).

## Findings that shape this spec

### Supplier detail and its sub-panels

- `GET /suppliers/{id}` is gated on the supplier lookup: Suppliers (3), Products (0) or Pricing (106) read. Its body embeds four sub-panels, each with its own gate elsewhere:

  | Sub-panel | Its own route requires |
  |---|---|
  | `bank_accounts` | Bank Accounts (13) read (`/bank-accounts`) |
  | `contacts` | Contacts (12) read (`/contacts`) |
  | `addresses` | Addresses (11) read (`/addresses`) |
  | `agreements` | Suppliers (3) read (`/suppliers/{id}/agreements`) |

  The detail applies the lookup gate to all four rather than each panel's own. `POST` and `PUT /suppliers` return the same body.
- mbe-ui (origin/main) has two callers of the supplier detail:
  - The supplier admin screen (route gated on Suppliers read). It uses every field and gates each sub-panel's actions on that panel's own privilege.
  - `supplierDisplayNameProvider`, which rebuilds a supplier filter chip's label in the product list, the pricing grid and the sales advanced search. It reads only `code` and `name`, and those screens' users may hold Products or Pricing read without Suppliers read.

  Moving the detail to Suppliers read would therefore break the filter chip for those users.
- The supplier form always sends all three link lists on save (`addressIds`, `contactIds`, `bankAccountIds`). The API replaces a sent list wholesale, and an empty list unlinks everything.
- mbe_dev has 25 non-admin users with Products or Pricing read and no Suppliers read, so the lookup gate must keep serving them.
- Customers are a different case. `GET /customers/{id}` embeds addresses and contacts under the customer lookup (Customers, Sales Orders, Sales Quotes or POS read), but the sales screens consume them by design: the delivery destination, the address and contact pickers, and the quote email dialog. The embed was added for them (mbe-api#132). The customer detail embeds no bank accounts.
- No other response embeds these records. Sales, quote and delivery documents carry only contact and address ids. Purchase orders, supplier payments and expense vouchers embed none. Invoice batch bank details are free text, not linked records.

### Serials

- `documents.assign_folio` numbers sales orders, sales quotes, delivery orders (including an itinerary's split child orders), customer refunds and supplier payments. It locks the facility row, then reads `MAX(serial)` with a plain read. Under REPEATABLE READ that read can come from a snapshot opened before the lock, so it misses the serial the previous holder committed.
- Migration 007 (and later ones) put a unique `(facility, serial)` index on every one of those tables and on the inventory documents. So today the race does not produce duplicate rows. The second confirmation fails on the unique key, which the global `IntegrityError` handler answers with a generic 409, "The request conflicts with existing data". The cashier sees a refusal that doesn't say why, and has to retry.
- The two existing race-safe patterns:
  - spec 027's `invoice_service._next_serial`: lock, then a locking read for the maximum;
  - spec 029's `inventory_documents`: commit, take the locks, and only then do ordinary reads, so the snapshot opens after the locks. Spec 029 avoided a locking `MAX` because its next-key locks deadlocked against a step holding the newest document. It also documented the facility lock order: refunds and delivery stops take warehouse locks before the facility's.
- `purchase_request_service.submit` already follows spec 029's pattern: commit, lock the request, lock the facility's transit warehouse, then read `MAX`. It is safe and needs no change. `supplier_payment_service` documents the same commit-then-lock order, so its numbering may already be safe; the plan verifies that.
- Customer payments are not numbered by this API. Both creation paths write `serial = 0`:
  - `customer_payment_service.py:244`;
  - `customer_refund_service.py:619`, for the credit-note payment and the cash payout.

### The current system's customer payment serial

From `mictlanix/mbe`, `origin/casa-maestra-2026`:

- **Scope:** one sequence per store, which is this API's facility. It uses `MAX(serial)+1` with no lock and no unique key (mictlanix/mbe#45).
- **What gets a number:** every payment it writes. That covers payments on sales orders, credit payments, payments in advance, and the credit-note backing payment (type 4). It writes no payment for cash refunds.
- **Where the serial appears:**
  - the credit payment, payment-in-advance and credit-note tickets (`{0:D8}`);
  - the received-payments report;
  - the payment list, as a tooltip only;
  - the payment search, where a number matches the payment's id or its serial.
- **This API already prints a payment's serial when it is not 0,** on the payment ticket and the account statement.

### Stock availability

- `sales_order_service.confirm_order` checks `stock_ledger.available` (on hand less reservations) per product and warehouse with no lock, then reserves. Two concurrent confirmations can therefore both claim the last units. It does not lock the order row either, so two confirmations of the same order can both run.
- Which other paths reserve or consume stock, and whether they lock:

  | Path | Effect on availability | Locks |
  |---|---|---|
  | Inventory issues, transfers out, counts (specs 029–030) | Lowers it, after a check | Yes: warehouses ascending, after a commit |
  | Inventory receipts | Raises it | Yes |
  | Delivery dispatch: direct delivery, itinerary departure | None. On hand and the reservation fall together | The delivery line; no check needed |
  | Itinerary returns | None. On hand and the re-reservation rise together | The delivery line |
  | Customer refunds | Raises it (returns stock) | The source order |
  | Sales order cancel | Raises it (releases reservations) | None needed |
  | **Sales order confirm** | **Lowers it, after a check** | **None: the gap** |

  An inventory issue and a sales order confirmation on one warehouse can also oversell each other today, since only the issue locks.

### Measured on mbe_dev (2026-10-27, read-only)

- **Where API data starts.** The current system's data ends on 2026-09-29: its last orders came from 9 regular users. Since 2026-09-30 every row was written by this API (users 1 and -1): 46 sales orders, 8 quotes, 16 delivery orders, 21 purchase requests and 20 customer payments.
- **Duplicate serials:**

  | Table | Duplicate groups (rows) | Facilities | Created | From |
  |---|---|---|---|---|
  | sales_order, sales_quote, customer_refund, delivery_order, supplier_payment, inventory documents | 0 (unique index) | — | — | — |
  | customer_payment | 11 (24) | 3, 8, 50, 51 | 2012-04 → 2026-06-23 | the current system |
  | purchase_request | 9 (28) | 6, 8, 47, 50 | 2024-08 → 2026-02-20 | the current system |
  | fiscal_document (per issuer and series) | 9 (18) | — | 2017 → 2025-05 | the current system |

  None comes from this API. Migration 007 had already renumbered 21 older document rows when it added the unique indexes.
- **Customer payment serials.** 295,349 payments; per-facility maximums track the counts (facility 8: 59,891 payments, max 60,070). All 20 API payments carry serial 0, and no other payment does. They are all at facility 51 (max serial 18,713) and were written between 2026-09-30 and 2026-10-27 by users 1 and -1. Nineteen are payment type 1 and one is type 2; none is a credit note's payment or a refund payout.
- **Payment serial index.** `customer_payment` has no `(facility, serial)` index. A facility's maximum is read through the facility index with a filesort over about 114k estimated rows.
- **Ledger lookup.** `lot_serial_tracking` has 631,995 rows and no index on `source` or `reference`. Finding an inventory document's entries (`source = ? AND reference = ?`) is a full index scan: 670k rows read, none filtered by an index.

## Clarifications

### Session 2026-10-27

Decided from the findings above. Bullets starting with Q were confirmed by question.

- **Supplier detail:** the gate stays the lookup. Each sub-panel is returned only to callers who may read that panel, because the product, pricing and sales filter chips read the detail under the lookup gate.
- Q: A caller saves a supplier with a link list for a sub-panel they cannot read. Ignore it, refuse only a change, or refuse whenever it is sent? → A: Ignore it. That panel's links stay unchanged and the rest of the save applies, because the supplier form sends every list on save and the caller cannot see what they would replace (FR-003).
- Q: Which privilege shows supplier contacts and addresses: their own, Suppliers read, or none (filter bank accounts only)? → A: Their own. Contacts need Contacts (12) read and addresses need Addresses (11) read, as their own routes and the supplier form's panel actions do. One rule covers every panel (FR-001).
- **Customers are unchanged,** because their embedded addresses and contacts are what the sales screens' lookup is for.
- Q: Backfill the 20 API payments that carry serial 0, leave them at 0, or set them to NULL? → A: Backfill. The migration gives them facility 51's next serials in id order (18,714–18,733 on mbe_dev), so every API payment carries a folio as every current-system payment does (FR-015, SC-006).
- Q: Include the concurrent credit-limit check, two credit orders that each fit the limit but together exceed it? → A: No, it stays out of scope. Fixing it needs a customer lock whose order every payment and refund path would have to follow. A roadmap issue is opened for it after this spec merges.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Supplier bank accounts only for those allowed to see them (Priority: P1)

A pricing analyst opens a product filtered by supplier. The screen still shows the supplier's code and name, but the analyst receives none of the supplier's bank accounts, contacts, addresses or agreements, since they hold none of those privileges. A purchasing user with Suppliers and Bank Accounts read still sees the full supplier form.

**Why this priority**: each sub-panel should follow the same privilege on the supplier detail as on its own route, and the roadmap puts this fix first.

**Independent Test**: as a user with Products read only, read a supplier that has bank accounts, contacts, addresses and agreements. The four lists come back empty. As a user who holds each privilege, the same read returns them all.

**Acceptance Scenarios**:

1. **Given** a user with Products read only, **When** they read a supplier with linked bank accounts, **Then** the response has the supplier's identity and an empty `bank_accounts`, `contacts`, `addresses` and `agreements`.
2. **Given** a user with Suppliers and Bank Accounts read but not Contacts read, **When** they read the supplier, **Then** they get its bank accounts, addresses (if they have Addresses read) and agreements, but no contacts.
3. **Given** an administrator, **When** they read the supplier, **Then** every sub-panel is returned.
4. **Given** a supplier editor without Bank Accounts read, **When** they save the supplier form with an empty bank account list, **Then** the supplier's bank account links are unchanged.
5. **Given** any caller, **When** they create or update a supplier, **Then** the returned supplier is filtered by the same rule as a read.

---

### User Story 2 - Concurrent confirmations get distinct, consecutive serials (Priority: P1)

Several cashiers at one store confirm sales orders at the same moment. Each confirmation succeeds and receives the next folio, with no gaps and no repeats, and none is refused because of another confirmation. The same holds for quotes, delivery orders, refunds and supplier payments.

**Why this priority**: today a simultaneous second confirmation fails outright. With a busier store this becomes a daily failure at the counter.

**Independent Test**: fire N confirmations of different orders in one facility at once, against the MariaDB database the API runs on. All N succeed with N distinct serials that follow the facility's previous maximum without gaps. On the code before this change, the same check fails.

**Acceptance Scenarios**:

1. **Given** 20 draft orders in one facility, **When** all 20 are confirmed concurrently, **Then** all 20 succeed with serials max+1 … max+20.
2. **Given** concurrent confirmations of different document types in one facility (an order, a quote, a refund), **When** they run together, **Then** each type's serials stay distinct and consecutive within that type.
3. **Given** two facilities, **When** both confirm at once, **Then** neither waits for the other's numbering.

---

### User Story 3 - Two confirmations cannot sell the same units (Priority: P1)

Two salespeople confirm orders that each want the last 5 units in one warehouse. One order confirms. The other is refused with the existing insufficient-stock error listing the line, and nothing of it is reserved.

**Why this priority**: an oversold order surfaces only when the truck is loaded, and the customer was promised goods that are not there.

**Independent Test**: with exactly 5 units available, confirm two 5-unit orders concurrently on MariaDB. One succeeds and one gets the insufficient-stock error. Availability then reads 0, not −5. On the code before this change, both succeed.

**Acceptance Scenarios**:

1. **Given** 5 units available in a warehouse, **When** two orders for 5 each are confirmed concurrently, **Then** one confirms and the other gets the insufficient-stock error, and availability is 0.
2. **Given** 5 units available, **When** a sales order for 5 and an inventory issue for 5 from the same warehouse are confirmed concurrently, **Then** one succeeds and the other is refused for insufficient stock.
3. **Given** one draft order, **When** it is confirmed twice concurrently, **Then** it is confirmed once, with one serial and one set of reservations, and the other request is refused because the order is already confirmed.
4. **Given** orders on different warehouses, **When** they confirm concurrently, **Then** neither waits for the other's stock check.

---

### User Story 4 - Customer payments carry a folio (Priority: P2)

A cashier takes a payment and the ticket shows its folio, as the current system's tickets do, from the same per-store sequence the current system uses. A clerk can find the payment by the number on that ticket.

**Why this priority**: the current system prints this number and staff search by it. This API's payments print a blank folio today. It matters for go-live, not for safety.

**Independent Test**: take a payment at a facility. It receives that facility's next payment serial, and the ticket prints it. Searching payments by that number finds it. Concurrent payments in one facility get distinct, consecutive serials.

**Acceptance Scenarios**:

1. **Given** a facility whose highest payment serial is 18,713, **When** a payment is taken there, **Then** it is numbered 18,714 and its ticket shows `00018714`.
2. **Given** a refund paid out as a credit note or in cash, **When** it is confirmed, **Then** the payment it writes is numbered in the refund facility's payment sequence.
3. **Given** 20 payments taken concurrently in one facility, **When** they commit, **Then** their serials are distinct and consecutive.
4. **Given** a payment numbered 18,714, **When** a clerk searches payments for `18714`, **Then** the payment is found, as it would be by its id.
5. **Given** the 20 payments this API wrote with serial 0, **When** the migration runs, **Then** each carries facility 51's next serial in id order, and no other payment's serial changes.

---

### User Story 5 - Cancelling an inventory document does not scan the ledger (Priority: P3)

A warehouse clerk cancels a confirmed receipt, issue or transfer. Finding that document's ledger entries uses an index rather than reading all 632k ledger rows while the warehouse locks are held.

**Why this priority**: it is a latency and lock-time cost that grows with the ledger, not a correctness bug.

**Independent Test**: on mbe_dev, the plan of the ledger lookup by source and reference reads a handful of rows through an index, compared with 670k rows before.

**Acceptance Scenarios**:

1. **Given** the migration has run, **When** the lookup of one document's ledger entries is explained on mbe_dev, **Then** it uses the new index and reads only that document's rows.

---

### Edge Cases

- A caller with Suppliers read but no Bank Accounts read (3 users on mbe_dev) stops seeing bank accounts on the supplier form. This is intended: it matches `/bank-accounts`. Likewise, the 1 user with Suppliers read but neither Contacts nor Addresses read stops seeing contacts and addresses.
- A supplier editor who can read a panel sends its list. It is applied as today, including an empty list meaning "unlink all".
- A caller who cannot read a panel sends a non-empty list for it. That list is ignored too: they cannot see what they would replace.
- Creating a supplier is different: with nothing to preserve, a list from a caller who cannot read that panel is still ignored, so the new supplier has no links on that panel.
- A confirmation waits while another holds the same facility or warehouse lock. If the database still declares a deadlock or a lock timeout, the request answers the existing "busy, retry" conflict, never a server error, and writes nothing.
- A refusal (insufficient stock, credit hold, credit limit) uses no serial: the next successful confirmation still gets the next consecutive number.
- A payment or refund whose cash session was closed before it got the lock is refused with 409 "The cash session is closed" and writes nothing. Today it is recorded into the closed session, after its count.
- An order line on a warehouse in another facility is common (14% of 2026 lines). Its warehouse is locked like any other, and the folio is still the order's facility.
- A product that does not track stock locks nothing and is not checked, as today.
- The current system and this API both writing payments for one store in production would share the sequence. This API's lock does not protect against the current system's unlocked read (mictlanix/mbe#45, out of scope).
- Existing duplicate serials from the current system are left as they are. No unique index is added where duplicates exist (customer payments, purchase requests).

## Requirements *(mandatory)*

### Functional Requirements

#### Supplier sub-panels (roadmap#152)

- **FR-001**: The supplier detail MUST keep the lookup gate (Suppliers, Products or Pricing read). Each sub-panel MUST be returned only to callers who may read it, and as an empty list otherwise:
  - bank accounts: Bank Accounts (13) read;
  - contacts: Contacts (12) read;
  - addresses: Addresses (11) read;
  - agreements: Suppliers (3) read.

  Administrators MUST receive every sub-panel.
- **FR-002**: Every response that returns the supplier detail (read, create, update) MUST apply FR-001.
- **FR-003**: On create or update, a link list for a sub-panel the caller cannot read under FR-001 MUST be treated as not sent: the supplier's links on that panel stay unchanged.
- **FR-004**: The supplier list and the product's embedded supplier MUST stay as they are; neither carries sub-panels.
- **FR-005**: The customer detail MUST stay as it is. Its embedded addresses and contacts serve the sales screens behind the customer lookup, and it embeds no bank accounts.

#### Serials (roadmap#153)

- **FR-006**: One numbering rule MUST serve every document numbered per facility by this API:
  - sales orders;
  - sales quotes;
  - delivery orders, including an itinerary's split child orders;
  - customer refunds;
  - supplier payments;
  - customer payments (FR-012).

  Under concurrent confirmations in one facility, it MUST give each document of a type a distinct serial, one above the highest committed serial of that type and facility.
- **FR-007**: The rule MUST NOT read the highest serial from a snapshot older than the lock that serialises numbering.
- **FR-008**: The rule MUST keep one lock order with the existing paths, so that the paths cannot deadlock one another in normal operation:
  - spec 029's inventory documents;
  - refunds and delivery stops, which today take warehouses before the facility, and draft creation, whose header insert holds the facility before its lines' warehouses;
  - payment applications, which lock orders;
  - supplier payments, which lock the cash session, then the payment (on cancel), then purchase orders.
- **FR-009**: A deadlock or lock-wait timeout that still occurs on a numbering path MUST answer the existing "busy, retry" conflict, never a server error, and MUST leave nothing written.
- **FR-010**: A refused confirmation MUST NOT consume a serial.
- **FR-011**: Purchase request numbering (already safe, spec 031) and invoice numbering (spec 027) MUST keep working as they do. The plan MUST record whether supplier payments were already safe.

#### Customer payment serials (roadmap#153)

- **FR-012**: Every customer payment this API writes MUST be numbered in its facility's payment sequence under FR-006. That covers payments, credit-note backing payments and refund payouts.
- **FR-013**: The payment ticket MUST show the folio as it already does for a non-zero serial (8 digits). The payment response MUST carry it.
- **FR-014**: A numeric payment search MUST match a payment by its serial as well as by its id, as the current system's search does.
- **FR-015**: A migration MUST give the payments this API wrote with serial 0 (20 on mbe_dev, all at facility 51) the next serials of their facility, in id order. No other payment's serial may change. Running it twice MUST change nothing the second time.
- **FR-016**: The migration MUST add a `(facility, serial)` index on customer payments so the facility's highest serial is read through it. The index is non-unique, because the current system's 11 duplicate groups stay.

#### Stock availability (roadmap#153)

- **FR-017**: Confirming a sales order MUST serialise with every other step that checks availability on the same warehouse: other confirmations, inventory issues, transfers and counts. It MUST lock the warehouses of its stocked lines in the same order spec 029 uses, and MUST check availability on a snapshot that includes everything committed before it obtained those locks.
- **FR-018**: Of two confirmations that together exceed what a warehouse has available, at most the available quantity MUST be reserved. The later one MUST get the existing insufficient-stock error with its lines and reserve nothing.
- **FR-019**: Confirming the same order twice concurrently MUST confirm it once. The other request MUST get the existing "already completed" refusal. An edit to the order's header or lines that waited behind its confirmation MUST get the same refusal, not change the confirmed order.
- **FR-020**: Confirmations on disjoint warehouses MUST NOT wait for each other's stock locks. Confirmations in one facility still queue on that facility's numbering lock, as they do today.
- **FR-021**: Paths that do not lower availability MUST keep their current locking: delivery dispatch, itinerary departures and returns, refunds and cancellations.

#### Ledger index (roadmap#153)

- **FR-022**: A numbered SQL migration (spec 009 convention, with its rollback) MUST add an index on the ledger's `(source, reference)`, and the same on the reservations table, whose cancel-time delete otherwise locks the whole table.
- **FR-023**: The plan of the inventory document ledger lookup on mbe_dev MUST be recorded before and after.

### Key Entities

- **Supplier detail**: the supplier with its linked bank accounts, contacts, addresses and agreements. Each sub-panel is visible according to its own privilege.
- **Document serial**: a per-facility, per-document-type folio printed on tickets and documents. It is unique by index on most document tables; customer payments and purchase requests have no unique index because of historical duplicates.
- **Customer payment serial**: the per-store (facility) folio shared with the current system's sequence. Today this API writes 0, meaning "not numbered".
- **Availability**: on hand less reservations, per product and warehouse. A confirmed sales order reserves; dispatch moves the reservation into a stock movement.
- **Ledger entry**: a stock movement tagged with its source document type and document id.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A user with Products read only reads a supplier with bank accounts, contacts, addresses and agreements. All four lists are empty: automated test, and confirmed on the dev deployment. A user holding each privilege still gets all four.
- **SC-002**: Saving a supplier, as an editor without Bank Accounts read, with an empty bank account list leaves its bank account links unchanged.
- **SC-003**: 20 concurrent sales order confirmations in one facility on MariaDB all succeed, with 20 distinct serials that follow the facility's previous maximum without gaps. Before the change, the same run refuses at least one with the generic 409 "The request conflicts with existing data".
- **SC-004**: 20 concurrent customer payments in one facility on MariaDB get 20 distinct, consecutive serials (before: all 0).
- **SC-005**: Two concurrent confirmations for the last 5 units of a warehouse on MariaDB: one confirms, one gets insufficient stock, and availability ends at 0. Before the change, both confirm and availability ends at −5.
- **SC-006**: After the migration, mbe_dev has no customer payment with serial 0 written by this API. The 20 backfilled ones carry 18,714–18,733 at facility 51, or the next free serials if more were written meanwhile. No other payment's serial differs from before.
- **SC-007**: The plan of the ledger lookup for one inventory document reads rows through the new index, against 670k rows before.
- **SC-008**: The automated suite and lint pass.

## Assumptions

- mbe_dev is the only database in scope. Its rows since 2026-09-30 are this API's; earlier rows are the current system's.
- Concurrency proofs run against MariaDB, because the automated suite uses SQLite, which serialises writers and cannot reproduce the race. Writes to mbe_dev for those proofs are cleaned up afterwards and need confirmation before they run, as in specs 027, 029 and 033.
- The plan chooses between spec 027's locking read and spec 029's commit-then-lock order for the shared numbering rule. Whichever it picks, it must meet FR-007 to FR-009.
- The customer payment sequence is per facility across all payment types, like the current system's per-store sequence.
- The search change is limited to matching a number against the payment serial. The current system matches across all stores; this API keeps its existing facility rules.

## Out of Scope

- The error response format (mictlanix/mbe-roadmap#147).
- The current system's own serial bugs, its unlocked `MAX+1` and its bare catch (mictlanix/mbe#45).
- Renumbering existing duplicates, and unique indexes on customer payments or purchase requests.
- A concurrent credit-limit check: two credit orders that each fit the limit but together exceed it. This spec serialises stock, not credit. A roadmap issue is opened for it after this spec merges.
- The redundant second unique `(facility, serial)` index on sales orders, which was noticed but is not changed here.
