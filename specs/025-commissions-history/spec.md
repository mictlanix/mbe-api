# Feature Specification: Commissions with Stable History

**Feature Branch**: `025-commissions-history`

**Created**: 2026-10-22

**Status**: Draft

**Input**: Implements mictlanix/mbe-roadmap#32 (the scope spec) and mictlanix/mbe-roadmap#33 (the API), in epic mictlanix/mbe-roadmap#3. Report 11 §37, "Commissions by Salesperson", depends on it.
- **Scope document:** commissions appear nowhere in `docs/specs/02-sales.md`. The request asks for a "§2.10 Commissions" scope section; the document's sections run §1–§9, so it becomes **§10 Commissions**. The section describes how the current system computes commissions, who earns them and how a period is paid, and adds the rule this system introduces: **history is stable**.
- **API:** maintain commission settings, compute a period, close and pay it, and read the history the report needs.

## Findings that shape this spec

### The current system (`mictlanix/mbe`): commissions are a live report, not a ledger

- **Settings** (maintained directly in the database; `CommissionsController` has no working screen):
  - **Labels** (`commission`): a name and a rate, 13 of them, e.g. CONCRETOS 2.5%, TABIQUES 4%, FERRETERÍA 4%, ARMEX 0.2%.
  - **Product → label** (`commission_product`): one label per product, 1,123 products. The report can add a missing label inline (`SetCommissionLabel`), but cannot change one.
  - **Agents** (`commission_agent`): 6 employees (17, 33, 54, 76, 77, 78).
  - **Participation types** (`commission_participation`):
    1. CLIENTE VITALICIO
    2. CANALIZACIÓN
    3. ATENCIÓN EN CAMPO
  - **Agent rates** (`commission_salesperson`): a share per (agent, label, participation). Today it is 100% for CLIENTE VITALICIO and 50% for ATENCIÓN EN CAMPO (added by `mbe-26.10.sql`), and one CANALIZACIÓN row at 50%.
- **Computation** (`ReportsController.GetCommissionDetailsSQLQuery`, as fixed by mbe PR #58 on 2026-09-13):
  - **Lines:** every line of a completed, non-cancelled, paid sales order whose order `modification_time` falls in the date range the user typed.
  - **Line amount:** the line's price after discount, tax included, times quantity.
  - **Who earns it:** two salespeople are compared, `osp` (the order's salesperson) and `csp` (the customer's salesperson, read **live from the customer at report time**).
    - **CLIENTE VITALICIO:** when `csp = osp`, at csp's rate.
    - **CANALIZACIÓN:** when they differ, to osp at osp's rate.
    - **ATENCIÓN EN CAMPO:** when they differ, to csp at csp's rate.
  - **Rate:** label rate × participation rate. **Commission:** line amount × rate, rounded to cents. A product with no label, or an agent with no rate row, earns 0, and the line still lists.
  - **Refunds** are negative lines:
    - in the period the order was paid, for refunds up to the period end;
    - in the refund's own period, for an order paid in an earlier period. The `so.modification_time < start` guard is the September fix.
- **Periods:** there is no period. The user types any date range, the report recomputes from live sales every time, and **nothing is ever closed or marked paid**.
  - The agent ticket (`PrintCommissionTicket`) sums the lines by label.
  - `DownloadCSV` exports all agents' lines.
- **Access:** the report requires only a login. SystemObject 112, CommissionsBySalesPerson, controls only the menu entry and the inline label assignment.
- **`commissions_history`** is a table nothing reads or writes. It is a manual snapshot of the report's output from May to September 2025 (inferred from its columns and types), with `paid` set on most rows.
- **The September 2026 correction** (mbe PR #58): agents were over-debited **$23,196.76 across 15 months** because a refund booked before its order was paid was charged twice, once in its own month and once in the month the order was paid.
  - It was a refund-timing bug in the query, not a customer reassignment.
  - The fix changed the query. No data was moved, because there was none to move: the report recomputes everything.
  - The same PR also fixed the participation branches, which had given the customer's agent 100% on orders someone else sold (−11% of total commission).

### Why history is not stable today

- Because `csp` is the customer's salesperson **at the time the report runs**, reassigning a customer re-credits every past period the next time anyone runs it.
- Any change to rates or labels also rewrites the past.
- A period "paid" outside the system can show a different amount on the next run.

### Measured in mbe_dev (2026-10-22)

| Measure | Value |
|---|---|
| `commissions_history` rows | 1,650, dated 2024-06-13 → 2025-09-15; last modified 2025-09-15; nothing newer |
| … marked `paid` / `confirmed` | 1,347 / 0 |
| … negative (refund) lines | 61 |
| … total commission | $309,765.05 |
| … by participation | CLIENTE VITALICIO 1,625 · ATENCIÓN TELEFÓNICA (an old literal, no longer in the catalog) 24 · ATENCIÓN EN CAMPO 1 |
| History rows whose agent is **no longer the customer's salesperson** | **230 rows, 8 customers, $22,538.53**. The live report would now credit these to someone else. |
| Customer reassignments recorded anywhere | none. The customer incidences' JSON snapshots carry `SalesPersonId: null`, so reassignments leave no trace. |
| Paid orders in the last year (`completed`, not cancelled) | 35,296 with `balance_zeroed_time` set |
| … whose `modification_time` month differs from the `balance_zeroed_time` month | 19, orders a later edit would move to another period |
| Customers with paid orders involving an agent, last year | 804 |
| Commission settings | 13 labels; 1,123 products labelled; 6 agents (agent 76 has no rate rows); 3 participation types; 129 agent-rate rows: 64 CLIENTE VITALICIO at 100%, 64 ATENCIÓN EN CAMPO at 50%, 1 CANALIZACIÓN at 50% |

## Clarifications

### Session 2026-10-22

- Q: What happens to `commissions_history`? → A: Nothing. The current system has only its on-the-fly report, and `commissions_history` is a stale table. The history design starts from scratch: new periods and stored lines. The legacy table is neither read nor migrated (FR-013).
- Q: Which date places a paid order in a period? → A: `balance_zeroed_time`, which is set once when the balance reaches zero. This API never stamped it, so its payment path now stamps it the way the current system's `PaymentsController` does (FR-008, FR-008a).
- Q: What is a commission period? → A: A calendar month in the business time zone, created on demand and identified by its year and month. Periods therefore never overlap or leave gaps (FR-006, FR-010).
- Q: Can a closed but unpaid period be reopened? → A: Yes, until it is paid. Reopening discards its stored lines and returns it to open, so the next close recomputes it with current data. It requires Update on commissions, and who reopened it and when is recorded. A paid period never reopens (FR-010, FR-011).
- Q: Does "from scratch" also cover the commission settings? → A: No. This API maintains the existing shared settings tables (`commission`, `commission_product`, `commission_agent`, `commission_participation`, `commission_salesperson`), which the current system's live report also reads, so both agree during the pilot. Only periods and stored lines are new (FR-002, FR-013).
- Q: When may a month's period be closed? → A: Only after the month has ended in business time. Closing October is refused before November 1st (FR-007).

### Session 2026-10-22 (commission schemes)

- Input: the module must support two schemes. **Scheme 1** considers only fully paid orders. **Scheme 2** pays commission on partial payments, under strict control so that no commission is paid twice across periods.
- Q: How is the scheme chosen? → A: Per agent. Each agent has a scheme. A line credited to two agents, under CANALIZACIÓN and ATENCIÓN EN CAMPO, follows each agent's own scheme for that agent's share, because each (line, agent, participation) is computed separately (FR-015).
- Q: Under scheme 2, how is a partial payment turned into commission? → A: Proportionally. A payment covering 40% of the order total earns 40% of every line's full commission. What a month earns is the share paid by the month's end, times the full commission, minus what closed periods already stored, so rounding never accumulates and the total never passes 100% (FR-016, FR-017).
- Q: Under scheme 2, which date places a payment's commission in a month? → A: The application date (`sales_order_payment.date`), when the money was applied to this order. An advance received in March and applied in April earns in April (FR-016).
- Q: Under scheme 2, what happens when a payment whose commission was paid in a closed month is later reversed? → A: It is clawed back. The first open month computed after the reversal carries a negative line for the share no longer collected, which the cumulative formula yields by itself (FR-016). Scheme 1 keeps its rule: no clawback for reversals.
- Q: Under scheme 2, how is a refund handled? → A: As under scheme 1: a negative line for the refund's full commission, in the month of the refund's date. The payment share is measured against the order total **net of refunds** dated by the month's end, so the share still reaches 100% when the customer has paid everything they owe. The answer was first recorded with the share measured on the original total. That deducted every refund twice: once as the negative line, and once because the share could never reach 100%. It was revised the same day (FR-016, FR-017).
- Q: A refund hits a line whose sale is stored in a closed period. Whose commission is reduced, and at what rate? → A: The agent who was paid. The refund takes its agents, participations and rates from the sale's stored lines, so it reverses exactly what was paid, even after a reassignment or a rate change (FR-009a).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A manager pays a period whose amounts never change afterwards (Priority: P1)

At the end of a period, a manager computes each agent's commissions, reviews them, closes the period and pays it. Later, a customer is reassigned to another agent, a label's rate changes, or a product gets a different label. The paid period still shows exactly what was paid: the same lines, rates, agents and totals.

**Why this priority**: This is the rule the new MBE adds (#32). Today the current system recomputes every period from live data, so a reassignment rewrites paid history. In mbe_dev, 230 historical lines ($22,538.53) would now go to a different agent.

**Independent Test**:
- Compute a period and close it.
- Reassign one of its customers, change a label rate, and relabel a product.
- Read the closed period: every line and total is identical. Computing the next, open period uses the new settings.

**Acceptance Scenarios**:

1. **Given** an open period, **When** it is computed, **Then** the result lists every commissionable line with its agent, participation, rates and commission, using the current settings and assignments.
2. **Given** a computed period, **When** it is closed, **Then** its lines and totals are stored as they were computed, along with who closed it and when.
3. **Given** a closed period, **When** a customer is reassigned, a rate or label changes, or an order is edited, **Then** reading the closed period returns exactly what was stored.
4. **Given** a closed period, **When** it is marked paid, **Then** it records who paid it and when. A paid period is never recomputed or changed.
5. **Given** a closed period, **When** a later refund hits one of its orders, **Then** the deduction goes to the period that contains the refund, never back into the closed one.
6. **Given** an agent on scheme 2 and an order paid 40% in March and 60% in April, **When** March and April are closed, **Then** March stores 40% and April 60% of each of the agent's lines, and the two sum to the full commission.
7. **Given** that order, **When** the agent switches to scheme 1 before April closes, **Then** April stores only the remaining 60%, never the full commission again.
8. **Given** an agent on scheme 2, **When** an application already commissioned in a closed month is reversed, **Then** the next open month carries the negative share.

---

### User Story 2 - A manager maintains commission settings (Priority: P1)

A manager keeps the commission catalog current:
- labels and their rates;
- which label each product belongs to;
- which employees are agents;
- the participation types;
- each agent's share per label and participation.

**Why this priority**: There is no screen for this in the current system. Rates are edited in the database, and a product's label can be added but never changed.

**Independent Test**:
- Create a label, assign it to a product, and give an agent a 100% share of it as CLIENTE VITALICIO.
- Change the label's rate and the product's label.
- Remove the agent.

Each change is reflected in the next computation and in no closed period.

**Acceptance Scenarios**:

1. **Given** a user with the commissions privilege, **When** they create, edit or delete a label, a participation type, an agent or an agent rate, **Then** the change is saved and validated. Rates and shares are between 0 and 1, and an agent rate is unique per (agent, label, participation).
2. **Given** a product, **When** its label is set or changed, **Then** the product has exactly one label, or none.
3. **Given** a setting referenced by a closed period, **When** it is changed or deleted, **Then** the closed period is unaffected.
4. **Given** a user without the privilege, **Then** reading or changing settings is refused.

---

### User Story 3 - The report reads commission history (Priority: P2)

Report §37, "Commissions by Salesperson", and the agent ticket can read commission history:
- any period's lines, optionally for one agent;
- each agent's totals by label;
- whether the period is open, closed or paid.

History starts with the first period this system closes; the stale `commissions_history` table plays no part.

**Why this priority**: The report itself is out of scope, but it depends on this read (#32).

**Independent Test**: Read a closed period for one agent. The lines carry order, customer, product, label, participation, rate, amount and movement (sale or refund), and the totals by label equal the sum of the lines.

**Acceptance Scenarios**:

1. **Given** a period, **When** its history is read for an agent, **Then** the response carries the lines and the per-label totals the current system's ticket and CSV show.
2. **Given** an open period, **Then** its history is the live computation, marked as not final.

---

### Edge Cases

- **No label:** a product with no label still lists, at 0%, as the current system does, so that someone can label it.
- **Missing rate row:** an agent with no rate row for a label and participation earns 0 on that line.
- **No agent involved:** an order whose customer's salesperson and order's salesperson are both non-agents earns no commission and does not list.
- **Customer without a salesperson:** the order earns no commission at all, not even for an agent who wrote it. The current system's query compares the two salespeople, and a missing one matches no branch. This is kept for parity (SC-002).
- **Refund of an order paid in a closed period:** it lands in the period that contains the refund, as a negative line, charged to the agent who was paid at the stored rate (FR-009a). A reassignment or rate change in between does not move it to another agent.
- **Refund before the order is paid:** it nets against the sale in the period the order is paid, and is never charged twice (the September fix).
- **An order that becomes unpaid again** (a payment application is reversed) **under scheme 1**: while its period is open, it drops out of the computation, and it comes back when it is paid again, still dated by its first `balance_zeroed_time`. If its period has already closed, the closed lines stand, as with any later change, and no clawback is made. A refund, not a payment reversal, is how money comes back out of a closed period (FR-009).
- **Closing the current month:** refused until the month has ended.
- **A refund on a partly paid order under scheme 2:** the refund line is deducted in full, so while the order is partly paid the net can sit below what the agent has collected for. This is accepted. Because the share is measured net of refunds, it corrects itself when the order is settled. A month with a refund but no payment can show a positive share movement next to the negative refund line, because the shrinking net total raises the share.
- **A reversal under scheme 2:** the share is clawed back in the first open month (FR-016).
- **Two periods for one month:** refused. A line belongs to at most one closed period, the month of its paid date, or of its refund's date.
- **An order edited after its period closed:** the closed period does not change. If the edit is a refund, see above.
- **Rounding:** commissions are rounded to cents per line, and totals are the sum of the rounded lines.

## Requirements *(mandatory)*

### Functional Requirements

**Scope document (#32)**

- **FR-001**: `docs/specs/02-sales.md` MUST gain **§10 Commissions**, describing:
  - **The current system:** the settings tables; the computation (lines, amounts, the three participation branches, refunds including the September fix); the absence of periods, closing and payment; the report, ticket and CSV; access; and `commissions_history`'s status.
  - **The new MBE:** periods that close and are paid, stable history, and the paid-date basis.

**Settings (#33)**

- **FR-002**: The API MUST maintain:
  - labels (name, rate 0–1);
  - product labels (one per product, or none);
  - agents (an employee flag);
  - participation types (name);
  - agent rates (agent, label, participation, share 0–1, unique per triple).

  Each supports list, create, update and delete, with validation and clear refusals.
- **FR-003**: Changing or deleting a setting MUST NOT alter any closed period, which holds its own copies of names and rates. Deleting a setting that other settings still reference is refused (409), as for other master data; for example, a label still assigned to products or used in an agent rate.

**Computation**

- **FR-004**: Computing a period MUST apply the current system's rules as fixed in September 2026 (Findings), for one agent or all:
  - **Lines:** every line of a completed, non-cancelled, paid order whose paid date (FR-008) falls in the period.
  - **Line amount:** after discount, tax included.
  - **Participation:** CLIENTE VITALICIO when the order's and the customer's salesperson coincide. Otherwise CANALIZACIÓN to the order's salesperson and ATENCIÓN EN CAMPO to the customer's.
  - **Rate and commission:** rate = label rate × participation share; commission = line amount × rate, rounded to cents.
  - **Refunds:** negative lines, as the current system computes them after the fix.
- **FR-005**: Each computed line MUST carry what the report needs:
  - order, line, and the order and paid dates;
  - customer and its name;
  - product and its name;
  - label and its name;
  - the agent and the participation (name and share);
  - the label rate, the effective rate, the line amount and the commission;
  - the movement: sale or refund.

**Periods and stable history**

- **FR-006**: A period MUST be one calendar month: from the 1st at 00:00 to the last day at 23:59:59, business time. It has a state (**open → closed → paid**, with **closed → open** allowed by reopening, FR-010), who closed, reopened and paid it, and when. There is at most one period per month.
- **FR-007**: A period MUST NOT be closed before its month has ended in business time (refused with 409). Closing a period MUST store its computed lines, per FR-005, as they are at that moment: agent, participation, rates, amounts, and customer, product and label names. From then on, reading it MUST return the stored lines, whatever later happens to customers, settings or orders.
- **FR-008**: The paid date that places an order in a period MUST be the order's `balance_zeroed_time`: the moment its balance reached zero. Later edits do not move it, unlike the current system's `modification_time`, which put 19 orders in the last year in a different month.
- **FR-008a**: When applications make an order paid, this API MUST set `balance_zeroed_time` to that moment if it is not already set, as the current system does in every payment path (`PaymentsController.cs:1458`, `:1526`, `:1600`, `:1948`). Once set, it stays. This corrects spec 011 FR-065a's reading of the column as a supervisor-only act; the refund path still never writes it. The current system only began stamping it in 2025: 51% of that year's paid orders, 99.9% of 2026's, none before. The migration backfills paid orders dated from 2025-10-01 that have no stamp, or have the `0001-01-01` sentinel, from their last applied payment's date. There are 38 in mbe_dev, 6 of them paid through this API. Older months are not backfilled, and a paid order with no stamp earns no commission.
- **FR-009**: A refund is dated by `customer_refund.date`, set when it is completed. Its month never differs from the current system's `modification_time` in the last year (1,695 refunds). A refund dated after the period holding its order closed MUST be deducted in the period containing the refund, never in the closed one.
- **FR-009a**: A refund of a line that already has stored lines in a closed period MUST be charged to the agents, participations and rates of those stored lines, not to the current settings. The refund's commission = refund amount × the stored rate, for each stored (agent, participation) of that line. This holds in both schemes. A refund of a line with no stored line (its sale has not yet been closed into any period) uses the current settings, like its sale.
- **FR-010**: A period is created on demand for a given year and month, and computing a month that has no period yet treats it as open. A closed, unpaid period MAY be reopened by a user with Update on commissions. Its stored lines are discarded, it returns to open, and who reopened it and when is recorded. A paid period MUST NOT be reopened.
- **FR-011**: Paying a period MUST mark it paid and record who and when. A paid period is final: it cannot be reopened, recomputed or changed.

**Reading history**

- **FR-012**: The API MUST return, for a period and optionally one agent:
  - the lines (FR-005);
  - the totals per agent and label;
  - the period's state.

  Open periods are computed live and flagged as not final. Closed and paid periods are served from their stored lines.
- **FR-013**: `commissions_history` is a stale table: no system reads or writes it. This feature MUST NOT read, migrate or change it. Commission history starts with the first period this system closes. The settings tables (`commission`, `commission_product`, `commission_agent`, `commission_participation`, `commission_salesperson`) remain the shared live configuration that both this API and the current system's report read during the pilot.

**Commission schemes**

- **FR-015**: Each agent MUST have a commission scheme, maintained with the agent settings (FR-002):
  - **Scheme 1, fully paid** (the current system's rule, and the default): a line earns its whole commission in the month its order is paid (FR-004, FR-008).
  - **Scheme 2, partial payments:** a line earns commission as payments are applied to its order, in the month of each payment (FR-016).

  The scheme applies per (line, agent, participation), so two agents on one line may follow different schemes. Each stored line records the scheme it was computed under.
- **FR-016**: Under scheme 2, a (line, agent, participation) earns in a month:

  `ROUND(full commission × min(applied by month end ÷ order total, 1), 2) − commission already stored for it in closed periods`

  - **Full commission:** the FR-004 amount for the whole line.
  - **Applied:** the sum of the order's payment applications that are not cancelled and whose application date (`sales_order_payment.date`) is on or before the month end. An advance counts when it is applied, not when it was received.
  - **Lines:** a month lists only (line, agent, participation) whose earned amount is not zero.
  - **Completed orders only:** an order that is not completed, or is cancelled, earns nothing.
  - **Refunds:** each refund line is a negative line for its full commission, in the month of `customer_refund.date`, as under scheme 1. The line's full commission stays that of the original line. The share is measured against the order total net of refunds:

    `share = min(applied by month end ÷ (order total − refunds dated by month end), 1)`

    Example: an order of $10,000 with one line at 1% (full commission $100) is paid $4,000 in March, has $3,000 returned in April, and is paid $3,000 more in May.

    | Month | Share movement | Refund line | Month total |
    |---|---|---|---|
    | March | +$40 (40%) | — | $40 |
    | April | +$17.14 (57.14%) | −$30 | −$12.86 |
    | May | +$42.86 (100%) | — | $42.86 |

    The total is $70, the commission on the $7,000 net sale.
  - **Reversals:** a reversed (cancelled) application drops out of "applied". The first open month computed after the reversal therefore earns a negative amount for that share, a clawback, even though the original month is closed and untouched.
- **FR-017**: Whatever the scheme, and across any scheme change, the positive commission stored for one (line, agent, participation) over all closed periods MUST NOT exceed that line's full commission. No payment, and no part of one, earns commission in two periods, and each refund line is deducted exactly once. When an agent's scheme changes, the next computation counts what closed periods already stored for each line:
  - scheme 1 pays only the remainder when the order is paid;
  - scheme 2 continues from the stored amount.

**Access**

- **FR-014**: Every commission route MUST require SystemObject 112, CommissionsBySalesPerson: Read to view settings, computations and history; Create, Update or Delete to change settings and to close or pay a period. Administrators bypass the check, as everywhere.

### Key Entities

- **Commission label** (`commission`): name and rate.
- **Product label** (`commission_product`): product → label, at most one.
- **Agent** (`commission_agent`): an employee who can earn commissions, with their commission scheme (FR-015).
- **Participation type** (`commission_participation`): why an agent earns on a line.
- **Agent rate** (`commission_salesperson`): an agent's share for a label and a participation.
- **Commission period** (new): a calendar month (year, month), its state, who closed and paid it, and when.
- **Commission line** (stored on close): one row per (order line or refund line, agent, participation), with every FR-005 value frozen.
- **`commissions_history`** (existing, stale): not used.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a period is closed, reassigning any of its customers, changing any rate or label, and editing any of its orders change none of its lines or totals: 0 differences between two reads.
- **SC-002**: For an open period, the computed totals per agent and label equal what the current system's report (post-PR #58) produces for the same range and data, checked against mbe_dev for at least one full month.
- **SC-003**: No order line or refund line appears in two closed periods, so a deduction is never charged twice.
- **SC-004**: Every settings change, close and payment is permitted only to users with the commissions privilege, and records who made it.
- **SC-006**: For any order and agent, under scheme 1, scheme 2 or a switch between them, the positive commission stored across all closed periods never exceeds the line's full commission. This is checked by tests that pay in instalments, reverse, refund and switch schemes across months.
- **SC-005**: `commissions_history` is untouched: the same 1,650 rows before and after deployment.

## Assumptions

- **Deployment database:** mbe_dev, a copy of live data through 2026-10-21. Its `commission_salesperson` already includes the ATENCIÓN EN CAMPO rows that `mbe-26.10.sql` adds.
- **Rules:** the computation follows the current system after mbe PR #58, the rules agents are paid by today. The pre-September rules are not reproduced.
- **Attribution:** the customer's salesperson is read when a period is computed. Closing freezes it, so a reassignment affects only periods that are still open, which is the stability rule #32 asks for.
- **Card commissions:** `payment_method_option.commission` and `customer_payment.commission` are card-processing surcharges, unrelated to this feature.
- **The current system:** it keeps computing its own live report during the pilot. This feature does not change it.

## Out of Scope

- The UI (mictlanix/mbe-roadmap#34).
- The commissions report and its PDF ticket or CSV (Reports epic, mictlanix/mbe-roadmap#100/#101). This feature provides the data they read.
- Changing the current system's report.
- Recomputing or correcting any period paid outside this system.
- `commissions_history`: it is not read, migrated or dropped. Dropping it belongs to the current system's schema.
