# Implementation Plan: Price List Retirement

**Branch**: `015-price-list-retirement` | **Date**: 2026-08-29 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/015-price-list-retirement/spec.md`

## Summary

One `frozenset` naming what a price list's deletion sweeps, read by everything that needs the
answer, plus one parameter naming where the list's customers go:

1. `_DELETE_CASCADE = frozenset({'product_price'})` in `price_list_service` — the list's own
   contents, the only relation a retirement may delete. Everything else keeps blocking.
2. `delete_price_list(db, pl, replacement_id)` moves the list's customers onto `replacement_id`
   when one is named, then runs the existing `assert_not_referenced(..., exempt=_DELETE_CASCADE)`,
   then deletes the swept relations by looping `referencing_columns(PriceList)` and filtering on the
   same set, then deletes the list. One commit, at the end.
3. `preview_delete(db, pl)` counts the same relations through `find_blocking_references` with no
   `exempt`, reported as `{items: [{category, count}], total}`.

The set is read twice — once as `exempt`, once as the cascade filter — so a relation cannot be
exempted without being swept or swept without being exempted. `find_blocking_references` reads the
mapped metadata, so a foreign key to `price_list` added later blocks the retirement and appears in
the report with no edit to either (FR-010, FR-011).

Fixes GitHub issue #181, and follows `010-product-merge-integrity` for the parts the two operations
share: the configuration-versus-history split, the derivation from `Base.metadata` rather than a
hand list, and a read-only report of scale before an irreversible action.

## Technical Context

**Language/Version**: Python 3.12+

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 — no new dependencies

**Storage**: MariaDB via aiomysql. No schema change: no column, table, index or migration. The
relations are read from `Base.metadata` through `referencing_columns`, established by feature 006
and already shared with the merge.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`. Three layers, because each one is
blind to what the others catch: mocked service tests in `tests/api/` for the endpoint contract,
statement-level tests in `tests/unit/` for the SQL the service issues, and `tests/integration/`
against a real schema with `PRAGMA foreign_keys=ON` for the outcomes the mocks cannot observe —
prices actually gone, customers actually moved, nothing left behind on a failure.

**Target Platform**: Linux server (FastAPI/ASGI)

**Project Type**: Web service — one service function rewritten, one added, one endpoint added

**Performance Goals**: The retirement issues at most four statements regardless of catalog size: one
bulk `UPDATE` by indexed foreign key, one `UNION ALL` of two counts, one bulk `DELETE`, one row
delete. It replaces a client loop that was one request per priced product. The report is the same
`UNION ALL` of two counts a delete guard already issues. Retirements are rare and operator-initiated.

**Constraints**:
- What is exempted from the blocker check and what is deleted must be one set, not two (FR-008).
- No relation may be handled by a hand list; a foreign key to `price_list` added later must be
  covered as soon as its model exists (FR-010).
- Anything other than the prices must keep blocking, including relations nobody has thought about
  yet (FR-011).
- The whole retirement is one transaction with one commit; every refusal leaves the data untouched
  (FR-006).
- The report changes nothing and is refused for a list that does not exist (FR-007, FR-009).
- Omitting the replacement preserves today's behaviour byte for byte (FR-004).

**Scale/Scope**: 1 service function rewritten (`delete_price_list`), 1 added (`preview_delete`),
1 constant added (`_DELETE_CASCADE`), 1 endpoint added, 1 query parameter added to an existing
endpoint, 2 schemas added, 1 test file updated, 2 test files added. No existing helper changes
behaviour.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | ✅ | The service grows by roughly twenty lines and one constant. No new module, no new helper in `references.py`, no generalisation of the cascade pattern across the four services that use `exempt` today — that was offered and deliberately left out of scope. |
| II. Think Before Coding | ✅ | Nine decisions recorded in [research.md](./research.md). Three of them reject the obvious answer and say why: the cascade is derived from the exempt set rather than mirroring `delete_product`'s two-place statement (R3), the report stays flat rather than labelling deleted-versus-reassigned (R5), and two schemas are duplicated rather than renaming a published one (R6). |
| III. Surgical Changes | ✅ | `references.py`, `product_service.py` and `product_price_service.py` are not touched. The four other services passing `exempt` are left as they are. The pre-existing question of whether the price-list router should gate on `SystemObject.PRICE_LISTS` is noted in R9 and not acted on. |
| IV. Goal-Driven Execution | ✅ | Success = the quickstart scenarios: suite green, ruff clean, the exempt/cascade invariant asserted by test, and a retirement against a real schema leaving no orphan price and every customer on the named list. |
| V. Reuse Over Rebuild | ⚠️ | Two new Pydantic schemas, justified in Complexity Tracking below. Everything else is reuse: `referencing_columns`, `find_blocking_references` and `assert_not_referenced` are used as they stand. |
| VI. Async-First | ✅ | Both service functions are `async def` taking `AsyncSession`; both endpoints are `async def`. Every statement is awaited on the request's session. |
| VII. Security by Default | ✅ | Both the new report and the changed delete require an authenticated caller via `get_current_user`, matching every endpoint in the router (R9). Neither is public. The report discloses row counts for a price list to a caller who can already read, edit and delete that list. |
| VIII. Ruff Compliance | ✅ | `uv run ruff check app/ migrations/ tests/` gates completion, clean at baseline. `ruff format --check` is scoped to the files this feature touches: repo-wide it already reports 51 files, the unresolved quote-style contradiction of GH #96, and reformatting them is not this feature's change. The four touched files are format-clean today and stay that way. mypy held at its baseline of 170 errors in 48 files. |

**Testing rule**: `GET /price-lists/{id}/delete/preview` is a new endpoint, so the constitution's
mandatory-test rule binds: happy path, 404, 401. `delete_price_list` is not new but its correctness
is entirely about which statements it issues and in what order, so it is tested by observing them,
and again end-to-end against a real schema. Tests are written first and confirmed failing.

## Project Structure

### Documentation (this feature)

```text
specs/015-price-list-retirement/
├── plan.md              # This file
├── research.md          # Phase 0 output
├── data-model.md        # Phase 1 output
├── quickstart.md        # Phase 1 output
├── checklists/
│   └── requirements.md  # /speckit-specify output
├── contracts/
│   └── price-list-retirement.md
└── tasks.md             # /speckit-tasks output — not created by /speckit-plan
```

### Source Code (repository root)

```text
app/
├── api/v1/endpoints/
│   └── price_lists.py            # + GET /{id}/delete/preview; DELETE gains `replacement`
├── schemas/
│   └── product.py                # + PriceListDeletePreviewItem, PriceListDeletePreviewResponse
└── services/
    ├── price_list_service.py     # + _DELETE_CASCADE, preview_delete; delete_price_list rewritten
    └── references.py             # unchanged — read, not modified

tests/
├── api/
│   └── test_products.py          # price-list section: preview endpoint, replacement pass-through
├── unit/
│   └── test_price_list_service.py    # new — the statements a retirement issues, and their order
└── integration/
    └── test_price_list_retirement.py # new — real schema, FKs enforced, outcomes observed
```

**Structure Decision**: The existing layout, unchanged. Price-list endpoint tests live in
`tests/api/test_products.py` today (its docstring says so) and stay there rather than being split
out, which would move code this feature has no other reason to touch.

## Complexity Tracking

| Violation | Why Needed | Simpler Alternative Rejected Because |
|-----------|------------|-------------------------------------|
| Two new schemas (`PriceListDeletePreviewItem`, `PriceListDeletePreviewResponse`) structurally identical to the merge preview's | The report needs a response model, and Constitution V requires new schemas to be justified rather than assumed | Reusing `ProductMergePreviewResponse` puts a merge-named class in a generated client's price-list call — the readability complaint of GH #175. Renaming both to a neutral shared model changes the class name a shipped endpoint generates, breaking existing consumers of the merge preview. Eight lines of duplication is the cheapest of the three; R6 records when to revisit it (a third caller). |
