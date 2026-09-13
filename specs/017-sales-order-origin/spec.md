# Feature Specification: Sales Order Origin

**Feature Branch**: `017-sales-order-origin`

**Created**: 2026-09-13

**Status**: Draft

**Input**: User description: "Record on sales_order which workflow raised the order, so a
back-office order and a register sale can be told apart after the fact. Corresponds to GitHub issue
#209. A nullable, enum-valued field set at creation and never afterwards, exposed on the order and
on the list row, and filterable — with NULL meaning 'not recorded' rather than a default, and
nothing backfilled."

## Overview

A back-office order and a register sale produce `sales_order` rows that are identical in every
readable field. Both workflows write the same document through the same endpoints — that part
works and needs nothing new. What does not work is telling the two apart afterwards, and the
practical result is that both inboxes are wrong in both directions: the back-office list shows
register sales, and the register's own list shows back-office orders raised on that register.

The three fields that look like they might stand in do not. `point_sale` is `NOT NULL` and is
derived from the caller when the body omits it, so a back-office user with a register configured
stamps orders with *the same register a walk-in sale would carry*; it is also immutable after
create, so it cannot be corrected later. `fulfillment_intent` describes how goods leave, not where
the order came from — `PICKUP` is the ordinary register value and `DELIVERY` is shared by both
workflows. `customer` is weakest of the three: the generic walk-in customer is a register
*convention*, not a rule, and a register sale to a named credit customer is perfectly ordinary.

This feature records the fact directly. It follows the precedent already set by
`fulfillment_intent` (migration `017_sales_order_fulfillment_intent.sql`): an optional field whose
absence means **not recorded**, never a default value, never inferred on the caller's behalf. The
shape is deliberately narrow — one fact, written once at creation, readable everywhere the order is
readable, and filterable.

The client cannot solve this on its own. mbe-ui's back-office workspace applies rules a register
sale must not be subjected to: it requires a non-generic customer, it forces delivery, and it offers
no payment step or counter-pickup. Handed a register sale, it would either demand the user change
that sale's customer or push a counter-pickup sale toward mandatory delivery. Its only defence is
to refuse to open what it did not raise — and it currently has no reliable way to know.

For what it is worth, the legacy monolith had the same shape: one table, two screens, no
discriminator. This is inherited rather than a regression.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - An order records the workflow that raised it (Priority: P1)

A back-office user captures an order for delivery and a cashier rings up a counter sale. Each
resulting order carries a record of which of the two workflows produced it, and neither can be
mistaken for the other afterwards.

**Why this priority**: Everything else in this feature reads a fact that has to exist first.
Without the write path there is nothing to expose and nothing to filter.

**Independent Test**: Create one order declaring each workflow, read each back, and confirm each
reports the workflow that raised it — while two orders raised on the same register, one from each
workflow, remain distinguishable.

**Acceptance Scenarios**:

1. **Given** a back-office client creating an order, **When** it declares the back-office workflow on creation, **Then** the stored order reports that workflow when read back.
2. **Given** a register client creating a sale, **When** it declares the point-of-sale workflow on creation, **Then** the stored order reports that workflow when read back.
3. **Given** a back-office user whose account has a register configured, **When** they raise a back-office order, **Then** the order reports the back-office workflow even though it carries that user's register, and a walk-in sale on the same register remains separable from it.
4. **Given** a client that declares nothing, **When** it creates an order, **Then** the order records no origin at all — the system does not infer one from the register, the customer, or how the goods will leave.

---

### User Story 2 - A converted quote lands in the back office without the client's help (Priority: P1)

A user converts an accepted sales quote into an order. The resulting order reports the back-office
workflow, and the fact that a quote preceded it stays a separate, independently readable fact.

**Why this priority**: Same priority as User Story 1 and the half of it that no client can supply.
Quote conversion creates an order without passing through order creation and without a request body
of any kind, so nothing on that path can declare an origin. Left out, every converted order records
none — permanently, not just during the transition — and converted orders are exactly what a
back-office list most needs to show: a quote the customer accepted.

**Independent Test**: Convert a quote and read the resulting order; confirm it reports the
back-office workflow with no client involvement, and that the quote it came from is readable
separately from a list row.

**Acceptance Scenarios**:

1. **Given** an accepted quote, **When** a user converts it to an order, **Then** the resulting order reports the back-office workflow.
2. **Given** a converted order, **When** it is listed, **Then** the quote it came from is readable from the list row without opening the order.
3. **Given** a converted order and a back-office order raised directly, **When** both are listed, **Then** both report the back-office workflow and only the converted one names a preceding quote.
4. **Given** a cashier with a register converts a quote, **When** the order is created, **Then** it still reports the back-office workflow — origin records which workflow the order belongs to, not who pressed the button.

---

### User Story 3 - A list screen separates the two inboxes from the page it already has (Priority: P1)

An operator opens the back-office order list. It shows back-office orders and not register sales,
and it decides that from the page of rows it already fetched. The register's own list makes the
opposite cut, and keeps the history it has always shown.

**Why this priority**: The list row is where this fact does most of its work, and it is where its
absence costs most: a list screen today cannot even post-filter a page without one extra request
per row. A field readable only by opening each order would leave the reported problem substantially
unsolved.

**Independent Test**: Create orders from both workflows, list them, and confirm each row reports its
workflow — with no per-row follow-up request needed to learn it.

**Acceptance Scenarios**:

1. **Given** a page of orders from both workflows, **When** the list is read, **Then** every row reports the workflow that raised its order, or reports that none was recorded.
2. **Given** a client showing a back-office list, **When** it asks for back-office orders only, **Then** it receives only orders that recorded that workflow, and register sales are absent.
3. **Given** orders raised before this feature shipped, **When** a client selects a workflow, **Then** those orders do not appear under either workflow, because neither is what they recorded.
4. **Given** a register list showing the sales of one register, including sales raised long before this feature, **When** it asks for everything except back-office orders, **Then** the back-office orders raised on that register are gone and every one of its older sales is still there.

---

### User Story 4 - Orders that predate the field keep working exactly as they did (Priority: P1)

An operator opens, lists, edits and searches orders raised before this feature existed. Nothing
about them changes.

**Why this priority**: This is a schema change against a table of 300,000+ existing rows, and the
value of the feature is a fact that is honestly absent from all of them. Any attempt to supply one
retroactively would assert something nobody verified — including the legacy "Pedidos" orders that
were genuinely back-office. Guaranteeing the absence is safe is part of shipping it.

**Independent Test**: Capture the responses for a sample of existing orders before the change, apply
it, and confirm those responses are unchanged apart from the new field reading as not recorded.

**Acceptance Scenarios**:

1. **Given** an order raised before this feature, **When** it is read, **Then** it reports no recorded origin and every other field is unchanged.
2. **Given** an order raised before this feature, **When** it is listed, edited, confirmed, delivered or cancelled, **Then** it behaves exactly as it did before the change.
3. **Given** the full existing table, **When** the change is applied, **Then** no existing row's origin is written, derived, or inferred.

---

### Edge Cases

- **A client omits the origin on creation.** The order records none. Omission is not an error and
  not a default — the system never guesses, for the same reason it does not guess
  `fulfillment_intent` from the address.
- **A client sends a value that is not one of the recognised workflows.** The creation is rejected
  with a validation error, exactly as any other out-of-range enum is.
- **A client tries to change an order's origin after creation.** There is no way to: origin is a
  fact about how the order was raised, not an editable attribute. The update path offers no such
  field, and a caller that sends one does not thereby change the order.
- **A client tries to clear a recorded origin.** Same answer — the field is write-once at creation.
  "Not recorded" is reachable only by never recording it.
- **Filtering when most rows recorded nothing.** A workflow filter returns only orders that recorded
  that workflow. Orders recording nothing are not silently folded into either one.
- **The register client is never updated to declare its workflow.** Its sales keep recording
  nothing, which is honest and harmless in both directions: a back-office list excludes them because
  it selects only orders that recorded the back-office workflow, and the register's own list keeps
  them because it excludes back-office orders rather than selecting its own. Neither client waits on
  the other.
- **Rolling the change back.** The rollback drops the column, and every origin recorded up to that
  point is lost — there is no other copy of the fact. The rollback script states this, as the
  repository's rollback scripts do.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A sales order MUST be able to record which workflow raised it, drawn from a fixed
  vocabulary with one member per capture surface — not a boolean — so that a later third surface
  needs no further field.
- **FR-002**: The recorded value MUST be optional, and its absence MUST mean **not recorded**. It
  MUST NOT mean, default to, or be read as any particular workflow.
- **FR-003**: A client creating an order MUST be able to declare the workflow raising it.
- **FR-004**: The system MUST NOT infer an order's origin from the register that raised it, the
  customer it names, or how its goods are to leave. An undeclared origin stays unrecorded.
- **FR-005**: An order's origin MUST be fixed at creation and MUST NOT be changeable afterwards —
  neither to another workflow nor back to unrecorded.
- **FR-006**: Converting a sales quote into an order MUST record the back-office workflow on the
  resulting order, without the converting client supplying anything. A converted order belongs to
  the back-office workflow regardless of which user converted it.
- **FR-007**: The recorded origin MUST be readable when a single order is read.
- **FR-008**: The recorded origin MUST be readable on a list row, so that a client can separate the
  two workflows from a page of orders without one follow-up request per row.
- **FR-009**: The quote an order was converted from MUST be readable on a list row. It answers
  "what preceded this order", which is a different question from "which workflow raised it", and
  the two MUST remain independently readable rather than collapsed into one field.
- **FR-010**: A client listing orders MUST be able to ask for a single workflow's orders, **and**
  MUST be able to ask for every order *except* one workflow's. Exclusion is what lets the register's
  own list stop showing back-office orders without also losing every sale raised before this
  feature existed; selection alone fixes only the back-office inbox.
- **FR-011**: Selecting a workflow MUST return only orders that recorded that workflow. Orders that
  recorded nothing MUST NOT be returned under any workflow.
- **FR-012**: Excluding a workflow MUST return every order that did not record it — including
  orders that recorded nothing. "Not back-office" is a question about what an order is not, and an
  order that recorded no origin is not a back-office order.
- **FR-013**: A value outside the recognised vocabulary MUST be rejected at creation with a
  validation error.
- **FR-014**: No existing order's origin MUST be written, derived, backfilled or inferred by this
  change. Every order raised before it records nothing.
- **FR-015**: No existing order's behaviour MUST change. An order whose origin was never recorded
  MUST keep listing, opening, reading, editing and progressing through its lifecycle exactly as it
  does today.
- **FR-016**: The change MUST be reversible, and the reversal MUST state that every recorded origin
  is lost with it.

### Key Entities

- **Sales Order**: gains one optional attribute — the workflow that raised it — alongside the
  `sales_quote` reference it already carries. Nothing else about the order changes.
- **Order Origin**: the vocabulary of capture surfaces. Two members for now: the point of sale (the
  register) and the back office. Absence is not a member of the vocabulary; it is the absence of a
  record. Extensible by adding a member, which is why this is an enumeration rather than a flag.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A client showing a back-office list determines the workflow of every order on a page
  of 20 rows using exactly one request — down from 21 today (one list request plus one per row),
  and down from "impossible", since the per-row request is the only thing that can answer it and
  the list row exposes neither candidate field.
- **SC-002**: 100% of orders produced by quote conversion record the back-office workflow, with no
  change required in the converting client.
- **SC-003**: Of orders raised after the change by a client that declares its workflow, 100% record
  it; of orders raised by a client that does not, 0% record anything — no order acquires an origin
  it was not given.
- **SC-004**: A back-office list filtered by workflow returns 0 register sales, and 0 orders that
  predate the change.
- **SC-005**: For a sample of existing orders, the read and list responses before and after the
  change are identical except for the new field reading as not recorded; 0 existing rows are
  modified by the migration.
- **SC-006**: A register list that excludes back-office orders returns 0 back-office orders while
  retaining 100% of the sales that record no origin — including every sale raised before the change.
- **SC-007**: An attempt to change a recorded origin after creation succeeds 0% of the time.

## Assumptions

- **Backfill is deliberately not performed** (issue #209, option A). Every existing row records
  nothing, and a filtered list therefore shows nothing that predates the change. mbe-ui accepts
  that transition: its own spec deliberately leaves its list unfiltered for now (OS-2), and will
  begin filtering once the field has been recording long enough to be useful. Backfilling to the
  register value would make the transition disappear at the cost of asserting something about
  300,000+ rows nobody verified, including legacy "Pedidos" orders that were genuinely back-office.
  Inference-based backfill is worse still: a wrong origin is worse than an absent one because it
  looks authoritative.
- **Two vocabulary members, not three** (issue #209, second comment). "Converted from a quote" is
  not a third origin; it is a different fact, already recorded by the order's `sales_quote`
  reference, which is non-null exactly when the order came from a quote, is set only by conversion,
  and cannot be forged by a client. Keeping the two facts separate keeps the client's list filter
  and its open-guard a single equality check rather than a set-membership test, and avoids
  overloading one field with two orthogonal meanings.
- **mbe-ui declares the back-office workflow when it creates orders**, and the register client may
  be updated to declare its own at any later point, independently. Neither client is blocked by the
  other: a register that never declares anything leaves its sales unrecorded, which the back-office
  filter already excludes.
- **The numbering of the vocabulary's members** follows the repository's existing convention that
  the ordinary, overwhelmingly common case takes the low value — as `FulfillmentType.PICKUP` does.
  The concrete values are settled in the data model, not here.
- **Storage follows the `fulfillment_intent` precedent**: a nullable column added by a numbered SQL
  migration with a matching rollback script, the way this repository does schema changes.
- **Scale**: `sales_order` holds 300,000+ rows in `mbe_dev`; the migration must be a column
  addition that writes no row data.

## Out of Scope

- **The conversion endpoint's register requirement.** Converting a quote is refused when the caller
  has no register configured, so a back-office user without one cannot convert a quote at all. It
  is a real defect on the same code path this feature touches, but it is a separate concern with no
  issue filed yet, and fixing it here would widen this change beyond recording a fact.
- **Making `point_sale` filterable by set or by exclusion.** Issue #209 notes the existing
  register filter is equality-only; changing it is not required to record an order's origin.
- **Any client-side work.** mbe-ui's back-office workspace, its list filtering, and its open-guard
  are specified in mictlanix/mbe-ui `specs/039-back-office-order-workspace`.
- **Reinstating mbe-ui `specs/029-back-office-sales-orders`.** That spec recorded "exclude
  point-of-sale orders from the Pedidos list" as dropped because inexpressible. This feature makes
  it expressible; acting on it is that spec's business.

## Dependencies

- Blocks mictlanix/mbe-ui `specs/039-back-office-order-workspace` (FR-051 – FR-054), which cannot
  safely open an order it did not raise until this fact exists.
- Sibling findings from the same audit are already resolved and do not gate this: #207 (→ #217),
  #208 (→ #214), #210 (→ #215), #211 (→ #216).
