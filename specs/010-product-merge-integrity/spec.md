# Feature Specification: Product Merge Integrity

**Feature Branches**: `015-product-merge-preview`, `016-merge-all-references`,
`017-merge-discard-config-rows`

**Created**: 2026-07-25

**Status**: Implemented (PRs #114, #115; commits `990fa83`, `caa4fcc`, `c9e83ad`)

**Input**: User description: "Merging a duplicate product into a canonical one must carry
everything that happened to the duplicate, discard how the duplicate was set up, and tell the
operator the scale of what they are about to do before they do it. Corresponds to GitHub
issues #111 and #112 and the follow-up correction to the configuration rows."

## Overview

Records what a product merge means and what it guarantees. Filed after two findings: the merge
handled a hand-written list of relationships rather than all of them, so the products people
most need to merge — anything ever quoted, delivered, invoiced, refunded or requested — could
not be merged at all; and the operator triggering this irreversible operation was shown nothing
about how much history rode on the record about to disappear.

A third finding followed the first fix: once every relationship was carried over, the ones that
describe *how a catalog record is set up* — its prices, labels, commission assignment and
per-customer discounts — turned out to need discarding rather than moving, because the record
being kept already has its own and the two sets cannot coexist.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Merging a product that has actually been used (Priority: P1)

An operator merges a duplicate that has been sold, delivered and invoiced over the years. The
merge succeeds: everything that happened to the duplicate now reads as having happened to the
record being kept, and the duplicate is gone.

**Why this priority**: A duplicate nobody ever used is a duplicate nobody needs to merge. The
records that need merging are exactly the ones with history, and those were the ones that
failed — 13,248 of 21,542 products in the reference dataset.

**Independent Test**: Merge the product carrying the most history in a populated database and
confirm the duplicate is deleted, nothing still points at it, and every relationship's row
count on the record being kept is the sum of both sides.

**Acceptance Scenarios**:

1. **Given** a duplicate referenced by sales, delivery, invoicing, refund and purchase-request records, **When** the operator merges it into the canonical product, **Then** the merge succeeds, every one of those records now refers to the canonical, and the duplicate is removed.
2. **Given** a duplicate referenced through a relationship added to the data model after this feature shipped, **When** the operator merges it, **Then** that relationship is carried over too, with no change to the merge logic.
3. **Given** a duplicate referenced by records that the merge cannot carry over, **When** the operator merges it, **Then** nothing at all is changed — no partially merged state exists.
4. **Given** a duplicate that appears on stamped fiscal documents, **When** the operator merges it, **Then** what those documents state was invoiced — the code, the description, the amounts — is unchanged; only the catalog record they point at moves.

---

### User Story 2 - The kept record's setup is the one that survives (Priority: P1)

An operator merges two records that are each configured — both have prices, both carry labels,
both may be assigned to a commission scheme or carry per-customer discounts. Afterwards the
kept record is configured exactly as it was before the merge.

**Why this priority**: Same priority as User Story 1 and discovered because of it. Once every
relationship was carried over, configuration was being carried over too, and the result was a
kept record holding a mixture of both products' setup — an outcome that could not be stated
without naming which individual rows happened to clash.

**Independent Test**: Merge two products that both carry prices, a label and a commission
assignment, and confirm every configuration count on the kept record is identical before and
after, while every history count is the sum of the two sides.

**Acceptance Scenarios**:

1. **Given** both products carry prices, **When** they are merged, **Then** the kept record's prices are unchanged and the duplicate's are gone.
2. **Given** both products carry the same kind of configuration — a label, a commission assignment, a per-customer discount — **When** they are merged, **Then** the kept record's stands and the duplicate's is discarded, for every such record and regardless of what the two sides happened to have in common.
3. **Given** a label or per-customer discount that exists only on the duplicate, **When** the products are merged, **Then** it is not carried over — the operator sets it on the canonical beforehand if it should survive.

---

### User Story 3 - Seeing the scale before committing (Priority: P2)

Before triggering a merge that cannot be undone, an operator is shown how much history rides on
the record about to disappear, broken down by kind and totalled.

**Why this priority**: It changes no data and prevents no failure; it informs a decision. It
ranks below the two correctness stories but is what makes an irreversible action reviewable, and
the review step that displays it otherwise has nothing to show.

**Independent Test**: Request the preview for a pair, confirm the breakdown matches what the
database holds, confirm nothing changed, then merge and confirm the merge acted on exactly the
kinds of record the preview listed.

**Acceptance Scenarios**:

1. **Given** a duplicate with history across several kinds of record, **When** the operator requests the preview, **Then** each kind is listed with its count, largest first, together with a total.
2. **Given** a duplicate nothing refers to, **When** the operator requests the preview, **Then** the breakdown is empty and the total is zero.
3. **Given** any pair, **When** the operator requests the preview, **Then** nothing in the data is changed by asking.
4. **Given** a pair the merge would refuse — the same record on both sides, or a record that does not exist — **When** the operator requests the preview, **Then** it is refused in the same way and for the same reason, so a preview that answers is a preview of a merge that would be accepted.
5. **Given** a preview and the merge that follows it, **When** both run, **Then** the kinds of record counted by the one are exactly the kinds acted on by the other.

### Edge Cases

- A duplicate nothing refers to previews as empty and merges to a plain deletion.
- Merging a record into itself is refused, distinctly from a record not existing.
- A missing record is reported as missing on the side it is missing from, so the operator knows which identifier is wrong.
- A record referred to from outside the modelled data set cannot be carried over; the deletion at the end of the merge fails and the whole merge is undone, leaving everything as it was.
- The preview counts what the merge *touches*, which includes the configuration it discards. A client that labels the total as work that will be reassigned overstates it.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: A merge MUST carry over every record that refers to the duplicate, with no kind of record omitted.
- **FR-002**: Coverage MUST extend to new kinds of record automatically as the data model grows, without any hand-maintained list of relationships.
- **FR-003**: After a successful merge, no record anywhere MUST still refer to the deleted duplicate.
- **FR-004**: A merge that cannot complete MUST leave everything unchanged; no partially merged state may be observable.
- **FR-005**: A merge MUST NOT suppress the failure of any step it performs — a step that fails must undo the merge rather than be skipped.
- **FR-006**: A merge MUST NOT alter what a stamped fiscal document states was invoiced.
- **FR-007**: A merge MUST discard the duplicate's configuration — its prices, labels, commission assignment and per-customer discounts — rather than carrying it over.
- **FR-008**: A merge MUST leave the kept record's own configuration entirely intact.
- **FR-009**: The outcome of a merge for a given record MUST NOT depend on what the kept record happens to already have.
- **FR-010**: A merge MUST NOT delete any record describing something that happened; only configuration may be discarded.
- **FR-011**: System MUST report, before a merge and on request, how many records of each kind refer to the duplicate, ordered largest first, with a total.
- **FR-012**: The report MUST cover exactly the kinds of record the merge acts on — neither fewer nor more — so the two cannot describe different operations.
- **FR-013**: Requesting the report MUST change nothing.
- **FR-014**: The report MUST refuse exactly the pairs a merge refuses, with the same distinction between an invalid pair and a missing record.
- **FR-015**: The report MUST be available to a user permitted to review merges without requiring the permission to perform one.
- **FR-016**: The report MUST be understood as what a merge touches, not as what it reassigns; the configuration it discards is included in the counts.

### Key Entities

- **Canonical product**: The record being kept. Its identity, its configuration and its own history all survive the merge unchanged.
- **Duplicate product**: The record being removed. It is understood to describe the same physical product, entered twice.
- **History reference**: A record describing something that happened to a product — a sale, a delivery, an invoice line, a stock movement, a commission earned. Carried over to the canonical.
- **Configuration reference**: A record describing how a product is set up — a price, a label, a commission assignment, a per-customer discount. Discarded with the duplicate.
- **Blast-radius item**: One kind of reference paired with how many of its records refer to the duplicate. Reported before the merge, never persisted.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every product in a populated catalog can be merged — zero products fail because of a relationship the merge does not handle, against 13,248 of 21,542 before this feature.
- **SC-002**: After a merge, zero records anywhere still refer to the deleted duplicate.
- **SC-003**: No record survives referring to a product that no longer exists, including through relationships the data store does not itself enforce (1,008 and 248 products carried such records before this feature).
- **SC-004**: The result of a merge can be stated in one sentence, without reference to which individual records the two products had in common.
- **SC-005**: An operator can obtain the scale of a merge, by kind and in total, before triggering it.
- **SC-006**: The kinds of record counted by the report and acted on by the merge are identical, and cannot drift apart.
- **SC-007**: Adding a new kind of record that refers to products requires no change to either the merge or the report for it to be covered by both.

## Assumptions

- The two records describe the same physical product entered twice. That is what makes carrying history over correct; if they are different products, the operation being asked for is not a merge.
- A merge cannot be undone. This is why the report exists, and why the report must describe the merge that would follow rather than an approximation of it.
- The canonical's configuration wins, in full. A label or per-customer discount that existed only on the duplicate is lost. This is a product decision taken in preference to an outcome that depends on which records the two sides had in common; it is called out in the changelog so a client can advise the operator to set such things on the canonical first.
- Fiscal history follows the merge rather than blocking it. A stamped document carries its own snapshot of what was invoiced, so the document's content does not change when the catalog record it points at moves — and that record is about to stop existing. Refusing to touch fiscal history instead would have blocked 10,434 products.
- Uniqueness rules that keep a product from holding two configuration records of the same kind are enforced by the data store, and are not all visible in the modelled data set. The design does not depend on discovering them: a rule missed by the split fails loudly and undoes the merge rather than corrupting anything.
- Merges are rare, administrator-initiated actions, so the cost of counting references twice — once for the report, once implicitly at merge time — is acceptable.
- Reference coverage is that of the modelled data set, as established in feature 006. References from unmodelled legacy tables are not carried over; they surface as a failed deletion that undoes the merge.
