# Quickstart: validating Commissions with Stable History

The routes are specified in [contracts/commissions.md](contracts/commissions.md), the tables in [data-model.md](data-model.md), and the decisions in [research.md](research.md).

## Automated gate

```bash
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

| Requirement | Covered by |
|---|---|
| FR-004, FR-015–FR-017, FR-009a (rules) | `tests/unit/test_commission_rules.py`, the pure stage-2 function over hand-built facts: each participation branch; NULL csp; unlabelled product; missing rate. **Scheme 1:** paid month only; refund before payment counted once; refund of an earlier month's order. **Scheme 2:** 40/60 instalments; the spec's net-of-refunds example ($40, −$12.86, +$42.86 = $70); reversal clawback. **Switches** 2→1 and 1→2 pay only the remainder. **Keys** frozen from stored lines after a reassignment and a rate change. A property-style test: across random instalments, refunds, reversals and switches over 6 months, the sale-commission sum per key never exceeds `full` (SC-006) |
| FR-004, FR-008, FR-009 (facts) | `tests/integration/test_commission_compute.py`: seeded orders, applications and refunds on the SQLite schema; stage 1 selects the right candidates for (a)–(d); scheme 1 with no stored lines equals the current system's query result on the same data (SC-002 parity in miniature) |
| FR-006–FR-007, FR-010–FR-012, FR-010a (periods) | `tests/integration/test_commission_periods.py`: close refused before month end and out of sequence; close stores lines; reassign, change a rate and relabel, then re-read is identical (SC-001); reopen only the latest; pay is final; the April-before-March case is refused |
| FR-002–FR-003, FR-014, schemes on agents | `tests/api/test_commission_settings.py`, `tests/api/test_commission_periods.py`: each route's 200/401/403/404/409/422; one incidence per settings change |
| FR-008a | `tests/unit/test_customer_payment_service.py`: the stamp is set when paid and never cleared |

## Migration on mbe_dev

Confirm with the user before applying any DDL to mbe_dev, then run `uv run python -m app.db.migrate`.

```sql
SHOW COLUMNS FROM commission_agent LIKE 'scheme';  -- smallint, default 1; all 6 agents = 1
SHOW TABLES LIKE 'commission_period%';             -- 2 tables
SELECT COUNT(*) FROM sales_order WHERE paid=1 AND completed=1 AND cancelled=0
  AND date >= '2025-10-01' AND (balance_zeroed_time IS NULL OR balance_zeroed_time < '1900-01-01');  -- 0 (was 38)
SELECT COUNT(*) FROM commissions_history;          -- 1650, unchanged (SC-005)
```

## Parity check against the current system (SC-002)

Read only, on mbe_dev, with every agent on scheme 1 and no closed periods. For September 2026:
1. Run the current system's query (`ReportsController.cs:665-786`, with the range 2026-09-01 to 2026-09-30 23:59:59 and all agents).
2. Run `GET /commissions/periods/2026/9`.
3. Compare the totals per (agent, label).

Expected: equal, except for orders whose `modification_time` and `balance_zeroed_time` months differ (about 2 a month) and the refunds that follow them. List every difference and its order, and record the comparison here.

### Result: September 2026, run 2026-10-22

**Parity holds, with no unexplained difference.** The check ran read only on mbe_dev, after migration 023, with all 6 agents on scheme 1 and no period rows.

| | Total | Rows |
|---|---:|---:|
| Current system: `GetCommissionDetailsSQLQuery`, all agents, 2026-09-01 to 2026-09-30 23:59:59 | 128,061.12 | 802 |
| New MBE: `compute(2026, 9)` | 129,536.82 | 795 |

Every legacy line with a non-zero commission matches a new line on order, line, agent, participation, movement, total and commission. Cent rounding made no difference this month. Agent 76 has a 0 rate everywhere, so its rows are $0 on both sides.

**Money: +1,475.70, from 4 orders (FR-008).** None of the 4 has a payment application. Each was completed in August, so `modification_time` falls in August. Each was settled in September by a full refund, which stamps `balance_zeroed_time` (`CustomerRefundsController.cs:343-348`) but leaves `modification_time` alone.

| Order | Agent | `modification_time` | `balance_zeroed_time` | Current system, Sept | New MBE, Sept |
|---|---|---|---|---:|---:|
| 340435 | 33 | 2026-08-26 | 2026-09-05 | −65.00 | 0 (+65.00 −65.00) |
| 340557 | 17 | 2026-08-27 | 2026-09-03 | −434.00 | 0 (+434.00 −434.00) |
| 340816 | 54 | 2026-08-29 | 2026-09-02 | −793.10 | 0 (+793.10 −793.10) |
| 340974 | 77 | 2026-08-31 | 2026-09-01 | −183.60 | 0 (+183.60 −183.60) |

- **Current system:** counts these sales in August and claws them back in September through the PR #58 branch.
- **New MBE:** places the sale and its refund in the same month, so August is lower by the same 1,475.70. The two months agree in total.
- **Why the new MBE's version is right:** an August report printed before the refund would not have shown these sales, because the orders were still unpaid. The September clawback would then charge commission that was never paid.
- **Completeness:** a probe for every agent order whose two dates fall in different months, with one of them in September, returned exactly these 4.

**Rows: 15 legacy refund rows at 0.00 that the new MBE does not emit.** They belong to agent 76, one unlabelled product and one 0-rate label. Research R1 says only non-zero amounts become lines; zero lines are listed only for sales.

## Access

Measured at plan time: 5 of 33 active non-admin users hold Read on SystemObject 112 (3 with full rights). Re-check before deploying.
