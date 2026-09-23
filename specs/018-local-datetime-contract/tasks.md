# Tasks: Local Datetime Contract

**Input**: Design documents from `/specs/018-local-datetime-contract/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/datetime-convention.md](./contracts/datetime-convention.md)

**Tests**: Required. The constitution (v1.2.0, Development Workflow › Testing) makes tests mandatory and test-first for all work. This overrides the template's "tests are optional" text. Write each test task, run it, confirm it fails, then implement.

**Organization**: Grouped by user story. US1 and US3 share one mechanism (`LocalDateTime`), so they share Phase 3. Their tests come before the type, so each fails first.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1, US2 or US3 from spec.md

---

## Phase 1: Setup

- [X] T001 Add `BUSINESS_TIMEZONE=America/Mexico_City` to `.env` (untracked, local) and `.env.example` (tracked), with a one-line comment that it is required and that stored datetimes are wall-clock time in this zone.

---

## Phase 2: Foundational (blocks every story)

- [X] T002 Write `tests/unit/test_clock.py`, failing first. Cover:
  - `local_now()` follows the setting, not the host. Monkeypatch `app.core.config.settings.business_timezone` to `ZoneInfo('Asia/Tokyo')`, compare to `datetime.now(ZoneInfo('Asia/Tokyo')).replace(tzinfo=None)` within 2 seconds, and assert the result is naive.
  - `local_today()` equals `local_now().date()` under the same patch.
  - `to_local`: a naive value is returned unchanged. `2026-09-27T18:00:00+00:00` becomes `2026-09-27T12:00:00` under `America/Mexico_City`. `2026-09-27T12:00:00-05:00` becomes `11:00`. Results are naive.
  - `Settings(_env_file=None)` with `BUSINESS_TIMEZONE` unset (monkeypatch `delenv`) raises `ValidationError` mentioning `business_timezone`. `BUSINESS_TIMEZONE=Mars/Olympus` raises one mentioning `invalid timezone`.
- [X] T003 Add `business_timezone: ZoneInfo` (no default) to `Settings` in `app/core/config.py`, with a `#` comment in the style of the neighbouring settings: why it is required, and that the legacy monolith writes the same zone. Import `from zoneinfo import ZoneInfo`.
- [X] T004 Create `app/core/clock.py` with `local_now()`, `local_today()` and `to_local(value: datetime) -> datetime`, each reading `settings.business_timezone` at call time (research R2). Add a module docstring stating the convention and citing #228. Run T002: it passes.

**Checkpoint**: the setting and helpers exist and are tested. Nothing calls them yet.

---

## Phase 3: User Stories 1 and 3 - One shared type documents and converts datetimes (P1, P2) 🎯 MVP

US1 and US3 are delivered by the same type, `LocalDateTime`, so they share this phase. Both stories' tests come first and must fail against plain `datetime`.

**US1 goal**: every date-time node in the OpenAPI document states the local-time convention (FR-006, SC-001). Response values are unchanged (FR-007).
**US3 goal**: offset-bearing input is converted at the boundary (FR-004), and naive input is untouched (FR-005).

**Independent Test**:
- US1: `app.openapi()` has no `format: date-time` node without the convention text, and the existing suite still passes.
- US3: the service layer receives the converted naive value for a body field and for a query parameter.

### Tests first (confirm each fails before T008)

- [X] T005 [P] [US1] Create `tests/unit/test_datetime_contract.py`. Walk `app.openapi()` recursively, over both `components.schemas` and every path's `parameters`. Collect every dict with `format == 'date-time'`. Assert the list is not empty and that each node's `description` contains `'no UTC offset'` and `settings.business_timezone.key`. Name the failing component and field, or path and parameter, in the assertion message. Follow the docstring style of `tests/unit/test_field_descriptions.py`.
- [X] T006 [P] [US3] In `tests/api/test_sales_orders.py`, add tests following the file's patched-service pattern:
  - POST a create with `"date": "2026-09-27T18:00:00Z"` and `"promise_date": "2026-09-27T12:00:00"`. Assert the patched `create_sales_order` received `date == datetime(2026, 9, 27, 12, 0)` with `tzinfo is None`, and `promise_date` unchanged.
  - The same with `-05:00`, expecting `11:00`.
  - GET the list with `date_from=2026-09-27T06:00:00Z`. Assert the patched list service received `datetime(2026, 9, 27, 0, 0)`, naive.
  Before T008 these fail, because the service receives an aware `18:00+00:00`.
- [X] T007 [P] [US3] In `tests/api/test_customer_payments.py`, add a test: GET the list with `date_to=2026-09-27T06:00:00Z` and assert the patched `list_payments` received `datetime(2026, 9, 27, 0, 0)`, naive.

### Implementation

- [X] T008 [US1] Add `LocalDateTime = Annotated[datetime, AfterValidator(to_local), Field(description=LOCAL_DATETIME_DESCRIPTION)]` to `app/schemas/__init__.py`. Build `LOCAL_DATETIME_DESCRIPTION` from `settings.business_timezone.key` using the exact wording in contracts/datetime-convention.md. Extend the module docstring's `Field(description=...)` note with one short paragraph on why every datetime earns a description (#228, mictlanix/mbe-ui#176).
- [X] T009 [P] [US1] Replace every `datetime` field annotation with `LocalDateTime` (keeping `| None` and defaults) in `app/schemas/sales_order.py`, `app/schemas/sales_quote.py`, `app/schemas/customer_payment.py` and `app/schemas/customer_refund.py`. Drop `datetime` imports that become unused.
- [X] T010 [P] [US1] Same replacement in `app/schemas/delivery_order.py`, `app/schemas/delivery_itinerary.py`, `app/schemas/cash_session.py` and `app/schemas/credit_note.py`.
- [X] T011 [P] [US1] Same replacement in `app/schemas/core.py` and `app/schemas/fiscal.py`. In `core.py`:
  - leave the `dt.date.today()` line for T020;
  - leave `dt.date` fields alone, since dates are out of scope.
  `app/schemas/auth.py` needs no change: `expires_at` is a `str`.
- [X] T012 [P] [US3] Change the `date_from`/`date_to` query parameters from `datetime | None` to `LocalDateTime | None` in `app/api/v1/endpoints/customer_payments.py` (3 endpoints), `app/api/v1/endpoints/sales_orders.py`, `app/api/v1/endpoints/delivery_orders.py` and `app/api/v1/endpoints/cash_sessions.py`. Drop unused `datetime` imports. Leave the `date`-typed filters in `exchange_rates.py` and `delivery_itineraries.py` alone.
- [X] T013 [US1] Run T005–T007: they pass now. Then run `uv run pytest tests/api tests/unit/test_field_descriptions.py`: all green, so existing field descriptions and response shapes survived.

**Checkpoint**: the contract is documented and offsets are converted. This slice alone closes the gap that caused mbe-ui#176.

---

## Phase 4: User Story 2 - Timestamps do not depend on the server's clock setting (P1)

**Goal**: every "now" and "today" comes from `app/core/clock.py` (FR-002, FR-003, SC-002, SC-004). JWT expiry stays aware UTC (FR-008).

**Independent Test**: no host-clock read remains outside `app/core/clock.py` and `app/core/security.py`. `local_now()` follows the setting (T002).

### Tests first (confirm each fails before T017)

- [X] T014 [P] [US2] Add a test to `tests/unit/test_datetime_contract.py`. Scan every `.py` under `app/` for the regex `datetime\.now\(|date\.today\(|datetime\.utcnow\(`, excluding `app/core/clock.py` and `app/core/security.py`. Assert no matches, listing `file:line` for each offender. Add a second assertion that `app/core/security.py` still uses `datetime.now(UTC)`, so FR-008 cannot be "fixed" by accident. Depends on T005 (same file).
- [X] T015 [P] [US2] Add a unit test covering FR-003 in `tests/unit/` (a new `test_vehicle_operator_service.py` if none exists). Patch `app.services.vehicle_operator_service.local_now` to return a fixed value, and assert `create_vehicle_operator` and the update path stamp exactly that value. It fails before T021, because the name does not exist yet and the code stamps UTC.

- [X] T016 [P] [US2] In `tests/unit/test_sales_order_service.py`, add a test for the overdue cutoff in `_overdue_credit_orders` (spec US2 scenario 3). Patch `app.services.sales_order_service.local_now` to return a fixed `datetime(2026, 9, 27, 12, 0)`. Give it a mock `db` whose `execute` captures the statement. Assert the fixed value appears in `statement.compile().params.values()`. It fails before T017, because the cutoff still comes from `datetime.now()`.

### Implementation

- [X] T017 [P] [US2] Replace `datetime.now()` with `local_now()` in `app/services/sales_order_service.py` (9), `app/services/sales_quote_service.py` (10) and `app/services/order_expiry.py` (1). Drop `datetime` imports that become unused. Keep them where `datetime` is still used as a type or constructor.
- [X] T018 [P] [US2] Same in `app/services/delivery_order_service.py` (11), `app/services/delivery_itinerary_service.py` (8) and `app/services/delivery_events.py` (2).
- [X] T019 [P] [US2] Same in `app/services/customer_payment_service.py` (3), `app/services/customer_refund_service.py` (5), `app/services/stock_ledger.py` (1) and `app/services/incidences.py` (1).
- [X] T020 [US2] In `app/services/cash_session_service.py`, replace `datetime.now()` (2) with `local_now()` and `date.today()` (2) with `local_today()`. In `app/schemas/core.py:573` replace `dt.date.today()` with `local_today()`. Depends on T011 (same file).
- [X] T021 [P] [US2] In `app/services/vehicle_operator_service.py:76` and `:114`, replace `datetime.now(tz=UTC).replace(tzinfo=None)` with `local_now()`, imported so T015's patch target resolves. Drop the `UTC` import if unused.
- [X] T022 [US2] Run T014–T015 (they pass now) and `uv run pytest tests/unit`. If an existing unit test fails because it compared a stored time against wall-clock `datetime.now()`, patch `local_now` in the service module under test to a fixed value instead. None is expected: no test in `tests/unit/` patches or compares against `datetime.now()` today.

**Checkpoint**: the host timezone no longer affects any stored or compared time. All three stories are independently verified.

---

## Phase 6: Polish & Cross-Cutting

- [X] T023 Add a `### Changed` entry to `CHANGELOG.md` `[Unreleased]`. Cover the local-time contract (#228), offset conversion, and vehicle operator timestamps now local. Give an **upgrade note** its own bullet: `BUSINESS_TIMEZONE` is now required, and the API will not start without it. Add a note for clients that the mbe-ui `…T00:00:00.000Z` workaround now stores the previous evening (mictlanix/mbe-ui#176).
- [X] T024 `uv run ruff check app/ migrations/ tests/`: zero violations.
- [X] T025 `uv run pytest`: full suite green.
- [X] T026 Run quickstart.md §2 (required setting fails at import, both cases) and §3 (`TZ=UTC` host, order created without a date carries Mexico City time) against `mbe_dev`. Record the observed values in the PR description.

---

## Dependencies & Execution Order

- **Setup (T001)** → **Foundational (T002 → T003 → T004)** → stories.
- **US1 + US3 (T005–T007 in parallel → T008 → T009–T012 in parallel → T013)**: needs T004.
- **US2 (T014–T016 → T017–T021 → T022)**: needs T004. It may run alongside Phase 3, with two exceptions:
  - T014 waits for T005 (same test file);
  - T020 waits for T011 (`app/schemas/core.py`).
- **Polish (T023–T026)**: after all stories.

## Parallel Examples

```text
# Phase 3, tests first:
T005 contract test   T006 sales-order offset tests   T007 customer-payment filter test

# Phase 3, after T008:
T009 schemas: sales_order, sales_quote, customer_payment, customer_refund
T010 schemas: delivery_order, delivery_itinerary, cash_session, credit_note
T011 schemas: core, fiscal
T012 endpoints: customer_payments, sales_orders, delivery_orders, cash_sessions

# US2, after T014–T016:
T017 sales services   T018 delivery services   T019 payment/refund/stock/incidences   T021 vehicle operator
```

## Implementation Strategy

- **MVP = Phase 1 + 2 + 3.** It fixes the written contract, which the issue calls the blocking item, and it converts offsets, which comes free with the same type.
- **US2 next.** It removes the host-timezone risk and the vehicle operator inconsistency.
- Ship with the mbe-ui change coordinated (spec Assumptions). The client's midnight workaround changes meaning once offsets are honored.
