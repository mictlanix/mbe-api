# Data Model: User Profiles as Permission Templates

**Feature**: `014-user-profiles` | **Date**: 2026-08-12 | **Research**: [research.md](./research.md)

Two new tables, one new nullable column. No existing column changes type or nullability.

---

## What changes

```
user_profile (NEW)                      user (EXISTING)
├── user_profile_id  PK                 ├── user_id            PK
├── name             UNIQUE, ci         ├── ... unchanged ...
├── description      NULL               └── profile     NEW, NULL, FK ─┐
└── status           NOT NULL DEFAULT 0                                │
        │                                                             │
        │ 1:N cascade                   access_privilege (EXISTING)    │
        ▼                               ├── access_privilege_id  PK    │
user_profile_privilege (NEW)            ├── user   FK → user           │
├── user_profile_privilege_id  PK       ├── object                     │
├── user_profile   FK → user_profile    └── privileges                 │
├── object                                    ▲                        │
└── privileges                                │ an apply rewrites      │
                                              │ all 107 rows          │
        └──────────── copied on apply ────────┘                        │
        └──────────── origin recorded ─────────────────────────────────┘
```

The two arrows out of `user_profile` are the whole feature: permissions are **copied** into
`access_privilege` (a value, taken once), and the profile is **referenced** from `user.profile`
(a pointer, kept). Nothing reads the reference to make an authorization decision.

---

## `user_profile` (new)

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `user_profile_id` | `int(11)` | NO | `AUTO_INCREMENT` | PK |
| `name` | `varchar(100)` | NO | — | `UNIQUE`; collation `utf8mb3_unicode_ci` → case-insensitive (FR-004) |
| `description` | `varchar(250)` | YES | `NULL` | Optional (FR-001) |
| `status` | `int(11)` | NO | `0` | `EntityStatus`; `0 = ACTIVE` (FR-009) |

`varchar(100)` for the name: longer than any role title needs and short enough to index without a
prefix. `varchar(250)` for the description matches `price_list.name`, `product.name` and every other
free-text field in this schema — the local convention, not a considered width.

**Model**: `app/models/user.py`, class `UserProfile`. Placed with `User` rather than in `core.py`
because it is only ever reached through a user or through its own endpoints.

```python
privileges: Mapped[list['UserProfilePrivilege']] = relationship(
    back_populates='profile', cascade='all, delete-orphan', lazy='selectin'
)
```

`lazy='selectin'` matches `User.privileges` — a profile is never useful without its masks, and every
read of one wants them.

## `user_profile_privilege` (new)

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `user_profile_privilege_id` | `int(11)` | NO | `AUTO_INCREMENT` | PK |
| `user_profile` | `int(11)` | NO | — | FK → `user_profile.user_profile_id` |
| `object` | `int(11)` | NO | — | `SystemObject` (107 members after this feature); mapped as `system_object` (Python builtin) |
| `privileges` | `int(11)` | NO | `0` | `AccessRight` mask, 0–15 |

Column names deliberately mirror `access_privilege` (research R6), so the copy in
`_write_privileges_from` reads as a field-for-field transfer rather than a translation.

**Sparse by contract** (FR-003): a row exists only for an object the profile grants something on.
An entry with `privileges = 0` is accepted on write and then indistinguishable from absence — the
service drops zero-mask entries rather than storing them, so "no entry" is the single
representation and a round-trip is stable.

**No unique index on `(user_profile, object)`** — the write path replaces the whole set (see
below), so duplicates cannot arise from this API, and adding a constraint `access_privilege` itself
lacks would be an inconsistency rather than a safeguard. Research R2 explains why fixing
`access_privilege` belongs to its own change; the audit found zero duplicates there today.

## `user.profile` (new column on an existing table)

| Column | Type | Null | Default | Notes |
|---|---|---|---|---|
| `profile` | `int(11)` | YES | `NULL` | FK → `user_profile.user_profile_id` |

Nullable is load-bearing, not incidental: every existing account has no origin (FR-017), and an
account created without a profile keeps none (FR-011). `NULL` is the normal state for most rows.

**Mapped as** `profile_id: Mapped[int | None]` with `mapped_column('profile', ...)`, following
`employee_id: Mapped[int] = mapped_column('employee', ...)` on the same class.

**No `ON DELETE SET NULL`.** The FK is plain, so `assert_not_referenced` refuses a delete while any
user points at the profile (FR-008, research R5). A database-level `SET NULL` would silently rewrite
user rows behind a delete, which the spec's Assumptions rule out.

---

## Validation rules

Traced from the spec, with where each is enforced:

| Rule | Requirement | Enforced in |
|---|---|---|
| Name unique, case-insensitively | FR-004 | `assert_unique` on `func.lower(name)` — service, before the index (research R4) |
| Name non-empty, ≤100 chars | FR-001 | Pydantic `Field(min_length=1, max_length=100)` |
| Mask in 0–15 | FR-002 | Pydantic `Field(ge=0, le=15)` — the existing `PrivilegeUpdate` rule |
| `object` is a known `SystemObject` | FR-010 | Pydantic validator against `SystemObject` → 422 |
| At most one entry per object in a payload | FR-002 | Pydantic validator on the list → 422 |
| Zero-mask entries dropped | FR-003 | Service, on create and update |
| Inactive profile cannot be applied | FR-017 | Service, in both apply and create-with-profile |
| Profile deletable only when unreferenced | FR-008 | `assert_not_referenced` — no new code (research R5) |

Two of these are new validator work; the rest reuse what exists.

---

## State transitions

`user_profile.status` uses `EntityStatus` unchanged — `ACTIVE (0)`, `INACTIVE (1)`, `ARCHIVED (2)`.

```
ACTIVE ──────► INACTIVE / ARCHIVED        applyable → not applyable, still readable (FR-009)
   ▲                  │
   └──────────────────┘                    freely reversible; no side effect on any user
```

Status is a property of the template only. Changing it never touches a provisioned account —
including reactivating a profile after users were provisioned from it, which grants nobody anything.
Only ACTIVE is applyable; INACTIVE and ARCHIVED are both refused, since FR-017 draws the line at
"not active" rather than naming a single retired state.

---

## The apply, as a data operation

For a target user and a profile whose entries are `{object: mask}`:

1. `DELETE FROM access_privilege WHERE user = :user_id` — expressed as `user.privileges.clear()`, which
   `cascade='all, delete-orphan'` turns into the deletes.
2. `INSERT` one row per member of `SystemObject` — **107 rows** — with `privileges = masks.get(obj, 0)`.
3. `user.profile = profile.user_profile_id`
4. `user.session_version += 1` (FR-015)
5. One `commit()`.

Steps 1–5 are one transaction, so FR-018's all-or-nothing holds by construction rather than by
compensation. A failure anywhere rolls back to the prior rows, origin and session version.

**Row counts**: 107 inserts per apply regardless of how sparse the profile is. A profile granting one
object still denies the other 106 explicitly, because full replace (FR-013) is a statement about all
107.

### The matrix is 107 wide, and why step 1 is a blanket delete

`SystemObject` gains **`PRODUCTION_SITES = 107`** in this feature (research R9). It was the one live
legacy object the enum omitted — behind a `# 107 absent` comment that is simply wrong — and without it
no profile could express `ProductionSites`. 29 of 31 accounts already hold a row for object 107, so
nothing is backfilled; the 2 that do not get one on their first apply.

Three other values appear in the table and not in the enum — **70, 104, 105**, across **59 rows, 24 of
them grants**. The legacy source has all three **commented out**: they are retired features whose
privilege rows outlived them, and neither application reads them. **The blanket delete removes them**,
lazily, as each account is provisioned. FR-013 is therefore satisfied literally rather than over a
carve-out.

This was decided twice in the other direction first. While those objects were unidentified, scoping the
delete to enum values was the right call — deleting a permission the API cannot name is not something
to do on a guess. Reading `../mbe/Model/Constants/SystemObjects.cs` removed the guess. Research R3
records all three versions.

**No healing claim.** An earlier draft justified the blanket delete as repairing duplicates and gaps.
The audit found **zero duplicates** and every user holding the complete matrix, so there was nothing to
repair. The insert-missing path still matters: adding `107` creates the gap condition for 2 accounts
immediately, and any future addition does the same for all of them.

---

## What does *not* change

- `access_privilege` — no column added, no type changed, no index added. An apply writes rows through
  the existing model. Its **data** does change: the 59 rows on retired objects 70/104/105 are removed as
  accounts are provisioned (research R9).
- `deps.py` privilege enforcement — reads `access_privilege` exactly as it does now (FR-024).
- `update_user`'s privilege upsert — stays a partial upsert (FR-026). The two write paths differ
  deliberately.
- `user_settings`, `employee`, `user.administrator`, `user.status` — a profile carries permissions
  only (spec Assumptions).
- `references.py` — `assert_not_referenced` and `assert_unique` are both used unmodified.
- `fk_expansion.py` — untouched, and deliberately **not used** for `profile_name`. It loads entities,
  which drags in `UserProfile.privileges` (`lazy='selectin'`); a two-column projection costs one query
  instead of two. Measured, not assumed.
- `test_model_schema.py` — the new tables and column are already accounted for by its existing
  migration parsing (research R6).
