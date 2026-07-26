# Quickstart: Validating Sales Cycle Endpoints

How to prove this feature works. Details live in [contracts/README.md](./contracts/README.md) and
[data-model.md](./data-model.md); this file is the run guide.

## Prerequisites

```bash
uv sync                                  # dependencies (nothing new added by this feature)
cp .env.example .env                     # configure DATABASE_URL and JWT_SECRET_KEY
uv run python -m app.db.migrate status   # expect: no pending migrations for this feature
```

This feature adds **one migration**, `007_document_serial_unique.sql`. Apply it with
`uv run python -m app.db.migrate`. It nulls 4,240 `serial = 0` placeholders, renumbers 21 genuinely
duplicated folios, and then adds the unique index on `(facility, serial)` to the three document
tables. **The data changes are not reversible** — the rollback script drops the indexes only. Read
the header comment before applying it to a database you care about.

## Gates that must pass before the feature is done

```bash
uv run ruff check app/ migrations/ tests/    # zero violations (Constitution VIII)
uv run pytest tests/ -q                      # all green, including the new files
```

The constitution makes tests mandatory here — every new router needs a `tests/api/` file covering
happy path, 404, 401, 403 and its resource-specific 409/422 cases.

## Run the API

```bash
uv run uvicorn app.main:app --reload
```

Interactive docs at `http://localhost:8000/docs`. All routes require a bearer token from
`POST /api/v1/auth/login`.

---

## Scenario 1 — The core revenue path (US1, US2)

Proves an order can be built, confirmed, paid and unwound.

1. `POST /api/v1/sales-orders` with an empty body → a draft carrying the default customer, your
   employee as salesperson, your facility and point of sale, and terms derived from the customer's
   credit standing.
2. `POST /api/v1/sales-orders/{id}/lines` for a stockable product → the line carries the price from
   the customer's list, the product's tax rate, cost, and a code/name snapshot.
3. `POST /api/v1/sales-orders/{id}/confirm` → **expect**: `serial` populated, status completed, a
   negative `lot_serial_tracking` row per stocked line.
4. `POST /api/v1/customer-payments` then
   `POST /api/v1/customer-payments/{id}/applications` covering the total → **expect**: order balance
   zero, order marked paid.
5. `POST /api/v1/customer-payments/{id}/applications/{app_id}/reverse` **without** a reason →
   **expect 422**. Repeat **with** a reason → **expect**: application cancelled but still listed,
   balance restored, order no longer paid, and an `incidence` row naming who, when and why.

**Negative checks**: confirming an order with a zero-priced line returns 409 naming those lines;
applying a payment to a draft returns 409.

## Scenario 2 — Cash session lifecycle (US3)

1. `POST /api/v1/cash-sessions` with a drawer and opening amount → open session.
2. `POST /api/v1/cash-sessions` again on the same drawer → **expect 409**.
3. `GET /api/v1/cash-sessions/current` → open, with payments summarised by method.
4. `POST /api/v1/cash-sessions/{id}/close` with denomination counts → `end` set; `current` now
   reports no open session.

To exercise the stale path (FR-053), backdate a session's `start` and confirm `current` reports it
as stale — distinguishably from "none".

## Scenario 3 — The lifecycle rules (the point of the last clarification)

This is the scenario that catches regressions in the rules you set.

| Step | Expect |
|---|---|
| Refund an order that is confirmed but **unpaid** | **409**, reason says "not paid" |
| Refund an order that is **not confirmed** | **409**, reason says "not completed" — a *different* reason |
| Cancel an order that is **paid** | **409**, telling you to refund it instead |
| Cancel an order carrying a **partial** payment | **409**, naming the blocking applications |
| Reverse that application, then cancel | **Succeeds**; stock restored by a compensating positive ledger row, original negative row untouched |

## Scenario 4 — Refund a paid order (US5)

1. Complete Scenario 1 through step 4 so the order is paid.
2. `POST /api/v1/customer-refunds` with that order → pre-populated with refundable lines at
   quantity zero.
3. `PUT .../lines/{line_id}` setting a return quantity above what was sold → **expect 422**.
4. Set a valid quantity, then `POST /api/v1/customer-refunds/{id}/confirm` with
   `payout: "credit_note"` → **expect**: folio assigned, positive `lot_serial_tracking` row, a
   credit note for the **full** refund total, and the source order **still paid** (FR-064).
5. Repeat with `payout: "cash"` on another order → payout attributed to the open cash session and
   visible in that session's close.
6. Confirm a refund with **no open cash session** → **expect 409**.

**Over-refund check**: refund the same line twice for quantities that together exceed what was
sold → the second is capped or refused (SC-006).

## Scenario 5 — Quotes and credit notes (US4, US6)

1. `POST /api/v1/sales-quotes` → draft with `serial: null`.
2. Confirm → folio assigned. Duplicate → new draft dated today with prices re-fetched.
3. Convert → a draft sales order referencing the quote. Backdate a quote past its `due_date` and
   convert → **expect 409**.
4. `GET /api/v1/credit-notes?customer={id}` → the note from Scenario 4 with its remaining balance.
5. Redeem it via `POST /api/v1/customer-payments/{backing_payment_id}/applications` against another
   outstanding order → both the note's remaining balance and the order's balance fall.
6. Reverse that application → both are restored exactly (US6 scenario 4).

---

## Concurrency checks

These verify the two locking decisions in [research.md](./research.md) and cannot be caught by
single-threaded tests.

**Folio uniqueness (SC-005, R1)** — confirm two orders for the same facility simultaneously:

```bash
curl -X POST .../sales-orders/{id1}/confirm -H "$AUTH" &
curl -X POST .../sales-orders/{id2}/confirm -H "$AUTH" &
wait
```

Expect two **different** serials. The unique index added by migration 007 means a regression now
fails loudly rather than silently duplicating a folio; this check confirms the facility row lock is
still doing its job, so callers queue instead of one of them hitting a constraint violation.

**Over-refund (SC-006, R2)** — confirm two refunds of the same order line concurrently; their
combined quantity must never exceed what was sold.

## What "done" looks like

- [ ] `uv run ruff check app/ migrations/ tests/` clean
- [ ] `uv run pytest tests/ -q` green
- [ ] Scenarios 1–5 pass by hand against a real database
- [ ] Both concurrency checks pass
- [ ] `CHANGELOG.md` `[Unreleased]` updated (Constitution: Development Workflow)
- [ ] Migration 007 applied; no new model, no new dependency, no column change
