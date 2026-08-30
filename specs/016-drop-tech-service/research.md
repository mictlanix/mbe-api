# Phase 0 Research: Retire Technical Service and Vehicle Service Orders

Four decisions. Two of them reject the answer that looks obvious, and one is a trap found while
planning rather than a choice.

---

## R1 — How this repository learns about a drop it did not perform

**Decision**: Remove the seven `CREATE TABLE` definitions from `docs/mbe_schema.sql`.

**Rationale**: This repository answers "what does the deployed schema look like?" by replaying its
own `migrations/*.sql` over the checked-in dump. Two standing checks read that answer —
`test_model_schema.py`, which asserts every mapped column exists in the schema, and
`test_data_dictionary.py`, which asserts every live column is documented. The drop happened in
neither input, so both are currently comparing against a schema with seven tables the deployment
does not have, **and passing**. That is worse than failing: it is a check that has quietly stopped
measuring the thing it was written for, and it stays that way for every unrelated change until
corrected.

Editing the dump keeps the derivation exactly as it is — dump plus migrations, no third input, no
list to maintain — and needs no new file. It is the option with the fewest moving parts, which for a
derivation that two checks depend on is the deciding property.

**Alternatives considered**:

- *Record the drop as a migration here.* A forward-only `DROP TABLE IF EXISTS` file, a no-op against
  the already-migrated deployment. Preserves the dump as a faithful snapshot and keeps the
  derivation automatic. Rejected on two grounds: it re-states a change another system owns, which is
  how two repositories end up disagreeing about who applied what, and the originating issue ruled it
  out explicitly. SC-008 pins the outcome.
- *An exclusion list read by the two checks.* A named `DROPPED_ELSEWHERE` set subtracted from the
  derivation. Honest about provenance and localizes the knowledge to the checks that need it.
  Rejected because it is a hand-maintained list that grows every time this recurs, and a
  hand-maintained list drifting from the metadata it shadows is the exact failure #112 was.

**Accepted cost**, recorded so nobody rediscovers it as a surprise: the dump is `mysqldump` output
from a real database at a moment in time, and deleting tables that existed at that moment makes it
no longer a faithful record of it. This is acceptable because nothing reads it as history — both
checks read it only as the baseline to replay migrations onto, and `test_model_schema.py` already
documents it as a pre-spec-005 snapshot that disagrees with 18 tables on its own. Its value is
entirely in describing the schema as it now stands.

---

## R2 — Why no permission-row cleanup is written

**Decision**: Write no code to delete `access_privilege` rows for the four retired objects. Removing
the enum members is the entire change.

**Rationale**: `user_service._write_privileges_from` already does both halves of the job. It writes
one row per member of `SystemObject`, and then — in a loop that exists for exactly this situation —
removes every row whose object the enum does not define. That loop was written for objects 70, 104
and 105, features commented out of the legacy catalog whose grants outlived them (spec 014, research
R9). The four retired here are the same case arriving again.

So removing the members is simultaneously the fix and the cleanup: this API stops writing the four,
and any account that still holds them is cleaned on its next full replace. Adding a deletion step
would duplicate a behaviour that already exists, and would breach the constraint that this
repository deletes no permission rows of its own accord (FR-003).

**Measured before deciding**: `mbe_dev` currently holds **zero** `access_privilege` rows for objects
58, 64, 65 and 90 — the monolith's migration removed them — and **zero** stored profiles grant any of
the four (there is one profile in total). So the cleanup path has nothing to do today. It matters
only for a row created between now and this change shipping, which is precisely the window this
feature closes: until the members go, the next account created re-creates all four.

**Alternatives considered**:

- *A one-off cleanup script or migration.* Rejected: nothing to clean, and it would assert ownership
  of data the monolith's migration already handled.
- *Leave the members and filter them at the write site.* Rejected outright — it is a special case in
  the one function that currently has no special cases, to avoid deleting four lines from an enum.

---

## R3 — Retired identifiers are left unused, not recycled

**Decision**: Delete the four members. Do not renumber anything, do not reuse 58, 64, 65 or 90 for a
future entry.

**Rationale**: `SystemObject` is not this API's catalog — it mirrors the legacy application's menu
identifiers, and `access_privilege` rows across two databases are keyed on those numbers. A number
that changes meaning would silently re-point every stored grant that names it. The catalog already
carries gaps for this reason (31, 70, 76–78, 104, 105 are absent by design and pinned by a test), so
leaving four more is the established shape rather than a new compromise.

This also matches the precedent set one feature ago: spec 015 retired the margin validation but kept
`EXCLUDE_PRICE_RANGE_VALIDATION = 102`, because rows existed against it. The difference here is that
the rows are gone — the monolith deleted them — which is what makes removing the member correct in
this case and incorrect in that one.

**Alternatives considered**:

- *Keep the four members as unused entries*, mirroring 102's treatment. Rejected because the
  situations differ in the way that matters: 102's rows still exist and the legacy catalog still
  declares it, while these four have had both their rows and their screens deleted. Keeping them
  would mean re-creating four rows per account forever, for menu entries that no longer exist.
- *Renumber to close the gaps.* Rejected — it would re-point stored grants.

---

## R4 — `107` means two different things, and this change separates them

**Not a decision — a trap found while planning, recorded so the implementation does not walk into
it.**

The number 107 appears in this codebase as two unrelated facts:

1. **The count** of `SystemObject` members — the width of the permission matrix, one
   `access_privilege` row per member. This becomes **103**.
2. **The identifier** `PRODUCTION_SITES = 107`, pinned by name in `test_system_objects.py` and used
   as a test fixture value in `test_user_profile_service.py`. This does **not** change.

That the two were equal is a coincidence: identifiers run to **113**, with gaps, so the count and
the maximum identifier were never the same number and the count happening to equal one member's id
was luck. A careless find-and-replace of `107` breaks `test_production_sites_is_107`, and — worse,
because it fails less obviously — silently rewrites the fixture in
`test_user_profile_service.py:87`, which asserts that a profile entry naming object 107 validates.

Every occurrence must be classified before it is touched. The count occurrences are in
`test_system_objects.py`, `test_user_service.py`, `test_user_profiles_flow.py`,
`test_user_profiles.py`, `user_service.py` and `models/user.py`; the identifier occurrences are in
`test_system_objects.py` (which contains both) and `test_user_profile_service.py`.

The change is a small improvement on top: once the count is 103 and the identifier is still 107, the
two can no longer be confused for each other by a reader.

---

## R5 — Mutation proof: the check did **not** catch it

**The result contradicts what the plan predicted, and the contradiction is the finding.**

The plan's Ordering section asserted that correcting the dump while the models still existed would
make `test_model_schema.py` fail with "seven tables' worth of mapped columns missing from the
schema — a loud, accurate failure that proves the check is live." That claim was wrong, and it was
written confidently enough that only running it caught it.

**What actually happened.** During T001 that exact state existed naturally for a moment — the dump
corrected, the models not yet deleted — so the proof was taken there rather than being reconstructed
afterwards:

```
$ uv run pytest -q tests/unit/test_model_schema.py
375 passed, 43 skipped in 1.02s
```

**Green.** The 43 skips are the answer:

```python
known = DUMPED.get(table.name, set()) | CREATED_BY_MIGRATION.get(table.name, set())
if not known:
    pytest.skip(f'{table.name} is described by neither the dump nor a migration')
```

A mapped table that appears in **neither** the dump nor a migration is *skipped*, not failed. The
check verifies the columns of tables it can find; a model pointing at a table that does not exist at
all falls through the hole between them. Measured at that moment: exactly seven tables took the skip
branch, and they were exactly the seven — before the dump edit, no mapped table hit it at all.

**Why this matters beyond this feature.** `test_model_schema.py` exists because of #154, a model
mapped with a column the table did not have. It catches that. It does not catch the coarser version
of the same mistake — a model mapped to a *table* the database does not have — which is precisely
the situation a drop performed outside this repository creates, and precisely what this feature is
cleaning up. The check that would most naturally be expected to have flagged this feature's need
was structurally unable to.

**Raised as #190**, which records this alongside two further gaps found by auditing the rest of the
file: four of the five checks read `DUMPED_COLUMNS`, which is parsed from the dump alone, so the
five migration-created tables — 28 columns — get no nullability or width check at all; and column
type is never compared, which is currently hiding `supplier_agreement.start`/`.end` mapped as
`String(10)` against a `date` column.

**Not fixed here.** Turning that `skip` into a failure is a one-line change and, once T001 lands,
would flag nothing — zero mapped tables take the branch again. That makes it cheap and safe, and it
is still a different defect from the one this spec addresses: it predates this change, it affects
all 100 tables rather than these seven, and fixing a standing check is not what "retire two modules"
authorises (Constitution III). Recorded here and raised separately.

**What the proof was worth.** It was commissioned to confirm something already believed. It refuted
it instead — which is the only reason a verification step earns its place. Had the loud ordering
been used, the intermediate suite would have been *green*, the "proof" would have been an absence of
evidence read as presence, and the hole would have stayed hidden.

