# Feature Specification: SQL Migrations

**Feature Branch**: `009-sql-migrations`

**Created**: 2026-07-21

**Status**: Draft

**Input**: User description: "migrations using scripts (sql), purge alembic, unified location (migrations/sql, scripts/)"

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Apply pending schema changes to a database (Priority: P1)

An operator preparing a database (local, demo, or production) needs to bring its schema
up to date. They run a single command, and every schema change that has not yet been
applied to that database is applied, in the order it was authored. Changes that were
already applied are skipped. The operator does not have to know or remember which
changes a given database has seen.

**Why this priority**: This is the core value. Today the operator must manually determine
which hand-written SQL files a database has already received — knowledge that lives only
in a person's head and is already known to have diverged across databases.

**Independent Test**: Point the command at a database that has never been migrated and
confirm the schema ends up complete; run it a second time and confirm it reports nothing
to do and changes nothing.

**Acceptance Scenarios**:

1. **Given** a database with no migration history, **When** the operator runs the apply
   command, **Then** every migration is applied in order and each is recorded as applied.
2. **Given** a database that is fully up to date, **When** the operator runs the apply
   command again, **Then** no schema changes are made and the operator is told the
   database is current.
3. **Given** a database missing only the newest migration, **When** the operator runs the
   apply command, **Then** only that migration is applied and recorded.
4. **Given** a migration that fails partway, **When** the operator runs the apply command,
   **Then** the command stops at the failing migration, reports which one failed and why,
   does not record it as applied, and does not attempt later migrations.

---

### User Story 2 - Author a new schema change (Priority: P2)

A developer changing the data model writes a new SQL file describing the change, places
it in the one directory where all schema changes live, and names it so its order relative
to existing changes is unambiguous. No code generation, no model introspection, no
framework-specific scaffolding.

**Why this priority**: Authoring must be obvious for the convention to survive. It depends
on the apply mechanism (P1) existing, but a repository where new changes are easy to add
correctly is what keeps the history trustworthy.

**Independent Test**: Add a trivial new migration file following the documented
convention, run the apply command against an up-to-date database, and confirm exactly
that change is applied.

**Acceptance Scenarios**:

1. **Given** the documented convention, **When** a developer adds a new migration file,
   **Then** the apply command picks it up with no other registration step.
2. **Given** a new migration file, **When** the developer consults the project
   documentation, **Then** the naming, ordering, and rollback conventions are stated
   there explicitly.

---

### User Story 3 - Retire the unused migration framework (Priority: P3)

A developer opening the repository sees exactly one migration mechanism. The previously
configured migration framework — which was wired up but never produced a single versioned
migration — is removed entirely, along with its configuration, scaffolding, dependency,
and every reference to it in project documentation.

**Why this priority**: Purely a clarity and maintenance win; nothing depends on it. It is
last because removing it is only safe once the replacement (P1, P2) is in place.

**Independent Test**: Search the repository for references to the retired framework and
confirm none remain outside of historical records; confirm the project still installs,
starts, and passes its test suite.

**Acceptance Scenarios**:

1. **Given** the retired framework is removed, **When** the project's dependencies are
   installed from scratch, **Then** the framework is not installed.
2. **Given** the retired framework is removed, **When** a developer reads the project
   README, **Then** it documents only the SQL migration workflow.
3. **Given** the retired framework is removed, **When** the test suite runs, **Then** it
   passes unchanged.

---

### Edge Cases

- **Two databases at different versions**: `mbe_demo` has already received some changes
  by hand while another database has not. The first run against each must apply only what
  that database is missing, without assuming a shared starting point.
- **Migration already applied by hand**: A change was applied manually before this feature
  existed. Re-running it would fail or corrupt data. The operator MUST have a documented
  way to mark a migration as already applied without executing it.
- **Failure mid-file**: The database engine does not roll back structural changes, so a
  file that fails halfway leaves partial changes behind. The operator must be told exactly
  which file failed so they can inspect and finish or reverse it by hand.
- **Two developers add migrations with the same order number**: The system must surface
  the collision rather than pick an arbitrary order.
- **A recorded migration's file is missing or altered**: Reported as an inconsistency
  rather than silently ignored.
- **Rollback**: Reversing a change is a deliberate manual act using the paired rollback
  file, not an automated command.

## Requirements *(mandatory)*

### Functional Requirements

**Location and authoring**

- **FR-001**: All schema-change SQL files MUST live in a single top-level `migrations/`
  directory, flat, with no subdirectories.
- **FR-002**: The existing schema-change files under `migrations/sql/` and the standalone
  script in `scripts/` MUST be moved into that directory, preserving their content, and
  both `migrations/sql/` and `scripts/` MUST cease to exist.
- **FR-003**: Each migration file MUST carry an ordering prefix that determines the order
  it is applied in, and a descriptive name.
- **FR-004**: A migration MAY have a paired rollback file that reverses it. Rollback files
  MUST be identifiable as such and MUST NOT be treated as migrations to apply.
- **FR-005**: Adding a migration MUST require nothing beyond placing a correctly named
  file in the directory — no registry, index, or generated code to update.

**Applying**

- **FR-006**: The system MUST provide a single command that applies every migration a
  target database has not yet received, in ordering-prefix order.
- **FR-007**: The system MUST record each successfully applied migration in the target
  database itself, so that the applied set is a property of the database and not of the
  operator's memory.
- **FR-008**: Running the apply command against an up-to-date database MUST make no
  changes and MUST report that the database is current.
- **FR-009**: When a migration fails, the system MUST stop immediately, MUST NOT record
  the failed migration as applied, MUST NOT apply any later migration, and MUST report
  the failing file and the underlying error.
- **FR-010**: The system MUST provide a way to inspect which migrations a target database
  has and has not received.
- **FR-011**: The system MUST provide a way to record a migration as applied without
  executing it, for changes that were applied by hand before this feature existed.
- **FR-012**: The system MUST refuse to run and MUST report the problem when two
  migrations share an ordering prefix.
- **FR-013**: The apply command MUST target the database identified by the project's
  existing database configuration; no separate connection configuration is introduced.

**Purging the retired framework**

- **FR-014**: The retired migration framework's configuration file, scaffolding
  directory contents, and package dependency MUST be removed from the repository.
- **FR-015**: Project documentation MUST describe only the SQL migration workflow —
  where files live, how to name them, how to apply them, how to check status, and how to
  roll back.
- **FR-016**: Code comments that exist solely to serve the retired framework MUST be
  updated or removed; behavior of application code MUST NOT change.
- **FR-017**: Removal MUST NOT change the database schema and MUST NOT alter any
  application endpoint, model, or test outcome.

### Key Entities

- **Migration**: One forward schema change, expressed as SQL. Has an ordering prefix, a
  descriptive name, and optionally a paired rollback. Applied at most once per database.
- **Rollback**: SQL that reverses a specific migration. Paired to it by name. Never
  applied automatically.
- **Applied-migration record**: Per-database evidence that a given migration has been
  applied, and when. Lives in the target database.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: An operator can bring an empty database to the current schema with one
  command and no prior knowledge of migration history.
- **SC-002**: Running the apply command twice in a row produces zero schema changes on the
  second run, 100% of the time.
- **SC-003**: For any database, the set of applied migrations can be determined in one
  step, without consulting a person or an external document.
- **SC-004**: Every schema-change SQL file in the repository lives in exactly one
  directory; a repository-wide search finds zero schema-change SQL outside it.
- **SC-005**: A repository-wide search for the retired framework returns zero hits outside
  historical records (changelog, prior feature specs).
- **SC-006**: The full test suite passes before and after the change, with the same
  results.
- **SC-007**: A developer new to the repository can author and apply a new migration using
  only the project README.

## Assumptions

- The database in use does not roll back structural changes inside a transaction, so
  automatic recovery from a partially applied migration is out of scope. Failures are
  reported for manual resolution.
- Migrations are applied by a developer or operator from a checkout of the repository.
  Automated application during application startup or deployment is out of scope.
- Rollback remains a deliberate manual act. No automatic "downgrade one step" command is
  provided, matching how rollback files are already used.
- The two existing migrations (`005_unified_entity_status`, `006_facility_logo_nullable`)
  and the standalone `facility_rename` script are moved as-is; their SQL is not rewritten.
- The `mbe_demo` database has already received some of these changes by hand, so the
  mark-as-applied capability (FR-011) is needed on first adoption rather than
  hypothetically.
- The retired framework never produced a versioned migration, so there is no migration
  history to convert — only configuration and scaffolding to delete.
- Ordering prefixes continue the existing numeric convention, which currently tracks
  feature numbers; the numbering scheme itself is unchanged.
