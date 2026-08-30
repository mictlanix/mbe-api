# Implementation Plan: Retire Technical Service and Vehicle Service Orders

**Branch**: `016-drop-tech-service` | **Date**: 2026-08-30 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/016-drop-tech-service/spec.md`

## Summary

Seven tables were dropped from the deployment by the monolith that owns them
(`mictlanix/mbe#37`, `Schema/changes/mbe-26.08.sql`, applied and verified). This repository still
describes all seven — as mapped models, as data-dictionary sections, and as `CREATE TABLE` blocks in
the checked-in dump — and still declares the four `SystemObject` members whose permission rows that
migration deleted. The work is deletion in four places plus the constant that follows from it: drop
`app/models/technical_service.py` and its import, remove `SystemObject` 58, 64, 65 and 90, remove
the seven dictionary sections and the seven dump definitions, drop the six waivers in
`tests/unit/test_data_dictionary.py`, and move the matrix width from 107 to 103 everywhere it is
stated.

No new dependency, no migration, no endpoint change. The one thing that is *not* deletion is the
reason this is worth doing carefully: `user_service._write_privileges_from` writes one
`access_privilege` row per enum member and deletes any row naming a member the enum does not have,
so the enum removal is simultaneously what stops this API re-creating the deleted rows and what
cleans up any that reappear — no data-cleanup code is written here, and none should be.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 — unchanged; nothing added or
removed

**Storage**: MariaDB via aiomysql. **No migration in this repository** — the drop was applied by the
monolith and verified against both databases on 2026-08-30. This repository's picture of that schema
is corrected by editing `docs/mbe_schema.sql`, decided at the review gate (FR-007, research R1).

**Testing**: pytest + pytest-asyncio. The three existing layers are unchanged in shape; what changes
is that the integration schema, built from `Base.metadata`, has seven fewer tables to create, and
the two schema-derivation checks have seven fewer to compare.

**Target Platform**: Linux server (FastAPI/ASGI)

**Project Type**: Web service — a removal touching models, one enum, two documents and five test
files

**Performance Goals**: Not applicable — nothing here runs at request time. The one measurable effect
is that account provisioning writes 103 rows rather than 107.

**Constraints**:
- No migration is added by this repository, and no `access_privilege` row is deleted by it (FR-003,
  FR-009, SC-008). The monolith owns that change and has made it.
- The four retired identifiers are not reused or renumbered, and no surviving identifier moves
  (FR-004).
- No endpoint changes shape. The only observable difference to a caller is the number of privilege
  entries on an account.
- The module is removed in full, not reduced (FR-001) — it maps `vehicle_service_order` and
  `service_order_detail` as well as the five `tech_service_*` tables.

**Scale/Scope**: 1 module deleted, 1 import removed, 4 enum members removed, 7 dictionary sections
removed, 7 dump definitions removed, 6 test waivers removed, 4 statements of the matrix width
updated. 12 files.

## Project Structure

```
app/
├── models/
│   ├── __init__.py                 # drop the `technical_service` import (1 line)
│   ├── technical_service.py        # DELETED — all seven tables are mapped here
│   └── user.py                     # prose: "all 107 rows" → 103
├── enums.py                        # remove SystemObject 58, 64, 65, 90
└── services/
    └── user_service.py             # prose: "Every one of the 107" → 103

docs/
├── data-dictionary.md              # remove 7 sections + the section-11 drop-candidate note
└── mbe_schema.sql                  # remove 7 CREATE TABLE definitions

tests/
├── api/test_user_profiles.py       # prose: "the 107-row write" → 103
├── integration/
│   └── test_user_profiles_flow.py  # OBJECT_COUNT = 107 → 103, and prose
└── unit/
    ├── test_data_dictionary.py     # remove the 6 tech_service_request_component waivers
    ├── test_system_objects.py      # matrix width 107 → 103
    └── test_user_service.py        # OBJECT_COUNT = 107 → 103, and prose

CHANGELOG.md                        # the Unreleased entry
```

**Structure Decision**: No new file is created. Every change is a deletion or the correction of a
number that a deletion invalidates, which is what makes the task ordering below the only real design
question in the feature.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | ✅ | The whole feature is deletion. The one thing that could have been added — a data-cleanup step for surviving permission rows — is deliberately not written, because `_write_privileges_from` already does it (research R2). No exclusion list, no migration, no compatibility shim. |
| II. Think Before Coding | ✅ | Four decisions recorded in [research.md](./research.md). Two reject the obvious answer: the schema dump is edited rather than a migration added (R1), and no cleanup code is written for the permission rows (R2). R4 records a trap found while planning — the number 107 means two different things in the codebase. |
| III. Surgical Changes | ✅ | Nothing is refactored on the way past. `_write_privileges_from` is not touched: it already behaves correctly once the enum shrinks, and changing it would be the definition of an unrequested improvement. The nine sectionless legacy tables and the `EXCLUDE_PRICE_RANGE_VALIDATION` member left unused by spec 015 are noticed and left alone. |
| IV. Goal-Driven Execution | ✅ | Success is the nine measurable criteria in the spec, every one of them checkable by a command rather than by reading: a grep for the seven names, a count of enum members, the suite green. The quickstart names the command for each. |
| V. Reuse Over Rebuild | ✅ | The permission cleanup reuses the existing full-replace path rather than adding a parallel one. |
| VI. Async-First | ✅ | No I/O is added. |
| VII. Security by Default | ✅ | The change *narrows* the permission surface: four entries an account could hold a grant on cease to exist. No grant is widened, and no authorization decision changes for a surviving entry. |
| VIII. Ruff Compliance | ✅ | `ruff check` must pass; the deleted import would otherwise be flagged, which is the linter doing the work of catching a half-done removal. |

No violations. **Complexity Tracking** omitted.

## Phase 0 — Research

See [research.md](./research.md). Four decisions: how the repository learns about an externally
applied drop (R1), why no permission-row cleanup is written (R2), why the retired identifiers are
left unused rather than the enum being renumbered (R3), and the 107-means-two-things trap (R4).

## Phase 1 — Design

- [data-model.md](./data-model.md) — what the change removes from the model layer and what it does
  to the shape of the permission matrix.
- [contracts/permission-matrix.md](./contracts/permission-matrix.md) — the one observable difference
  to an API consumer, recorded because `mbe-ui` reads privilege lists and a silent change in their
  length is the kind of thing that is noticed late.

**Constitution re-check after design**: unchanged — still no violations. The design added no
abstraction, no file, and no code path; it removed one and corrected a constant.

## Ordering

**Decided at the review-plan gate: the dump correction and the model deletion land in one task, so
the suite never goes red mid-feature.**

The constraint is real either way. Both `tests/unit/test_model_schema.py` and
`tests/unit/test_data_dictionary.py` compare the mapped metadata against the schema derived from the
dump, so the two sides have to move together or one of them is briefly wrong about the other. The
choice was which failure to accept in between.

The alternative — correct the dump first, leave the models — was offered and rejected as costing an
intermediate commit whose suite is red: a red commit cannot be bisected through, and "this one is
red on purpose" survives exactly as long as the person who made it remembers.

**That reasoning reached the right answer for a wrong reason, and the record is corrected rather
than quietly amended.** The rejected option was described here as failing "loudly and accurately".
It does not fail at all — `test_model_schema.py` *skips* a mapped table absent from the schema
rather than failing on it, so the loud ordering would have produced a **green** intermediate suite
and an absence of evidence that read as proof. See research R5, which records the measurement and
the standing gap it exposed.

**What is given up, and how it is bought back.** The loud order would have demonstrated that
`test_model_schema.py` actually notices a schema that disagrees with the models. That evidence is
worth having, so it is taken separately and without a red commit: a task mutates the pair locally —
correct the dump, run the check, confirm it fails naming the seven tables, revert — and records the
observed output. Same proof, no red commit. This mirrors how #172 and #181 were verified by
mutation rather than by assertion.

Everything else is order-independent. The enum removal, the dictionary sections, the waivers and the
matrix constants touch disjoint files and can be done in any sequence, though the constants must
follow the enum removal to be checkable.
