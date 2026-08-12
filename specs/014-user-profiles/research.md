# Research: User Profiles as Permission Templates

**Feature**: `014-user-profiles` | **Date**: 2026-08-12 | **Spec**: [spec.md](./spec.md)

Nine decisions, **no open questions**. R9 was blocking and is resolved; R1 and R3 were both rewritten
after evidence contradicted them, and R3 twice. Superseded reasoning is kept visible rather than
replaced, so a reader can see which conclusions were earned and which were guesses that got corrected.

---

## R0 — The deployment database, measured

**Status**: audited 2026-08-12 against `mbe_dev`. The first version of this document recorded that
the audit *could not run* (socket absent); the socket came up and every query below is now measured
rather than derived from `docs/mbe_schema.sql`.

| Question | Result |
|---|---|
| Duplicate `(user, object)` rows | **none** |
| Users | **31** (3 administrators, 0 unlinked from an employee) |
| `access_privilege` rows | **3,355** |
| Privilege rows per user | min **106**, max **110** |
| Users with no privilege rows | **0** |
| Distinct `object` values in use | **110**, range 0–113 |
| Existing `profile`/`role` table | **none** |
| `user.profile` column | **does not exist** |
| Collation, `user` and `access_privilege` | `utf8mb3_unicode_ci` (case-insensitive) |

Two results are load-bearing and neither was predicted:

1. **Every user holds the complete known matrix** — `min = 106` means no account has a gap in the
   objects this API defines. R3's original "heals gaps" justification therefore describes a condition
   that does not currently exist.
2. **The maximum is 110, not 106.** The database uses object values the API's enum does not define.
   That is R9.

Masks in use: `0` (2,207 rows), `15` (706), `2` (221), `7` (152), `3` (51), `6` (11), `13` (5),
`10` (1), `11` (1). All within 0–15, so `PrivilegeUpdate`'s existing `ge=0, le=15` bound matches the
data.

---

## R1 — The matrix is 107 wide once one missing value is added

**Decision**: `SystemObject` defines **106** members today and gains **one** — `PRODUCTION_SITES = 107`
— as part of this feature (R9). Every full-replace write is therefore **107 rows**.

**Measured**: `len(list(SystemObject)) == 106`, `max == 113`. The spec's Background and SC-001 both
said "roughly eighty"; both prior completion reports said "~82". All were wrong, and the spec has been
corrected.

**Correction to an earlier version of this document.** It claimed *"the enum is stale"* and that the
`# 70 absent` comment *"asserts the opposite of what the data shows"*. **Both claims were wrong.**
Diffing `../mbe/Model/Constants/SystemObjects.cs` against `SystemObject`:

| | Count |
|---|---|
| Legacy values, active | **107** |
| Legacy values, commented out | 7 — exactly `31, 70, 76, 77, 78, 104, 105` |
| Python enum members | 106 |
| **Live in legacy, missing from Python** | **1 — `ProductionSites = 107`** |
| In Python but not active in legacy | **0** |

So the enum's absences at 31, 70, 76–78, 104 and 105 mirror legacy's disabled set **exactly**, and
`# 70 absent` is accurate — 70 is absent from the *catalog*; the rows in the table are leftovers from
when the feature was live. The enum is faithful.

**The one genuinely wrong comment is `app/enums.py:286`:**

```python
# 107 absent          ← legacy has `ProductionSites = 107`, NOT commented out
```

It is the only live object the enum omits, and the comment asserting its absence is what made the
omission look deliberate. `docs/constants.md` propagates the same gap — its table runs 103 → 106 → 108.

One name differs on a shared value: 29 is `Stores` in legacy and `FACILITIES` here. That is
`migrations/004_facility_rename.sql` working as intended, not a defect.

---

## R2 — `access_privilege` has no unique constraint; the hazard is latent, not live

**Finding**: there is no unique index on `(user, object)` (`docs/mbe_schema.sql:50-58`), and
`app/core/deps.py:90` reads a privilege with `scalar_one_or_none()`, which **raises** on two matching
rows. A duplicate would therefore make every request gated on that object answer 500 for that user —
not resolve to one mask or the other.

**Measured**: **zero duplicates** in `mbe_dev`. The defect is reachable in principle and absent in
practice.

`user_service.update_user:77` would also silently collapse duplicates
(`existing = {p.system_object: p for p in user.privileges}`), keeping whichever row loaded last.

**Decision**: unchanged from the first version — this feature does not add the index and does not
repair anything. It is out of scope (Principle III), and the correct sequence for a constraint on a
table with no enforcement of it is measure, repair, constrain, across separate changes. The measurement
half is now done and came back clean, which makes the index a low-risk follow-up rather than a
prerequisite. Worth its own issue.

**What changed**: the first version justified delete-then-insert partly as "heals duplicates". With
zero duplicates measured, that benefit is hypothetical and is no longer offered as a reason.

**Alternatives considered**: changing `scalar_one_or_none()` to `first()` (rejected — converts a loud
500 into a silent arbitrary authorization decision, and edits a path FR-024 requires unchanged).

---

## R3 — An apply is a blanket delete and a full rewrite

**Decision**, settled after R9 identified what the unknown rows actually are:

```python
user.privileges.clear()                                    # delete-orphan issues the DELETEs

for obj in SystemObject:                                   # 107 inserts, once 107 is added
    user.privileges.append(
        AccessPrivilege(system_object=int(obj), privileges=masks.get(int(obj), 0))
    )
```

**This decision was reversed twice and the reasoning is kept visible rather than tidied away.**

- **v1** was this blanket `clear()`, justified as "heals duplicates and gaps".
- **v2** scoped the delete to enum values, because the audit found 88 rows on four objects the enum
  omitted — deleting them looked like revoking legacy access the API could not restore.
- **v3, this one**, returns to the blanket `clear()` because R9 established what those four objects
  are: three are features **commented out in the legacy application** and one was a genuine enum
  omission now being fixed. Nothing in either application reads the three retired objects.

**Rationale**:

- Full replace (FR-013) is satisfied literally, over the whole table rather than over a carve-out that
  would need a comment explaining it forever.
- The 59 rows it removes (objects 70, 104, 105 — 24 of them carrying a grant) are permissions on
  features that exist in neither application. Legacy's own permission UI does not render them either,
  because its enum has them commented out.
- Once `PRODUCTION_SITES = 107` exists, the only live object the enum was missing is covered, so an
  apply speaks for every object either application can enforce.
- One code path for every case: row present (all 31 users), row absent (2 users lack a 107 row today),
  duplicated (none).

**Withdrawn justification**: v1 claimed this heals duplicates and gaps. The audit found **zero
duplicates** and **every user holding the complete 106-object matrix**, so there was nothing to heal.
The insert-missing path still matters — adding `107` creates the gap condition for 2 accounts
immediately, and any future addition does the same for all of them.

**Irreversibility, stated plainly**: those 24 grants cannot be recovered from the API afterwards. The
decision accepts that, on the evidence that no code path in either application consults them. If a
retired feature is ever re-enabled, access has to be re-granted — which is the right process for
re-enabling a feature anyway.

**`user.privileges` loads unknown-object rows without error** — checked:
`system_object: Mapped[int] = mapped_column('object', Integer)` is a plain integer with no enum
coercion (`app/models/user.py:42`), so `104` loads as `104`. Had it been mapped as an `Enum`, reading
any affected user would already be raising.

**Alternatives considered**: the v2 scoped delete (rejected — preserves provably dead data at the cost
of a permanent special case); `UPDATE ... SET privileges = 0` then set the granted masks (rejected —
leaves absent objects absent, so FR-013 fails for the 2 accounts missing a 107 row, and for every
account the next time a value is added); `INSERT ... ON DUPLICATE KEY UPDATE` (rejected — needs the
unique key R2 declines to add, and is MariaDB-only so the SQLite integration layer could not run it);
reusing `update_user`'s upsert (rejected — it is a partial upsert by contract, which FR-026 fixes in
place).

---

## R4 — Case-insensitive uniqueness must be explicit, because SQLite is not MariaDB

**Decision**: compare on `func.lower()` and reuse `assert_unique` unchanged:

```python
await assert_unique(db, UserProfile, func.lower(UserProfile.name), name.lower(),
                   exclude_pk=..., label='Profile name')
```

**Measured**: both `user` and `access_privilege` carry `utf8mb3_unicode_ci`, confirming MariaDB
satisfies FR-004 with a plain `==`. But `tests/integration/` runs the real services against SQLite
(`aiosqlite`), where `=` on `TEXT` is case-**sensitive**. A plain comparison would pass in production
and fail the integration test — or pass both while the two environments disagree about what a conflict
is. `func.lower()` makes the stated rule the enforced rule in both dialects.

**Reuse note**: `assert_unique`'s `column` parameter is typed `Any` and used as
`select(model).where(column == value)` (`app/services/references.py:106`), so a SQL expression is
accepted where a column is. **No change to `references.py`.**

**Alternatives considered**: relying on the collation (rejected — makes the rule invisible in code and
dialect-dependent in tests); normalizing names to lowercase on write (rejected at clarification, Q5
option C — administrators type "Cashier" and should read back "Cashier").

---

## R5 — Profile deletion needs no new guard

**Decision**: `assert_not_referenced(db, profile)` and nothing else.

`references.py:referencing_columns` derives blocking tables from `Base.metadata` foreign keys —
*"a new foreign key is covered the moment its model exists"*. Once `User.profile_id` is a mapped FK to
`user_profile.user_profile_id`, a delete of a referenced profile is refused with the blocking table and
row count named, which is FR-008's wording. Zero new code satisfies a requirement written before this
was checked.

**Alternatives considered**: a hand-written count query (rejected — duplicates the shared guard and
would drift from its message); `ON DELETE SET NULL` (rejected — silently rewrites user rows behind a
delete, which the spec's Assumptions exclude).

---

## R6 — Two tables, one nullable column, one migration

**Decision**:

| Object | Change |
|---|---|
| `user_profile` | **NEW** — id, name, description, status |
| `user_profile_privilege` | **NEW** — id, profile FK, object, privileges |
| `user`.`profile` | **NEW** nullable column, FK → `user_profile` |

One migration, `migrations/014_user_profiles.sql`, plus rollback. **Measured**: neither table nor the
column exists in `mbe_dev`, and no `profile`/`role` table exists to collide with — so the legacy
application has no profile concept this must respect.

**Naming**: `user_profile_privilege` mirrors `access_privilege`'s columns exactly — `object` (a Python
builtin, aliased to `system_object` in the model as `access_privilege` already does) and `privileges`.
The parallel is the point. `user`.`profile` follows the schema's convention of naming an FK column
after the target table (`user`.`employee`, `user_settings`.`facility`).

**Why a separate privilege table, not a blob**: masks are queried and edited per object,
`access_privilege` already models this shape, and a blob could not reuse the same validation.

**Schema-test interaction, checked**: `tests/unit/test_model_schema.py` accepts a mapped column if it
appears in the dump, in a migration's `CREATE TABLE`, or in `ADDED_BY_MIGRATION` (`ADD COLUMN`, line
67). The new tables arrive via `CREATE TABLE`; `user`.`profile` via `ADD COLUMN`. Nullability tests
skip tables absent from the dump and columns in `ALTERED_BY_MIGRATION`. **No edit to
`test_model_schema.py`** — but `tests/unit/test_migrate.py` gains a `014` discovery test following its
existing `008`–`011` pattern.

**Alternatives considered**: reusing `access_privilege` with a nullable `user` plus a `profile` column
(rejected — makes a NOT NULL FK nullable on the one table the authorization path reads, and every
existing query would need a `profile IS NULL` filter it does not have); storing the profile name on
`user` instead of an FK (rejected — no referential integrity, and FR-008 would have nothing to detect).

---

## R7 — `session_version` default disagrees between dump, database and model

**Measured**: `mbe_dev` has `session_version int(11) NOT NULL DEFAULT 1`, matching
`docs/mbe_schema.sql:2509`. `app/models/user.py:26` declares `default=0, server_default='0'`. The
model disagrees with both.

**Impact on this feature**: none. FR-015 invalidates sessions the way `update_user` already does —
`user.session_version += 1`, a relative increment correct from any starting value. Nothing here reads
or asserts an absolute value.

**Decision**: leave it. Pre-existing, outside scope (Principle III), and `test_model_schema.py` does
not flag it because it compares names and nullability, not defaults. Recorded so the next person finds
it diagnosed rather than thinking this feature caused it.

**Also observed and also out of scope**: `user.password` is `varchar(40)` in `mbe_dev`, while
`app/models/user.py:14` declares `String(255)` with the comment *"extended to 255 for bcrypt
migration"*. The widening migration has not been applied to this database. Nothing here writes a
password, so it does not affect this feature — but a bcrypt hash would be truncated by that column
today, which is worth its own issue.

---

## R8 — Creation with a profile is one transaction

**Decision**: `create_user` builds the `User`, resolves and validates the profile, writes the 106
known-object rows from it, sets `profile_id`, and commits **once**. It does not call the apply service
after committing.

**Rationale**: FR-011 requires that naming a missing or inactive profile leaves no user behind.
`create_customer`/`update_customer` shipped a bug of exactly this shape — `_attach_links` ran after
`await db.commit()`, so a 500 came back on a request that had already committed (CHANGELOG, #154).
Validate first, one commit, nothing after it.

**Shared helper**: the mask-writing loop is identical for create and apply, so it is one private
function in `user_service` taking `(user, profile)` and staging rows without committing; each public
entry point owns its commit. `create_user` already contains this loop today (seeding zeros), so the
helper replaces existing code rather than adding a layer.

**Alternatives considered**: `create_user` calling `apply_profile` internally (rejected — apply commits
and bumps `session_version`, neither meaningful for an account being created, and it would produce two
commits where FR-011 requires one).

---

## R9 — RESOLVED: what the four unknown object values are

**Was blocking; settled 2026-08-12** by reading the legacy source at `../mbe`, which the first version
of this document assumed unavailable. Two decisions, both confirmed by the feature owner.

**Measured**: the database holds privilege rows for four object values `SystemObject` does not define.

| Object | Rows | Rows with a grant (mask > 0) | Masks in use |
|---|---|---|---|
| 70 | 9 | 5 | `15` ×5 |
| 104 | 22 | 11 | `2` ×6, `15` ×5 |
| 105 | 28 | 8 | `2` ×4, `3` ×1, `15` ×3 |
| 107 | 29 | 4 | `2` ×1, `15` ×3 |
| **Total** | **88** | **28** | — |

**13 of 31 accounts** hold at least one granted permission on one of these objects.

### What they are

From `../mbe/Model/Constants/SystemObjects.cs` — the same legacy source migration 012 consulted for
its "other writer" check:

| Object | Legacy declaration | State | Anything reads it? |
|---|---|---|---|
| 70 | `//SalesOrderShipments = 70,` | **commented out** | No — neither application |
| 104 | `//SearchAllSalesOrderFromAllUsers = 104,` | **commented out** | No |
| 105 | `//SearchAllSalesOrderFromAllStores = 105,` | **commented out** | No |
| 107 | `ProductionSites = 107,` | **active** | **Yes — the legacy application** |

Three are retired features whose privilege rows outlived them. One is a live permission this API's
enum omits, behind a `# 107 absent` comment that is simply wrong (R1).

### Decision 1 — `PRODUCTION_SITES = 107` is added to `SystemObject`

**In scope for this feature, not a follow-up.** Without it, no profile could ever grant
`ProductionSites`, and full replace would either wipe the 4 live grants or have to carve them out
permanently. One line in `app/enums.py`, replacing the wrong comment. `docs/constants.md` gains the
matching row, since its table skips 107 too.

Consequences: the matrix becomes **107 wide**; 29 of 31 accounts already hold a row for 107 so nothing
is backfilled, and the 2 that do not get one on their first apply. Enforcement is unaffected — this
API exposes no production-sites endpoint, so no `require_privilege(PRODUCTION_SITES)` call exists. The
value's purpose here is that a profile can express it and full replace can cover it.

### Decision 2 — an apply deletes rows on 70, 104 and 105

**FR-013 is read literally**: an apply replaces every row for the user, and R3 is a blanket
`user.privileges.clear()`. The 59 rows on the three retired objects (24 of them grants) are removed
as accounts are provisioned.

Rationale: the data is dead in both applications — legacy's own permission UI cannot render an object
its enum has commented out — so preserving it buys nothing and costs a permanent special case in the
one code path that decides what a user may do. The alternative was considered seriously while those
objects were unidentified, and was the right call under that uncertainty; identifying them removed it.

**Accepted cost**: the 24 grants are unrecoverable through the API afterwards. Re-enabling a retired
feature would require re-granting access, which is appropriate for re-enabling a feature.

**Rejected**: preserving them behind an enum filter (a permanent carve-out for provably dead data);
exporting the 24 rows first (offered and declined — the evidence that these features are retired is
the legacy source itself, not an inference).
