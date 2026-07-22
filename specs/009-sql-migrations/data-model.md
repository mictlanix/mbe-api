# Phase 1 Data Model: SQL Migrations

Two "models" exist here: a table in the target database, and a filename convention that acts
as the on-disk schema. Neither is a SQLAlchemy model — see the note at the end.

---

## `schema_migrations` (ledger table)

Created by the runner on first use via `CREATE TABLE IF NOT EXISTS`. Lives in the target
database, so the applied set travels with the database rather than with the operator
(FR-007).

```sql
CREATE TABLE IF NOT EXISTS `schema_migrations` (
  `version`    VARCHAR(255) NOT NULL,
  `checksum`   CHAR(64)     NOT NULL,
  `applied_at` DATETIME     NOT NULL,
  PRIMARY KEY (`version`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
```

| Column | Meaning | Notes |
|---|---|---|
| `version` | Migration filename without `.sql` — e.g. `005_unified_entity_status` | Primary key. Re-applying is impossible by construction. |
| `checksum` | SHA-256 hex digest of the file's bytes at apply time | Enables `ALTERED` detection (research decision 4). For rows created by `mark`, the digest of the file as it stands when marked. |
| `applied_at` | `UTC_TIMESTAMP()` at insertion | Audit trail; not used for ordering. |

**Write rule**: the ledger row is inserted **after** the migration's last statement succeeds,
in the same connection. If any statement fails, no row is written (FR-009) — so a partially
applied file shows as *pending*, which correctly signals that it needs human attention.

---

## Migration file naming convention

The filesystem *is* the migration registry (FR-005). No index, no manifest.

```
migrations/<NNN>_<descriptive_name>.sql             # a migration
migrations/<NNN>_<descriptive_name>_rollback.sql    # its optional reverse
```

| Rule | Detail | Enforced by |
|---|---|---|
| Ordering prefix | Leading digits, parsed as an integer. Zero-padded to 3 by convention. | Discovery sorts by the parsed integer, so `010` sorts after `009` — not lexically. |
| Uniqueness | No two migrations may share a prefix. | Startup check; the runner exits non-zero and names both files (FR-012). |
| Rollbacks | Filenames ending `_rollback.sql` are **excluded** from the apply set and never appear in the ledger (FR-004). | Discovery filter. |
| Flat | Subdirectories are not scanned. | `Path.glob('*.sql')`, not `rglob`. |
| Non-conforming files | A `.sql` file without a numeric prefix is an error, not a silent skip. | Startup check. |

### Current corpus

| Version | Rollback | Origin |
|---|---|---|
| `004_facility_rename` | — | moved from `scripts/facility_rename.sql` (research decision 3) |
| `005_unified_entity_status` | ✅ | moved from `migrations/sql/` |
| `006_facility_logo_nullable` | ✅ | moved from `migrations/sql/` |

Numbers track feature numbers where one exists; 004 predates that habit. The scheme is
unchanged by this feature.

---

## Derived state (computed, never stored)

For each discovered migration, `status` reports exactly one:

| State | Condition |
|---|---|
| `applied` | Ledger row exists, checksum matches the file |
| `pending` | No ledger row |
| `ALTERED` | Ledger row exists, checksum differs from the file → warning |
| `MISSING` | Ledger row exists with no corresponding file → warning |

`ALTERED` and `MISSING` are reported but never block (research decision 4).

---

## Why no SQLAlchemy model

`schema_migrations` is deliberately **not** added to `app/models/` or `Base.metadata`. It is
infrastructure describing the schema, not part of it — an ORM model would make the table
appear in the application's domain surface and in `docs/data-dictionary.md`, where it does
not belong. The runner touches it with three `text()` statements (`CREATE TABLE IF NOT
EXISTS`, `SELECT`, `INSERT`) over the existing async engine.
