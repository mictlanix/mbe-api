# Phase 0 Research: SQL Migrations

Two questions were settled with the user before the spec was written (unified location =
flat `migrations/`; tracking = runner + ledger table). This document resolves the five
remaining unknowns.

---

## Decision 1 — Runner lives at `app/db/migrate.py`, invoked via `python -m`

**Decision**: `app/db/migrate.py`, run as `uv run python -m app.db.migrate`.

**Rationale**:
- `migrations/` stays purely declarative — listing it shows the whole schema history and
  nothing else. That is the point of FR-001's "flat, no subdirectories".
- The runner needs `app.core.config.settings` and `app.db.session.engine` (Principle V,
  Reuse Over Rebuild). Living under `app/` makes those ordinary imports.
- Already covered by the constitution's lint gate (`ruff check app/ …`) and by `mypy app`.

**Alternatives considered**:
- **`migrations/migrate.py` run as `python migrations/migrate.py`** — *does not work*.
  Python puts the **script's** directory on `sys.path[0]`, not the CWD, so `import app`
  raises `ModuleNotFoundError`. This project has no build backend section in
  `pyproject.toml`, so `uv sync` does not install `app` into site-packages to compensate.
  Verified reasoning, not assumed.
- **`migrations/migrate.py` run as `python -m migrations.migrate`** — works, but requires
  adding `migrations/__init__.py`, putting a Python package marker inside the directory the
  spec asks to keep flat and SQL-only.
- **`scripts/migrate.py`** — rejected outright: the user's chosen layout deletes `scripts/`.

---

## Decision 2 — Split files into statements client-side

**Decision**: A small splitter strips `--` line comments and splits on `;` while tracking
single-quote, double-quote, and backtick state. Each statement is executed separately.

**Rationale**:
- aiomysql refuses multiple statements in one `execute()` unless the connection is opened
  with the `CLIENT.MULTI_STATEMENTS` flag. Setting that flag would mean **either** mutating
  the shared application engine's `connect_args` (unacceptable — it would change the app's
  own runtime posture and widen its SQL-injection blast radius) **or** building a second
  engine, which costs more than the splitter does.
- MariaDB DDL is not transactional. When a file fails halfway, knowing *which statement*
  failed is what lets an operator finish or reverse by hand. FR-009 only requires naming the
  file; statement-level reporting is strictly better for the same ~25 lines.
- Quote tracking is not gold-plating: today's corpus has string literals (`''`, `'~/%'`) and
  a naive `split(';')` would corrupt the first migration anyone writes containing a
  semicolon inside a string.

**Alternatives considered**:
- **`CLIENT.MULTI_STATEMENTS` + one `execute()` per file** — ~10 lines instead of ~35, but
  requires the second engine described above and reports failures only at file granularity.
- **Shell out to the `mysql` client** — no Python dependency at all, but adds an external
  binary requirement, bypasses `settings.database_url` parsing, and makes error capture and
  the ledger write awkwardly non-atomic with the apply.
- **`sqlparse` library** — a new dependency (Principle V) for a 25-line problem, on a corpus
  that has no procedures, triggers, or `DELIMITER` blocks.

**Known limitation, accepted**: the splitter does not support `DELIMITER` blocks (stored
procedures, triggers, functions). The corpus contains none, and adding one would be a
deliberate act. Documented in the README so the constraint is discoverable rather than
discovered at 2am.

---

## Decision 3 — `004_facility_rename.sql`, not a later number

**Decision**: `scripts/facility_rename.sql` is renumbered **004** — ahead of 005 and 006.

**Rationale**: Ordering is a correctness constraint here, not bookkeeping.
`005_unified_entity_status.sql` contains `ALTER TABLE `facility` ADD COLUMN `status``. That
table is *named* `facility` only because `facility_rename.sql` renamed it from `store`.
Running 005 before 004 on a fresh database fails outright. Git history confirms the real
order: `da6cbaa` (the rename) predates `f3c0705` (which introduced 005 as plain SQL).

004 is unoccupied — features 001–003 shipped no SQL — so this needs no renumbering of
existing files, preserving Principle III.

**Alternatives considered**:
- **Number it 007+ (chronological by adoption)** — would encode an execution order that is
  simply wrong and would break every fresh-database bootstrap.
- **Leave it unnumbered as a "one-off script"** — contradicts FR-002 and re-creates exactly
  the split-brain the feature exists to remove.

---

## Decision 4 — Ledger drift is a warning, not a hard stop

**Decision**: `schema_migrations` stores a `checksum`. `status` reports `ALTERED` and
`MISSING` rows; `migrate` prints a warning to stderr and **proceeds** with pending
migrations.

**Rationale**: Once a migration is applied, editing its file cannot change what the runner
does next — it will never be re-executed. Blocking unrelated pending work over it would be
a fussy tool. Surfacing it is the whole obligation. This also keeps `migrate` from
acquiring a `--force` escape hatch, which Principle I would rightly object to.

**Alternatives considered**:
- **Refuse to run on drift** — safer-sounding, but the safety is illusory (the altered file
  is already applied) and it forces a bypass flag into existence.
- **No checksum at all** — smaller, but leaves the spec's "or altered" edge case
  unimplemented and hides the most dangerous form of drift. See Complexity Tracking in
  [plan.md](./plan.md).

---

## Decision 5 — `mark` is explicit and per-version, not a bulk `--baseline`

**Decision**: `migrate mark <version>...` records the named migrations as applied without
executing them. No "mark everything currently on disk" shortcut.

**Rationale**: FR-011 exists for one concrete situation — `mbe_demo` received 004/005/006 by
hand before this feature existed. That is a one-time, small, known list. A bulk baseline
command is a loaded gun: run it against the wrong database and the schema silently never
gets built, with the ledger asserting otherwise. Naming versions explicitly makes the
operator state their claim.

**Alternatives considered**:
- **`--baseline` marking all discovered files** — one keystroke for the one time it is
  needed, permanent footgun thereafter.
- **Auto-detect applied migrations by inspecting the schema** — would require per-migration
  detection predicates; far more machinery than the problem deserves.
