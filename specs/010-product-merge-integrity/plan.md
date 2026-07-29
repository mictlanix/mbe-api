# Implementation Plan: Product Merge Integrity

**Branches**: `015-product-merge-preview`, `016-merge-all-references`,
`017-merge-discard-config-rows` | **Date**: 2026-07-25 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/010-product-merge-integrity/spec.md`

## Summary

One enumeration of the relations that reference `product`, read by three callers that must not
disagree:

1. `referencing_columns(Product)` — extracted from `find_blocking_references` (feature 006), the
   mapped metadata's answer to "what points at this row". 19 foreign keys, not the 8 the merge
   had been handling.
2. `merge_products` loops over that enumeration once. Membership in `_MERGE_DISCARD` is the only
   thing that varies: 15 `UPDATE`s that move history onto the canonical, 4 `DELETE`s that drop
   the duplicate's configuration. Then the duplicate is deleted and the transaction commits.
3. `preview_merge` counts the same enumeration through `find_blocking_references` and reports it
   as `{items: [{category, count}], total}`.

`_load_merge_pair` is shared by the merge and the preview, so a preview that answers is a preview
of a merge that would be accepted. Fixes GitHub issues #111 and #112 and the configuration
correction that followed #112.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 — no new dependencies

**Storage**: MariaDB via aiomysql. No schema change: no column, table, index or migration. The
relations are read from SQLAlchemy's `Base.metadata`, the same source feature 006 established.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`, plus a merge executed against the
populated `mbe_dev` database inside a transaction that is rolled back

**Target Platform**: Linux server (FastAPI/ASGI)

**Project Type**: Web service — one service function rewritten, one endpoint added

**Performance Goals**: The merge issues 19 statements plus a delete, against 8 before, all bulk
`UPDATE`/`DELETE` by indexed foreign key. The preview is one `UNION ALL` of 19 counts, the same
shape a delete guard already issues. Merges are rare and administrator-initiated.

**Constraints**:
- No relation may be handled by a list; a new foreign key to `product` must be covered as soon
  as its model exists (FR-002).
- The preview's categories and the merge's targets must come from one enumeration, so they
  cannot drift (FR-012).
- No statement may suppress its own failure — the merge must be all-or-nothing (FR-005).
- History is never deleted; only configuration is (FR-010).
- The preview is read-only (FR-013).

**Scale/Scope**: 1 service function rewritten (`merge_products`), 2 added (`_load_merge_pair`,
`preview_merge`), 1 helper extracted (`referencing_columns` out of `find_blocking_references`),
1 endpoint added, 2 schemas added, 2 test files updated.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | ✅ | The rewrite is shorter than what it replaced: one loop over the metadata, one `frozenset` naming the exception. The intermediate `UPDATE IGNORE` + `DELETE` pair and the separate `product_price_service` call were both removed, not added to. |
| II. Think Before Coding | ✅ | Three decisions were taken deliberately and are recorded in research: fiscal history follows rather than blocks (R4), the four configuration relations are discarded rather than partially moved (R5, which reverses the first answer), and the preview reports a superset rather than only what is reassigned (R6). |
| III. Surgical Changes | ✅ | `referencing_columns` is an extraction, not a rewrite — `find_blocking_references` keeps its behaviour and its only caller change is to call the extracted function. No adjacent service was touched. |
| IV. Goal-Driven Execution | ✅ | Success = quickstart scenarios: suite green, the preview/merge invariant asserted by test, and a real merge of the most-referenced product against `mbe_dev` leaving no orphans. |
| V. Reuse Over Rebuild | ✅ | No new module. The reference-counting machinery from feature 006 is reused in both directions; the merge borrows the scan the delete guards already trusted rather than keeping its own list. |
| VI. Async-First | ✅ | Both service functions are `async def` taking `AsyncSession`; the endpoint is `async def`. |
| VII. Security by Default | ✅ | The preview is gated by `require_privilege(SystemObject.PRODUCTS_MERGE, AccessRight.READ)` — the same object as the merge, at read level, because it discloses history volumes for a product. |
| VIII. Ruff Compliance | ✅ | `uv run ruff check app/ migrations/ tests/` and `ruff format --check` gate completion; mypy held at its pre-existing baseline. |

**Testing rule**: `GET /products/merge/preview` is a new endpoint, so the constitution's
mandatory-test rule binds: it has API tests. The merge is not new, but its correctness is
entirely about which statements it issues, so it is tested by observing them.

## Project Structure

### Documentation (this feature)

```text
specs/010-product-merge-integrity/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── checklists/
│   └── requirements.md  # /speckit-specify output
├── contracts/
│   └── product-merge.md
└── tasks.md             # Phase 2 output (/speckit-tasks)
```

### Source Code (repository root)

```text
app/
├── api/v1/endpoints/
│   └── products.py            # ADD GET /merge/preview, declared before GET /{product_id}
├── schemas/
│   └── product.py             # ADD ProductMergePreviewItem, ProductMergePreviewResponse
└── services/
    ├── references.py          # EXTRACT referencing_columns() from find_blocking_references
    └── product_service.py     # ADD _load_merge_pair, preview_merge, _MERGE_DISCARD;
                               # REWRITE merge_products as one metadata-driven loop

tests/
├── unit/
│   └── test_product_service.py  # UPDATED: statements observed, nothing stubbed
└── api/
    └── test_products.py         # UPDATED: preview shape, totalling, routing, auth
```

**Structure Decision**: No new files. The merge already lived in `product_service.py` and the
scan already lived in `references.py`; this feature makes the second the only source the first
reads.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| `_MERGE_DISCARD` is a declared set, not derived (Principle II) | The four relations that must be discarded are identified by what they *mean* — configuration rather than history — which no metadata expresses. | Deriving it from the unique keys covering the product column was the alternative, and would have been derivable in principle: those four are exactly the relations with such a key. It was rejected because the models do not carry those keys, only the database does, so "derived" would have meant hard-coding the same four names while implying they were discovered. A relation whose meaning is configuration but which is missed here fails loudly on the constraint and undoes the merge. |
