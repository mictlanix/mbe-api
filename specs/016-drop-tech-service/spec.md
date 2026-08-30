# Feature Specification: Retire Technical Service and Vehicle Service Orders

**Feature Branch**: `016-drop-tech-service`
**Created**: 2026-08-30
**Status**: Draft
**Input**: Follow the monolith's drop of seven abandoned tables (mictlanix/mbe#37) through this API

## Context

Seven tables were dropped from the deployment on 2026-08-30 by the monolith that owns them, in
`Schema/changes/mbe-26.08.sql`, and the change has been applied and verified against both
databases. Two modules went with them: technical service (five tables) and vehicle service orders
(two). The same migration deleted the permission rows for the four menu entries behind those
screens.

This API never exposed either module. It carries them only as mapped tables, dictionary entries and
a checked-in schema dump — all of which now describe tables that do not exist. Nothing here is a
behaviour change for any caller; the whole feature is bringing this repository's description of the
database back into agreement with the database.

The urgency is not cosmetic. This repository derives "what the deployed schema looks like" from its
own checked-in dump plus its own migration files, and two standing checks compare the code against
that derivation. A drop performed outside those files is invisible to it, so today both checks are
comparing against a schema that has seven tables the deployment does not — and they pass, which is
the problem. Every future schema check is measured against that stale picture until it is corrected.

## User Scenarios & Testing

### User Story 1 - The repository stops describing tables that no longer exist (Priority: P1)

An engineer reading this repository's documentation or models to answer "what is in the database?"
gets an answer that matches the database. Today they are told about seven tables that were dropped,
in three separate places that all agree with each other and disagree with reality.

**Why this priority**: It is the whole point of the change and the only part with a correctness
consequence beyond tidiness — the standing schema checks are only as good as the picture they
compare against, and a wrong picture makes them silently weaker for every unrelated change that
follows.

**Independent Test**: Search the repository for each of the seven table names. Every remaining hit
is either historical record (a changelog entry, this spec) or nothing at all. Run the schema checks
and confirm they now derive a table list with none of the seven in it.

**Acceptance Scenarios**:

1. **Given** the seven tables are gone from the deployment, **When** the repository's live-schema
   derivation is asked which tables exist, **Then** none of the seven appears.
2. **Given** an engineer opens the data dictionary, **When** they look for any of the seven tables,
   **Then** there is no section describing it and no note promising a future drop.
3. **Given** the model layer is loaded, **When** the set of mapped tables is enumerated, **Then**
   none of the seven is mapped.
4. **Given** the documentation check that requires every live table to be documented, **When** it
   runs, **Then** it passes with no waiver naming any of the seven.

---

### User Story 2 - Provisioning an account stops re-creating the deleted permission rows (Priority: P1)

An administrator creates a user, or applies a permission template to an existing one. The account
ends up with permission rows for the menu entries this system actually has — and not for the four
that were deleted along with their screens.

**Why this priority**: This is the one place where leaving the stale description in place actively
undoes the monolith's migration. Account provisioning writes one permission row per known menu
entry, so for as long as this API still believes those four entries exist, the very next account
created or template applied puts the deleted rows back. It is P1 for the same reason as Story 1 and
independently testable from it.

**Independent Test**: Provision an account with no template and count its permission rows, then
confirm none of them is for one of the four retired entries.

**Acceptance Scenarios**:

1. **Given** an account is created, **When** its permission rows are counted, **Then** the count
   equals the number of menu entries this system defines, and none of the four retired entries is
   among them.
2. **Given** an existing account that still holds rows for the four retired entries, **When** a
   permission template is applied to it, **Then** those rows are removed as part of the normal
   full-replace, with no special-case deletion written for them.
3. **Given** a permission template that grants a retired entry, **When** it is applied, **Then** the
   grant is refused or ignored rather than written, consistent with how the system already treats a
   grant naming an entry it does not define.
4. **Given** the four retired entries, **When** the surviving neighbouring entries are inspected,
   **Then** each keeps its own identifier unchanged.

---

### User Story 3 - The permission matrix width is stated once and stays true (Priority: P2)

An engineer changing anything near permissions sees one authoritative number for how wide the
permission matrix is, and the tests that assert on it agree with the code that produces it.

**Why this priority**: Correctness follows from Stories 1 and 2; this is about not leaving a stale
constant repeated in four places to be tripped over later. It is P2 because the system is already
correct once Story 2 lands — this is the maintainability half.

**Independent Test**: Change the set of menu entries and confirm exactly one assertion has to move,
in a place that names why.

**Acceptance Scenarios**:

1. **Given** the four entries are retired, **When** the matrix-width assertion runs, **Then** it
   states the new width and passes.
2. **Given** the documentation that explains the matrix width, **When** it is read, **Then** it
   states the new width and no longer the old one.
3. **Given** the retired identifiers, **When** future entries are added, **Then** the retired
   numbers are not reused for a different meaning.

---

### Edge Cases

- What happens to an account that still carries a row for one of the four retired entries? Nothing
  should break; the row is surplus and must be removed by the existing full-replace path rather than
  by a deletion written for this feature.
- What if a permission template stored in this system still grants one of the four retired entries?
  Applying it must not fail, and must not write the grant.
- What happens if the checked-in schema dump is corrected but a stale record of the drop is not, or
  vice versa? The live-schema derivation must not end up counting a table twice or resurrecting one.
- The check that every mapped column exists in the schema must not start passing vacuously because
  both the model and the table disappeared — it should simply have seven fewer tables to check.
- A test database is built from the mapped models rather than from the deployment; it must build and
  run with the seven tables absent.
- The changelog and this specification will keep naming the seven tables. Any check that scans the
  repository for their names must not treat historical record as a live reference.

## Requirements

### Functional Requirements

- **FR-001**: The system MUST NOT map any of the seven dropped tables, and the module that maps them
  MUST be removed in full rather than reduced.
- **FR-002**: The system MUST remove the four retired menu entries from the catalog of menu entries
  it defines, so that no code path treats them as existing.
- **FR-003**: The system MUST NOT itself delete permission rows for the retired entries. Removal of
  any surviving rows MUST happen through the existing full-replace behaviour, which already discards
  rows naming an entry the catalog does not define.
- **FR-004**: The system MUST NOT reuse or renumber the four retired identifiers, and MUST NOT
  change the identifier of any surviving entry.
- **FR-005**: The data dictionary MUST NOT contain a section for any of the seven tables, nor any
  note describing them as pending removal.
- **FR-006**: The documentation completeness check MUST NOT carry a waiver naming any of the seven
  tables or their columns.
- **FR-007**: The repository's derivation of the deployed schema MUST NOT include any of the seven
  tables, so that both standing schema checks compare against the schema as it now stands.
- **FR-008**: The stated width of the permission matrix MUST be consistent everywhere it appears —
  in the code that produces it, in the prose that explains it, and in every assertion that pins it.
- **FR-009**: The change MUST NOT alter the behaviour of any endpoint, and MUST NOT require a
  database migration from this repository, the drop having already been applied by the system that
  owns those tables.
- **FR-010**: The full test suite MUST pass with no reference to the seven tables remaining outside
  historical record.

**FR-007 resolved (review gate, 2026-08-30): correct the checked-in schema dump.** The seven table
definitions are removed from it, so the derivation stays automatic — dump plus this repository's own
migrations — with no new file and no list to maintain.

Two alternatives were weighed and rejected. Recording the drop as a migration file here would keep
the dump untouched, but it re-states a change another system owns and adds a migration, which the
originating issue ruled out and SC-008 pins. An exclusion list read by the two checks would be
honest about provenance, but it is a hand-maintained list of exactly the shape this project has been
burned by before, and it grows every time this situation recurs.

The accepted cost: the dump is output from a real database at a moment in time, and removing tables
that existed at that moment makes it no longer a faithful record of it. That is judged acceptable
because nothing reads the dump as history — both checks read it only as the baseline to replay
migrations onto, and its value is entirely in describing the schema as it now stands.

### Key Entities

- **Retired table**: one of the seven dropped from the deployment — five belonging to technical
  service, two to vehicle service orders. Each currently has a mapped model, a dictionary section,
  and a definition in the checked-in schema dump.
- **Menu entry**: one addressable area of the legacy application, identified by a stable number.
  Four are retired here. The set of them defines the width of the permission matrix, and an account
  holds one permission row per entry.
- **Permission row**: an account's mask for one menu entry. Accounts are dense — one row per entry —
  while templates are sparse, naming only what they grant.
- **Live-schema derivation**: this repository's answer to "what does the deployed database look
  like?", assembled from the checked-in dump and the repository's own migration files, and the
  baseline both standing schema checks compare against.

## Success Criteria

### Measurable Outcomes

- **SC-001**: Zero of the seven retired tables appear in the repository's live-schema derivation.
- **SC-002**: Zero mapped models reference a retired table.
- **SC-003**: Zero dictionary sections and zero pending-removal notes reference a retired table.
- **SC-004**: Zero waivers in the documentation completeness check reference a retired table.
- **SC-005**: A newly provisioned account holds exactly one permission row per defined menu entry,
  and zero rows for the four retired entries.
- **SC-006**: The stated matrix width is identical in every location that states it, and matches the
  count the code produces.
- **SC-007**: The full test suite passes, with no test skipped or removed to achieve it other than
  those whose subject no longer exists.
- **SC-008**: Zero database migrations are added by this feature.
- **SC-009**: Every reference to a retired table remaining in the repository is historical record —
  a changelog entry or a specification — and none is a live dependency.

## Assumptions

- The drop is final. The originating issue records an explicit decision not to export the rows
  before dropping them, so no recovery path is expected of this feature.
- The four retired menu entries are retired permanently. Their identifiers are treated the way this
  project has treated other retired identifiers: left unused rather than recycled, because the
  numbers are shared with the legacy system and shifting them is worse than leaving a gap.
- Removing the four entries narrows the permission matrix, and that narrowing is the intended
  observable outcome rather than a side effect to be compensated for.
- The existing full-replace behaviour is sufficient to clear surviving permission rows on accounts,
  so no data-cleanup step is in scope. The deployment's rows were already deleted by the migration
  that dropped the tables; this covers only any that reappear before the change ships.
- No consumer of this API depends on the four retired entries being present in the permission
  matrix, since neither module was ever exposed by this API.

## Verbatim Constraints

Retired tables:

- `tech_service_receipt`
- `tech_service_receipt_component`
- `tech_service_report`
- `tech_service_request`
- `tech_service_request_component`
- `vehicle_service_order`
- `service_order_detail`

Retired menu entry identifiers: `58`, `64`, `65`, `90`

Surviving neighbours that must keep their identifiers: `88`, `89`, `91`

Originating change: `mictlanix/mbe#37`, `Schema/changes/mbe-26.08.sql`
