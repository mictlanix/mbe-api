# Implementation Plan: SQL Migrations

**Branch**: `009-sql-migrations` | **Date**: 2026-07-21 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/009-sql-migrations/spec.md`

## Summary

Replace the never-used Alembic scaffolding with a ~150-line async runner that applies
hand-written SQL files from a single flat `migrations/` directory, tracking what each
database has received in a `schema_migrations` ledger table.

The runner lives at `app/db/migrate.py` and reuses the application's existing async engine
and settings — no new dependency, and `alembic` is removed. Three SQL files move into
`migrations/`: `scripts/facility_rename.sql` becomes `004_facility_rename.sql` (it must run
*before* 005, which already references the renamed `facility` table), and `migrations/sql/005*`
and `006*` move up one level with their rollbacks.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: SQLAlchemy 2.0 async + aiomysql (both existing). Runner uses only
stdlib beyond that (`argparse`, `hashlib`, `asyncio`, `pathlib`). **Removes** `alembic`.

**Storage**: MariaDB 10.11 — the database named by `settings.database_url`

**Testing**: pytest + pytest-asyncio (existing). Unit tests in `tests/unit/test_migrate.py`
for the pure-logic parts (statement splitting, file discovery, duplicate-prefix detection).

**Target Platform**: Developer/operator workstation with a checkout of the repository

**Project Type**: CLI utility inside an existing web-service codebase

**Performance Goals**: Not a concern. The corpus is 3 migrations; runs are interactive and
one-off. Correctness and clear failure reporting dominate.

**Constraints**: MariaDB DDL is **not transactional** — a failed migration cannot be rolled
back automatically. The runner must fail loudly and precisely (naming the file *and* the
failing statement) so an operator can finish or reverse by hand.

**Scale/Scope**: 1 new module (~150 lines), 1 test file, 6 SQL files relocated, 5 files
deleted, 3 files edited (README, `app/models/__init__.py` comment, CHANGELOG).

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Evidence |
|---|---|---|
| I. Simplicity First | ✅ PASS | One module, one table, three subcommands. No plugin points, no config, no migration *generation*. Net line count is negative once Alembic scaffolding is deleted. |
| II. Think Before Coding | ✅ PASS | Two decisions were resolved with the user before the spec (location, tracking). Three more are recorded in [research.md](./research.md) with rejected alternatives. |
| III. Surgical Changes | ✅ PASS | SQL file *contents* are moved verbatim, not rewritten. The only application-code edit is a two-line comment in `app/models/__init__.py` that exists solely to serve Alembic (FR-016). |
| IV. Goal-Driven Execution | ✅ PASS | Every phase below states a verify step; [quickstart.md](./quickstart.md) is the end-to-end acceptance run. |
| V. Reuse Over Rebuild | ✅ PASS | Reuses `app.db.session.engine` and `app.core.config.settings`. No new model, service, or dependency. **Removes** the `alembic` dependency. |
| VI. Async-First | ✅ PASS *(with noted deviation)* | Runner uses the existing `AsyncEngine`; `asyncio.run()` at the CLI boundary is the only sync seam. No blocking DB calls. **Deviation**: DB access goes through a raw `AsyncConnection`, not `AsyncSession`. The principle's letter says `AsyncSession`; its rationale is "don't block the event loop", which is fully honored. `AsyncSession` is an ORM unit-of-work and cannot express `CREATE TABLE`/`ALTER TABLE` batches — using it here would mean wrapping raw `text()` calls in a session that provides nothing. No constitution amendment sought; recorded for audit. |
| VII. Security by Default | ✅ N/A | No endpoints introduced. The runner is operator-invoked from a checkout and uses the same credentials the app already holds. |
| VIII. Ruff Compliance | ✅ PASS | New module and test are under `app/` and `tests/`, both already covered by `uv run ruff check app/ migrations/ tests/`. |

**Note on the lint gate**: the constitution's gate command includes `migrations/`. After this
change that directory holds only `.sql` files, so ruff no-ops there. The command stays valid
and needs no constitution amendment.

**Post-Phase-1 re-check**: ✅ All gates still pass. Design added no abstractions beyond what
Phase 0 specified. See [Complexity Tracking](#complexity-tracking) for the one item that goes
beyond a literal FR.

## Project Structure

### Documentation (this feature)

```text
specs/009-sql-migrations/
├── plan.md              # This file
├── spec.md              # Feature specification
├── research.md          # Phase 0 output — decisions + rejected alternatives
├── data-model.md        # Phase 1 output — ledger table + file naming contract
├── quickstart.md        # Phase 1 output — end-to-end validation run
├── contracts/
│   └── cli.md           # Phase 1 output — command surface, exit codes, output
├── checklists/
│   └── requirements.md  # Spec quality checklist
└── tasks.md             # Phase 2 output (/speckit-tasks — NOT created here)
```

### Source Code (repository root)

```text
migrations/                              # FR-001: flat, SQL only, no subdirectories
├── 004_facility_rename.sql              # ← moved from scripts/ (no rollback exists)
├── 005_unified_entity_status.sql        # ← moved from migrations/sql/
├── 005_unified_entity_status_rollback.sql
├── 006_facility_logo_nullable.sql
└── 006_facility_logo_nullable_rollback.sql

app/db/
├── session.py                           # unchanged — engine reused by the runner
├── base.py                              # unchanged
└── migrate.py                           # NEW — the runner (~150 lines)

tests/unit/
└── test_migrate.py                      # NEW — splitter, discovery, duplicate detection

DELETED:
├── alembic.ini
├── migrations/env.py
├── migrations/script.py.mako
├── migrations/README
├── migrations/sql/                      # emptied by the moves above
└── scripts/                             # emptied by the move above

EDITED:
├── pyproject.toml                       # drop the `alembic>=1.18.4` dependency
├── README.md                            # rewrite the "Database migrations" section
├── app/models/__init__.py               # comment no longer cites Alembic autogenerate
└── CHANGELOG.md                         # Added / Changed / Removed / Docs entries
```

**Structure Decision**: The runner lives at `app/db/migrate.py`, invoked as
`uv run python -m app.db.migrate`, **not** inside `migrations/`.

FR-001 and FR-002 govern where *schema-change SQL files* live; they are about the migration
corpus, not about executable code. Keeping `migrations/` purely declarative means a reader
can list it and see the entire schema history with nothing else in the way. Placing the
runner in `app/db/` also makes `import app.db.session` work without turning `migrations/`
into a Python package (`__init__.py`) — see [research.md](./research.md) decision 1, which
records why `python migrations/migrate.py` does not work at all.

## Phased Approach

Phases map to the spec's user stories and are independently verifiable.

**Phase A — Relocate the SQL corpus (US2 groundwork, FR-001/002/003/004)**
1. `git mv` the five files into `migrations/`, renaming `facility_rename.sql` → `004_facility_rename.sql`
   → verify: `ls migrations/` shows exactly 5 `.sql` files and no subdirectories; `git log --follow` still traces each file's history
2. Remove the now-empty `migrations/sql/` and `scripts/`
   → verify: neither path exists

**Phase B — Build the runner (US1, FR-006..FR-013)**
3. Write `app/db/migrate.py` per [contracts/cli.md](./contracts/cli.md)
   → verify: `uv run ruff check app/` and `uv run mypy app` both clean
4. Write `tests/unit/test_migrate.py` for the pure functions
   → verify: `uv run pytest tests/unit/test_migrate.py` passes
5. Run the [quickstart](./quickstart.md) against a scratch database
   → verify: acceptance scenarios 1–4 of US1 all observed

**Phase C — Purge Alembic (US3, FR-014..FR-017)**
6. Delete `alembic.ini`, `migrations/env.py`, `migrations/script.py.mako`, `migrations/README`;
   drop the dependency; `uv sync`
   → verify: `uv run python -c "import alembic"` fails; `grep -ri alembic` returns hits only in
   `CHANGELOG.md` and prior `specs/`
7. Update `README.md` and the `app/models/__init__.py` comment
   → verify: README documents only the SQL workflow
8. Full suite + lint
   → verify: `uv run pytest`, `uv run ruff check app/ migrations/ tests/`, `uv run mypy app` all clean
   and pytest results match the pre-change baseline (SC-006)

**Phase D — Adopt on existing databases (FR-011)**
9. On `mbe_demo`, mark the already-hand-applied migrations rather than executing them
   → verify: `... migrate status` shows all applied and `... migrate` reports the database current

## Complexity Tracking

> No constitution violations. One design element goes beyond a literal FR and is recorded here
> for transparency.

| Item | Why Needed | Simpler Alternative Rejected Because |
|------|-----------|--------------------------------------|
| `checksum` column on `schema_migrations` | The spec's Edge Cases require reporting when "a recorded migration's file is missing or altered". Detecting *altered* is impossible without a content hash. Costs one `hashlib.sha256` call per file. | Version-only ledger cannot distinguish an edited migration from an untouched one, silently hiding the most dangerous class of drift. Kept cheap: drift is a **warning**, never a hard stop, because an already-applied file's contents no longer affect what `migrate` will do. |
