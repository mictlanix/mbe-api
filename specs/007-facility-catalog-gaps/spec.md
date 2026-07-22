# Feature Specification: Facility Catalog Gaps

**Feature Branch**: `010-open-issues-fixes`

**Created**: 2026-07-21

**Status**: Implemented (PR #103)

**Input**: Three gaps reported by the mbe-ui team against spec `014-facility-scoped-catalogs`: a facility's taxpayer cannot be listed, searched or resolved (#100); a facility's address arrives as a bare key while every other reference arrives expanded (#101); and a point of sale accepts a warehouse belonging to a different facility (#102).

## Overview

Three independent gaps in the facility catalog surface, all reported by the team building
Facilities management in the client. Each was filed as non-blocking with a client-side
workaround already in place — the value here is deleting those workarounds and closing a rule
the client could only enforce for itself.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Choosing a facility's taxpayer (Priority: P1)

An operator creating or editing a facility must say which taxpayer entity issues its fiscal
documents. Today they type a 13-character tax identifier from memory with no list to pick
from, no search, and no confirmation it is registered until the save fails.

**Why this priority**: The only one of the three that blocks an operator mid-task. The other
two are cosmetic or preventive from the operator's point of view.

**Independent Test**: Search the taxpayer catalog by identifier or name, select a result,
and confirm an existing facility's taxpayer renders as a name rather than a bare identifier.

**Acceptance Scenarios**:

1. **Given** registered taxpayer entities, **When** the operator searches by part of a name, **Then** matching entities are returned for selection.
2. **Given** a facility with a taxpayer set, **When** its detail is displayed, **Then** the taxpayer can be rendered as human-readable text.
3. **Given** an operator with no rights over taxpayer data, **When** they attempt to read the catalog, **Then** they are refused.
4. **Given** a taxpayer entity that facilities or fiscal records still use, **When** an operator deletes it, **Then** the deletion is refused rather than orphaning those records.

---

### User Story 2 - Seeing a facility's address without a second lookup (Priority: P2)

An operator viewing the facility list sees each facility's address inline. The client should
not have to fetch addresses separately and stitch them together to avoid a request per row.

**Why this priority**: Affects every facility screen, but the client already works around it,
so it is a simplification rather than an unblocking.

**Independent Test**: Request the facility list and confirm each entry carries the full
address, with no further address requests needed to render the page.

**Acceptance Scenarios**:

1. **Given** facilities with addresses, **When** the list is requested, **Then** each carries its full address.
2. **Given** a single facility, **When** its detail is requested, **Then** it carries its full address.
3. **Given** a facility embedded inside another record (a warehouse, point of sale or cash drawer), **When** that record is returned, **Then** the embedded facility stays in its compact form and does not trigger address lookups.

---

### User Story 3 - Preventing a mismatched point of sale (Priority: P3)

A point of sale belongs to a facility and draws stock from a warehouse. The warehouse must
belong to the same facility. Today nothing stops a client from saving a mismatched pair, and
the rule holds only because one client chooses to enforce it.

**Why this priority**: No operator is currently blocked, because the client's own form
prevents it. It matters because that guarantee protects one client and nothing else.

**Independent Test**: Attempt to save a point of sale pairing a warehouse with a facility it
does not belong to, and confirm refusal; then change only the facility on an existing point
of sale and confirm the now-mismatched pair is also refused.

**Acceptance Scenarios**:

1. **Given** a warehouse belonging to facility A, **When** a point of sale is created for facility B naming that warehouse, **Then** the request is refused with a message identifying the mismatch.
2. **Given** an existing valid point of sale, **When** only its facility is changed, leaving a warehouse that now belongs elsewhere, **Then** the request is refused.
3. **Given** a warehouse that does not exist, **When** it is named on a point of sale, **Then** the request is refused as a missing reference rather than a mismatch.
4. **Given** a warehouse belonging to the same facility, **When** the point of sale is saved, **Then** it succeeds.

### Edge Cases

- Updating a point of sale without touching either the facility or the warehouse must not re-validate, so unrelated edits to an existing pair never fail.
- A taxpayer entity whose tax regime or postal code is not on file still returns, with those parts absent rather than the whole record failing.
- Expanding a facility's address must not disturb the compact facility embedded in other records — both are read from the same underlying record within one request.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: Users MUST be able to list taxpayer entities and search them by tax identifier or name.
- **FR-002**: Users MUST be able to retrieve a single taxpayer entity by its tax identifier.
- **FR-003**: Users MUST be able to create, amend and remove taxpayer entities.
- **FR-004**: System MUST present a taxpayer entity's tax regime and postal code in human-readable form, not as bare codes.
- **FR-005**: System MUST restrict all taxpayer entity access to users holding the taxpayer permission.
- **FR-006**: System MUST refuse to remove a taxpayer entity that facilities or fiscal records still reference.
- **FR-007**: System MUST record which certification provider a taxpayer entity uses, from a fixed set of known providers, rejecting unknown values.
- **FR-008**: System MUST return a facility's full address wherever the facility itself is the subject of the response.
- **FR-009**: System MUST keep the compact representation of a facility unchanged where it is embedded in another record, so embedding does not become more expensive.
- **FR-010**: System MUST reject a point of sale whose warehouse belongs to a different facility, on creation and on amendment.
- **FR-011**: System MUST validate the resulting pair when only one side is amended, so changing a point of sale's facility cannot leave a warehouse that no longer belongs to it.
- **FR-012**: System MUST distinguish a warehouse that does not exist from one that exists but belongs elsewhere.
- **FR-013**: System MUST NOT re-validate the pair when an amendment touches neither the facility nor the warehouse.

### Key Entities

- **Taxpayer entity**: The legally registered entity that issues fiscal documents, identified by its tax identifier. Referenced by every facility, and by fiscal records. Carries a name, a tax regime, a postal code and a certification provider.
- **Facility**: A store or production site. References a taxpayer entity, an address and a postal location.
- **Point of sale**: A till belonging to a facility, drawing stock from a warehouse. The two references must agree.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can select a facility's taxpayer from a searchable list instead of typing an identifier from memory.
- **SC-002**: An existing facility's taxpayer is displayed as readable text without an additional lookup by the operator.
- **SC-003**: A facility list of any length renders addresses with no address requests beyond the list request itself.
- **SC-004**: No mismatched facility/warehouse pairing can be stored through the interface, by any client.
- **SC-005**: The client's own address-stitching and warehouse-picker guards can be deleted with no loss of behaviour.
- **SC-006**: Removing a taxpayer entity in use is refused rather than leaving records pointing at nothing.

## Assumptions

- A warehouse shared across facilities is **not** legitimate — confirmed with the product owner, who chose enforcement over documenting it as intentional. If that ever changes, FR-010 and FR-011 are what to revisit.
- The taxpayer permission (a long-defined but previously unused permission) governs the new taxpayer surface. Because nothing used it before, no non-administrator holds it: it must be granted before any client can reach these endpoints. Recorded as a deployment prerequisite, not a defect.
- Expanding the address on the facility's own responses is a breaking change to that field's shape. The client is updated in step; no transitional form is offered, consistent with the project's existing practice on breaking changes.
- The compact facility form deliberately keeps its references as bare keys. Expanding them there would add lookups to every warehouse, point-of-sale and cash-drawer listing, which is the cost this feature is trying to remove, not add.
- Write access to taxpayer entities was not requested — the reporter asked for read only — but was included so the resource is not half-exposed. Recorded as a deliberate scope extension.
