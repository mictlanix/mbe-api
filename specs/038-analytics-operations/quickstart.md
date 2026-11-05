# Quickstart: validating operations analytics (spec 038)

How to prove the feature works. Shapes are in [contracts/api.md](contracts/api.md), definitions in [data-model.md](data-model.md), decisions in [research.md](research.md).

**Prerequisite:** spec 037's cost fix (mictlanix/mbe-api#296) is on main, so margin, stock value and purchase prices share one cost basis.

## 1. Local

```bash
cd /Users/eddy/Projects/mictlanix/mbe-api-038
uv sync
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

Expected: everything passes, including:

- **Stock:**
  - on hand as of a date equals `stock_ledger.on_hand` over the same entries;
  - with a date dimension, each bucket equals the as-of sum at its end;
  - transit stock goes to the destination facility;
  - a date before the history start is refused.
- **Velocity, cover and restock:** fixture pairs on each side of every threshold (stock-out, below reorder, dead, slow, uncosted at 0 and at 99,999).
- **Kardex:** paging across any range continues the running balance exactly; values appear only with the cost privilege.
- **Receivables:**
  - `open_credit_orders(as_of=None)` equals `as_of=now` on fixtures;
  - past moments honour application, refund and credit-note dates, cancellations after T, and `balance_zeroed_time`;
  - the snapshot job is idempotent;
  - DSO and CEI from fixture month-ends;
  - refused on a non-month-end period.
- **Commissions:** a closed month reads stored lines, an open month equals `commission_service.compute`, and an agent without 112 sees only themself.
- **Fiscal:** coverage caps at the line quantity; invoiced + not invoiced = sold to the cent; status classes; output VAT.
- **Purchases:** the tax-included flag is ignored; previous price and change % via the window; price-0 lines are excluded from history.
- **Views:** every relative period on edge dates (31 March, 29 February, ISO week 53); a shared view runs with the opener's privileges; an invalid view is refused.
- **Schedules:**
  - next occurrence for each cadence (including day 31 in short months);
  - two claims of the same read value send once;
  - a missed run sends once;
  - ineligible recipients are dropped and recorded;
  - an owner without a family fails with the code;
  - an oversized attachment is skipped;
  - no mailer means skipped.
- **Security:** a sweep over every new route and export finds no cost, value, margin, turnover or spread key without the cost privilege (SC-006).

## 2. mbe_dev, read only, then migration 032

Before applying anything, record the EXPLAIN of the movement query for 2025 (expected: `ALL` on `lot_serial_tracking`, since `date` has no index).

Apply the migration on mbe_dev only after asking: `uv run python -m app.db.migrate`. Then:

| Check | Expected |
|---|---|
| EXPLAIN of movements for October 2025 | `range` on `lot_serial_tracking_date_idx` |
| Seven new tables exist, empty | yes |

The receivables backfill writes rows, so it also needs the user's OK: `uv run python -m app.jobs.snapshot_receivables --from 2025-01`. Record the time it takes and the rows per month.

## 3. Reconciliation on mbe_dev

The branch is deployed to dev :8765.

- **Stock today (SC-001):** for every product × warehouse pair, on hand from `POST /analytics/query` equals `GET /stock` (spec 030). Expected: zero differences.
- **Stock in the past:** for 20 pairs, as of 10 dates since 2025-02-07, the result equals the spec 030 kardex balance at that date.
- **Receivables today (SC-002):** by customer and bucket, the query equals `GET /accounts-receivable` (spec 033) to the cent.
- **Receivables history:** rebuilding 3 month-ends with `--rebuild` gives identical rows.
- **Commissions (SC-003):** for 3 agents and 2 months, the totals equal `GET /commissions/periods/{y}/{m}` for each agent, and the `commission_lines` sums equal them.
- **Invoice coverage (SC-004):** for 3 months, invoiced + not invoiced = sold, and the `uninvoiced_lines` detail sums to not invoiced.
- **Purchases:** a month by supplier equals the sum of that month's confirmed PO lines from `GET /purchase-orders`.

## 4. Performance on mbe_dev (SC-009)

Run each request 30 times warm, with the cache cleared, and take p95.

| Request | Target |
|---|---|
| Dashboard with the new tiles, one month, all facilities | < 2 s |
| Same, one year | < 5 s |
| Stock as of a date, all pairs, by warehouse × product | < 5 s |
| Receivables by customer × bucket over 12 month-ends | < 5 s |
| Commissions, 12 months, by agent × label (all live on mbe_dev, since no month is closed) | recorded; < 5 s target |
| Kardex of the busiest product × warehouse, 2025-02-07 to today, first and last page | < 5 s |

Record the numbers in research R18. If a target is missed, stop and report before redesigning.

## 5. Scheduled reports

- **Without mail:** on dev, mail isn't configured. Schedule a view, set `SCHEDULED_REPORTS_ENABLED=true` for one shell only, and run the job by hand. Expected: an `analytics_send` row with outcome `skipped` and code `email.not_configured`.
- **With mail:** a real send needs SMTP settings from the user and their OK, since it is real email. Expected: one email per run, with the attachment.
- **Concurrency:** run the job twice at once against the same due schedule. Expected: exactly one send row for the occurrence.
- **Cron:** installing the cron entry (`*/5 * * * *` as the instance's user) on any server is a deployment step that needs confirmation.

## 6. Legacy questions (SC-005)

For each row of the spec's legacy table marked Q, D, Detail or V, run the stated request on dev and record the request and a one-line result in the verifications.

## 7. Hand-off to mbe-ui

After merge, open an mbe-ui issue that links this contract and lists:
- the new keys, kinds, tiles and detail kinds;
- the views and schedules routes;
- the six new `analytics.*` codes for the Spanish catalog (mictlanix/mbe-ui#232);
- `AnalyticsAlert.detail` is now nullable (null for `payables_overdue`, which has no detail kind).
