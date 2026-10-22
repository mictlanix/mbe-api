# Quickstart: validating Commissions with Stable History

The routes are specified in [contracts/commissions.md](contracts/commissions.md), the tables in [data-model.md](data-model.md), and the decisions in [research.md](research.md).

## Automated gate

```bash
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

| Requirement | Covered by |
|---|---|
| FR-002–FR-003, FR-014 (settings, access, audit) | `tests/api/test_commission_settings.py` |
| FR-004–FR-005, FR-009 (computation) | `tests/integration/test_commission_compute.py`, seeded orders and refunds on the SQLite schema: each participation branch; NULL customer salesperson; unlabelled product; missing rate row; refund before payment (counted once); refund of an earlier month's order; tax-included and tax-excluded prices |
| FR-006–FR-007, FR-010–FR-012 (periods) | `tests/integration/test_commission_periods.py`: close refused before month end; close stores lines; reassign customer, change rate and relabel product, then re-read is identical (SC-001); reopen discards; pay is final; no line in two months (SC-003) |
| FR-008, FR-008a (paid date) | `tests/unit/test_customer_payment_service.py`, stamped when paid and never cleared; `tests/integration/test_commission_compute.py`, an order joins the month of its stamp |
| Migration 023 | applied to mbe_dev, row counts below |

## Migration on mbe_dev

Confirm with the user before applying any DDL to mbe_dev. Then run `uv run python -m app.db.migrate`.

```sql
SHOW TABLES LIKE 'commission_period%';           -- 2 tables
SELECT COUNT(*) FROM sales_order WHERE paid=1 AND completed=1 AND cancelled=0
  AND date >= '2025-10-01' AND (balance_zeroed_time IS NULL OR balance_zeroed_time < '1900-01-01');  -- 0 (was 38)
SELECT COUNT(*) FROM commissions_history;          -- 1650, unchanged (SC-005)
```

## Parity check against the current system (SC-002)

Read only, on mbe_dev. For September 2026:
1. Run the current system's query (`ReportsController.cs:665-786`, with the range 2026-09-01 to 2026-09-30 23:59:59 and all agents).
2. Run `GET /commissions/periods/2026/9`.
3. Compare the totals per (agent, label).

Expected: equal, except for orders whose `modification_time` and `balance_zeroed_time` months differ (about 2 a month), and the refunds that follow them. List every difference and its order. Record the comparison in this file.

## Access check

Measured at plan time: 5 of 33 active non-admin users hold Read on SystemObject 112 (3 with full rights). Re-check before deploying.
