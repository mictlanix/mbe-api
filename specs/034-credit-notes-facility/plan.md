# Implementation Plan: Credit Note Balances and the Facility Rule

**Branch**: `034-credit-notes-facility` | **Date**: 2026-10-27 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/034-credit-notes-facility/spec.md`

## Summary

- **Credit note balances** (mictlanix/mbe-api#277): a note is a view over its backing payment.
  - Its amount is the payment's amount.
  - Its remaining is the payment's unapplied amount (spec 033), or 0 when it was paid back in cash.
  - It is open when remaining is above 0.01, it wasn't paid back, and it isn't the walk-in customer's. That is the current system's spendable pool.
  - It reports its facility (R1, R7, R8).
- **The API's refund writes notes the current system's way:** `refunded = 0`, `cash_session` NULL. That way both systems can spend them (R2).
- **Open-only paging** (mictlanix/mbe-api#278): `open_only` filters in SQL before counting and paging. No other list filters after paging (R3).
- **The facility rule** (mictlanix/mbe-roadmap#156), with one helper, `deps.can_see_every_facility` (R4):
  - payment apply and reverse answer 404 for another facility's payment without PaymentsEditor (100);
  - verify, reject and `/unverified` are company-wide for PaymentsVerification (108) (R5);
  - sales orders: the list's `facility` answers 403, and the writes answer 404 for another facility's order without SearchCreditsFromAllStores (101). Reads stay open by id (R6);
  - credit notes: the list is scoped to a facility, as payments are; the detail answers 404 (R7).
- **No schema change** (R9).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2. No new dependency.

**Storage**: MariaDB `mbe_dev`. No migration; no data correction (R2).

**Testing**: pytest with httpx `ASGITransport`.
- **Unit:**
  - the open rule and the remaining rule (pure functions);
  - `can_see_every_facility` (administrator, holder, non-holder).
- **Integration (SQLite):**
  - balances of legacy-shaped notes (refunded 0), a note paid back in cash, the walk-in customer and change counted;
  - open-only paging (full pages, `total`, no repeats);
  - a refund's note written as the current system writes one;
  - the facility rule on every changed path, for a scoped clerk and an administrator.
- **API (service mocked):** 403/404 on each changed route, and the parameters each passes.

**Target Platform**: Linux server (xolotl)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: the credit-note list answers in one query for the page and one for the count. The open filter is part of both.

**Constraints**:
- The open rule is written once as an SQL expression, used by the list's `WHERE` and selected for `remaining`, so the filter and the reported figure can't disagree.
- Change is counted as used, matching `unapplied_amount`.

**Scale/Scope**:
- 984 notes, 137 spendable.
- 12 routes change: credit-note list and detail; payment apply, reverse and `/unverified`; the sales-order list; and 6 sales-order writes sharing one helper.

## Constitution Check

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | No new table, setting or privilege. The note's figures come from its payment. One helper replaces two private copies and serves the new checks | PASS |
| II. Think Before Coding | Three clarifications recorded. Measured before deciding: note balances, notes paid back in cash, the walk-in pool, cross-facility applications (2%), order changes (0.8%), notes spent elsewhere (5 in 2026), privilege holders | PASS |
| III. Surgical Changes | Credit-note service and schema; the refund writer's two fields; four route guards in customer payments; the sales-order list check and one write helper; `deps` gains a helper and the two existing private copies call it | PASS |
| IV. Goal-Driven | SC-001 to SC-005 map to tests and to the quickstart's recorded runs | PASS |
| V. Reuse Over Rebuild | Reuses `unapplied_amount`'s arithmetic, `_visible_payment_or_404`, `list_payments(cross_facility=True)`, `has_any_privilege`, the 403/404 conventions of #271 | PASS |
| VI. Async-First | All routes and services stay async | PASS |
| VII. Security by Default | Closes the facility gaps #271 left; every check is server-side; administrators short-circuit as everywhere | PASS |
| VIII. Ruff | Gate before each commit | PASS |
| Testing | Tests first for every changed service branch and route; tests committed with their code | PASS |
| Changelog | `[Unreleased]`: Fixed (#277 balances, #278 paging, the API refund's note shape); Changed (the facility rule on payment writes, sales orders, credit notes; `/unverified` company-wide; `CreditNoteResponse` fields) | PASS |

Post-design: no new violations.

## Project Structure

### Documentation (this feature)

```text
specs/034-credit-notes-facility/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/api.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── core/deps.py                                  # + can_see_every_facility
├── api/v1/endpoints/credit_notes.py              # facility parameter, 403/404, current passed to detail
├── api/v1/endpoints/customer_payments.py         # apply/reverse via _visible_payment_or_404; /unverified cross-facility; helper reuse
├── api/v1/endpoints/accounts_receivable.py       # helper reuse
├── api/v1/endpoints/sales_orders.py              # list facility 403; _writable_order_or_404 on 6 write routes
├── schemas/credit_note.py                        # amount, facility, refunded_to_customer; field meanings
└── services/
    ├── credit_note_service.py                    # one query: note + payment + applied; open rule in SQL; facility
    └── customer_refund_service.py                # _pay_customer_back writes refunded 0, cash_session NULL
tests/
├── unit/test_credit_note_service.py              # remaining and open rules
├── unit/test_deps_privileges.py                  # + can_see_every_facility
├── integration/test_credit_notes_flow.py         # NEW: balances, open paging, refund's note, facility
├── integration/test_facility_rule.py             # NEW: payments, sales orders, unverified as a scoped clerk
├── api/test_credit_notes.py                      # 403/404, parameters, new fields
├── api/test_customer_payments.py                 # apply/reverse 404; /unverified passes cross-facility
└── api/test_sales_orders.py                      # list 403; write 404; reads open
docs/specs/02-sales.md                            # credit notes and the facility rule
CHANGELOG.md
```

**Structure Decision**: the existing single-project layout. No new module.

## Complexity Tracking

No violations to justify.
