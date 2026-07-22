# Quickstart: Validating SQL Migrations

End-to-end validation of the acceptance scenarios in [spec.md](./spec.md). Run against a
**scratch database**, never `mbe` or `mbe_demo`.

## Prerequisites

- MariaDB 10.11 reachable with the credentials in `.env`
- `uv sync` completed after the `alembic` dependency was dropped
- A scratch database created from a pre-004 dump — i.e. one that still has the `store` table,
  since `004_facility_rename` is what renames it

```bash
mysql -e "CREATE DATABASE mbe_scratch"
mysql mbe_scratch < <pre-004-dump.sql>
export DATABASE_URL="mysql+aiomysql://user:password@localhost/mbe_scratch"
```

**Scenarios are independent and are not run in file order.** [tasks.md](./tasks.md) interleaves
them by user story: T017 runs 1–5 and 8, T019 runs 6, T026 runs 7, T031 tears down. Each
scenario states its own precondition and cleans up after itself, so scenarios 2–8 can be run
alone against a fully applied scratch database; Scenario 1 needs the pre-004 dump described
above.

---

## Scenario 1 — Fresh database reaches current schema (US1 #1, SC-001)

```bash
uv run python -m app.db.migrate status   # expect: all 3 pending, never migrated
uv run python -m app.db.migrate          # expect: applies 004, 005, 006 in that order
```

**Expected**: exit 0; output names the migrations in ascending order. Verify the schema
actually moved:

```bash
mysql mbe_scratch -e "SHOW TABLES LIKE 'store'"        # expect: empty (renamed by 004)
mysql mbe_scratch -e "DESCRIBE facility" | grep status # expect: a status column (005)
mysql mbe_scratch -e "SELECT version, applied_at FROM schema_migrations ORDER BY version"
```

The `store`→`facility` check is the one that matters most: it proves ordering was enforced
rather than accidentally correct.

---

## Scenario 2 — Second run is a no-op (US1 #2, SC-002)

```bash
uv run python -m app.db.migrate
```

**Expected**: `Database is up to date (3 migrations applied).`, exit 0, and the
`schema_migrations` row count is still 3 with unchanged `applied_at` values.

---

## Scenario 3 — Only the newest migration is applied (US1 #3)

```bash
mysql mbe_scratch -e "DELETE FROM schema_migrations WHERE version='006_facility_logo_nullable'"
uv run python -m app.db.migrate
```

**Expected**: output mentions **only** `006_facility_logo_nullable`; 004 and 005 are not
re-executed (re-running 005 would fail on an already-dropped column — a useful canary).

---

## Scenario 4 — Failure stops the run and records nothing (US1 #4, FR-009)

Create a deliberately broken migration:

```bash
cat > migrations/999_broken.sql <<'SQL'
ALTER TABLE `facility` ADD COLUMN `zzz_tmp` INT NULL;
ALTER TABLE `no_such_table` ADD COLUMN `x` INT NULL;
SQL

uv run python -m app.db.migrate; echo "exit=$?"
```

**Expected**: exit 1; stderr names the file, statement `2 of 2`, and the MariaDB error.
Then confirm the partial-application semantics are exactly as documented:

```bash
mysql mbe_scratch -e "SELECT * FROM schema_migrations WHERE version='999_broken'"  # expect: empty
mysql mbe_scratch -e "DESCRIBE facility" | grep zzz_tmp                            # expect: present
```

That combination — statement 1 applied, no ledger row — is the documented non-transactional
behaviour, not a bug. Clean up:

```bash
mysql mbe_scratch -e "ALTER TABLE \`facility\` DROP COLUMN \`zzz_tmp\`"
rm migrations/999_broken.sql
```

---

## Scenario 5 — Duplicate prefix is refused (FR-012)

```bash
touch migrations/006_something_else.sql
uv run python -m app.db.migrate; echo "exit=$?"
rm migrations/006_something_else.sql
```

**Expected**: exit 1, both colliding filenames named, no schema change and no ledger write.

---

## Scenario 6 — Authoring a new migration (US2, SC-007)

Following only the README, add `migrations/007_quickstart_probe.sql` containing a trivial
`ALTER TABLE`, then run `migrate`. **Expected**: it is picked up with no registration step
(FR-005). Remove it and its ledger row afterwards.

---

## Scenario 7 — Adopting an already-migrated database (FR-011, Phase D)

Rehearse on scratch before touching `mbe_demo`:

```bash
mysql mbe_scratch -e "DROP TABLE schema_migrations"
uv run python -m app.db.migrate mark 004_facility_rename 005_unified_entity_status 006_facility_logo_nullable
uv run python -m app.db.migrate status   # expect: all applied
uv run python -m app.db.migrate          # expect: up to date, nothing executed
```

**Expected**: no schema changes at any point. Re-running `mark` on an already-marked version
exits 1.

---

## Scenario 8 — Drift is reported but never blocks (spec Edge Cases, research decision 4)

With the scratch database fully applied:

```bash
# ALTERED: change an already-applied file
echo "-- touched" >> migrations/006_facility_logo_nullable.sql
uv run python -m app.db.migrate status   # expect: ALTERED on 006
uv run python -m app.db.migrate; echo "exit=$?"
git checkout migrations/006_facility_logo_nullable.sql

# MISSING: a ledger row with no file
mysql mbe_scratch -e "INSERT INTO schema_migrations VALUES ('003_ghost', REPEAT('0',64), UTC_TIMESTAMP())"
uv run python -m app.db.migrate status   # expect: MISSING on 003_ghost
uv run python -m app.db.migrate; echo "exit=$?"
mysql mbe_scratch -e "DELETE FROM schema_migrations WHERE version='003_ghost'"
```

**Expected**: both warnings appear on stderr, **both runs exit 0**, and neither changes the
schema. This is the only check that exercises the `checksum` column — the one piece of
complexity in this feature that goes beyond a literal FR.

---

## Purge verification (US3, SC-005, SC-006)

```bash
uv run python -c "import alembic"                       # expect: ModuleNotFoundError
grep -ril alembic . --exclude-dir={.venv,.git,images,.mypy_cache,specs} 
                                                        # expect: CHANGELOG.md only
ls migrations/                                          # expect: 5 .sql files, nothing else
ls scripts migrations/sql 2>&1                          # expect: No such file or directory
uv run pytest                                           # expect: matches pre-change baseline
uv run ruff check app/ migrations/ tests/               # expect: clean
uv run mypy app                                         # expect: clean
```

Capture the `uv run pytest` summary line **before** starting Phase C so SC-006's
"same results" claim is checkable rather than asserted.

## Teardown

```bash
mysql -e "DROP DATABASE mbe_scratch"
```
