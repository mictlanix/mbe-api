---
description: "Task list for spec 039: CFDI stamping and cancellation against a simulated PAC"
---

# Tasks: CFDI stamping and cancellation against a simulated PAC

**Input**: Design documents from `specs/039-cfdi-stamping-cancellation/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R14), data-model.md, contracts/ (pac-provider.md, endpoints.md, error-codes.md), quickstart.md

**Tests**: Required by the constitution. In every phase the test tasks come first and must fail before the code that makes them pass. A route and its `tests/api` cases are one task.

**Test placement** (existing conventions):
- `tests/unit/`: mocked session, branch by branch.
- `tests/api/`: routes with the service patched out, in the style of `tests/api/test_fiscal_documents.py`.
- `tests/integration/`: real SQLite per test (`db`, `sessions`, `client` fixtures in `tests/integration/conftest.py`).

**Providers in tests**: integration tests build `SimulatedPac(session_factory=sessions)` from the `sessions` fixture, or a `ScriptedPac`, and override `get_pac_provider` with it. Tests that need the simulator routes set `monkeypatch.setattr(settings, 'pac_provider', 'simulated')`. No environment variable beyond the usual three is needed:

```bash
export DATABASE_URL='sqlite+aiosqlite:///:memory:' JWT_SECRET_KEY=test-secret-test-secret-test-secret-0123 \
       BUSINESS_TIMEZONE=America/Mexico_City
```

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an unfinished task)
- **[Story]**: the user story (US1–US5) the task belongs to

---

## Phase 1: Setup

**Purpose**: settings, the migration and the error codes that every story uses.

- [ ] T001 Write `tests/unit/test_pac_settings.py` first and confirm it fails: `settings.app_env` defaults to `production` and `pac_provider` to `none`; `check_pac_settings` raises `RuntimeError` naming both settings for `simulated` + `production` and passes for the other five combinations; `get_pac_provider()` returns `None` for `none` and a `SimulatedPac` for `simulated`; `require_provider(None)` raises `pac.not_configured` (503); the app's `lifespan` raises for `simulated` + `production` (R4, R5)
- [ ] T002 Add `app_env: Literal['development', 'staging', 'production'] = 'production'` and `pac_provider: Literal['none', 'simulated'] = 'none'` to `Settings` in `app/core/config.py`, with comments in the file's style, and `check_pac_settings(settings)` raising `RuntimeError` naming both settings when `pac_provider == 'simulated'` and `app_env == 'production'`. The settings part of T001 passes
- [ ] T003 [P] Write `migrations/034_cfdi_stamping_cancellation.sql` and `migrations/034_cfdi_stamping_cancellation_rollback.sql` in migration 033's header and style, exactly as data-model.md: `ALTER TABLE fiscal_document ADD COLUMN stamping_since DATETIME NULL`; table `fiscal_document_cancellation` (FKs to `fiscal_document`, `sat_reason_cancellation`, `employee`; virtual `open_document` with UNIQUE; index `(status, requested_at)`); table `pac_simulation` (UNIQUE `(issuer, batch, serial)`, UNIQUE `uuid`). Document `fiscal_document.stamping_since` and every column of both tables in `docs/data-dictionary.md`. Do NOT apply it to mbe_dev (T052 asks first)
- [ ] T004 [P] Add the 12 rows from contracts/error-codes.md to `specs/036-localizable-error-codes/contracts/error-codes.md` (alphabetical within each domain, a new `pac` section, `fiscal_document` added to `lock.busy`'s resources). Write params in 036's typed form (`documents: list[str]`, `pac_code: str, pac_message: str`, `state: str`, `reason: str`, `substitution: str`), and widen `fiscal_document.not_issued`'s Situation text to cover cancelling a document that is not issued. Run `uv run pytest tests/unit/test_contract_docs.py -q` and confirm it fails
- [ ] T005 Add the 12 codes to `ErrorCode` in `app/core/error_codes.py` (8 `fiscal_document.*`; `pac.not_configured` 503, `pac.rejected` 422, `pac.timeout` 504, `pac.unavailable` 503) with HTTP status and params declared as the existing members do. Update the pinned count in `test_the_parser_still_finds_the_error_codes` (`tests/unit/test_contract_docs.py:193`) from 268 to 280. `tests/unit/test_contract_docs.py` passes

**Checkpoint**: `uv run ruff check app/ migrations/ tests/` clean; contract-docs test passes.

---

## Phase 2: Foundational (blocks every story)

**Purpose**: models, the provider contract and dependency, the startup guard and the two new status values.

- [ ] T006 Extend `tests/unit/test_fiscal_document_service.py` first and confirm it fails: with no completion and `stamping_since` set → `stamping`; completed with an open request → `cancellation_pending`; completed with only closed requests → `issued`; cancelled with an open request → `cancelled`; the list's `status` filter builds a condition for `stamping` and for `cancellation_pending`, `issued` excludes documents with an open request, and `sealed` excludes documents with `stamping_since` set
- [ ] T007 Add `stamping_since: Mapped[datetime | None]` to `FiscalDocument`, and models `FiscalDocumentCancellation` (leave the virtual `open_document` unmapped, as `sales_order_payment.live` from migration 033 is; data-model.md) and `PacSimulation` in `app/models/fiscal.py`. Run `uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py -q` and make it pass
- [ ] T008 [P] Write `tests/unit/test_pac_provider.py` first and confirm it fails: `repr(Csd(...))` contains neither the key bytes nor the password (R14). Then create `app/services/pac/provider.py` exactly as contracts/pac-provider.md: dataclasses `Stamp`, `Csd`, `CancellationRequest`, `SatStatus`; `CancellationAnswer(StrEnum)`; exceptions `PacError`, `PacRejected(code, message)`, `PacUnavailable`, `PacTimeout`; `PacProvider(Protocol)` with async `stamp`, `find_stamp`, `request_cancellation`, `query_status`; `SIMULATED_PAC_RFC = 'SIM010101SIM'`. `Csd.__repr__` must not print the key or password, so the test passes
- [ ] T009 Create `app/services/pac/__init__.py` with `get_pac_provider() -> PacProvider | None` in the style of `get_mailer()` (`app/services/mail.py:89`): `None` for `none`, `SimulatedPac()` for `simulated`, importing the simulator inside the function. Add `require_provider(provider)` raising `DomainError(ErrorCode.PAC_NOT_CONFIGURED)` for `None`. Create a stub `app/services/pac/simulated.py` with the `SimulatedPac(session_factory=AsyncSessionLocal)` constructor only; US1 and US2 fill it
- [ ] T010 Call `check_pac_settings(settings)` at the start of `lifespan` in `app/main.py`. T001 passes
- [ ] T011 Update `document_status()` in `app/services/fiscal_document_service.py:27` to the order in data-model.md (cancelled → cancellation_pending → issued → stamping → sealed → draft), taking `has_open_cancellation` as a keyword argument the way it takes `has_xml`. Its two callers (:202, :281) load that flag with one query per read or page (an `EXISTS` on `status IN ('requested','pending')`), not per row. Add `stamping` and `cancellation_pending` to `DocumentStatus` (:21) and to the list filter (:237) as SQL conditions, and narrow the existing branches: `issued` excludes an open request, `sealed` requires `stamping_since IS NULL`. Leave `live()` (:96) unchanged. T006 passes
- [ ] T012 [P] Add `stamping` and `cancellation_pending` to the status literal in `app/schemas/fiscal.py`
- [ ] T013 Create `tests/fakes/__init__.py` and `tests/fakes/pac.py` with `ScriptedPac`: an in-memory `PacProvider` whose results or raised errors are queued per operation, which counts calls per operation, and whose TFD uses an RFC other than `SIM010101SIM` (R13). Needs T008's types

**Checkpoint**: `uv run pytest -q` passes, with the existing suite unchanged.

---

## Phase 3: User Story 1 – Stamp a sealed document (P1) 🎯 MVP

**Goal**: a sealed invoice, receipt or note becomes issued with its stamp, exactly once.

**Independent test**: seal one document of each type, stamp it, read it back: issued, UUID stored, the XML has the TFD, the PDF shows the stamp and the simulated banner, and reopening is refused.

### Tests for User Story 1 (write first, confirm they fail)

- [ ] T014 [P] [US1] Write `tests/unit/test_pac_simulated.py` (stamp part), with `SimulatedPac(session_factory=…)` on a temporary SQLite engine built like `tests/integration/conftest.py`'s: `stamp` returns a TFD 1.1 inside `cfdi:Complemento` that `cfdi.parse()` reads back (UUID upper case 36 chars, `SelloCFD` equal to the sealed `Sello`, 344-char `SelloSAT`, 20-digit `NoCertificadoSAT`, `RfcProvCertif` `SIM010101SIM`); a CFDI without `Serie` is keyed with `batch = ''`; `find_stamp` returns the stamp after `stamp` and `None` before; outcomes `rejected` (default `CFDI40999` / "Rechazo simulado", and a chosen code and message), `unavailable`, `timeout_stamped` (raises `PacTimeout`, then `find_stamp` returns the stamp), `timeout_not_stamped` (raises, then `find_stamp` returns `None`); each outcome applies once and then reverts to `stamped`
- [ ] T015 [P] [US1] Write `tests/unit/test_fiscal_stamping.py` (stamp part), with a mocked session and `ScriptedPac`, in the style of `tests/unit/test_invoice_service.py`: the state checks in `/stamp`'s order (draft → `not_sealed`; issued, pending or cancelled → returned unchanged with 0 PAC calls; stamping → `fiscal_document.stamping` with 0 PAC calls; issue date over 72 h → `issue_date_out_of_range` with 0 PAC calls); stamped → stamp fields written, XML replaced, `completed` set, marker cleared; rejected and unavailable → marker cleared and `pac.rejected {pac_code, pac_message}` / `pac.unavailable`; timeout → marker kept and `pac.timeout {state: 'stamping'}`; the second lock does not store when the document is already completed or its XML changed
- [ ] T016 [P] [US1] Write `tests/integration/test_stamp_flow.py`, parametrised over `SimulatedPac(sessions)` and `ScriptedPac` (SC-006), sealing one document of each type with the `invoicing`, `receipts` and `notes` fixtures. They are module-local, so import them from `tests/integration/test_invoice_flow.py`, `test_receipt_flow.py` and `test_credit_note_flow.py` with `# noqa: F401`, as `tests/integration/test_refund_ticket.py:11` does. `receipts` and `notes` seed the same certificate, so use them in separate tests: stamp → issued with `stamp_uuid`, `stamped`, `authority_digital_seal`, `authority_certificate_number` and `rfc_pac` set, and `FiscalDocumentXml.data` the stamped XML; the detail read returns the `stamp` object and `stamping_since: null`; a repeat on issued returns it unchanged with no new PAC call; after a timeout, a second stamp returns `fiscal_document.stamping` and the PAC's stamp count stays 1 (the SC-002 rule; the concurrent run is quickstart §3); reopen is refused for issued and for stamping; incidence `CFDI stamped {uuid}` is recorded
- [ ] T017 [P] [US1] Extend `tests/unit/test_print_contexts.py` and `tests/unit/test_rendering.py`: the print context's `is_simulated` is true for `rfc_pac = 'SIM010101SIM'` and false otherwise; the rendered fiscal document contains "DOCUMENTO SIMULADO — SIN VALIDEZ FISCAL" only when simulated (FR-009)

### Implementation for User Story 1

- [ ] T018 [US1] Implement `stamp`, `find_stamp`, `set_next(...)` and `get_record(...)` in `SimulatedPac` in `app/services/pac/simulated.py` per research R6: read issuer RFC, `Serie` (`''` when absent), `Folio` and `Sello` from the sealed XML; keep state in `pac_simulation` through `self.session_factory()`, committing at once; build the TFD with lxml and insert it into `cfdi:Complemento` (created if absent); apply and clear `next_stamp`; `timeout_stamped` stores the stamp, then raises `PacTimeout`; `timeout_not_stamped` raises without storing. T014 passes
- [ ] T019 [US1] Create `app/services/fiscal_stamping.py` with `stamp(db, document_id, provider, user)` per research R3: transaction A (row lock under `busy_as_conflict`, the checks in `/stamp`'s order, the 72-hour check reused from confirm, set `stamping_since`, commit); `provider.stamp(sealed_xml)` with no transaction open; transaction B in a reusable `_store_stamp(...)` (lock again; if uncompleted and the XML is unchanged, write the stamp fields, replace the XML, set `completed`, clear the marker, record the incidence). Map the errors as T015 asserts. Never log the XML or the PAC's raw error. T015 and T016 pass
- [ ] T020 [P] [US1] Add `is_simulated` to the print context in `app/services/print_contexts.py` (≈:821, `rfc_pac == SIMULATED_PAC_RFC`) and the "DOCUMENTO SIMULADO — SIN VALIDEZ FISCAL" banner to `app/rendering/templates/fiscal_document.html`, styled like the CANCELADO stamp (:17). T017 passes
- [ ] T021 [P] [US1] Add `stamp` (`uuid`, `stamped`, `sat_certificate`, `provider_rfc`, `simulated`; `null` until `stamp_uuid` is set) and `stamping_since` to `FiscalDocumentResponse` in `app/schemas/fiscal.py`, and fill them in the detail builder in `app/services/fiscal_document_service.py`
- [ ] T022 [US1] Write the `/stamp` cases in `tests/api/test_fiscal_stamping.py` first (patched service): 200; 401; 403 without Update on 23; 404; 409 `not_sealed`, `stamping` and `lock.busy`; 422 `issue_date_out_of_range` and `pac.rejected`; 503 `pac.not_configured` (provider overridden to `None`) and `pac.unavailable`; 504 `pac.timeout`. Confirm they fail, then add `POST /fiscal-documents/{id}/stamp` to `app/api/v1/endpoints/fiscal_documents.py` with that privilege, `provider = Depends(get_pac_provider)` and `require_provider`, returning `FiscalDocumentResponse`

**Checkpoint**: US1 works on its own; `uv run pytest -q` passes.

---

## Phase 4: User Story 2 – Cancel a stamped document (P1)

**Goal**: an issued document is cancelled at once or goes cancellation pending; cancelling frees spec 028's holds.

**Independent test**: cancel one document that is accepted at once and one that goes pending; check the state and the history.

### Tests for User Story 2 (write first, confirm they fail)

- [ ] T023 [P] [US2] Add cancellation cases to `tests/unit/test_pac_simulated.py`: the default rule (stamp under 24 h → `ACCEPTED`, at or over 24 h → `PENDING`, by setting `stamped_at` back); chosen outcomes `accepted`, `pending`, `not_cancellable`, `rejected`, `unavailable`, and `timeout` (records cancellation `pending`, raises `PacTimeout`); `query_status` reflects each; `answer_as_receiver` with `accept` → cancelled/accepted, `reject` → valid/rejected, `expire` → cancelled/expired, and with nothing pending raises; an unknown UUID (a document the current system stamped) gets a row and the default answers `PENDING`
- [ ] T024 [P] [US2] Add cancel cases to `tests/unit/test_fiscal_stamping.py`: the six refusals in R8's order, then `no_valid_certificate` when the issuer has no certificate valid now (not issued, open request, unknown reason, 01 without a substitute, invalid substitute, live related documents listed as `batch + serial`), each before any PAC call; the substitute must be issued or pending with the same issuer and recipient (unknown, unstamped, cancelled, other issuer, other recipient each refused); the transaction B table (accepted, pending, not_cancellable, rejected, unavailable, timeout → request status, document state, raised code, and the R10 incidence recorded for each change: `Cancellation requested`, `Cancellation pending the receiver`, `CFDI cancelled`)
- [ ] T025 [P] [US2] Write `tests/integration/test_cancel_flow.py`, parametrised over `SimulatedPac(sessions)` and `ScriptedPac`, covering every row of contracts/endpoints.md `/cancel` on real rows. Seed `SatReasonCancellation` 01–04 first; the integration template does not, and foreign keys are enforced. Use the `receipts` fixture (imported as in T016) for the invoice, its live receipt and its credit note: accepted sets `cancelled`, `cancellation_date`, `cancellation_reason` and `cancellation_substitution` and closes the request `accepted`; pending leaves it `cancellation_pending`; an invoice with a live receipt and a pending credit note returns `has_live_documents` naming both; 01 with a valid substitute is accepted; a document stamped by the current system (`seed_fiscal_document`, `completed=1`, no request rows) can be cancelled; the CSD password never appears in `caplog` across the file (SC-007)
- [ ] T026 [US2] Add SC-005 and FR-016 cases to `tests/integration/test_cancel_flow.py`: once an invoice is cancelled in the request, its units are refundable and a later receipt's previous balance excludes it; while pending, both still count it; a cancelled receipt's customer payment can back a new receipt; adding or confirming a receipt or credit note against an invoice with an open request fails with `invoice_not_payable` / `invoice_not_creditable`; email is allowed for a pending document and refused for a cancelled one
- [ ] T027 [P] [US2] Extend `tests/unit/test_invoice_service.py`: `_payable(..., open_cancellation=True)` and `_creditable(..., open_cancellation=True)` return False, and with `False` behave as today; `_open_cancellations(db, ids)` returns the ids with a `requested` or `pending` request, in one query

### Implementation for User Story 2

- [ ] T028 [US2] Implement `request_cancellation`, `query_status` and `answer_as_receiver(keys, answer)` in `SimulatedPac` (R6 table, the 24-hour default, one-shot `next_cancellation`). They find the row by UUID; an unknown UUID gets a row with the issuer and UUID only and no stamp date, so the default answers `pending`. T023 passes
- [ ] T029 [US2] Add `cancel(db, document_id, reason, substitution, provider, user)` to `app/services/fiscal_stamping.py` per research R8: transaction A runs the checks in order (reason looked up in `sat_reason_cancellation`; substitute found by `stamp_uuid`; live related documents through `fiscal_document_relation` with `document` of type 200 or 100), inserts the request as `requested` and commits; raise `no_valid_certificate` when `_certificate(db, issuer, local_now())` finds none; build `CancellationRequest` with that certificate's CSD; call the PAC; transaction B applies R8's answer table, sets `answered_at` / `resolved_at` and records R10's incidences. Put the cancelled-document write in a `_apply_cancelled(...)` helper shared with refresh. T024–T026 pass
- [ ] T030 [P] [US2] In `app/services/invoice_service.py`, give `_payable` (:1076) and `_creditable` (:1425) a keyword argument `open_cancellation: bool` and refuse when true, with their existing codes; add `async def _open_cancellations(db, invoice_ids) -> set[int]` (one query on `fiscal_document_cancellation`, `status IN ('requested','pending')`); call it once at each of the five call sites (:1134, :1246, :1363, :1451, :1615) and pass the flag. Nothing else in the file changes. T027 passes
- [ ] T031 [US2] Write the `/cancel` cases in `tests/api/test_fiscal_stamping.py` first (patched service): 200 cancelled and pending; 401, 403, 404; 409 `not_issued`, `cancellation_open`, `has_live_documents {documents}` and `not_cancellable`; 422 `unknown_cancellation_reason`, `substitution_required`, `substitution_invalid`, `no_valid_certificate`, `pac.rejected`, and a `substitution` that is not a UUID; 503; 504 `pac.timeout {state: 'cancellation_pending'}`. Confirm they fail, then add the request schema `{reason: str, substitution: UUID | None}` to `app/schemas/fiscal.py` and `POST /fiscal-documents/{id}/cancel` to `app/api/v1/endpoints/fiscal_documents.py`, with the same privilege and provider handling as `/stamp`

**Checkpoint**: US1 and US2 work on their own.

---

## Phase 5: User Story 3 – Resolve documents left in flight (P2)

**Goal**: refresh on demand and in a periodic job.

**Independent test**: leave one document stamping and one pending, run the job once, and both are resolved.

### Tests for User Story 3 (write first, confirm they fail)

- [ ] T032 [P] [US3] Add refresh cases to `tests/unit/test_fiscal_stamping.py`: stamping + stamp found → `_store_stamp`; not found at 4:59 → still stamping; at 5:01 → sealed with incidence `CFDI stamp not found, sealed again`; open request + SAT `cancelled` → cancelled, request `accepted` or `expired`; `valid` + `rejected` → issued, request `rejected`; `valid` + `none` → issued, request `failed`; `valid` + `pending` on a `requested` request → request `pending` with `answered_at` set and incidence `Cancellation pending the receiver`; `not_found` → unchanged; anything else → unchanged with `last_checked_at` set; other states → `nothing_to_refresh`; unavailable and timeout leave the state and raise
- [ ] T033 [P] [US3] Add refresh cases to `tests/integration/test_stamp_flow.py`: timeout-stamped → refresh → issued with the PAC's UUID and 1 stamp call in total (SC-004); timeout-not-stamped → refresh after setting `stamping_since` back 6 minutes → sealed, and the document can then be stamped; a stamp answer that arrives after the reset is still stored
- [ ] T034 [P] [US3] Add refresh cases to `tests/integration/test_cancel_flow.py`: pending → receiver accepts → refresh → cancelled; pending → receiver rejects → refresh → issued with the rejection in the history, and a new cancel is then accepted; pending → expire → refresh → cancelled, request `expired`; each refresh that changes the state records its R10 incidence (`CFDI cancelled`, `Cancellation rejected`, `Cancellation not received by SAT`)
- [ ] T035 [P] [US3] Write `tests/unit/test_resolve_fiscal_documents.py` (mocked session factory, `ScriptedPac`): `main()` exits 0 when the provider is `None`, refuses `simulated` + `production`; `run` continues past a failing document and returns 1, else 0. Then write `tests/integration/test_resolve_job.py` calling `run(provider, session_factory=sessions)`: one stamping and one pending document are resolved in one run (SC-003); a document whose PAC call raises does not stop the others and `run` returns 1; `main()` refuses to start with `simulated` + `production`; incidences are recorded as the system employee

### Implementation for User Story 3

- [ ] T036 [US3] Add `refresh(db, document_id, provider, user)` to `app/services/fiscal_stamping.py` per research R8 "Refresh", with `STAMP_LOOKUP_GRACE = timedelta(minutes=5)`, reusing `_store_stamp` and `_apply_cancelled`. T032–T034 pass
- [ ] T037 [US3] Write the `/refresh` cases in `tests/api/test_fiscal_stamping.py` first (patched service): 200; 409 `nothing_to_refresh`; 401, 403, 404; 503; 504. Confirm they fail, then add `POST /fiscal-documents/{id}/refresh` to `app/api/v1/endpoints/fiscal_documents.py` with the same privilege and provider handling as `/stamp`
- [ ] T038 [US3] Create `app/jobs/resolve_fiscal_documents.py` in the pattern of `app/jobs/send_scheduled_reports.py`: `async def run(provider, session_factory=AsyncSessionLocal) -> int` selects stamping documents and open requests oldest first and refreshes each in its own session as the system employee (`ensure_system_employee`), printing failures without PAC payloads and returning 1 if any failed; `main()` runs `check_pac_settings`, builds the provider with `get_pac_provider()` (exit 0 with a message when `None`), and exits with `run`'s result. T035 passes

**Checkpoint**: US1–US3 work; SC-003 and SC-004 covered.

---

## Phase 6: User Story 4 – See and filter fiscal states and history (P2)

**Goal**: the list filters by every state; the detail shows the stamp and every cancellation request.

**Independent test**: documents in every state; filter by each; read a document rejected once and then cancelled.

### Tests for User Story 4 (write first, confirm they fail)

- [ ] T039 [P] [US4] Write `tests/integration/test_fiscal_stamping_http.py` (the `client` fixture, real rows) with the state cases: with documents in all six states, `GET /fiscal-documents?status=X` returns exactly those for each X, with paging as today; a document rejected once and then cancelled returns both requests, newest first, with reason, substitute, requester, times and outcome; a document stamped by the current system reads `issued` with `stamp` filled, `simulated: false` and `cancellations: []`
- [ ] T040 [P] [US4] Extend `tests/unit/test_fiscal_document_service.py`: the detail builder maps cancellation rows to `cancellations` newest first and fills `cancellation_substitution`

### Implementation for User Story 4

- [ ] T041 [US4] Add `FiscalDocumentCancellationResponse` (`id, reason, substitution, status, pac_code, pac_message, requested_by, requested_at, answered_at, resolved_at, last_checked_at`), plus `cancellation_substitution` and `cancellations` on `FiscalDocumentResponse`, in `app/schemas/fiscal.py`
- [ ] T042 [US4] Load the document's cancellation rows newest first in the detail read in `app/services/fiscal_document_service.py` (one `selectinload` or one extra query) and fill the new fields. T039 and T040 pass

**Checkpoint**: US4 works.

---

## Phase 7: User Story 5 – Drive every PAC outcome in the simulator (P2)

**Goal**: every outcome reachable through the API alone.

**Independent test**: walk contracts/endpoints.md "Driving each scenario" through HTTP.

### Tests for User Story 5 (write first, confirm they fail)

- [ ] T043 [US5] Add the scenario walk to `tests/integration/test_fiscal_stamping_http.py`, with `pac_provider` set to `simulated` by `monkeypatch` and `get_pac_provider` overridden with `SimulatedPac(sessions)`: one test per row of "Driving each scenario" (10 rows), all through HTTP (SC-001). For "timeout, did not stamp", set `stamping_since` back instead of waiting

### Implementation for User Story 5

- [ ] T044 [US5] Add the simulator schemas (`PacSimulationResponse`; `PacSimulationUpdate` with the `stamp` and `cancellation` literals from endpoints.md plus `pac_code` and `pac_message`; `ReceiverAnswer`) to `app/schemas/fiscal.py`
- [ ] T045 [US5] Write `tests/api/test_pac_simulator.py` first (patched simulator): GET, PUT and receiver return 200; 401; 403 without Update on 23; 404 for an unknown document; 409 `not_sealed` for a draft; 409 `nothing_to_refresh` for a receiver answer with nothing pending; 422 for an unknown outcome; every route answers 404 when `settings.pac_provider` is `none` (monkeypatch). Confirm they fail, then create `app/api/v1/endpoints/pac_simulator.py` with `GET` and `PUT /pac-simulator/fiscal-documents/{id}` and `POST /pac-simulator/fiscal-documents/{id}/receiver`, each depending on `require_simulator()` (404 unless `settings.pac_provider == 'simulated'`, checked per request) and Update on 23. They map the id to issuer, series and folio and call `set_next`, `get_record` and `answer_as_receiver`. Include the router in `app/api/v1/router.py` with prefix `/pac-simulator` and tag `pac-simulator`. T043 passes

**Checkpoint**: every story works on its own.

---

## Phase 8: Polish & cross-cutting

- [ ] T046 [P] Add a "New MBE: stamping and cancellation (spec 039)" section to `docs/specs/10-fiscal-documents.md`: the states, the simulator, the 5-minute grace, the job and its cron, and `APP_ENV`/`PAC_PROVIDER` (documentation, exempt from tests)
- [ ] T047 [P] Add a CHANGELOG `[Unreleased]` `Added` entry in `CHANGELOG.md` for the three endpoints, the simulator routes, the job, the two settings and migration 034
- [ ] T048 Run the SC-007 scan from quickstart.md (`git grep -nIiE 'pac_(user|password)|FacturacionMexico(User|Password)' -- . ':!specs'`) and confirm it finds nothing
- [ ] T049 Check SC-006 and FR-031: only `app/services/pac/__init__.py` and `app/api/v1/endpoints/pac_simulator.py` import `app.services.pac.simulated` (`git grep -n 'pac.simulated' app/`); migration 034 only adds a column and creates tables; and `fiscal_stamping.py` and `app/services/pac/` write only `fiscal_document`, `fiscal_document_xml`, `fiscal_document_cancellation`, `pac_simulation` and `incidence`
- [ ] T050 Run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`, and `uv run ruff format --check` on only the files this branch adds or changes (`git diff --name-only origin/main -- '*.py'`); main already has unformatted files, which stay untouched (Surgical Changes). Fix anything failing
- [ ] T051 Re-read spec.md's acceptance scenarios and edge cases against the tests and mark any without one; add the missing test before closing
- [ ] T052 Ask the user to confirm applying migration 034 to mbe_dev and adding `APP_ENV=development` and `PAC_PROVIDER=simulated` to dev's `.env`. After confirmation, apply them, deploy the branch to dev :8765 (pre-authorized) and walk quickstart.md §2 steps 1–4. Clean up the created documents only with confirmation

### After merge (each needs confirmation; not part of this branch)

- Test :8766: `APP_ENV=staging`, `PAC_PROVIDER=simulated`, migration 034, restart, then quickstart.md §3, which measures SC-002 (20 concurrent stamps → 1 UUID).
- Cron on dev and test: `*/10 * * * * cd <instance> && uv run python -m app.jobs.resolve_fiscal_documents`.
- Open the mbe-ui handoff for mbe-roadmap#52, #54 and #56 from contracts/endpoints.md.

---

## Dependencies & execution order

- **Phase 1 → Phase 2 → stories.** T002 needs T001; T005 needs T004; T007 needs T003; T009 needs T008; T010 needs T002 and T009; T013 needs T008; T011 needs T006 and T007.
- **US1** needs Phase 2. It is the MVP.
- **US2** needs Phase 2, and its integration tests stamp through US1's T018–T019. T027/T030 depend only on Phase 2.
- **US3** needs US1 (`_store_stamp`) and US2 (`_apply_cancelled`, open requests).
- **US4** needs Phase 2 for the filter; its history test needs US2 and US3 to create the rows.
- **US5** needs the simulator from US1 (T018) and US2 (T028), and the routes from US1–US3 for the scenario walk.
- **Polish** last; T052 needs the user's confirmation.

```text
Setup ─▶ Foundational ─▶ US1 ─▶ US2 ─▶ US3
                          │      │      │
                          └──────┴──────┴─▶ US4, US5 ─▶ Polish
```

## Parallel opportunities

- Phase 1: T003 and T004 after T001–T002.
- Phase 2: T008 and T012 alongside T006–T007; T013 after T008.
- US1: tests T014–T017 together; T020 and T021 alongside T018–T019.
- US2: tests T023, T024, T025 and T027 together, then T026 (same file as T025); T030 alongside T028–T029.
- US3: tests T032–T035 together.
- US4/US5: T039 and T040 together; T043 after T039 (same file).
- Polish: T046 and T047 together.

### Example: US1

```text
Parallel: T014 tests/unit/test_pac_simulated.py
          T015 tests/unit/test_fiscal_stamping.py
          T016 tests/integration/test_stamp_flow.py
          T017 tests/unit/test_print_contexts.py, test_rendering.py
Then:     T018 SimulatedPac.stamp → T019 fiscal_stamping.stamp → T022 /stamp with its API tests
Parallel: T020 print context + banner, T021 response fields
```

## Implementation strategy

1. **MVP**: Phases 1–3. A sealed document can be stamped against the simulator, read and printed. That unblocks the stamping step of #54 and #56.
2. **+ US2**: cancellation, which unblocks #52.
3. **+ US3**: refresh and the job, so nothing stays stuck.
4. **+ US4, US5**: the history in reads and the simulator controls the UI testers need; then the handoff.
5. Each phase ends with `uv run pytest -q` and `ruff check` green, and a commit on the spec branch.
