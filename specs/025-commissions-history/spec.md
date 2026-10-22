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

- (Pending: `/speckit.clarify` settles the business rules, as the request asks: FR-008, FR-010 and FR-013.)

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

The 1,650 historical lines already in `commissions_history` (2024–2025) remain readable.

**Why this priority**: The report itself is out of scope, but it depends on this read (#32).

**Independent Test**: Read a closed period for one agent. The lines carry order, customer, product, label, participation, rate, amount and movement (sale or refund), and the totals by label equal the sum of the lines.

**Acceptance Scenarios**:

1. **Given** a period, **When** its history is read for an agent, **Then** the response carries the lines and the per-label totals the current system's ticket and CSV show.
2. **Given** an open period, **Then** its history is the live computation, marked as not final.
3. **Given** the historical `commissions_history` rows, **Then** they remain readable, untouched.

---

### Edge Cases

- **No label:** a product with no label still lists, at 0%, as the current system does, so that someone can label it.
- **Missing rate row:** an agent with no rate row for a label and participation earns 0 on that line.
- **No agent involved:** an order whose customer's salesperson and order's salesperson are both non-agents earns no commission and does not list.
- **Customer without a salesperson:** treated as a non-agent.
- **Refund of an order paid in a closed period:** it lands in the period that contains the refund, as a negative line.
- **Refund before the order is paid:** it nets against the sale in the period the order is paid, and is never charged twice (the September fix).
- **Overlapping periods:** refused. A line belongs to at most one closed period.
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
- **FR-003**: Changing or deleting a setting MUST NOT alter any closed period. Deleting a setting that open data still needs is allowed, and the next computation simply no longer finds it.

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

- **FR-006**: A period MUST have a date range, a state (**open → closed → paid**), who closed it and when, and who paid it and when. Periods MUST NOT overlap.
- **FR-007**: Closing a period MUST store its computed lines, per FR-005, as they are at that moment: agent, participation, rates, amounts, and customer, product and label names. From then on, reading it MUST return the stored lines, whatever later happens to customers, settings or orders.
- **FR-008**: The paid date that places an order in a period is [NEEDS CLARIFICATION: the order's `balance_zeroed_time`, which is set once when the balance reaches zero (recommended), or the current system's `modification_time`, which moves with later edits (19 orders in the last year)?]
- **FR-009**: A refund dated after the period holding its order closed MUST be deducted in the period containing the refund, never in the closed one.
- **FR-010**: A period is [NEEDS CLARIFICATION: always a calendar month, or any date range a manager chooses, as in the current system? Can a closed but unpaid period be reopened, and by whom?]
- **FR-011**: Paying a period MUST mark it paid and record who and when. A paid period is final: it cannot be reopened, recomputed or changed.

**Reading history**

- **FR-012**: The API MUST return, for a period and optionally one agent:
  - the lines (FR-005);
  - the totals per agent and label;
  - the period's state.

  Open periods are computed live and flagged as not final. Closed and paid periods are served from their stored lines.
- **FR-013**: The 1,650 rows already in `commissions_history` MUST remain unchanged and readable as legacy history. [NEEDS CLARIFICATION: should they also be grouped into closed or paid periods (May–September 2025, by month), or only read as-is?]

**Access**

- **FR-014**: Every commission route MUST require SystemObject 112, CommissionsBySalesPerson: Read to view settings, computations and history; Create, Update or Delete to change settings and to close or pay a period. Administrators bypass the check, as everywhere.

### Key Entities

- **Commission label** (`commission`): name and rate.
- **Product label** (`commission_product`): product → label, at most one.
- **Agent** (`commission_agent`): an employee who can earn commissions.
- **Participation type** (`commission_participation`): why an agent earns on a line.
- **Agent rate** (`commission_salesperson`): an agent's share for a label and a participation.
- **Commission period** (new): a date range, its state, who closed and paid it, and when.
- **Commission line** (stored on close): one row per (order line or refund line, agent, participation), with every FR-005 value frozen.
- **Legacy history** (`commissions_history`): 1,650 rows from 2024–2025, kept as-is.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: After a period is closed, reassigning any of its customers, changing any rate or label, and editing any of its orders change none of its lines or totals: 0 differences between two reads.
- **SC-002**: For an open period, the computed totals per agent and label equal what the current system's report (post-PR #58) produces for the same range and data, checked against mbe_dev for at least one full month.
- **SC-003**: No order line or refund line appears in two closed periods, so a deduction is never charged twice.
- **SC-004**: Every settings change, close and payment is permitted only to users with the commissions privilege, and records who made it.
- **SC-005**: The 1,650 legacy history rows are unchanged after the feature is deployed.

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
- Recomputing or correcting any period paid outside this system, including the legacy `commissions_history` rows.
