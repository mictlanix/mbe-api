# Feature Specification: Constraint Violation Handling

**Feature Branch**: `013-integrity-error-handling`

**Created**: 2026-07-21

**Status**: Implemented (PR #108)

**Input**: User description: "Constraint violation handling: database constraint violations must reach the client as 409 Conflict, never 500. Three layers: a referential guard on delete that names what blocks it, duplicate-key pre-checks for unguarded unique constraints, and a global backstop. Policy: deletes are always hard deletes, never soft; a delete is refused while referenced and the client removes the references itself; archiving is an explicit user action via the status field; no hidden delete logic, with a single closed exemption for two pre-existing owned-child cascades. Corresponds to GitHub issue #107 and PR #108."

## Overview

Records the delete-and-conflict policy for the API and the behaviour that enforces it. Filed
after an audit found that ordinary client mistakes — deleting a record something still uses,
or reusing a code that already exists — were being reported to the operator as system
failures, indistinguishable from a genuine outage and carrying nothing they could act on.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Deleting a record that is still in use (Priority: P1)

An operator tries to remove a warehouse that points of sale still use. Instead of an opaque
failure, they are told the deletion cannot proceed and exactly what still depends on the
record, so they can go and clear those references themselves.

**Why this priority**: This is the most common destructive action in the catalogs, and the
one where a confusing failure is most costly — an operator cannot tell whether the record
was deleted, partially deleted, or left untouched.

**Independent Test**: Attempt to delete any record that other records reference, and confirm
the response identifies the blocking relationships and row counts, and that the record still
exists afterwards.

**Acceptance Scenarios**:

1. **Given** a warehouse referenced by three points of sale, **When** the operator deletes the warehouse, **Then** the request is refused as a conflict, the message names the referencing relationship and the count, and the warehouse remains.
2. **Given** a warehouse nothing references, **When** the operator deletes it, **Then** it is removed.
3. **Given** a record referenced through several different relationships, **When** the operator deletes it, **Then** the largest blockers are listed first and the remainder are summarised as a count.
4. **Given** a record referenced twice by the same kind of record through different roles (for example a transfer's origin and destination warehouse), **When** the operator deletes it, **Then** each role is reported separately rather than as one ambiguous total.

---

### User Story 2 - Reusing a code that already exists (Priority: P2)

An operator creating a warehouse, point of sale, cash drawer or vehicle enters a code that
another record already uses. They are told the code is taken, on the field they can fix,
rather than receiving a server error.

**Why this priority**: The most likely mistake on a create form. It is less severe than the
delete case because nothing is destroyed, but it is far more frequent.

**Independent Test**: Create a record reusing an existing code and confirm the response says
the code already exists; then edit an existing record without changing its code and confirm
it saves.

**Acceptance Scenarios**:

1. **Given** a warehouse with code `WH1`, **When** the operator creates another warehouse with code `WH1`, **Then** the request is refused as a conflict stating the code already exists.
2. **Given** an existing warehouse, **When** the operator saves it without changing its code, **Then** it saves — a record never conflicts with itself.
3. **Given** an existing warehouse, **When** the operator changes its code to one another warehouse already uses, **Then** the request is refused as a conflict.

---

### User Story 3 - Any other conflict never appears as a server fault (Priority: P3)

Whatever the operator does, a rejection by the data store is reported as a conflict with the
request, not as a failure of the system.

**Why this priority**: A safety net rather than a feature. It carries less information than
the first two stories, so it matters only where they do not apply — but it must always hold,
including for endpoints added later.

**Independent Test**: Trigger any constraint no service checks in advance and confirm the
response is a conflict carrying no internal detail.

**Acceptance Scenarios**:

1. **Given** a constraint that no up-front check covers, **When** it is violated, **Then** the response is a conflict, not a server error.
2. **Given** any such conflict, **When** the response is returned, **Then** it contains no table, column, or index names, and the underlying detail is recorded in the server log instead.

### Edge Cases

- A record that has never been saved has nothing referencing it, and reports no blockers.
- A record whose only referencing rows are its own owned children (see Assumptions) is always deletable.
- Referencing records that live outside the modelled data set still block the delete, but produce the generic conflict rather than a named one.
- A blocker list long enough to be unreadable is truncated to the largest few, with the rest summarised as a count.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: System MUST refuse to delete a record while any other record still references it.
- **FR-002**: System MUST report a refused delete as a conflict, never as a server fault.
- **FR-003**: System MUST name the blocking relationships and the number of blocking records, so the client can identify what to remove.
- **FR-004**: System MUST distinguish blockers that arrive through different relationships from the same kind of record, rather than merging them into one total.
- **FR-005**: System MUST order reported blockers by size, largest first, and summarise the remainder as a count when the list exceeds five entries.
- **FR-006**: System MUST leave the target record unchanged when a delete is refused.
- **FR-007**: System MUST cover new relationships automatically as the data model grows, without requiring a per-record list to be maintained by hand.
- **FR-008**: System MUST reject creating or updating a record whose unique identifying code duplicates an existing record's, reporting it as a conflict that names the field.
- **FR-009**: System MUST NOT treat a record's own value as a duplicate of itself when that record is being updated.
- **FR-010**: System MUST report every remaining data-store rejection as a conflict, so no constraint violation can present as a server fault.
- **FR-011**: System MUST NOT disclose internal data-store detail — table, column, or index names — in any conflict response.
- **FR-012**: System MUST record the underlying detail of an unchecked conflict where operators can diagnose it.
- **FR-013**: System MUST delete records outright when a delete succeeds; it MUST NOT substitute a hidden state change for removal.
- **FR-014**: System MUST NOT remove any record the client did not ask to remove, except for the owned children named in the Assumptions.
- **FR-015**: Users MUST be able to archive a record as an explicit action distinct from deleting it, and archiving MUST NOT occur as a side effect of a delete.

### Key Entities

- **Blocking reference**: A relationship through which one kind of record points at another, together with the number of records currently using it. Reported to the client so it can be cleared.
- **Owned child**: A record with no lifecycle of its own, existing only as part of its parent. Removed with the parent and never counted as a blocker. The set is fixed and closed — see Assumptions.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: No constraint violation reachable through the API is reported as a server fault — measured as zero such responses across the full catalog surface.
- **SC-002**: An operator refused a deletion can identify what to remove from the response alone, without consulting a developer or the data model.
- **SC-003**: Deleting a record that nothing references continues to succeed, with no new failures introduced on the existing surface.
- **SC-004**: Re-saving an unchanged record never fails as a duplicate.
- **SC-005**: Adding a new kind of record that references an existing one requires no change to the deletion logic for that record to be protected.
- **SC-006**: Conflict responses disclose nothing about the internal data model.
- **SC-007**: Behaviour of every existing endpoint is otherwise unchanged — the published interface contract is identical before and after.

## Assumptions

- Every record that other records reference is deleted rarely and by an administrator, so the cost of counting references at delete time is acceptable in exchange for a useful message.
- The client is capable of removing the referencing records itself; the API does not offer to do it. This follows the product owner's decision that deletes are never cascading and never silently soft.
- Archiving already exists as a lifecycle state on every affected record, so refusing a delete leaves the operator a supported alternative rather than a dead end.
- Two owned-child relationships predate this policy and are exempt from FR-014 as a closed list: a product's prices, and a user's settings and privileges. They have no independent lifecycle, and the user's are unreachable through the API — without the exemption, no user could ever be deleted. Any further exemption requires its own decision.
- Reference detection covers the modelled data set. A small number of legacy tables are not modelled; those are caught by the general rule in FR-010 instead of the named one in FR-003, which is a deliberate trade rather than a gap to close.
- Codes are the only uniqueness rule surfaced to operators on the affected records; other unique constraints are covered by FR-010.
