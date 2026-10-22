# Research: Commissions with Stable History

The spec's Findings and Clarifications record the current system and the decided rules. This file records the design.

## R1. The computation: a port of the current system's query, with stable dates

**Decision**: `commission_service.compute(db, year, month, *, salesperson=None)` runs one SQL statement through `text()`. It ports `ReportsController.GetCommissionDetailsSQLQuery` as it stands after mbe PR #58 (`ReportsController.cs:665-786`), with exactly three changes:

1. **Order date:** an order belongs to a month by `sales_order.balance_zeroed_time`, not `modification_time` (FR-008). `balance_zeroed_time = '0001-01-01'` counts as unset.
2. **Refund date:** a refund belongs to a month by `customer_refund.date`, not `modification_time` (FR-009).
3. **`payable`:** the legacy column and its `paid` flags are dropped. Every line the query returns is payable, because the query already restricts itself to paid orders.

Everything else is kept as written:
- the CTEs `details_orders`, `details_refunds` (both branches: refunds of this month's orders dated up to the month end; refunds dated this month on orders paid before it) and `details`;
- the three participation branches with their `csp = osp` / `csp != osp` tests and their LEFT JOINs to `commission_product` → `commission` → `commission_salesperson`;
- the price formula `ROUND(price*(1-discount)*(1+IF(tax_included,0,tax_rate)),2)`;
- `total = ROUND(price*quantity,2)` and `commission = ROUND(price*quantity*rate,2)`;
- the agent filter (`IN (SELECT employee FROM commission_agent)` or `= :salesperson`).

**Rationale**:
- SC-002 asks for parity with the report agents are paid from today. Porting the statement keeps its edge behaviour, which the spec documents: a NULL customer salesperson earns nothing, and an unlabelled product lists at 0.
- One statement is one round trip for a month: about 3,000 paid orders a month in mbe_dev.
- The two date changes are the only rule changes the spec makes.

**Alternatives considered**: re-expressing the rules in Python over ORM queries. That is more code, and divergence from the legacy SQL is likely, which SC-002 is meant to catch.

**Parameters**: the month bounds are `[first day 00:00, first of next month 00:00)` in business local time; the legacy `BETWEEN … end` becomes `< next`. MariaDB-specific syntax (`IF`, `IFNULL`) is kept. The integration tests run on SQLite, which has `IFNULL` but not `IF`, so the port uses `CASE WHEN` for the one `IF` it needs, which is portable.

## R2. Periods and stored lines (new tables)

**Decision**: migration `023_commission_periods.sql` creates two tables. The full columns are in data-model.md.

- **`commission_period`:** one row per closed (or reopened) month.
  - Columns: `year`, `month`, `status` (0 open, 1 closed, 2 paid), and who/when for close, reopen and pay.
  - `UNIQUE(year, month)`.
  - A month with no row is open. Reopening sets `status = 0` and deletes the stored lines, but keeps the row and its audit columns.
- **`commission_period_line`:** the frozen lines, one row per (period, source line, agent, participation).
  - It holds every FR-005 value, with names and rates copied, not referenced.
  - The only FKs are `commission_period` and the employee. Order, line, product and customer ids are kept for traceability, but not as FKs, so editing master data never touches history.

**Rationale**:
- FR-007 says closed periods must not change when customers, settings or orders change, so every value is copied, not joined.
- Keeping the period row through a reopen preserves who closed it and who reopened it (FR-010).

**Alternatives considered**:
- Reusing `commissions_history`: ruled out by the clarification (stale table).
- One JSON blob per period: the report needs to filter and sum by agent and label, which rows do and a blob doesn't.

## R3. Closing, reopening, paying

**Decision**: each is a service function that runs in one transaction.

- **Close:**
  - **Refusals:** a month that hasn't ended in business time (409 `Period has not ended`), or one already closed or paid (409).
  - **Effect:** runs `compute` for all agents, inserts the lines, and upserts the period row with `status = 1`, `closed_by` and `closed_at`.
- **Reopen:** only from status 1; paid gives 409. Sets `status = 0` and `reopened_by`/`reopened_at`, and deletes the lines.
- **Pay:** only from status 1; open gives 409 `Close the period first`, paid gives 409. Sets `status = 2` and `paid_by`/`paid_at`.

All three require `COMMISSIONS_BY_SALES_PERSON` Update (FR-014).

**Concurrency**: `UNIQUE(year, month)` plus a status check inside the transaction (`SELECT … FOR UPDATE` on the period row) stops two closes, or a close and a reopen, from interleaving. SQLite tests skip the row lock and still test the state rules.

## R4. Reading history

**Decision**: `GET /commissions/periods/{year}/{month}` with an optional `salesperson` returns:
- `status`;
- the audit fields;
- `final` (true once closed);
- `lines` (FR-005);
- `totals` per (agent, label): line count and commission sum.

A closed or paid month reads its stored lines. An open month runs `compute`. `GET /commissions/periods` lists the months that have a row, with their status and totals per agent.

**Rationale**: one shape for open and closed months, so the report doesn't care which it reads, and `final` tells it whether the numbers can still move (FR-012).

## R5. Settings routes over the shared tables

**Decision**: CRUD routes under `/commissions/...` for the five existing tables, which are already mapped in `app/models/commission.py` (FR-002, FR-013):

| Resource | Routes | Rules |
|---|---|---|
| Labels (`commission`) | list, create, update, delete | rate in [0, 1]; name required (≤50) |
| Participation types (`commission_participation`) | list, create, update, delete | name required (≤50) |
| Agents (`commission_agent`) | list, add (`employee`), remove | the employee exists; adding twice is a 409 |
| Agent rates (`commission_salesperson`) | list (filter by agent or label), create, update, delete | the agent is in `commission_agent`; share in [0, 1]; unique (agent, label, participation) → 409 |
| Product labels (`commission_product`) | list (filter by label or product), set (PUT `/commissions/product-labels/{product}` `{"commission": id}` creates or **changes** it), remove | one per product (unique) |

- **Deletes:** a delete refused by a foreign key (a label in use, an agent with rates, a participation in use) gives 409 with a message naming what references it. The existing `IntegrityError` → 409 handling covers this (`tests/api/test_constraint_errors.py`).
- **Audit:** each settings change writes an `incidence` with a new `SourceType.COMMISSION = 12`. It is API-only, as are 10 and 11. `instance_id` is the changed row's id (the employee id for agents, the product id for product labels), and the content names the table and the change. This satisfies SC-004's "records who made it" with the project's existing mechanism.
- **Missing column:** `commission.comment` is `NOT NULL DEFAULT '0'` in the schema but nullable in the model. Creates send `comment or '0'` to match the database.

## R6. Stamping `balance_zeroed_time` (FR-008a)

**Decision**:
- **Stamping:** in `customer_payment_service._sync_paid_flag`, after computing `paid`, set `order.balance_zeroed_time = local_now()` when the order is now paid and the column is NULL or the `0001-01-01` sentinel. It is never cleared.
- **Backfill:** migration 023 sets, for `paid = 1 AND completed = 1 AND cancelled = 0 AND (balance_zeroed_time IS NULL OR balance_zeroed_time < '1900-01-01') AND date >= '2025-10-01'`, `balance_zeroed_time = ` the latest `customer_payment.date` among that order's applications that are not cancelled, falling back to `modification_time`. There are 38 rows in mbe_dev.
- **Docs:** spec 011's FR-065a gets a note that its reading of the column was corrected by spec 025.

**Rationale**: this matches `PaymentsController.cs:1458/1526/1600/1948`, so orders paid in either system follow one rule. The backfill window covers the year of data a manager might reasonably compute.

## R7. Access

**Decision**: `SystemObject.COMMISSIONS_BY_SALES_PERSON` (112):
- **Read:** every GET.
- **Create:** creating settings rows.
- **Update:** editing settings, setting a product label, close, reopen and pay.
- **Delete:** deleting settings rows.

Administrators bypass, as everywhere. Measured in mbe_dev: of 33 active non-admin users, 5 hold Read on 112 (2 Read only, 3 full) and 27 hold nothing. The current system's report checks no privilege, so this API is stricter by design (FR-014).

## R8. Documentation

- **`docs/specs/02-sales.md` §10 Commissions (FR-001):** the current system (settings, computation with the three branches and the refund rules, no periods, report, ticket and CSV, access, `commissions_history`'s status) and the new rules (calendar-month periods, close, reopen and pay, stable stored lines, the paid-date and refund-date basis, settings routes).
- **Data docs:** the new tables go into `docs/data-dictionary.md` and `docs/mbe_schema.sql`, which `test_data_dictionary.py` and `test_model_schema.py` enforce. `docs/constants.md` gains SourceType 12.
- **Report spec:** `docs/specs/11-reports.md` §37 gets a note that it reads `/commissions/periods/{year}/{month}`.
