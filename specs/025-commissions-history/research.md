# Research: Commissions with Stable History

The spec's Findings and both clarification sessions record the current system and the decided rules: two schemes, the net-of-refunds share, and refunds charged to the agent who was paid. This file records the design.

## R1. Computing a month: SQL gathers facts, Python applies the rules

**Decision**: `commission_service.compute(db, year, month, *, salesperson=None) -> list[Line]` works in two stages.

**Stage 1, the facts (SQL, `text()`).** Three queries over the month `[first 00:00, next month 00:00)` in business time:

1. **Candidate order lines.** These are the lines of completed, non-cancelled orders where the order's or the customer's salesperson is an agent, and at least one of these holds:
   - (a) **scheme 1 sales:** the order is paid now and either its `balance_zeroed_time` falls in the month, or it falls in an earlier month on or after the chain start (the first closed month, R3) **and the line has no stored sale line yet**. The second arm picks up an order whose payment was reversed before its own month closed and that was paid again later: its stamp stays in the closed month, and without this arm it would never earn (analysis C2);
   - (b) an application to the order is dated in the month: **scheme 2 sales**;
   - (c) a refund of one of its lines is dated in the month, or dated on or before the month end for an order selected by (a) in this month;
   - (d) the line has **any** stored scheme 2 sale line. A fully stored line must stay a candidate, because a later reversal has to claw it back (analysis C1). The rules drop the zero amounts. This set grows with every scheme 2 line ever stored; at mbe_dev's volume, about 2.5% of agent orders paid across months, it stays in the thousands. If it ever matters, bound it with an `order_fully_settled` flag on the stored line, which is not needed now.

   Each candidate row carries:
   - `osp` and `csp`;
   - the unit price `ROUND(price*(1-discount_rate)*(1+CASE WHEN tax_included THEN 0 ELSE tax_rate END),2)`;
   - quantity, product, customer and names;
   - the order's `balance_zeroed_time` (`'0001-01-01'` counts as unset);
   - the current label and rate (`commission_product` → `commission`).
2. **Per-order money as of the month end:**
   - `applied` = Σ `sales_order_payment.amount` over applications that are not cancelled, dated `COALESCE(sop.date, cp.date) < next`;
   - `refunded` = Σ of the refund line totals, completed and not cancelled, `customer_refund.date < next`;
   - `order_total` = Σ of the line totals (`ROUND(unit price × quantity, 2)`).
3. **Refund lines of the candidates:**
   - `customer_refund_detail_id`, the line, the quantity;
   - the unit price `ROUND(-1*price*(1-discount)*(1+…),2)`;
   - the refund's `date`.

Agent rates (`commission_salesperson`) and agent schemes (`commission_agent.scheme`) are read once, as small dicts. The stored lines for the candidate lines come from `commission_period_line`, closed periods only.

**Stage 2, the rules (pure Python, `commission_rules.py`).** For each candidate line, the participation branches decide the (agent, participation) keys:
- CLIENTE VITALICIO if `csp == osp`;
- otherwise CANALIZACIÓN to osp and ATENCIÓN EN CAMPO to csp;
- an agent filter applies, and **a NULL csp yields no key**, for parity.

**Key freeze (FR-009a and stability):** if the line already has stored lines, its keys, participations and **rates** are those stored, whatever the current salesperson or settings say. Only a line with no stored line uses the current settings.

For each key:

| | Sale amount this month | Refund lines this month |
|---|---|---|
| Agent on **scheme 1** | if (a): `full − stored_sales(key)`; 0 otherwise | each refund detail that falls in this month by the legacy rule (dated ≤ month end and the order paid this month, or dated in the month and the order paid earlier), **not already stored for this key**, as `ROUND(refund total × rate, 2)` negative |
| Agent on **scheme 2** | `ROUND(full × min(applied ÷ (order_total − refunded), 1), 2) − stored_sales(key)` | each refund detail dated in the month, not already stored for this key, as a negative line |

Where:
- `full = ROUND(line total × rate, 2)`;
- `rate = label rate × participation share`;
- `stored_sales(key)` = Σ commission of the stored sale lines for that key.

Only non-zero amounts become lines. The agent's **current** scheme decides the column, and the stored amounts make a switch pay only the remainder (FR-017).

**Rationale**:
- The two schemes, the cap and the key freeze are cross-row rules that read stored history. One SQL statement can't express them readably, but a pure function over facts can be unit-tested exhaustively (constitution, Testing).
- Stage 1 keeps the round trips per month fixed: 3 queries plus the stored lines.
- **Scheme 1 parity (SC-002):** with no stored lines and every agent on scheme 1, stage 2 reduces exactly to the current system's query with the two documented date changes. A test asserts the reduction on a seeded month.

**Alternatives considered**:
- Porting the legacy statement whole and adding scheme 2 beside it: two code paths for one set of rules.
- Computing in Python from ORM objects: N+1 queries over thousands of orders.

**Bounds**: (d) grows only with partly earned scheme 2 lines. A line whose stored sum reaches its full commission leaves the candidate set, unless a refund or a reversal brings it back through (b) or (c).

**Reversals**: a cancelled application leaves `applied` (FR-016). The order stays a candidate through (d), so the next computation yields a negative sale amount.

**Rounding**: per line, as in the current system. The scheme 2 cumulative form recomputes `ROUND(full × share)` each month and subtracts what was stored, so the sum of the months equals `ROUND(full × final share)` exactly.

## R2. Storage (migration 023)

**Decision**:
- **Scheme column:** `commission_agent.scheme SMALLINT NOT NULL DEFAULT 1`. The current system maps only `employee`, and agents are created directly in the database, so a defaulted column is invisible to it. Every existing agent starts on scheme 1, the current system's rule.
- **`commission_period`:** year and month, status 0 open / 1 closed / 2 paid, and who/when for close, reopen and pay. `UNIQUE(year, month)`.
- **`commission_period_line`:** the frozen lines (data-model.md). Each carries its `scheme`, its `movement` (0 sale, 1 refund), `customer_refund_detail` (refunds), `share` (scheme 2 sales: the cumulative share at month end) and `full_commission`, along with every FR-005 value copied.
  - Index `(sales_order_detail, salesperson, participation)` for the per-key sums.
  - Index `(commission_period, salesperson)` for reads.
- **Backfill:** `sales_order.balance_zeroed_time` for paid orders since 2025-10-01 that have no stamp (38 rows), from their last application's date (R6).

**Rationale**: stored lines are both the history (FR-007) and the ledger the cap reads (FR-017). A separate ledger would be the same data twice.

## R3. Close, reopen, pay: months close in sequence

**Decision**: each runs in one transaction. Periods form a contiguous chain:

- **Close month M:**
  - **Refusals:**
    - the month hasn't ended (409 `Period has not ended`);
    - it is closed or paid (409);
    - **the previous month is not closed while some period has already been closed** (409 `The previous period is not closed`). The first close ever starts the chain.
  - **Effect:** `compute(all agents)`, insert the lines, upsert the row with `status = 1`.
- **Reopen M:** only from status 1, and only the **latest** closed month (409 `A later period is closed`). It deletes the lines and sets `status = 0`, `reopened_by` and `reopened_at`.
- **Pay M:** only from status 1, and sets `status = 2`. Paying needn't follow the order: an earlier closed month can be paid after a later one.

**Why sequential**: the scheme 2 cumulative formula subtracts what *earlier* months stored. If April closed before March, April would store 100% of an order paid 40% in March and 60% in April. Then March would compute 40% − 100% and store −60%, leaving the agent $40 for an order that earned $100. Closing in order makes every month's "already stored" mean exactly "the months before". Reopening only the latest closed month keeps the same guarantee in reverse.

**Where the chain starts**: months before the first close can never be closed afterwards, so they stay open and are computed live. Under scheme 1 they keep only the current system's report. Under scheme 2, the first closed month pays the cumulative share earned up to its end, including payments from before the chain, because nothing was stored earlier. Nothing is paid twice, and nothing earlier is lost. The first month to close should be chosen deliberately; the spec's Assumptions note it.

**Locking**: `SELECT … FOR UPDATE` on the latest period row and on M's row, plus `UNIQUE(year, month)`, so two closes cannot interleave.

## R4. Reading history

**Decision**: `GET /commissions/periods/{year}/{month}?salesperson=` returns:
- `status` and `final`;
- the audit fields;
- `lines` (FR-005 plus `scheme`, `movement`, `share`, `full_commission`, `customer_refund_detail`);
- `totals` per (agent, label).

Closed and paid months read stored lines; an open month runs `compute`. `GET /commissions/periods` lists the months that have a row.

## R5. Settings routes over the shared tables

As before, CRUD for labels, participations, agents, agent rates and product labels, with these changes:
- **Agents** carry `scheme` (1 or 2): `POST /agents {employee, scheme=1}` and `PUT /agents/{employee} {scheme}`. A scheme change takes effect at the next computation; closed months keep what they stored.
- **Audit:** each change writes an `incidence` with `SourceType.COMMISSION = 12` (API-only).
- **Deletes:** a delete refused by a foreign key gives 409.
- **`commission.comment`:** `NOT NULL DEFAULT '0'`, so creates send `comment or '0'`.

## R6. Stamping `balance_zeroed_time` (FR-008a)

`customer_payment_service._sync_paid_flag` sets `balance_zeroed_time = local_now()` when the order becomes paid and the column is NULL or the `0001-01-01` sentinel, and never clears it. Migration 023 backfills paid orders dated from 2025-10-01 that have no stamp, from the latest non-cancelled application's date (falling back to `modification_time`). Spec 011's FR-065a gets a note.

## R7. Access

SystemObject 112, CommissionsBySalesPerson:
- **Read:** GET.
- **Create:** settings rows.
- **Update:** settings edits, product labels, scheme changes, close, reopen and pay.
- **Delete:** settings deletes.

Measured in mbe_dev: 5 of 33 active non-admin users hold Read on 112 (3 with full rights). The current system's report checks only the login.

## R8. Documentation

- **`docs/specs/02-sales.md` §10 Commissions:**
  - the current system (settings, computation, refunds and the September fix, no periods, report, ticket and CSV, access, `commissions_history`);
  - the new rules (monthly periods; close, reopen and pay; stable stored lines; the two schemes per agent with the scheme 2 formula and worked example; refunds charged to the agent who was paid; the paid-date and refund-date basis).
- **Data docs:**
  - `docs/data-dictionary.md`: the two tables, plus `commission_agent.scheme`;
  - `docs/mbe_schema.sql`;
  - `docs/constants.md`: SourceType 12.
- **Other specs:**
  - `docs/specs/11-reports.md` §37: a note pointing at the period read;
  - spec 011, FR-065a: a note.
