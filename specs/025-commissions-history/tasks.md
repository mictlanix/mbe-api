# Tasks: Commissions with Stable History

**Input**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/commissions.md](./contracts/commissions.md), [quickstart.md](./quickstart.md)

**Tests**: required by the constitution; the template's "tests are optional" line does not apply. Each story's tests come first and must fail before the implementation. Each test task is marked **(commit with T0xx)**.

**Where the money logic lives**:
- **Rules:** schemes, cap, refunds and the key freeze are in `app/services/commission_rules.py`, a pure module, specified by research R1 and the spec's FR-004, FR-009/9a, FR-015–FR-017. Its unit tests are the main proof of correctness.
- **Service:** `app/services/commission_service.py` gathers the facts and owns the transactions.

**SQLite**: the integration schema is built from the models (`tests/integration/conftest.py`, `Base.metadata.create_all`), so the new tables appear there automatically. Fact SQL must be portable: `CASE WHEN`, not `IF`; `COALESCE`; date bounds passed as parameters.

## Format: `- [ ] T### [P?] [US#] Description · path`

---

## Phase 1: Setup

- [X] T001 Record the baseline before any change: run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. Write the counts under this task · `specs/025-commissions-history/tasks.md`
  - **Baseline (2026-10-22)**: ruff clean; pytest 3316 passed, 0 failed.

---

## Phase 2: Foundational (blocks all stories)

- [X] T002 Write `migrations/023_commission_periods.sql` and `023_commission_periods_rollback.sql`, with header comments in the style of `022_vehicle_operator_license_photo.sql` (why, idempotency, MariaDB 10.11, effect on the current system). Forward:
  1. **Scheme:** `ALTER TABLE commission_agent ADD COLUMN IF NOT EXISTS scheme SMALLINT NOT NULL DEFAULT 1 COMMENT '1 fully paid orders, 2 partial payments (spec 025)'`.
  2. **Periods:** `CREATE TABLE IF NOT EXISTS commission_period`, columns per data-model.md, `UNIQUE KEY (year, month)`, FKs to `employee` for `closed_by`, `reopened_by` and `paid_by`.
  3. **Lines:** `CREATE TABLE IF NOT EXISTS commission_period_line`, columns per data-model.md, FK `commission_period` → `commission_period` and FK `salesperson` → `employee`, `KEY (commission_period, salesperson)`, `KEY (sales_order_detail, salesperson, participation)`.
  4. **Backfill:** `UPDATE sales_order so SET balance_zeroed_time = COALESCE((SELECT MAX(COALESCE(sop.date, cp.date)) FROM sales_order_payment sop JOIN customer_payment cp ON cp.customer_payment_id = sop.customer_payment WHERE sop.sales_order = so.sales_order_id AND sop.cancelled = 0), so.modification_time) WHERE so.paid = 1 AND so.completed = 1 AND so.cancelled = 0 AND so.date >= '2025-10-01' AND (so.balance_zeroed_time IS NULL OR so.balance_zeroed_time < '1900-01-01')`.

  The comment states the before count (38) and that the backfill is not reversed by the rollback. Rollback: drop the two tables (lines first) and the column.
- [X] T003 In `app/models/commission.py`, add `CommissionAgent.scheme: Mapped[int] = mapped_column(SmallInteger, default=1)`, plus `CommissionPeriod` and `CommissionPeriodLine` with every data-model.md column. In `app/enums.py`, add:
  - `CommissionScheme(IntEnum)`: `FULLY_PAID = 1`, `PARTIAL_PAYMENTS = 2`;
  - `CommissionPeriodStatus`: `OPEN = 0`, `CLOSED = 1`, `PAID = 2`;
  - `CommissionMovement`: `SALE = 0`, `REFUND = 1`;
  - `SourceType.COMMISSION = 12`, with an API-only comment like 10 and 11.
- [X] T004 Document the schema change:
  - `docs/data-dictionary.md`: a `commission_agent.scheme` row, and sections for the two tables;
  - `docs/mbe_schema.sql`: the two `CREATE TABLE`s and the column, matching the migration;
  - `docs/constants.md`: SourceType row 12.

  Run `uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py -q` until green. Commit T002–T004.
  - **As built:** `docs/mbe_schema.sql` was **not** changed. It is the baseline dump, and earlier migrations (021, 022) never edited it; `test_data_dictionary.py` replays `migrations/*.sql` over it, so the new tables reach that test through migration 023.

**Checkpoint**: the schema exists in the code, the docs and SQLite.

---

## Phase 3: User Story 1 — A manager pays a period whose amounts never change afterwards (P1)

**Goal**:
- **Rules:** FR-004–FR-012, FR-008a, FR-009/9a, FR-010a, FR-015–FR-017.
- **Machinery:** compute, close, reopen, pay, and read one month.

**Independent test**:
- Close a month, then reassign a customer, change a rate and relabel a product. The closed month reads identically.
- The scheme 2 instalment, switch and reversal scenarios hold.
- Months can't close out of order.

- [ ] T005 [P] [US1] Write `tests/unit/test_commission_rules.py` (commit with T009). Build facts by hand as the T009 dataclasses, with month end 2026-04-30. Cases:
  1. **Participation branches:** csp == osp → one CLIENTE VITALICIO key to csp; csp != osp, both agents → CANALIZACIÓN to osp and ATENCIÓN EN CAMPO to csp; csp NULL → no key; a non-agent → no key for them; an unlabelled product → a line at rate 0; a missing rate row → share 0.
  2. **Scheme 1:** a line paid in the month → `full`; the same line when the order was paid in another month → nothing; a refund dated before payment, order paid this month → one negative line, once; a refund dated this month on an order paid last month → a negative line; a refund detail already stored for the key → nothing.
  3. **Scheme 2:**
     - the 40%/60% instalments over March and April, with March's stored line fed back in → March 40, April 60, sum = `full`;
     - the spec's net-of-refunds example (order $10,000, one line at 1%; $4,000 March, $3,000 refund April, $3,000 May) → +40; then +17.14 and −30; then +42.86; total 70;
     - reversal: March stored 40, then the application is cancelled → April −40;
     - **reversal after full payment** (analysis C1): March stored 100 (fully paid), then an application is cancelled, leaving 60% → April −40. The line is still a candidate through (d) although its stored sum equals `full`;
     - `paid_date` on a scheme 2 sale line is the last application date in the month.
  4. **Scheme 1 late pickup** (analysis C2): an order stamped in March, reversed before March closes (not stored), paid again in May → May pays `full`. Once May has stored it, June pays nothing.
  5. **Switches:** scheme 2 → 1 with 40 stored, order paid in April → April pays 60; scheme 1 → 2 after the full commission was stored → 0.
  6. **Key freeze:** a line with stored keys uses the stored agent, participation and rate even when csp and the rates changed; a refund of such a line is charged at the stored rate to the stored agent (FR-009a).
  7. **Invariant (SC-006):** a seeded `random.Random(25)` generates 200 histories, each one line over 6 months with random instalments, refunds, reversals and scheme switches, feeding each month's output back as stored lines. Assert the sale-commission sum per key ≤ `full` after every month, and that no refund detail is stored twice per key.
- [ ] T006 [P] [US1] Add stamp cases to `tests/unit/test_customer_payment_service.py` (commit with T010), patching `applied_amount` and `_order_total`:
  - becoming paid with `balance_zeroed_time` None sets it;
  - with the `datetime(1, 1, 1)` sentinel, sets it;
  - already set, it is unchanged;
  - becoming unpaid leaves it.
- [ ] T007 [P] [US1] Write `tests/integration/test_commission_compute.py` (commit with T011).
  - **Seed:** agents, rates and labels; customers with salespeople; completed orders with lines; `customer_payment` and `sales_order_payment` rows with dates; completed refunds. Follow `tests/integration/seed.py` `seed_sales_order` and the refund seeding in `tests/integration/test_refunded_balance.py`.
  - **Asserts:**
    - `commission_service.compute(db, 2026, 4)` returns the expected lines for a scheme 1 agent and a scheme 2 agent;
    - candidates (a)–(d) are each picked up, including a reversal found only through (d);
    - an order with `balance_zeroed_time` in March is excluded from April under scheme 1;
    - `salesperson=` filters.
- [ ] T008 [P] [US1] Write `tests/integration/test_commission_periods.py` (commit with T012). Drive the service with a patched `local_now`, so months can "end":
  - closing April before April ends → 409;
  - the first close (March) succeeds and stores lines;
  - closing May while April is open → 409 `The previous period is not closed`;
  - close April, then reassign a customer, change a label rate, relabel a product and edit an order comment; reading April returns identical lines and totals (SC-001);
  - reopen March while April is closed → 409; reopen April → lines deleted, `status` open, `reopened_by` set; close April again;
  - pay April, then reopen or close it → 409;
  - the scheme 1 late pickup, end to end: stamp an order paid in March, cancel its application before closing March, close March (no line), apply a new payment in May, close April and May → May stores the line;
  - a scheme 2 order fully paid and stored, then reversed → the next close stores the negative share;
  - for every key, Σ sale commission across closed months ≤ `full`, and each refund detail is stored at most once per key (SC-003/SC-006).
- [ ] T009 [US1] Create `app/services/commission_rules.py` (research R1), with no I/O and no async:
  - **Frozen dataclasses:** `CandidateLine`, `OrderMoney(applied, refunded, order_total)`, `RefundLine`, `StoredLine`, `AgentRate`.
  - **Entry point:** `def month_lines(*, month_start, month_end, candidates, money, refunds, stored, agents: dict[int, CommissionScheme], rates: dict[tuple, Decimal], labels: dict) -> list[ComputedLine]`. It implements the participation keys, the key freeze, scheme 1 and scheme 2 amounts, refund lines, and the cap, as the research R1 table specifies.
  - **Arithmetic:** `Decimal` throughout, with `quantize(Decimal('0.01'), ROUND_HALF_UP)`, matching MariaDB's `ROUND`.
  - **Docstring:** cites spec 025 and research R1. Run T005: green.
- [ ] T010 [US1] In `app/services/customer_payment_service.py` `_sync_paid_flag`, after setting `order.paid`, add: `if order.paid and (order.balance_zeroed_time is None or order.balance_zeroed_time.year < 1900): order.balance_zeroed_time = local_now()`. Add a comment citing spec 025 FR-008a and the current system's `PaymentsController`. Run T006: green.
- [ ] T011 [US1] Create `app/services/commission_service.py`: facts and compute.
  - **`_month_bounds(year, month)`:** returns `[start, next)`.
  - **`_facts(...)`:** the three `text()` queries of research R1 (candidates (a)–(d), per-order money, refund lines), the stored lines of closed periods for the candidate lines, agents with their schemes, rates and labels.
  - **`compute(db, year, month, *, salesperson=None)`:** calls `commission_rules.month_lines`.

  Run T007: green.
- [ ] T012 [US1] Add to `commission_service.py`:
  - **`close(db, year, month, *, current)`:** month ended, previous month closed unless this is the first close, not already closed; compute all agents; insert `CommissionPeriodLine` rows; upsert the period with `status=1` and `closed_by`/`closed_at`. One transaction. On MariaDB, `with_for_update()` on the period rows involved.
  - **`reopen(...)`:** latest closed only, not paid; delete the lines; `status=0` and `reopened_by`/`reopened_at`.
  - **`pay(...)`:** closed only; `status=2` and `paid_by`/`paid_at`.
  - **`read_period(db, year, month, *, salesperson=None)`:** stored lines if closed or paid, else `compute`; returns status, audit fields, `final`, lines and totals per (agent, label).

  The 409 messages follow the contract exactly. Run T008: green.
- [ ] T013 [P] [US1] Write `tests/api/test_commission_periods.py` (commit with T014), patching the service. Cover the period routes:
  - `GET /api/v1/commissions/periods/2026/4` → 200;
  - `month=13` → 422;
  - close, reopen and pay → 200, and each 409 message passes through;
  - 401 with no token;
  - 403 without SystemObject 112 Read for GET and Update for the actions (use `_deny_privileges` as in `tests/api/test_print_endpoints.py`).
- [ ] T014 [US1] Create `app/schemas/commission.py` (period and line response models per the contract; `movement` serialised as `sale`/`refund` and `status` as `open`/`closed`/`paid`) and `app/api/v1/endpoints/commissions.py`:
  - **Guards:** `_READ`/`_CREATE`/`_UPDATE`/`_DELETE = require_privilege(SystemObject.COMMISSIONS_BY_SALES_PERSON, …)`.
  - **Routes:** `GET /periods/{year}/{month}`, `POST …/close`, `…/reopen` and `…/pay`.
  - **Registration:** include the router in `app/api/v1/router.py` with `prefix='/commissions', tags=['commissions']`.

  Run `uv run pytest tests -q`: green. Commit T005–T014 in the "commit with" groups above.

**Checkpoint**: periods can be computed, closed, reopened, paid and read, and history is stable.

---

## Phase 4: User Story 2 — A manager maintains commission settings (P1)

**Goal**: FR-002, FR-003, FR-014, and agent schemes, with one incidence per change.

**Independent test**:
- Create a label, label a product, add an agent on scheme 2, and give them a rate.
- Change the label's rate and the product's label.
- Deleting a label in use → 409.
- Each change writes one incidence (source 12).

- [ ] T015 [P] [US2] Write `tests/api/test_commission_settings.py` (commit with T016–T017). For each resource in the contract (labels, participations, agents, agent rates, product labels):
  - list, create, update and delete → 200/201/204;
  - unknown id → 404;
  - validation → 422 (rate outside 0–1, scheme not 1 or 2, a share for a non-agent);
  - a duplicate (an agent, an agent-rate triple) → 409;
  - deleting something in use → 409;
  - 401 and 403 by right;
  - `incidences.record` called once per change with `source=SourceType.COMMISSION`.

  Add one integration case: `PUT /product-labels/{product}` changes an existing label.
- [ ] T016 [US2] Add the settings functions to `commission_service.py`:
  - list, create, update and delete for `Commission`, `CommissionParticipation`, `CommissionAgent` (with `scheme`), `CommissionSalesperson` and `CommissionProduct` (set or change, and remove);
  - each write calls `incidences.record(db, source=SourceType.COMMISSION, instance_id=…, updater=current.employee_id, reason=…, context=…)` in the same transaction;
  - `Commission.comment` defaults to `'0'` (research R5).

  FK violations surface as 409 through the existing IntegrityError handler; check that `tests/api/test_constraint_errors.py` covers the pattern.
- [ ] T017 [US2] Add the settings schemas to `app/schemas/commission.py` and the routes to `app/api/v1/endpoints/commissions.py` per the contract, with Create/Update/Delete guards. Run T015: green. Commit T015–T017.

**Checkpoint**: settings are maintainable through the API.

---

## Phase 5: User Story 3 — The report reads commission history (P2)

**Goal**: FR-012, plus the period list for the report (11 §37).

**Independent test**: `GET /commissions/periods` lists closed and paid months with totals. `GET /periods/{y}/{m}?salesperson=17` returns only agent 17's lines, with per-label totals equal to the sum of their lines. An open month comes back with `final: false`.

- [ ] T018 [P] [US3] Extend `tests/integration/test_commission_periods.py` and `tests/api/test_commission_periods.py` (commit with T019):
  - `GET /periods` lists only months that have a row, with the status, `closed_at`, `paid_at` and the total;
  - the salesperson filter narrows both `lines` and `totals`;
  - each total equals the sum of its lines' commission;
  - an open month returns `final: false` and a live computation;
  - a reopened month returns `final: false`.
- [ ] T019 [US3] Implement `list_periods` in `commission_service.py` and `GET /periods` in `commissions.py`, and make sure `read_period`'s `salesperson` filter and totals match T018. Run: green. Commit T018–T019.

---

## Phase 6: Polish & Cross-Cutting

- [ ] T020 [P] Write `docs/specs/02-sales.md` **§10 Commissions** (FR-001, research R8). Cover:
  - **The current system:** the settings; the computation, with the three branches, the refund rules and the September fix; no periods; the report, ticket and CSV; login-only access; `commissions_history` stale.
  - **The new MBE:**
    - monthly periods that close in sequence and can be reopened while unpaid;
    - stable stored lines, and refunds charged to the agent who was paid;
    - the two schemes per agent, with the scheme 2 formula and the spec's worked example;
    - the paid-date and refund-date basis;
    - the routes and SystemObject 112.
- [ ] T021 [P] Add notes to `docs/specs/11-reports.md` §37 (it reads `GET /commissions/periods/{y}/{m}`; `commissions_history` is not its source) and to `specs/011-sales-cycle-endpoints/spec.md` FR-065a (spec 025 corrected it: payments stamp `balance_zeroed_time`).
- [ ] T022 [P] Add to `CHANGELOG.md` `[Unreleased]`:
  - **`### Added`:** commissions (mictlanix/mbe-roadmap#32, #33, spec 025) — the settings API, schemes, periods and stable history.
  - **`### Changed`:** `balance_zeroed_time` is stamped when payments settle an order.
  - **Upgrade note:** migration 023; every agent starts on scheme 1; the first closed month starts the chain.
- [ ] T023 Run the full gate: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. No new failures against T001. Record the counts here.
- [ ] T024 **Confirm with the user before applying DDL to mbe_dev.** Then run `uv run python -m app.db.migrate` and the quickstart's migration checks (the scheme column, 2 tables, backfill 38 → 0, `commissions_history` still 1,650). Record the results here.
- [ ] T025 Run the SC-002 parity check per `quickstart.md`, read only, on mbe_dev for September 2026, with every agent on scheme 1 and no closed periods. Explain every difference by its order. Record it in `quickstart.md`.
- [ ] T026 **Confirm with the user before deploying.** Deploy the branch to `/var/www/mbe-api-test` and run read-only smoke checks (`GET /commissions/labels`, `/agents`, `/periods/2026/9`). Do **not** close or pay a period on the shared mbe_dev without the user's explicit go-ahead: a close starts the chain (FR-010a). Record the results in `quickstart.md`.

---

## Dependencies

- T001 → T002–T004 (foundational) → every story.
- **US1** (T005–T014) is the core. T009 (rules) has no dependency beyond T003, so it can be built first.
- **US2** (T015–T017) depends only on Phase 2. It can run alongside US1, though both edit `commission_service.py`, `schemas/commission.py` and `commissions.py`, so merge carefully or run it after.
- **US3** (T018–T019) depends on US1's `read_period`.
- Polish comes after the stories. T024–T026 need the user.

## Parallel opportunities

- **US1 tests:** T005, T006, T007 and T008 are four different files.
- **US1 implementation:** T009 (rules) and T010 (stamp) touch different files.
- **US2:** its test file is independent.
- **Polish:** T020–T022.

## Implementation strategy

1. **Phase 2,** then **T005 + T009:** the rules and their invariant test are the riskiest part, so build them first.
2. **The rest of US1:** facts, periods and routes. This is the MVP: periods with stable history and both schemes.
3. **US2,** settings, which currently can only be changed in the database.
4. **US3,** the report read.
5. **Polish:** docs, the gate, the migration on mbe_dev, the parity check, then deployment once confirmed.
6. **PR:** under "Roadmap", `Closes mictlanix/mbe-roadmap#32` and `Closes mictlanix/mbe-roadmap#33`.
