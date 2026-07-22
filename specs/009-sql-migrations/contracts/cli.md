# Contract: `app.db.migrate` command surface

The only external interface this feature exposes. Invoked from a repository checkout;
targets the database named by `settings.database_url` (FR-013). No connection flags.

```
uv run python -m app.db.migrate [command]
```

Implemented with `argparse`. Three commands, no global options.

---

## `migrate` (no command) — apply pending migrations

Applies every discovered migration with no ledger row, in ascending prefix order (FR-006).

**Behaviour**
1. Discover and validate `migrations/*.sql`; abort on duplicate prefix or non-conforming name.
2. `CREATE TABLE IF NOT EXISTS schema_migrations`.
3. Read the ledger; warn on `ALTERED`/`MISSING` rows but continue (research decision 4).
4. For each pending migration in order: split into statements, execute each, then insert the
   ledger row. Stop immediately on the first failure (FR-009).

**Output — up-to-date database (FR-008)**
```
Database is up to date (3 migrations applied).
```

**Output — work to do**
```
Applying 006_facility_logo_nullable ... ok (2 statements)
Applied 1 migration.
```

**Output — failure (stderr), exit 1**
```
Applying 007_add_widget ... FAILED

  file:      migrations/007_add_widget.sql
  statement: 3 of 5
  sql:       ALTER TABLE `widget` ADD COLUMN `color` VARCHAR(32) NOT NULL
  error:     (1054, "Unknown column 'widget' in 'field list'")

MariaDB does not roll back DDL. Statements 1-2 of this file have been applied and
007_add_widget was NOT recorded. Inspect the database before re-running.
```

The trailing warning is not decoration — it is the only thing standing between an operator
and a silently half-migrated schema.

---

## `migrate status` — report applied vs pending (FR-010)

Read-only. Never writes, not even the ledger table.

```
$ uv run python -m app.db.migrate status

  applied  004_facility_rename          2026-07-18 09:14:02
  applied  005_unified_entity_status    2026-07-18 09:14:05
  pending  006_facility_logo_nullable

1 pending migration.
```

If the ledger table does not exist yet, every migration reports `pending` and the summary
notes that the database has never been migrated.

Drift is called out inline and summarised at the end:
```
  ALTERED  005_unified_entity_status    2026-07-18 09:14:05  (file changed since it was applied)
  MISSING  003_old_thing                2026-05-02 11:00:00  (recorded but no file on disk)
```

---

## `migrate mark <version>...` — record without executing (FR-011)

For migrations already applied by hand before this feature existed — specifically `mbe_demo`.

```
$ uv run python -m app.db.migrate mark 004_facility_rename 005_unified_entity_status
Marked 004_facility_rename as applied (not executed).
Marked 005_unified_entity_status as applied (not executed).
```

**Rules**
- Each `<version>` must match a discovered migration file; unknown versions abort the whole
  command before writing anything.
- Marking an already-marked version is an error, not a no-op — it means the operator's mental
  model is wrong and they should look.
- No bulk/baseline form, by design (research decision 5).

---

## Exit codes

| Code | Meaning |
|---|---|
| `0` | Success — including "nothing to do" |
| `1` | Any failure: migration error, duplicate prefix, non-conforming filename, unknown version passed to `mark`, connection failure |

Drift warnings alone never change the exit code.

---

## Non-goals

Deliberately absent, each traceable to a spec assumption:

- **No `downgrade`/`rollback` command.** Rollback files are applied by hand.
- **No migration generation or autogenerate.** That was Alembic's job and nobody used it.
- **No `--database` / `--url` flag.** Exactly one source of truth: `settings.database_url`.
- **No application-startup hook.** Migrations are an operator action, never a side effect
  of booting the API.
