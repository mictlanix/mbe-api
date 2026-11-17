---
description: "Task list for spec 039: CFDI stamping and cancellation against a simulated PAC"
---

# Tasks: CFDI stamping and cancellation against a simulated PAC

**Input**: Design documents from `specs/039-cfdi-stamping-cancellation/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R14), data-model.md, contracts/ (pac-provider.md, endpoints.md, error-codes.md), quickstart.md

**Tests**: Included. The spec's success criteria (SC-002 concurrency, SC-004 no double stamp, SC-006 second provider, SC-007 credential scan) and quickstart.md name the test files; the constitution requires tests with each change.

**Organization**: Tasks are grouped by user story. Paths are relative to the worktree `../mbe-api-039`.

**Test environment** (every test task):

```bash
export DATABASE_URL='sqlite+aiosqlite:///:memory:' JWT_SECRET_KEY=test-secret-test-secret-test-secret-0123 \
       BUSINESS_TIMEZONE=America/Mexico_City APP_ENV=development PAC_PROVIDER=simulated
```

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story (US1–US5) the task belongs to

---

## Phase 1: Setup

**Purpose**: settings, the migration and the error codes that every story uses.

- [ ] T001 Add `app_env: Literal['development', 'staging', 'production'] = 'production'` and `pac_provider: Literal['none', 'simulated'] = 'none'` to `Settings` in `app/core/config.py`, with comments in the file's style (research R4, R5). Add a helper `check_pac_settings(settings)` that raises `RuntimeError` naming both settings when `pac_provider == 'simulated'` and `app_env == 'production'`
- [ ] T002 [P] Write `migrations/034_cfdi_stamping_cancellation.sql` and `migrations/034_cfdi_stamping_cancellation_rollback.sql`, following migration 033's header and style: `ALTER TABLE fiscal_document ADD COLUMN stamping_since DATETIME NULL`; create `fiscal_document_cancellation` (columns, FKs to `fiscal_document`, `sat_reason_cancellation`, `employee`; virtual `open_document` with UNIQUE; index `(status, requested_at)`) and `pac_simulation` (UNIQUE `(issuer, batch, serial)`, UNIQUE `uuid`) exactly as data-model.md defines them. Do NOT apply it to mbe_dev (needs confirmation)
- [ ] T003 [P] Add the 12 codes from contracts/error-codes.md to `ErrorCode` in `app/core/error_codes.py` (8 `fiscal_document.*`, new `pac` domain: `not_configured` 503, `rejected` 422, `timeout` 504, `unavailable` 503), with their HTTP status and params as the existing members declare them
- [ ] T004 [P] Add the same rows to `specs/036-localizable-error-codes/contracts/error-codes.md`, alphabetical within each domain, add the `pac` domain section, and add `fiscal_document` to `lock.busy`'s resource list; run `uv run pytest tests/unit/test_contract_docs.py -q` and make it pass

**Checkpoint**: settings load, `ruff check` clean, contract-docs test passes.

---

## Phase 2: Foundational (blocks every story)

**Purpose**: models, the provider contract, the provider dependency, the startup guard and the new status values.

- [ ] T005 Add `stamping_since: Mapped[datetime | None]` to `FiscalDocument`, and new models `FiscalDocumentCancellation` (with `open_document` as a `Computed(..., persisted=False)` column, matching how migration 033's virtual column is modelled) and `PacSimulation` in `app/models/fiscal.py`; export them from `app/models/__init__.py` if that module lists models
- [ ] T006 [P] Create `app/services/pac/provider.py` with the types and Protocol exactly as contracts/pac-provider.md: dataclasses `Stamp`, `Csd`, `CancellationRequest`, `SatStatus`; `CancellationAnswer(StrEnum)`; exceptions `PacError` base, `PacRejected(code, message)`, `PacUnavailable`, `PacTimeout`; `PacProvider(Protocol)` with async `stamp`, `find_stamp`, `request_cancellation`, `query_status`; constant `SIMULATED_PAC_RFC = 'SIM010101SIM'`. `Csd.__repr__` must not print the key or password (R14)
- [ ] T007 Create `app/services/pac/__init__.py` with `get_pac_provider() -> PacProvider | None` in the style of `get_mailer()` (`app/services/mail.py:89`): `None` for `none`, a `SimulatedPac()` for `simulated` (import inside the function so nothing else imports the simulator). Add a helper `require_provider(provider)` that raises `DomainError(ErrorCode.PAC_NOT_CONFIGURED)` when it is `None`
- [ ] T008 Call `check_pac_settings(settings)` at the start of `lifespan` in `app/main.py` (R5)
- [ ] T009 Update `document_status()` in `app/services/fiscal_document_service.py:27` to the order in data-model.md (cancelled → cancellation_pending → issued → stamping → sealed → draft). It needs to know whether an open `fiscal_document_cancellation` exists: load it in the list/detail queries (an `EXISTS` on `status IN ('requested','pending')`) and pass it in, rather than a query per row. Add `stamping` and `cancellation_pending` to the list's `status` filter (:237) as SQL conditions. Do not change `live()` (:96)
- [ ] T010 [P] Add `stamping` and `cancellation_pending` to the status literal in `app/schemas/fiscal.py`
- [ ] T011 [P] Create `tests/fakes/__init__.py` and `tests/fakes/pac.py` with `ScriptedPac`: an in-memory `PacProvider` whose next results and raised errors are set per call (queues for `stamp`, `find_stamp`, `request_cancellation`, `query_status`), which counts calls per operation and builds a TFD with its own RFC (not `SIM010101SIM`) (R13)
- [ ] T012 [P] Write `tests/unit/test_pac_settings.py`: `check_pac_settings` raises for simulated+production and passes for the other five combinations; the default `app_env` is `production`; `get_pac_provider()` returns `None` for `none`; `require_provider(None)` raises `pac.not_configured` (503)
- [ ] T013 Extend `tests/unit/test_fiscal_document_service.py` with the status order: a row with `stamping_since` and no completion reads `stamping`; completed with an open request reads `cancellation_pending`; completed with only closed requests reads `issued`; cancelled wins over an open request

**Checkpoint**: `uv run pytest -q` passes (existing suite unchanged), new unit tests pass.

---

## Phase 3: User Story 1 – Stamp a sealed document (P1) 🎯 MVP

**Goal**: a sealed invoice, receipt or note becomes issued with its stamp, exactly once.

**Independent test**: seal one document of each type, `POST /stamp`, read it back: issued, UUID stored, XML has the TFD, PDF shows the stamp and the simulated banner, reopen is refused.

### Tests for User Story 1

- [ ] T014 [P] [US1] Write `tests/unit/test_pac_simulated.py` (stamp part): `stamp` returns a TFD 1.1 inside `cfdi:Complemento` that `cfdi.parse()` reads back (UUID upper case 36 chars, `SelloCFD` equals the sealed `Sello`, 344-char `SelloSAT`, `NoCertificadoSAT` 20 digits, `RfcProvCertif` `SIM010101SIM`); `find_stamp` returns that stamp after `stamp`, `None` before; outcomes `rejected` (default code `CFDI40999`, chosen code/message), `unavailable`, `timeout_stamped` (raises `PacTimeout`, `find_stamp` then returns a stamp), `timeout_not_stamped` (raises, `find_stamp` returns `None`); each outcome applies once, then reverts to `stamped`
- [ ] T015 [P] [US1] Write `tests/integration/test_stamp_flow.py`, parametrised over `SimulatedPac` and `ScriptedPac` (SC-006), using the `invoicing`, `receipts` and `notes` fixtures to seal one document of each type: stamp → issued with `stamp_uuid`, `stamped`, `authority_digital_seal`, `authority_certificate_number`, `rfc_pac` set and `FiscalDocumentXml.data` the stamped XML; repeat on issued returns it unchanged and calls the PAC 0 more times; draft → `not_sealed`; issue date > 72 h → `issue_date_out_of_range` and 0 PAC calls; rejected → sealed, `pac.rejected {pac_code, pac_message}`; unavailable → sealed, `pac.unavailable`; timeout → stamping, `pac.timeout {state: stamping}`; stamp on stamping → `fiscal_document.stamping`, 0 PAC calls; reopen of issued and of stamping is refused; an incidence `CFDI stamped {uuid}` is recorded
- [ ] T016 [P] [US1] Add the SC-002 concurrency test to `tests/integration/test_stamp_flow.py`: 20 concurrent stamp calls on one sealed document (separate sessions, as the existing lock tests do) → `ScriptedPac` receives exactly 1 `stamp` call, the document ends with 1 UUID, the other 19 get the issued document, `fiscal_document.stamping` or `lock.busy`. If SQLite cannot exercise row locks, mark it to run only on MariaDB the way existing lock tests do and say so in the task notes

### Implementation for User Story 1

- [ ] T017 [US1] Create `app/services/pac/simulated.py` with `SimulatedPac` implementing `stamp` and `find_stamp` per research R6: read issuer RFC, `Serie`, `Folio` and `Sello` from the sealed XML; keep state in `pac_simulation` through its **own** `AsyncSessionLocal()` session, committed at once; build the TFD with lxml, insert it into `cfdi:Complemento` (create it if absent); apply and clear `next_stamp`; `timeout_stamped` stores the stamp then raises `PacTimeout`; `timeout_not_stamped` raises without storing. Add a function `set_next(document keys, stamp=?, cancellation=?, pac_code=?, pac_message=?)` and `get_record(...)` for the simulator routes
- [ ] T018 [US1] Create `app/services/fiscal_stamping.py` with `stamp(db, document_id, provider, user)` following research R3: Txn A (row lock via `busy_as_conflict`, state checks in endpoints.md `/stamp` order, 72-hour re-check reusing the confirm check, set `stamping_since`, commit); call `provider.stamp(sealed_xml)` with no transaction open; Txn B `_store_stamp(...)` (lock again; if uncompleted and XML unchanged, write stamp fields, replace XML, `completed=1`, clear `stamping_since`, record incidence) — keep `_store_stamp` reusable for refresh (US3). Map `PacRejected` / `PacUnavailable` to clearing the marker and raising `pac.rejected` / `pac.unavailable`; `PacTimeout` keeps the marker and raises `pac.timeout {state: 'stamping'}`. Never log the XML or the PAC's raw error
- [ ] T019 [US1] Add `POST /fiscal-documents/{id}/stamp` in `app/api/v1/endpoints/fiscal_documents.py`: Update on 23, facility check exactly as the existing fiscal-document routes do, `provider = Depends(get_pac_provider)` + `require_provider`, returns `FiscalDocumentResponse`
- [ ] T020 [P] [US1] Add `is_simulated` to the print context in `app/services/print_contexts.py` (≈:821, `rfc_pac == SIMULATED_PAC_RFC`) and a "DOCUMENTO SIMULADO — SIN VALIDEZ FISCAL" banner in `app/rendering/templates/fiscal_document.html`, styled like the existing CANCELADO stamp (:17) (FR-009, R11)
- [ ] T021 [P] [US1] Add the `stamp` object (`uuid`, `stamped`, `sat_certificate`, `provider_rfc`, `simulated`) and `stamping_since` to `FiscalDocumentResponse` in `app/schemas/fiscal.py` and fill them in the detail builder in `app/services/fiscal_document_service.py`; `stamp` is `null` until `stamp_uuid` is set
- [ ] T022 [US1] Write the US1 cases of `tests/api/test_fiscal_stamping.py`: stamp happy path 200; 401 without token; 404 unknown id; 403 without Update on 23; 404/403 for a facility outside scope (match the existing routes' behaviour); 409 `not_sealed`; `pac.not_configured` 503 with `get_pac_provider` overridden to return `None`; PDF of the stamped document contains the UUID and the simulated banner; XML download returns the stamped XML

**Checkpoint**: US1 independently testable; `uv run pytest -q` passes.

---

## Phase 4: User Story 2 – Cancel a stamped document (P1)

**Goal**: an issued document is cancelled at once or goes cancellation pending; cancelling frees spec 028's holds.

**Independent test**: cancel one document accepted at once and one pending; check state and history.

### Tests for User Story 2

- [ ] T023 [P] [US2] Add cancellation cases to `tests/unit/test_pac_simulated.py`: default rule (stamp < 24 h → `ACCEPTED`, ≥ 24 h → `PENDING`, set `stamped_at` back to test it); chosen outcomes `accepted`, `pending`, `not_cancellable`, `rejected`, `unavailable`, `timeout` (records `cancellation_status='pending'`, raises `PacTimeout`); `query_status` reflects each; receiver answers `accept` → cancelled/accepted, `reject` → valid/rejected, `expire` → cancelled/expired; a receiver answer with nothing pending raises
- [ ] T024 [P] [US2] Write `tests/integration/test_cancel_flow.py`, parametrised over `SimulatedPac` and `ScriptedPac`, covering every row of contracts/endpoints.md `/cancel`: accepted → `cancelled=1`, `cancellation_date`, `cancellation_reason`, `cancellation_substitution` set and request `accepted`; pending → `cancellation_pending`, request `pending`; not issued (draft, sealed, stamping, cancelled) → `not_issued`; open request → `cancellation_open`; unknown reason → `unknown_cancellation_reason`; 01 without substitute → `substitution_required`; 01 with a substitute that is unknown, unstamped, cancelled, or of another issuer or recipient → `substitution_invalid`; 01 with a valid substitute → accepted; invoice with a live receipt and a live (including pending) credit note → `has_live_documents {documents: [...]}` naming both; not cancellable → 409, issued, request `not_cancellable`; rejected → `pac.rejected`, request `rejected` with code/message; unavailable → `pac.unavailable`, request `failed`; timeout → `cancellation_pending`, request `requested`, `pac.timeout {state: cancellation_pending}`. Assert the CSD password never appears in `caplog` across the file (SC-007)
- [ ] T025 [P] [US2] Add SC-005 cases to `tests/integration/test_cancel_flow.py`: after an invoice is cancelled in the request, its units are refundable (spec 028 refund rule) and a later receipt's previous balance excludes it; while pending, both still count it; a cancelled receipt's customer payment can back a new receipt; adding or confirming a receipt or credit note against an invoice with an open request fails with `invoice_not_payable` / `invoice_not_creditable`

### Implementation for User Story 2

- [ ] T026 [US2] Add `request_cancellation`, `query_status` and `answer_as_receiver(keys, answer)` to `SimulatedPac` in `app/services/pac/simulated.py` (R6 table, 24-hour default, one-shot `next_cancellation`)
- [ ] T027 [US2] Add `cancel(db, document_id, reason, substitution, provider, user)` to `app/services/fiscal_stamping.py` per research R8: Txn A checks in the stated order (reason looked up in `sat_reason_cancellation`; substitute by `stamp_uuid` must be issued or cancellation pending with same issuer and recipient; live related receipts/notes via `fiscal_document_relation` with `document` of type 200 or 100, listed as `batch + serial`), insert the request row `requested`, commit; build `CancellationRequest` with the CSD of the issuer's latest active certificate, chosen as confirm does; call the PAC; Txn B applies the R8 answer table, sets `answered_at`/`resolved_at`, records the R10 incidences. Keep a `_apply_cancelled(...)` helper shared with refresh
- [ ] T028 [US2] In `app/services/invoice_service.py`, make `_payable` (:1076) and `_creditable` (:1425) also refuse an invoice with an open `fiscal_document_cancellation`, raising their existing codes (R8). No other change to that file
- [ ] T029 [US2] Add `POST /fiscal-documents/{id}/cancel` with body `{reason: str, substitution: UUID | None}` (new request schema in `app/schemas/fiscal.py`) to `app/api/v1/endpoints/fiscal_documents.py`; same privilege, facility and provider handling as `/stamp`
- [ ] T030 [US2] Write the US2 cases of `tests/api/test_fiscal_stamping.py`: cancel 200 accepted and pending; 401, 404, 403 privilege, facility; 409 `not_issued`; 422 `unknown_cancellation_reason`; body validation of `substitution` as a UUID

**Checkpoint**: US1 + US2 independently testable.

---

## Phase 5: User Story 3 – Resolve documents left in flight (P2)

**Goal**: refresh on demand and by a periodic job.

**Independent test**: leave one document stamping and one pending, run the job once, see them resolved.

### Tests for User Story 3

- [ ] T031 [P] [US3] Add refresh cases to `tests/integration/test_stamp_flow.py`: timeout-stamped → refresh → issued with the PAC's UUID, `stamp` called once in total (SC-004); timeout-not-stamped → refresh within 5 min → still stamping; past 5 min (set `stamping_since` back) → sealed, incidence `CFDI stamp not found, sealed again`, and can be stamped again; a late stamp answer after a reset is still stored (XML unchanged); refresh with PAC unavailable → unchanged, `pac.unavailable`
- [ ] T032 [P] [US3] Add refresh cases to `tests/integration/test_cancel_flow.py`: pending + SAT cancelled → cancelled, request `accepted`; cancelled with `expired` → request `expired`; valid + rejected → issued, request `rejected`, a new cancel is then accepted; valid + none → issued, request `failed`; still pending → unchanged, `last_checked_at` set; refresh on an issued document → `nothing_to_refresh`
- [ ] T033 [P] [US3] Write `tests/integration/test_resolve_job.py`: one stamping and one pending document are resolved in one run (SC-003); a document whose PAC call raises does not stop the others and the job returns exit code 1; the job refuses to start with simulated+production; incidences are recorded as the system employee

### Implementation for User Story 3

- [ ] T034 [US3] Add `refresh(db, document_id, provider, user)` to `app/services/fiscal_stamping.py` per research R8 "Refresh": stamping → `find_stamp` → `_store_stamp` or the 5-minute grace (`STAMP_LOOKUP_GRACE = timedelta(minutes=5)`); open request → `query_status` → `_apply_cancelled` / back to issued / still pending, with the request status, timestamps and incidences from R8 and R10; any other state → `nothing_to_refresh`; unavailable/timeout leave the state and raise
- [ ] T035 [US3] Add `POST /fiscal-documents/{id}/refresh` to `app/api/v1/endpoints/fiscal_documents.py`, same privilege, facility and provider handling
- [ ] T036 [US3] Create `app/jobs/resolve_fiscal_documents.py` in the pattern of `app/jobs/send_scheduled_reports.py`: `check_pac_settings`, `get_pac_provider()` (exit 0 with a message when `None`), system employee via `ensure_system_employee`, select stamping documents and open requests oldest first, refresh each in its own session/transaction, print failures without PAC payloads, exit 1 if any failed
- [ ] T037 [US3] Write the US3 cases of `tests/api/test_fiscal_stamping.py`: refresh 200 for stamping and pending; 409 `nothing_to_refresh`; 401, 404, 403, facility

**Checkpoint**: US1–US3 testable; SC-003 and SC-004 covered.

---

## Phase 6: User Story 4 – See and filter fiscal states and history (P2)

**Goal**: lists filter by every state; the detail shows the stamp and every cancellation request.

**Independent test**: documents in every state; filter by each; read a document rejected once then cancelled.

- [ ] T038 [P] [US4] Add `FiscalDocumentCancellationResponse` (`id, reason, substitution, status, pac_code, pac_message, requested_by, requested_at, answered_at, resolved_at, last_checked_at`) and `cancellation_substitution` + `cancellations: list[...]` on `FiscalDocumentResponse` in `app/schemas/fiscal.py`
- [ ] T039 [US4] Load the document's cancellation rows newest first in the detail read in `app/services/fiscal_document_service.py` (one `selectinload` or one extra query, not per row) and fill the new fields
- [ ] T040 [US4] Add list tests to `tests/api/test_fiscal_stamping.py`: with documents in all six states, `GET /fiscal-documents?status=X` returns exactly those for each X, with unchanged paging and facility scope; a document rejected once then cancelled shows both requests in order with their outcomes; a document stamped by the current system (seeded with `seed_fiscal_document`, `completed=1`, no request rows) reads `issued` with `stamp` filled and `simulated: false`

**Checkpoint**: US4 testable.

---

## Phase 7: User Story 5 – Drive every PAC outcome in the simulator (P2)

**Goal**: every outcome reachable through the API alone.

**Independent test**: walk contracts/endpoints.md "Driving each scenario" through the API.

- [ ] T041 [P] [US5] Add simulator request/response schemas (`PacSimulationResponse`, `PacSimulationUpdate` with `stamp`/`cancellation` literals from endpoints.md, `ReceiverAnswer`) in `app/schemas/fiscal.py`
- [ ] T042 [US5] Create `app/api/v1/endpoints/pac_simulator.py` with `GET` and `PUT /pac-simulator/fiscal-documents/{id}` and `POST /pac-simulator/fiscal-documents/{id}/receiver`: Update on 23, facility check, document must be sealed or later (else `not_sealed`), map the id to issuer/series/folio from the stored document and call `SimulatedPac.set_next` / `get_record` / `answer_as_receiver`; receiver with nothing pending → 409 `nothing_to_refresh`
- [ ] T043 [US5] Mount the router in `app/api/v1/router.py` only when `settings.pac_provider == 'simulated'`, prefix `/pac-simulator`, tag `pac-simulator`
- [ ] T044 [US5] Write the simulator tests in `tests/api/test_fiscal_stamping.py`: one test per row of endpoints.md "Driving each scenario" (10 rows; for "timeout, did not stamp" set `stamping_since` back instead of waiting), all through HTTP only (SC-001); routes return 404 when the app is built with `PAC_PROVIDER=none`; 403 without Update on 23

**Checkpoint**: every story independently functional.

---

## Phase 8: Polish & cross-cutting

- [ ] T045 [P] Add a "New MBE: stamping and cancellation (spec 039)" section to `docs/specs/10-fiscal-documents.md`: states, the simulator, the 5-minute grace, the job and its cron, `APP_ENV`/`PAC_PROVIDER`
- [ ] T046 [P] Add a CHANGELOG `Added` entry in `CHANGELOG.md` for the three endpoints, the simulator routes, the job, the two settings and migration 034, in the file's existing style
- [ ] T047 Run the SC-007 scan from quickstart.md (`git grep -nIiE 'pac_(user|password)|FacturacionMexico(User|Password)' -- . ':!specs'`) and confirm it finds nothing
- [ ] T048 Confirm only `app/services/pac/__init__.py` and `app/api/v1/endpoints/pac_simulator.py` import `app.services.pac.simulated` (`git grep -n 'pac.simulated' app/`) (SC-006, R13)
- [ ] T049 Run `uv run ruff check app/ migrations/ tests/` and `uv run ruff format --check app/ tests/`, then the whole suite `uv run pytest -q`; fix anything failing
- [ ] T050 Ask the user to confirm applying migration 034 to mbe_dev and adding `APP_ENV=development` and `PAC_PROVIDER=simulated` to dev's `.env`; after confirmation apply them, deploy the branch to dev :8765 (pre-authorized) and walk quickstart.md §2 steps 1–4 against it; clean up created documents only with confirmation

---

## Dependencies & execution order

- **Phase 1 → Phase 2 → stories.** T005 needs T002's column list; T007 needs T006; T009 needs T005.
- **US1 (Phase 3)** needs Phase 2. It is the MVP.
- **US2 (Phase 4)** needs Phase 2 and stamped documents, so in practice US1's T017–T018 (tests stamp through the flow). T028 is independent of the rest of US2.
- **US3 (Phase 5)** needs US1 (`_store_stamp`) and US2 (`_apply_cancelled`, open requests).
- **US4 (Phase 6)** needs Phase 2 only for the list filter; its history test needs US2 and US3 to create the rows.
- **US5 (Phase 7)** needs the simulator from US1 (T017) and US2 (T026).
- **Polish** last; T050 needs the user's confirmation.

```text
Setup ─▶ Foundational ─▶ US1 ─▶ US2 ─▶ US3
                          │      │
                          └──────┴─▶ US4, US5 ─▶ Polish
```

## Parallel opportunities

- Phase 1: T002, T003, T004 together after T001.
- Phase 2: T006, T010, T011, T012 together; T005 alongside them.
- US1: T014, T015, T016 (tests) together; T020 and T021 alongside T018.
- US2: T023, T024, T025 together; T028 alongside T026–T027.
- US3: T031, T032, T033 together.
- US4/US5: T038 and T041 together.
- Polish: T045, T046 together.

### Example: US1

```text
Parallel: T014 tests/unit/test_pac_simulated.py
          T015 tests/integration/test_stamp_flow.py
          T020 print context + template banner
          T021 response schema stamp fields
Then:     T017 SimulatedPac.stamp → T018 fiscal_stamping.stamp → T019 route → T022 API tests
```

## Implementation strategy

1. **MVP**: Phases 1–3. A sealed document can be stamped against the simulator, read and printed. This alone unblocks the stamping step of #54 and #56.
2. **+ US2**: cancellation, which unblocks #52.
3. **+ US3**: refresh and the job, so nothing stays stuck.
4. **+ US4, US5**: history in reads and the simulator controls the UI testers need; then the handoff.
5. Each phase ends with `uv run pytest -q` and `ruff check` green, and a commit on the spec branch.
