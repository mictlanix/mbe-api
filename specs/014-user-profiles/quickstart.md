# Quickstart: User Profiles as Permission Templates

**Feature**: `014-user-profiles` | **Date**: 2026-08-12 | **Plan**: [plan.md](./plan.md)

Nine validation scenarios plus a pre-migration gate. Each scenario proves one spec requirement group
and is runnable independently.

## Prerequisites

```bash
uv sync
uv run ruff check app/ migrations/ tests/     # must be clean before and after
uv run pytest -q                              # baseline: 1749 passed, 6 skipped
                                              # after this feature: 1844 passed, 10 skipped
                                              # (+4 skips are test_model_schema correctly skipping
                                              #  nullability checks for tables absent from the dump)
```

No live database is needed for the test suite — `tests/api/` overrides dependencies, `tests/unit/`
mocks the session, and `tests/integration/` builds a SQLite schema from the model metadata.

---

## Gate 0 — Audit: RUN, on `mbe_dev`, 2026-08-12

**Satisfied.** Results below; full tabulation in research R0. Re-run against any *other* target
database before applying `014` there, since only `mbe_dev` was measured.

```sql
-- Q1: duplicate rows for the same (user, object)?  → ZERO ROWS ✅
SELECT `user`, `object`, COUNT(*) c FROM `access_privilege`
GROUP BY `user`, `object` HAVING c > 1;

-- Q2: matrix completeness per user  → 31 users, fewest 106, most 110
SELECT COUNT(*) users, MIN(c) fewest, MAX(c) most
FROM ( SELECT `user`, COUNT(*) c FROM `access_privilege` GROUP BY `user` ) t;

-- Q3: pre-existing profile/role table?  → NONE ✅
SELECT table_name FROM information_schema.tables
WHERE table_schema = DATABASE()
  AND (table_name LIKE '%profile%' OR table_name LIKE '%role%');

-- Q4: THE FINDING — object values in use that SystemObject does not define
SELECT `object`, COUNT(*) rows_, SUM(`privileges` > 0) granted, COUNT(DISTINCT `user`) users
FROM   `access_privilege` WHERE `object` IN (70, 104, 105, 107)
GROUP  BY `object` ORDER BY `object`;
```

| Result | Value | Consequence |
|---|---|---|
| Duplicates | **0** | R2's latent 500 is not live. No repair needed |
| Users | **31** (3 admins, 0 unlinked) | — |
| `access_privilege` rows | **3,355** | — |
| Rows per user | **106 min, 110 max** | No user has a *gap* in the known matrix; the excess is Q4 |
| Distinct objects in use | **110**, range 0–113 | Enum defined only 106 → **4 unknown**, named below |
| Unknown-object rows | **88**, of which **28 carry a grant**, on **13 of 31 accounts** | Resolved — research R9 |

**Q4's four values, named from `../mbe/Model/Constants/SystemObjects.cs`**:

| Object | Legacy declaration | Disposition |
|---|---|---|
| 70 | `//SalesOrderShipments` — commented out | **Deleted** by the first apply per account |
| 104 | `//SearchAllSalesOrderFromAllUsers` — commented out | **Deleted** |
| 105 | `//SearchAllSalesOrderFromAllStores` — commented out | **Deleted** |
| 107 | `ProductionSites` — **active** | **Added to `SystemObject`** as `PRODUCTION_SITES` |

So the apply is a blanket delete and the matrix is **107 wide**. The 59 rows on the three retired
objects (24 of them grants) are removed lazily, per account, as profiles are applied — not by the
migration. Exporting them first was offered and declined: the evidence they are retired is the legacy
source itself.

Verify the enum diff still holds before implementing, since it is the whole basis of that decision:

```bash
# Expect: exactly one value live in legacy and missing from SystemObject — 107 ProductionSites.
# Any other output means the legacy catalog moved and research R9 needs re-checking.
grep -cE '^\s*[A-Za-z]\w*\s*=\s*[0-9]+\s*,' ../mbe/Model/Constants/SystemObjects.cs   # expect 107
```

---

## Migration

```bash
# Apply, then verify structure
uv run python -m app.db.migrate                     # discovers 014 after 013
```

```sql
SHOW CREATE TABLE `user_profile`;                    -- name UNIQUE, utf8mb3_unicode_ci
SHOW CREATE TABLE `user_profile_privilege`;          -- FK → user_profile
SHOW COLUMNS FROM `user` LIKE 'profile';             -- int(11), YES null, FK
SELECT COUNT(*) FROM `user_profile`;                 -- 0 — profiles are authored, not seeded
SELECT COUNT(*) FROM `user` WHERE `profile` IS NOT NULL;  -- 0 — no account is migrated
```

**Idempotence**: re-applying `014` changes nothing — **verified on `mbe_dev` 2026-08-12**, all five
statements re-run as no-ops (warnings only: "already exists", "Duplicate column/key name"), with the
two tables still at two rather than duplicated. Note the runner records the version in
`schema_migrations`, so `python -m app.db.migrate` will report "up to date" and skip; re-running the
file's statements directly is what tests idempotence.

**Syntax note**: `ADD CONSTRAINT ... FOREIGN KEY IF NOT EXISTS` is MariaDB-specific and was verified
by parse probe against 10.11 before the file was applied — running each `ALTER` against a nonexistent
table returns 1146 (table absent) rather than 1064 (syntax error), which validates the grammar without
writing anything.

**Rollback** (`014_user_profiles_rollback.sql`): drops the column first, then
`user_profile_privilege`, then `user_profile` — FK order matters. Rolling back **discards every
profile and every recorded origin**; the copied `access_privilege` rows are untouched, so no user
loses a permission. That asymmetry is the design working: permissions are values, origins are
pointers.

```bash
uv run pytest tests/unit/test_migrate.py -q          # 014 discovered, ordered after 013,
                                                     # rollback not auto-applied
```

---

## Scenario 1 — Provision at creation (US1, FR-011)

```bash
# 1. Create a profile granting read on products (0) and create+read on sales orders (7)
curl -sX POST localhost:8000/api/v1/user-profiles -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"Cashier","privileges":[{"system_object":0,"privileges":2},{"system_object":7,"privileges":3}]}'

# 2. Create a user naming it
curl -sX POST localhost:8000/api/v1/users -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"user_id":"qstest1","password":"secret1","email":"q@e.com","employee_id":<valid>,"profile_id":1}'
```

**Expect**: `201`. The response's `privileges` array has **107 entries** — including object 107
`PRODUCTION_SITES`, which this feature adds. Object 0 has `privileges: 2`, object 7 has `privileges: 3`,
and **all 105 others have `privileges: 0`**. `profile_id: 1`, `profile_name: "Cashier"`.

> The 107-vs-3 asymmetry is the single most important thing to eyeball: the *profile* is sparse (3
> entries), the *user* is dense (107). Getting this backwards is the likeliest implementation error.

## Scenario 2 — Provision an existing account, and full replace (US1, FR-013)

```bash
# Give qstest1 full permissions on warehouses (4) by hand first
curl -sX PUT localhost:8000/api/v1/users/qstest1 -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"privileges":[{"system_object":4,"privileges":15}]}'

# Then apply Cashier, which does not mention warehouses at all
curl -sX POST localhost:8000/api/v1/user-profiles/1/apply/qstest1 -H "$AUTH"
```

**Expect**: object 4 comes back `privileges: 0`. The hand-granted permission is gone, because the
profile did not name it. This is FR-013 and spec US1 scenario 6 — the difference between "restrictive"
and "partial".

Also expect `session_version` to have incremented (FR-015).

**And the R9 assertion** — pick one of the 13 accounts holding a grant on a retired object, apply a
profile, then confirm the retired rows are gone and object 107 is present:

```sql
-- Retired objects: expect ZERO rows after an apply (research R9, decision 2)
SELECT `object`, `privileges` FROM `access_privilege`
WHERE  `user` = '<one of the 13>' AND `object` IN (70, 104, 105);

-- ProductionSites: expect exactly one row, mask 0 unless the profile granted it
SELECT `object`, `privileges` FROM `access_privilege`
WHERE  `user` = '<one of the 13>' AND `object` = 107;
```

This pair pins both halves of R9 — the deletion of dead objects and the adoption of the live one — and
is the single check that would catch a regression to either earlier version of research R3.

## Scenario 3 — Creation is atomic (US1, FR-011, research R8)

```bash
# A profile id that names nothing
curl -sX POST localhost:8000/api/v1/users -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"user_id":"qsghost","password":"secret1","email":"g@e.com","employee_id":<valid>,"profile_id":99999}'

# An inactive profile
curl -sX PUT localhost:8000/api/v1/user-profiles/1 -H "$AUTH" -d '{"status":1}' -H 'Content-Type: application/json'
curl -sX POST localhost:8000/api/v1/users -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"user_id":"qsghost2","password":"secret1","email":"g2@e.com","employee_id":<valid>,"profile_id":1}'
```

**Expect**: `404 Profile not found` and `409 Profile is not active`. Then, critically:

```bash
curl -s localhost:8000/api/v1/users/qsghost -H "$AUTH"    # 404
curl -s localhost:8000/api/v1/users/qsghost2 -H "$AUTH"   # 404
```

**Neither account exists.** A 4xx that left a user behind is the #154 failure shape and is the
specific thing this scenario exists to catch.

## Scenario 4 — Editing a profile changes nobody (US3, FR-014, SC-006)

```bash
# Reactivate, then broaden Cashier to grant delete on sales orders
curl -sX PUT localhost:8000/api/v1/user-profiles/1 -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"status":0,"privileges":[{"system_object":0,"privileges":2},{"system_object":7,"privileges":15}]}'

curl -s localhost:8000/api/v1/users/qstest1 -H "$AUTH"
```

**Expect**: `qstest1` still has `privileges: 3` on object 7. The edit did **not** propagate.
Re-applying then makes it 15. This is the copy semantics the whole feature rests on, and the part
administrators are most likely to assume works the other way.

## Scenario 5 — Find and re-provision (US3, FR-020, FR-021, SC-008)

```bash
curl -s "localhost:8000/api/v1/users?profile_id=1" -H "$AUTH"
```

**Expect**: exactly the accounts provisioned from profile 1, each row carrying
`profile_id: 1` and `profile_name: "Cashier"`. Accounts with no origin show `null` for both and are
absent from the filtered result.

**Also check the N+1 rule**: the page costs **one** query for profile names regardless of row count
(research R6 / contracts). Confirm with `echo=True` on the engine, or by asserting query count in the
integration test — not by inspection.

## Scenario 6 — Name uniqueness is case-insensitive (US2, FR-004, research R4)

```bash
curl -sX POST localhost:8000/api/v1/user-profiles -H "$AUTH" -H 'Content-Type: application/json' \
  -d '{"name":"cashier"}'
```

**Expect**: `409 Profile name already exists`, against the stored `"Cashier"`.

**This must pass in the SQLite integration layer too**, which is the whole reason `func.lower()` is
used instead of relying on the MariaDB collation. A test that passes only under MariaDB has not
tested the requirement.

## Scenario 7 — Deletion is refused while referenced (US2, FR-008, research R5)

```bash
curl -sX DELETE localhost:8000/api/v1/user-profiles/1 -H "$AUTH"
```

**Expect**: `409` naming `user` as the blocking table with a row count. Then:

```bash
curl -sX POST localhost:8000/api/v1/user-profiles -H "$AUTH" -d '{"name":"Unused"}' -H 'Content-Type: application/json'
curl -sX DELETE localhost:8000/api/v1/user-profiles/2 -H "$AUTH"    # 204
```

**Expect**: `204` for the unreferenced one. Confirm afterwards that `qstest1`'s 107 privilege rows
are **unchanged** — the refusal path must not have written anything.

## Scenario 8 — A profile applied to an administrator is inert (edge case)

```bash
curl -sX POST localhost:8000/api/v1/user-profiles/1/apply/<some_admin> -H "$AUTH"
```

**Expect**: `200`, the 107 rows written, origin recorded — and the administrator can still do
everything, because `deps.py:83` bypasses per-object checks for administrators. The permissions are
recorded and dormant. Verify the admin can still reach a route the profile denies; that is the
behaviour, not a bug.

## Scenario 9 — Nothing changed for accounts that never saw a profile (FR-024, FR-027, SC-005)

```bash
uv run pytest tests/api/test_auth.py tests/unit/test_references.py -q
uv run pytest tests/integration/ -q
```

**Expect**: green, **unmodified**. SC-005 is specifically that the existing permission suite passes
without being edited to accommodate this feature. If a pre-existing test needed changing, that is a
finding to report, not a test to fix.

---

## Final gates

```bash
uv run ruff check app/ migrations/ tests/     # zero violations (Principle VIII)
uv run pytest -q                              # all green, including the new files
uv run pytest tests/unit/test_model_schema.py -q   # new tables/column accepted via migration parsing
```

- `CHANGELOG.md` `[Unreleased] > Added` updated (Constitution: Development Workflow > Changelog).
- Gate 0 re-run against the target database if it is not `mbe_dev`, and the legacy-catalog grep still returns 107.
- No edit to `app/core/deps.py`. If enforcement changed, FR-024 was violated.
- `SystemObject` has **107** members and no `# 107 absent` comment remains; `docs/constants.md` has a
  row for 107 (research R9).
- No edit to `references.py`. If either shared guard needed a parameter, research R4/R5 was wrong.
