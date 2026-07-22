---

description: "Task list for SQL Migrations"
---

# Tasks: SQL Migrations

**Input**: Design documents from `/specs/009-sql-migrations/`

**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/cli.md](./contracts/cli.md), [quickstart.md](./quickstart.md)

**Tests**: The spec does not request tests, and the constitution requires them only for new API
endpoints — this feature adds none. One unit-test task (T008) is included anyway because the
splitter and discovery logic are pure functions where a test is cheaper than a manual re-check.
Per the constitution, it is written **before** the code it covers.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies)
- **[Story]**: Which user story this task belongs to (US1, US2, US3)
- Include exact file paths in descriptions

## Path Conventions

Single project, repository root: `app/`, `migrations/`, `tests/` — per the Structure Decision in
[plan.md](./plan.md). The runner lives at `app/db/migrate.py`; `migrations/` holds only `.sql`.

**Note on `[P]` density**: most of User Story 1 lands in one file (`app/db/migrate.py`), so those
tasks are deliberately sequential. Marking them `[P]` would invite write conflicts for no gain.

---

## Phase 1: Setup

**Purpose**: Capture the baseline that SC-006 ("test suite passes before and after") is measured against

- [X] T001 Record the current `uv run pytest` summary line (counts + duration) into the scratch notes for this feature, before any file is moved — SC-006 is uncheckable without a pre-change baseline
- [X] T002 [P] Confirm a scratch database is available for validation per the Prerequisites in [quickstart.md](./quickstart.md), restored from a **pre-004 dump** (must still contain the `store` table)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: Relocate the SQL corpus into the single flat directory. Both US1 and US2 read from this layout, so nothing else can start until it exists.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete

- [X] T003 `git mv scripts/facility_rename.sql migrations/004_facility_rename.sql` — number is 004, **not** chronological; 005 does `ALTER TABLE \`facility\`` and that table only exists under that name because this script renames it (see [research.md](./research.md) decision 3)
- [X] T004 [P] `git mv migrations/sql/005_unified_entity_status.sql migrations/` and `git mv migrations/sql/005_unified_entity_status_rollback.sql migrations/`
- [X] T005 [P] `git mv migrations/sql/006_facility_logo_nullable.sql migrations/` and `git mv migrations/sql/006_facility_logo_nullable_rollback.sql migrations/`
- [X] T006 Remove the now-empty `migrations/sql/` and `scripts/` directories (FR-002) — verify: `ls migrations/` shows exactly 5 `.sql` files and no subdirectories, and `git log --follow migrations/004_facility_rename.sql` still traces the file's history through the rename

**Checkpoint**: `migrations/` is the single flat home for all schema-change SQL. File contents are unmodified (Principle III).

---

## Phase 3: User Story 1 - Apply pending schema changes (Priority: P1) 🎯 MVP

**Goal**: One command brings any database to the current schema, applying only what that database is missing and recording each success in a ledger.

**Independent Test**: Run against a fresh pre-004 scratch database → full schema built; run again → reports current and changes nothing.

### Tests for User Story 1

> **NOTE: Write these FIRST and confirm they FAIL before T009–T014** (constitution: Development Workflow → Testing)

- [X] T007 [P] [US1] Create `tests/unit/test_migrate.py` covering the pure functions: statement splitting (multi-statement file, `--` comment lines, semicolons inside single-quoted / double-quoted / backtick-quoted literals, trailing statement without a terminator), discovery (prefix parsed as **integer** so `010` sorts after `009`, `_rollback.sql` excluded, subdirectories not scanned), and validation (duplicate prefix rejected, non-numeric prefix rejected)
- [X] T008 [US1] Run `uv run pytest tests/unit/test_migrate.py` and confirm every test **fails** for the right reason (missing module, not a typo) before writing any implementation

### Implementation for User Story 1

- [X] T009 [US1] Create `app/db/migrate.py` with the statement splitter: strip `--` line comments, split on `;` while tracking `'`, `"`, and `` ` `` quote state, drop empty fragments. Do **not** support `DELIMITER` blocks — document the limitation in the module docstring ([research.md](./research.md) decision 2)
- [X] T010 [US1] Add migration discovery to `app/db/migrate.py`: `Path('migrations').glob('*.sql')` (not `rglob`), exclude `*_rollback.sql`, parse the leading digits as `int`, sort numerically; raise on duplicate prefixes naming **both** colliding files (FR-012) and on any `.sql` file without a numeric prefix ([data-model.md](./data-model.md) naming rules)
- [X] T011 [US1] Add ledger access to `app/db/migrate.py` reusing `engine` from `app/db/session.py`: `CREATE TABLE IF NOT EXISTS schema_migrations` per the DDL in [data-model.md](./data-model.md), read applied versions + checksums, insert a row with `UTC_TIMESTAMP()`. Compute checksums with `hashlib.sha256` over the file bytes. Do **not** add a SQLAlchemy model or register the table in `Base.metadata`
- [X] T012 [US1] Implement the default apply path in `app/db/migrate.py`: for each pending migration in order, execute statements one at a time, then insert the ledger row **only after the last statement succeeds** (FR-009). On failure, stop immediately, print file + statement index + SQL + driver error to stderr with the non-transactional-DDL warning from [contracts/cli.md](./contracts/cli.md), exit 1. Report `Database is up to date (N migrations applied).` when nothing is pending (FR-008)
- [X] T013 [US1] Implement `status` in `app/db/migrate.py` — read-only, must not create the ledger table; report `applied` / `pending` / `ALTERED` / `MISSING` per [data-model.md](./data-model.md), where drift is a warning that never changes the exit code ([research.md](./research.md) decision 4)
- [X] T014 [US1] Implement `mark <version>...` in `app/db/migrate.py`: validate every version against discovered files and abort before writing anything if any is unknown; error on an already-marked version rather than no-op; no bulk/baseline form ([research.md](./research.md) decision 5)
- [X] T015 [US1] Add the `argparse` entry point and `if __name__ == '__main__': asyncio.run(...)` guard to `app/db/migrate.py` so `uv run python -m app.db.migrate [status|mark]` matches [contracts/cli.md](./contracts/cli.md) exactly, including exit codes (0 success incl. no-op, 1 all failures)
- [X] T016 [US1] Run `uv run pytest tests/unit/test_migrate.py` and confirm all tests now pass; run `uv run ruff check app/` and `uv run mypy app` clean
- [X] T017 [US1] Execute [quickstart.md](./quickstart.md) scenarios 1–5 and 8 against the scratch database — fresh apply in order, no-op re-run, single-migration catch-up, deliberate mid-file failure (verify statement 1 applied **and** no ledger row), duplicate-prefix refusal, and drift reporting (`ALTERED`/`MISSING` warn without changing the exit code)

**Checkpoint**: US1 is fully functional. A database can be migrated from zero with one command. This is the MVP — the feature delivers value here even if US2/US3 slip.

---

## Phase 4: User Story 2 - Author a new schema change (Priority: P2)

**Goal**: A developer can add a migration by dropping a correctly named file in `migrations/`, using only the README.

**Independent Test**: Following only the README, add a trivial migration and apply it — no registration step anywhere.

- [X] T018 [US2] Rewrite the "Database migrations" section of `README.md`: where files live, the `NNN_name.sql` / `NNN_name_rollback.sql` convention, numeric-prefix ordering, the three commands from [contracts/cli.md](./contracts/cli.md), how to roll back by hand, and the `DELIMITER` limitation (FR-015, US2 acceptance #2)
- [X] T019 [US2] Execute [quickstart.md](./quickstart.md) scenario 6 — author `migrations/007_quickstart_probe.sql` following **only** the README, confirm `migrate` picks it up with no other change (FR-005, SC-007), then remove the file and its ledger row

**Checkpoint**: US1 and US2 both work. The convention is documented and proven by following the documentation rather than the source.

---

## Phase 5: User Story 3 - Retire the unused migration framework (Priority: P3)

**Goal**: Exactly one migration mechanism exists in the repository.

**Independent Test**: `import alembic` fails; `grep -ri alembic` finds hits only in historical records; the test suite matches the T001 baseline.

**Depends on**: T018 — both touch `README.md`, so run these after the migrations section is rewritten to avoid a conflicting edit.

- [X] T020 [P] [US3] Delete `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, and `migrations/README` (FR-014)
- [X] T021 [US3] Remove `"alembic>=1.18.4"` from `[project].dependencies` in `pyproject.toml`, then run `uv sync` — verify: `uv run python -c "import alembic"` raises `ModuleNotFoundError` and `uv.lock` no longer pins alembic
- [X] T022 [P] [US3] Update the comment at the top of `app/models/__init__.py` so it no longer cites Alembic autogenerate as the reason for the imports — keep the SQLAlchemy relationship-resolution rationale, which is still true. Behavior must not change (FR-016/FR-017)
- [X] T023 [US3] Scan `README.md` for any remaining Alembic references outside the section rewritten in T018 and remove them (FR-015)
- [X] T024 [US3] Run the purge verification block in [quickstart.md](./quickstart.md) — `grep -ril alembic` returns `CHANGELOG.md` only, `ls migrations/` shows 5 `.sql` files and nothing else, `scripts/` and `migrations/sql/` do not exist (SC-004, SC-005)
- [X] T025 [US3] Run `uv run pytest` and confirm the summary matches the T001 baseline exactly; run `uv run ruff check app/ migrations/ tests/` and `uv run mypy app` clean (SC-006, FR-017)

**Checkpoint**: All three stories complete. One mechanism, one directory, no dead framework.

---

## Phase 6: Polish & Cross-Cutting Concerns

- [X] T026 Rehearse adoption on the scratch database using [quickstart.md](./quickstart.md) scenario 7 — drop the ledger, `mark` all three versions, confirm `status` shows all applied and `migrate` executes nothing
- [X] T027 Determine what `mbe_demo` has **actually** received, before marking anything: `SHOW TABLES LIKE 'store'` (empty ⇒ 004 applied), `DESCRIBE facility` — a `status` column ⇒ 005 applied, `logo` nullable ⇒ 006 applied. Record the observed list; do not infer it from the CHANGELOG or from memory
- [X] T028 Adopt on `mbe_demo`: run `uv run python -m app.db.migrate mark <only the versions confirmed in T027>`, then `status` to confirm. Any version T027 did **not** confirm must be left pending so `migrate` applies it normally (FR-011, plan Phase D). **Do this only after T026 passes** — marking an unapplied migration is silent and permanent: the schema change never lands while `status` reports the database current
- [X] T029 [P] Update `CHANGELOG.md` `[Unreleased]`: **Added** the migration runner and `schema_migrations` ledger; **Changed** SQL migrations relocated to flat `migrations/` with `facility_rename` renumbered to 004; **Removed** Alembic (config, env, dependency); **Docs** README migration workflow rewritten (constitution: Development Workflow → Changelog)
- [X] T030 [P] Check `docs/` for references to `migrations/sql/`, `scripts/`, or Alembic and update any that are now wrong
- [X] T031 Drop the scratch database per the [quickstart.md](./quickstart.md) teardown

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1)**: No dependencies — T001 must happen before any file changes or the baseline is worthless
- **Foundational (Phase 2)**: Depends on Setup — **BLOCKS all user stories**
- **US1 (Phase 3)**: Depends on Phase 2. No dependency on US2/US3
- **US2 (Phase 4)**: Depends on Phase 2 for the layout and on US1 for the commands it documents
- **US3 (Phase 5)**: Depends on Phase 2; T020–T023 also depend on T018 (shared `README.md`)
- **Polish (Phase 6)**: Depends on US1 (T028 needs a working `mark`) and US3 (T029 describes the completed change)

### Story Independence

Genuine independence is limited here — this is one refactor, not three features:

- **US1** is independently testable and shippable (the MVP). It works whether or not Alembic is still installed
- **US2** is documentation over US1's command surface; it cannot be validated before US1 exists
- **US3** is pure deletion and depends on nothing in US1/US2 *functionally* — it could be done first — but is sequenced last so the replacement is proven before the old scaffolding goes

### Within User Story 1

Tests (T007–T008) before implementation. Then splitter → discovery → ledger → commands → CLI entry point, because each layer consumes the previous one. All land in `app/db/migrate.py`, so they are strictly sequential.

### Parallel Opportunities

- T004 and T005 (different file pairs)
- T020 and T022 (deletions vs. an app comment, different files)
- T029 and T030 (CHANGELOG vs. docs/)
- T002 can run alongside T001

---

## Parallel Example: Phase 2

```bash
# T004 and T005 touch disjoint file pairs — safe together:
Task: "git mv migrations/sql/005_unified_entity_status.sql and its rollback to migrations/"
Task: "git mv migrations/sql/006_facility_logo_nullable.sql and its rollback to migrations/"

# T003 runs separately — it also renames the file, so keep it visible in its own step.
```

---

## Implementation Strategy

### MVP First (User Story 1 Only)

1. Phase 1: Setup — capture the pytest baseline
2. Phase 2: Foundational — relocate the SQL corpus (**blocks everything**)
3. Phase 3: User Story 1 — the runner
4. **STOP and VALIDATE**: quickstart scenarios 1–5 on scratch
5. At this point the feature already delivers its core value; Alembic still being installed is untidy, not broken

### Incremental Delivery

1. Setup + Foundational → single flat `migrations/`
2. US1 → apply/status/mark work → **MVP**
3. US2 → README documents the convention → a second developer can author migrations
4. US3 → Alembic gone → one mechanism
5. Polish → `mbe_demo` adopted, CHANGELOG updated

### Risk Notes

- **T003's numbering is a correctness constraint, not bookkeeping.** Getting it wrong breaks every fresh-database bootstrap, and the failure surfaces far from the cause
- **T028 is the only task that touches a real database.** It is irreversible in practice — a wrongly marked database will never receive its migrations, and `status` will confidently claim it is current. T027 exists solely to make sure the list it is given is observed rather than assumed
- **T012's failure path is the feature's most important behavior.** MariaDB will not roll back DDL, so scenario 4 in the quickstart is a required check, not an optional one

---

## Notes

- `[P]` tasks = different files, no dependencies
- Commit after each task or logical group
- SQL file **contents** are never edited in this feature — only their locations and names
- Stop at any checkpoint to validate independently
