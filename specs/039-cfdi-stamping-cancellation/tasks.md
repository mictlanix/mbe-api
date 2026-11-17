---
description: "Task list for spec 039: CFDI stamping and cancellation against a simulated PAC"
---

# Tasks: CFDI stamping and cancellation against a simulated PAC

**Input**: Design documents from `specs/039-cfdi-stamping-cancellation/`

**Prerequisites**: plan.md, spec.md, research.md (R1–R15), data-model.md, contracts/ (pac-provider.md, endpoints.md, error-codes.md), quickstart.md

**Tests**: Required by the constitution. In every phase the test tasks come first and must fail before the code that makes them pass. A route and its `tests/api` cases are one task.

**Test placement** (existing conventions):
- `tests/unit/`: mocked session, branch by branch. Exception: `tests/unit/test_pac_simulated.py` runs the simulator on a temporary SQLite engine, since its state is the table.
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

- [ ] T001 Write `tests/unit/test_pac_settings.py` first and confirm it fails: `Settings(_env_file=None)` defaults `app_env` to `production` and `pac_provider` to `none` (so a local `.env` can't change the result); `check_pac_settings` raises `RuntimeError` naming both settings for `simulated` + `production` and passes for the other five combinations; `get_pac_provider()` returns `None` for `none` and a `SimulatedPac` for `simulated`; `require_provider(None)` raises `pac.not_configured` (503); the app's `lifespan` raises for `simulated` + `production` (R4, R5)
- [ ] T002 Add `app_env: Literal['development', 'staging', 'production'] = 'production'` and `pac_provider: Literal['none', 'simulated'] = 'none'` to `Settings` in `app/core/config.py`, with comments in the file's style, and `check_pac_settings(settings)` raising `RuntimeError` naming both settings when `pac_provider == 'simulated'` and `app_env == 'production'`. The settings part of T001 passes
- [ ] T003 [P] First add `test_034_is_discovered_and_ordered_after_033` and `test_034_keys_open_requests_only` to `tests/unit/test_migrate.py`, beside 033's (:162–174): 034 follows 033 and its rollback is not discovered; the SQL contains `` AS (IF(`status` IN ('requested','pending'), `fiscal_document`, NULL)) VIRTUAL `` and a UNIQUE key on `open_document`. Confirm they fail. Then write `migrations/034_cfdi_stamping_cancellation.sql` and `migrations/034_cfdi_stamping_cancellation_rollback.sql` in migration 033's header and style, with new tables as `ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci` like migration 032 (the FK to `sat_reason_cancellation` needs the matching collation), exactly as data-model.md: `` ALTER TABLE `fiscal_document` `` with `` ADD COLUMN IF NOT EXISTS `stamping_since` DATETIME NULL `` and `` ADD COLUMN IF NOT EXISTS `cancelling_since` DATETIME NULL ``, one per line with backticked names as migration 033 writes them (the schema tests parse backticked names); table `fiscal_document_cancellation` (FKs to `fiscal_document`, `sat_reason_cancellation`, `employee`; virtual `open_document` with UNIQUE; index `(status, requested_at)`); table `pac_simulation` (UNIQUE `(issuer, batch, serial)`, UNIQUE `uuid`). Document both new `fiscal_document` columns and every column of both tables in `docs/data-dictionary.md`. Do NOT apply it to mbe_dev (T052 asks first)
- [ ] T004 [P] Add the 12 rows from contracts/error-codes.md to `specs/036-localizable-error-codes/contracts/error-codes.md` (alphabetical within each domain, a new `pac` section, `fiscal_document` added to `lock.busy`'s resources). Write params in 036's typed form, as contracts/error-codes.md gives them (`documents: list[str]`, `pac_code: str, pac_message: str`, `state: str` with the six values listed, `reason: str`, `substitution: str`), and add to 036's params rule (its contract line 5): "Exception: `pac.rejected`'s `pac_message` is the PAC's display-only text (spec 039 FR-014)." Widen two reused rows' Situation text: `fiscal_document.not_issued` to cover cancelling a document that is not issued, and `fiscal_document.no_valid_certificate` to cover a certificate valid now, on cancel. Run `uv run pytest tests/unit/test_contract_docs.py -q` and confirm it fails
- [ ] T005 Add the 12 codes to `ErrorCode` in `app/core/error_codes.py` (8 `fiscal_document.*`; `pac.not_configured` 503, `pac.rejected` 422, `pac.timeout` 504, `pac.unavailable` 503) with their HTTP status, as the existing members declare them. Update the pinned count in `test_the_parser_still_finds_the_error_codes` (`tests/unit/test_contract_docs.py:193`) from 268 to 280. `tests/unit/test_contract_docs.py` passes

**Checkpoint**: `uv run ruff check app/ migrations/ tests/` clean; contract-docs test passes.

---

## Phase 2: Foundational (blocks every story)

**Purpose**: models, the provider contract and dependency, the startup guard and the two new status values.

- [ ] T006 Extend `tests/unit/test_fiscal_document_service.py` first and confirm it fails. Add `stamping_since=None, cancelling_since=None` to the existing `test_document_status` namespace (:29), keeping its assertions. New cases: with no completion and `stamping_since` set → `stamping`; completed with `cancelling_since` set → `cancellation_pending`; completed with it NULL → `issued`; cancelled with `cancelling_since` set → `cancelled`; the list's `status` filter builds a column condition for `stamping` and for `cancellation_pending`, `issued` requires `cancelling_since IS NULL`, and `sealed` requires `stamping_since IS NULL`; `FiscalDocumentSummary` (`app/schemas/fiscal.py`) accepts `status='stamping'` and `'cancellation_pending'`; the detail builder's `stamp` is `None` without `stamp_uuid`, filled with one, and `simulated` is true only for `rfc_pac == 'SIM010101SIM'`
- [ ] T007 Add `stamping_since` and `cancelling_since` (`Mapped[datetime | None]`) to `FiscalDocument`, and models `FiscalDocumentCancellation` (leave the virtual `open_document` unmapped, as `sales_order_payment.live` from migration 033 is; data-model.md) and `PacSimulation` in `app/models/fiscal.py`. Add `'fiscal_document_cancellation'` and `'pac_simulation'` to `RESOURCE_NAMES` in `app/core/errors.py` (each mapped to its own name, as spec 038 did). Run `uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py tests/unit/test_error_codes.py::test_every_table_has_a_public_name -q` and make it pass
- [ ] T008 [P] Write `tests/unit/test_pac_provider.py` first and confirm it fails: `repr(Csd(...))` contains neither the key bytes nor the password (R14). Then create `app/services/pac/provider.py` exactly as contracts/pac-provider.md: dataclasses `Stamp`, `Csd`, `CancellationRequest`, `SatStatus`; `CancellationAnswer(StrEnum)`; exceptions `PacError`, `PacRejected(code, message)`, `PacUnavailable`, `PacTimeout`; `PacProvider(Protocol)` with async `stamp`, `find_stamp`, `request_cancellation`, `query_status`; `SIMULATED_PAC_RFC = 'SIM010101SIM'`. `Csd.__repr__` must not print the key or password, so the test passes
- [ ] T009 Create `app/services/pac/__init__.py` with `get_pac_provider() -> PacProvider | None` in the style of `get_mailer()` (`app/services/mail.py:89`): `None` for `none`, `SimulatedPac()` for `simulated`, importing the simulator inside the function. Add `require_provider(provider)` raising `DomainError(ErrorCode.PAC_NOT_CONFIGURED, 'No PAC provider is configured')` for `None`. Create a stub `app/services/pac/simulated.py` with the `SimulatedPac(session_factory=AsyncSessionLocal)` constructor only; US1 and US2 fill it
- [ ] T010 Call `check_pac_settings(settings)` at the start of `lifespan` in `app/main.py`. T001 passes
- [ ] T011 Update `document_status()` in `app/services/fiscal_document_service.py:27` to the order in data-model.md (cancelled → cancellation_pending → issued → stamping → sealed → draft), reading `cancelling_since` and `stamping_since` from the document; its signature and its two callers (:202, :281) do not change. Add `stamping` and `cancellation_pending` to `DocumentStatus` (:21). Restructure the list filter's `elif` branch (:237–246), which today derives every non-cancelled value from `completed` and XML presence, so each of the six values gets its own column condition, and narrow the existing ones: `issued` requires `cancelling_since IS NULL`, `sealed` requires `stamping_since IS NULL`. Leave `live()` (:96) unchanged. T006 passes except its schema case (T012) and its `stamp` cases (T021)
- [ ] T012 Add `stamping` and `cancellation_pending` to the status literal in `app/schemas/fiscal.py`. T006's schema case passes
- [ ] T013 Create `tests/fakes/__init__.py` and `tests/fakes/pac.py` with `ScriptedPac`: an in-memory `PacProvider` whose results or raised errors are queued per operation, which counts calls per operation, and whose TFD uses an RFC other than `SIM010101SIM` (R13). Needs T008's types

**Checkpoint**: `uv run pytest -q` passes, with the existing assertions unchanged, except the reds expected until later tasks: T006's `stamp` cases (until T021) and `tests/unit/test_error_codes.py::test_every_code_is_asserted_by_some_test` (until T032 asserts the last new code, `nothing_to_refresh`).

---

## Phase 3: User Story 1 – Stamp a sealed document (P1) 🎯 MVP

**Goal**: a sealed invoice, receipt or note becomes issued with its stamp, exactly once.

**Independent test**: seal one document of each type, stamp it, read it back: issued, UUID stored, the XML has the TFD, the PDF shows the stamp and the simulated banner, and reopening is refused.

### Tests for User Story 1 (write first, confirm they fail)

- [ ] T014 [P] [US1] Write `tests/unit/test_pac_simulated.py` (stamp part), with `SimulatedPac(session_factory=…)` on a temporary SQLite engine built like `tests/integration/conftest.py`'s: `stamp` returns a TFD 1.1 inside `cfdi:Complemento` that `cfdi.parse()` reads back (UUID upper case 36 chars, `SelloCFD` equal to the sealed `Sello`, 344-char `SelloSAT`, 20-digit `NoCertificadoSAT`, `RfcProvCertif` `SIM010101SIM`); a CFDI without `Serie` is keyed with `batch = ''`; `find_stamp` returns the stamp after `stamp` and `None` before; outcomes `rejected` (default `CFDI40999` / "Rechazo simulado", and a chosen code and message), `unavailable`, `timeout_stamped` (raises `PacTimeout`, then `find_stamp` returns the stamp), `timeout_not_stamped` (raises, then `find_stamp` returns `None`); each outcome applies once and then reverts to `stamped`; a second `stamp` of the same sealed XML (same `Sello`) returns the stored stamp, with the same UUID
- [ ] T015 [P] [US1] Write `tests/unit/test_fiscal_stamping.py` (stamp part), with a mocked session and `ScriptedPac`, in the style of `tests/unit/test_invoice_service.py`: the state checks in `/stamp`'s order (draft → `not_sealed`; issued, pending or cancelled → returned unchanged with 0 PAC calls; stamping → `fiscal_document.stamping` with 0 PAC calls; issue date over 72 h → `issue_date_out_of_range` with 0 PAC calls); stamped → stamp fields written, XML replaced, `completed` set, marker cleared; rejected and unavailable → marker cleared (only if it still equals transaction A's value) and `pac.rejected {pac_code, pac_message}` / `pac.unavailable`; timeout → marker kept and `pac.timeout {state: 'stamping'}`; transaction A writes `stamping_since` with `microsecond == 0` (R15 rule 11); each transaction commits before its lock and the lock statement carries `with_for_update()` and `populate_existing` (research R15 rule 1); the state is recomputed from the locked row, never from `document.status` (rule 3): a document whose route copy says `sealed` but whose locked row has `stamping_since` set gets `fiscal_document.stamping`; transaction B returns an already-completed document as it is, and on a changed XML records `CFDI stamp {uuid} not stored: the document changed` and raises `pac.timeout` (rule 5). In `tests/unit/test_invoice_service.py`, `reopen` locks the row with `populate_existing` and refuses (`not_sealed`) a document whose locked row has `stamping_since` set even though its route copy says `sealed` (rule 8)
- [ ] T016 [P] [US1] Write `tests/integration/test_stamp_flow.py`, parametrised over `SimulatedPac(sessions)` and `ScriptedPac` (SC-006), sealing one document of each type with the `invoicing`, `receipts` and `notes` fixtures. They are module-local, so import them from `tests/integration/test_invoice_flow.py`, `test_receipt_flow.py` and `test_credit_note_flow.py` with `# noqa: F401`, as `tests/integration/test_refund_ticket.py:11` does. `invoicing`, `receipts` and `notes` seed the same certificate and issuer, so use each in its own test: stamp → issued with `stamp_uuid`, `stamped`, `authority_digital_seal`, `authority_certificate_number` and `rfc_pac` set, and `FiscalDocumentXml.data` the stamped XML; the detail read returns the `stamp` object and `stamping_since: null`; a repeat on issued returns it unchanged with no new PAC call; after a timeout, a second stamp returns `fiscal_document.stamping` and the PAC stamped once (`ScriptedPac`'s stamp count, or one `pac_simulation` row with a UUID for `SimulatedPac`) (the SC-002 rule; the concurrent run is quickstart §3); reopen is refused for issued and for stamping, and editing a line of an issued document is refused (`_require_draft`); incidence `CFDI stamped {uuid}` is recorded
- [ ] T017 [P] [US1] Extend `tests/unit/test_print_contexts.py`: the print context's `is_simulated` is true when the stamped XML's TFD has `RfcProvCertif="SIM010101SIM"` and false otherwise (the existing stubs carry no `rfc_pac`). Extend `tests/integration/test_print_render.py`, next to the CANCELADO cases (:742–753): the rendered fiscal document contains "DOCUMENTO SIMULADO — SIN VALIDEZ FISCAL" only when simulated (FR-009)

### Implementation for User Story 1

- [ ] T018 [US1] Implement `stamp`, `find_stamp`, `set_next(...)` and `get_record(...)` in `SimulatedPac` in `app/services/pac/simulated.py` per research R6: read issuer RFC, `Serie` (`''` when absent), `Folio` and `Sello` from the sealed XML; keep state in `pac_simulation` through `self.session_factory()`, committing at once; build the TFD with lxml and insert it into `cfdi:Complemento` (created if absent); apply and clear `next_stamp`; log one INFO line per `stamp` call, `pac simulator: stamp call {issuer} {batch}{serial}`, with no XML (quickstart §3 counts these lines for SC-002); a row that already holds a stamp for the same `Sello` returns it without a new UUID; `timeout_stamped` stores the stamp, then raises `PacTimeout`; `timeout_not_stamped` raises without storing. T014 passes
- [ ] T019 [US1] Create `app/services/fiscal_stamping.py` with `stamp(db, document_id, provider, employee_id: int)` per research R3 and R15: transaction A begins locked (rule 1: commit, then `with_for_update()` + `populate_existing` under `busy_as_conflict(db, 'The fiscal document is busy, retry', resource='fiscal_document')`), recomputes the state from the locked row and a plain read of its XML (rule 3), runs the checks in `/stamp`'s order and the 72-hour check reused from confirm, sets `stamping_since = local_now().replace(microsecond=0)` and commits; `provider.stamp(sealed_xml)` with no transaction open; transaction B in a reusable `_store_stamp(..., reason)` (begin locked; read the XML with `get_xml`, not a cached object (rule 11); rule 5's branches; store: write the stamp fields, replace the XML, set `completed`, clear the marker, record the incidence with `reason`: `CFDI stamped {uuid}` here, `CFDI stamp found {uuid}` from refresh). Map the errors as T015 asserts. Commit the cleared marker first and raise `pac.*` after leaving `busy_as_conflict` (rule 4). Never log the XML or the PAC's raw error. In `app/services/invoice_service.py`, make `reopen` (:983) lock the document with `populate_existing` and require `completed` false, `cancelled` false and `stamping_since` NULL on the locked row before deleting the XML (rule 8); nothing else in `reopen` changes. T015 passes, and T016 except its detail-read assertion (T021)
- [ ] T020 [P] [US1] Add `is_simulated` to the print context in `app/services/print_contexts.py` (≈:821), derived from the parsed TFD like every other stamp value there (`timbre.get('RfcProvCertif') == SIMULATED_PAC_RFC`, beside :933), so the existing stubs without `rfc_pac` keep working and the "DOCUMENTO SIMULADO — SIN VALIDEZ FISCAL" banner to `app/rendering/templates/fiscal_document.html`, styled like the CANCELADO stamp (:17). T017 passes
- [ ] T021 [US1] Add `FiscalDocumentStamp` and the field `stamp` (`uuid`, `stamped`, `sat_certificate`, `provider_rfc`, `simulated`; `null` until `stamp_uuid` is set) and `stamping_since` to `FiscalDocumentResponse`, both defaulting (`stamp: FiscalDocumentStamp | None = None`, `stamping_since: LocalDateTime | None = None`) as `relations = []` does, so the existing `SimpleNamespace` rows in tests/api keep validating in `app/schemas/fiscal.py`, and fill them in the detail builder in `app/services/fiscal_document_service.py`. T006's `stamp` cases and T016 pass in full
- [ ] T022 [US1] Write the `/stamp` cases in `tests/api/test_fiscal_stamping.py` first (patched service): 200; 401; 403 without Update on 23; 404; 409 `not_sealed`, `stamping` and `lock.busy`; 422 `issue_date_out_of_range` and `pac.rejected`; 503 `pac.not_configured` (provider overridden to `None`) and `pac.unavailable`; 504 `pac.timeout`. Confirm they fail, then add `POST /fiscal-documents/{id}/stamp` to `app/api/v1/endpoints/fiscal_documents.py` with that privilege, `provider = Depends(get_pac_provider)` and `require_provider`, returning `FiscalDocumentResponse`

**Checkpoint**: US1 works on its own; `uv run pytest -q` passes except `test_every_code_is_asserted_by_some_test`, which turns green at T032.

---

## Phase 4: User Story 2 – Cancel a stamped document (P1)

**Goal**: an issued document is cancelled at once or goes cancellation pending; cancelling frees spec 028's holds.

**Independent test**: cancel one document that is accepted at once and one that goes pending; check the state and the history.

### Tests for User Story 2 (write first, confirm they fail)

- [ ] T023 [P] [US2] Add cancellation cases to `tests/unit/test_pac_simulated.py`: the default rule (stamp under 24 h → `ACCEPTED`, at or over 24 h → `PENDING`, by setting `stamped_at` back); chosen outcomes `accepted`, `pending`, `not_cancellable`, `rejected`, `unavailable`, and `timeout` (records cancellation `pending`, raises `PacTimeout`); `query_status` reflects each; `answer_as_receiver` with `accept` → cancelled/accepted, `reject` → valid/rejected, `expire` → cancelled/expired, and with nothing pending raises; an unknown UUID (a document the current system stamped) gets a row and the default answers `PENDING`; `set_next` with keys carrying an unknown `stamp_uuid`, then `request_cancellation` for that UUID, follows the chosen outcome
- [ ] T024 [P] [US2] Add cancel cases to `tests/unit/test_fiscal_stamping.py`: transaction A begins locked (R15 rule 1); the six refusals in R8's order, each before any PAC call and before the request row is written (not issued, for a recomputed state of draft, sealed, stamping or cancelled; a `cancellation_pending` document gets `cancellation_open`, not `not_issued`; open request, i.e. `cancelling_since` set; unknown reason; 01 without a substitute; invalid substitute; live related documents listed as `batch + serial`), then `no_valid_certificate` when the issuer has no certificate valid now; the substitute must be issued or pending with the same issuer and recipient (unknown, unstamped, cancelled, other issuer, other recipient each refused); with reason 02–04 a substitute sent is ignored (not checked, not sent, not stored); cancelling a credit note runs the paid-flag update (`order_balances` before and after, `customer_payment_service.reopen` per order), and cancelling an invoice or a receipt does not; in the credit-note branch the transaction after the PAC call locks note, invoice and orders by primary key, with the ids read before the PAC call, before any plain read (R15 rule 10); transaction B's `not_cancellable`, `rejected` and `failed` record `Cancellation refused: not cancellable`, `Cancellation refused by the PAC ({pac_code})` and `Cancellation failed: PAC unavailable` (R10); a successful transaction A inserts the request, sets `cancelling_since` and records `Cancellation requested ({reason})`; the transaction B table (accepted, pending, not_cancellable, rejected, unavailable, timeout → request status, document state, `cancelling_since` cleared whenever the request closes, raised code, and `Cancellation pending the receiver` or `CFDI cancelled` recorded); transaction B applies the answer only if the request is still `requested`, read from the row with a column select, not from the object transaction A inserted: with the cached request saying `requested` but the row saying `accepted`, B leaves it alone (rules 7 and 11)
- [ ] T025 [P] [US2] Write `tests/integration/test_cancel_flow.py`, parametrised over `SimulatedPac(sessions)` and `ScriptedPac`, covering every row of contracts/endpoints.md `/cancel` on real rows except `lock.busy` (SQLite has no row locks; T031 covers it). Add `seed_cancellation_reasons(db)` to `tests/integration/seed.py`, inserting `SatReasonCancellation` 01–04 (the integration template does not, and foreign keys are enforced), and call it first. Use the `receipts` fixture (imported as in T016) for the invoice, its live receipt and its credit note, and set Product 1's SAT key to `01010101` as the `notes` fixture does (credit-note confirm requires it): accepted sets `cancelled`, `cancellation_date`, `cancellation_reason` and `cancellation_substitution` and closes the request `accepted`; pending leaves it `cancellation_pending`; an invoice with a live receipt and a pending credit note returns `has_live_documents` naming both; 01 with a valid substitute is accepted; a document stamped by the current system (`seed_fiscal_document`, `completed=1`, no request rows, plus a `TaxpayerCertificate` for its issuer valid now, which `seed_fiscal_document` does not create) can be cancelled; the CSD password never appears in `caplog` across the file (SC-007)
- [ ] T026 [US2] Add SC-005, FR-016 and FR-020 cases to `tests/integration/test_cancel_flow.py`: once an invoice is cancelled in the request, its units are refundable and a later receipt's previous balance excludes it; while pending, both still count it, it still limits credit notes, and a pending receipt still holds its customer payment; a cancelled receipt's customer payment can back a new receipt; adding or confirming a receipt or credit note against an invoice with an open request fails with `invoice_not_payable` / `invoice_not_creditable`; email is allowed for a pending document and refused for a cancelled one; an order settled by a credit note stays `paid` while the note's cancellation is pending and is flagged unpaid once the note is cancelled (spec 033 FR-011); `GET /xml` and `GET /pdf` succeed for a simulator-stamped document when issued, pending and cancelled, and the PDF text contains the UUID
- [ ] T027 [P] [US2] Extend `tests/unit/test_invoice_service.py`: `_payable` and `_creditable` return False for an invoice with `cancelling_since` set and behave as today when it is NULL; the two confirm-time invoice lock statements (receipt :1352–1355, credit note :1609–1612) carry `populate_existing`

### Implementation for User Story 2

- [ ] T028 [US2] Implement `request_cancellation`, `query_status` and `answer_as_receiver(keys, answer)` in `SimulatedPac` (R6 table, the 24-hour default, one-shot `next_cancellation`). They find the row by UUID; a row created from `keys` stores `stamp_uuid` when the document has one (R6); `set_next`, `get_record` and `answer_as_receiver` take `keys = (stamp_uuid | None, issuer, batch or '', serial)` and find the row by UUID first, then by issuer, series and folio, creating it when missing (R6). An unknown UUID gets a row with the issuer and UUID only and no stamp date, so the default answers `pending`. T023 passes
- [ ] T029 [US2] Add `cancel(db, document_id, reason, substitution, provider, employee_id: int)` to `app/services/fiscal_stamping.py` per research R8 and R15: transaction A begins locked (rule 1), then with plain reads runs the checks in R8's order (reason looked up in `sat_reason_cancellation`; substitute found with `func.upper(FiscalDocument.stamp_uuid) == str(substitution).upper()`, as the list search does, and stored upper case; live related documents through `fiscal_document_relation` with `document` of type 200 or 100, using `live()`), then `no_valid_certificate` when `_certificate(db, issuer, local_now())` finds none (keeping the CSD in a local), and only then inserts the request as `requested`, sets `cancelling_since`, records `Cancellation requested ({reason})` and commits; build `CancellationRequest` with that CSD; call the PAC; transaction B begins locked, reads the request status and the XML with column selects (rule 11), applies R8's answer table only if the request is still `requested` (rule 7), clears `cancelling_since` whenever the request closes, sets `answered_at` / `resolved_at` and records R10's other incidences; commit before raising `not_cancellable` or `pac.*` outside `busy_as_conflict` (rule 4). Put the cancelled-document write in a `_apply_cancelled(...)` helper shared with refresh; for a credit note it also runs reopen's paid-flag update (`sales_order_service.order_balances` before and after a flush, `customer_payment_service.reopen` per order; R8), computing `before` while the note is still live. For a credit note, transaction A also reads the note's invoice id and its order ids, and transaction B commits, then locks note → invoice → orders by primary key with `populate_existing` before any plain read, including the request-status read (R15 rule 10); do not reuse `_credited_orders`, which reads before it locks. T024 and T025 pass; T026 passes after T030
- [ ] T030 [US2] In `app/services/invoice_service.py`, add `and invoice.cancelling_since is None` to `_payable` (:1076) and `_creditable` (:1425), and add `.execution_options(populate_existing=True)` to the receipt-confirm and credit-note-confirm invoice locks (:1352–1355, :1609–1612) so the flag comes from the locked row (R8). Nothing else in the file changes. T027 passes, and T026 passes in full
- [ ] T031 [US2] Write the `/cancel` cases in `tests/api/test_fiscal_stamping.py` first (patched service): 200 cancelled and pending; 401, 403, 404; 409 `not_issued`, `cancellation_open`, `has_live_documents {documents}`, `not_cancellable` and `lock.busy {resource: 'fiscal_document'}`; 422 `unknown_cancellation_reason`, `substitution_required`, `substitution_invalid`, `no_valid_certificate`, `pac.rejected`, and a `substitution` that is not a UUID; 503; 504 `pac.timeout {state: 'cancellation_pending'}`. Confirm they fail, then add the request schema `{reason: str, substitution: UUID | None}` to `app/schemas/fiscal.py` and `POST /fiscal-documents/{id}/cancel` to `app/api/v1/endpoints/fiscal_documents.py`, with the same privilege and provider handling as `/stamp`

**Checkpoint**: US1 and US2 work on their own.

---

## Phase 5: User Story 3 – Resolve documents left in flight (P2)

**Goal**: refresh on demand and in a periodic job.

**Independent test**: leave one document stamping and one pending, run the job once, and both are resolved.

### Tests for User Story 3 (write first, confirm they fail)

- [ ] T032 [P] [US3] Add refresh cases to `tests/unit/test_fiscal_stamping.py`: refresh runs transaction 1 (begin locked, read, commit), the PAC call with no transaction open, then transaction 2 (begin locked) (R8, R15 rule 1); for a credit note, transaction 2 locks note → invoice → orders before any plain read (rule 10); stamping + stamp found → `_store_stamp` with incidence `CFDI stamp found {uuid}`; not found at 4:59 → still stamping; at 5:01 → sealed with incidence `CFDI stamp not found, sealed again`; open request + SAT `cancelled` → cancelled, request `accepted` or `expired`, `cancelling_since` cleared; `valid` + `rejected` → issued, request `rejected`, `cancelling_since` cleared; `valid` + `none` → issued, request `failed`, `cancelling_since` cleared; `valid` + `none` on a `requested` request under 5 minutes old → unchanged (rule 7); a different open request (a new cancel after the kept one closed) with the same status as the one transaction 1 kept → unchanged (rule 6: by id); `valid` + `pending` on a `requested` request → request `pending` with `answered_at` set and incidence `Cancellation pending the receiver`; `not_found` → unchanged; every `query_status` answer sets `last_checked_at`; a `stamping_since` that changed since `find_stamp` was called, or a request whose status changed since `query_status`, is left alone (rule 6), including when transaction 1's cached request object still shows the old status (rule 11); anything else → unchanged with `last_checked_at` set; other states → `nothing_to_refresh`; unavailable and timeout leave the state and raise
- [ ] T033 [P] [US3] Add refresh cases to `tests/integration/test_stamp_flow.py`: timeout-stamped → refresh → issued with the PAC's UUID and 1 stamp call in total (SC-004); timeout-not-stamped → refresh after setting `stamping_since` back 6 minutes → sealed, and the document can then be stamped; a stamp answer that arrives after the reset is still stored (call `fiscal_stamping._store_stamp` directly after the reset, with the stamp `ScriptedPac` would have returned)
- [ ] T034 [P] [US3] Add refresh cases to `tests/integration/test_cancel_flow.py`: pending → receiver accepts → refresh → cancelled; pending → receiver rejects → refresh → issued with the rejection in the history, and a new cancel is then accepted; pending → expire → refresh → cancelled, request `expired`; each refresh that changes the state records its R10 incidence (`CFDI cancelled`, `Cancellation rejected`, `Cancellation not received by SAT`)
- [ ] T035 [P] [US3] Write `tests/unit/test_resolve_fiscal_documents.py` (mocked session factory, `ScriptedPac`): `main()` exits 0 when the provider is `None`, refuses `simulated` + `production`; `run` continues past a failing document and returns 1, else 0. Then write `tests/integration/test_resolve_job.py` (calling `seed_cancellation_reasons` first) calling `run(provider, session_factory=sessions)`: one stamping and one pending document are resolved in one run (SC-003); a document whose PAC call raises does not stop the others and `run` returns 1; `main()` refuses to start with `simulated` + `production`; incidences are recorded as the system employee

### Implementation for User Story 3

- [ ] T036 [US3] Add `refresh(db, document_id, provider, employee_id: int)` to `app/services/fiscal_stamping.py` per research R8 "Refresh" (transaction 1, the PAC call with no transaction open, transaction 2) and R15 rules 1, 3, 4, 6, 7, 10 and 11 (transaction 1 keeps the open request's id; transaction 2 re-reads that id), with `STAMP_LOOKUP_GRACE = timedelta(minutes=5)`, reusing `_store_stamp` and `_apply_cancelled`. T032–T034 pass
- [ ] T037 [US3] Write the `/refresh` cases in `tests/api/test_fiscal_stamping.py` first (patched service): 200; 409 `nothing_to_refresh` and `lock.busy {resource: 'fiscal_document'}`; 401, 403, 404; 503 `pac.not_configured` and `pac.unavailable`; 504 `pac.timeout {state}`. Confirm they fail, then add `POST /fiscal-documents/{id}/refresh` to `app/api/v1/endpoints/fiscal_documents.py` with the same privilege and provider handling as `/stamp`
- [ ] T038 [US3] Create `app/jobs/resolve_fiscal_documents.py` in the pattern of `app/jobs/send_scheduled_reports.py`: `async def run(provider, session_factory=AsyncSessionLocal) -> int` selects stamping documents and open requests oldest first and refreshes each in its own session, acting as `employee_service.ensure_system_employee(db)`'s `employee_id` (`refresh`, like `stamp` and `cancel`, takes the actor as an employee id; the routes pass `CurrentUser.employee_id`), printing failures without PAC payloads and returning 1 if any failed (a `nothing_to_refresh` because an overlapping run already resolved the document counts as resolved, not failed); `main()` runs `check_pac_settings`, builds the provider with `get_pac_provider()` (exit 0 with a message when `None`), and exits with `run`'s result. T035 passes

**Checkpoint**: US1–US3 work; SC-003 and SC-004 covered.

---

## Phase 6: User Story 4 – See and filter fiscal states and history (P2)

**Goal**: the list filters by every state; the detail shows the stamp and every cancellation request.

**Independent test**: documents in every state; filter by each; read a document rejected once and then cancelled.

### Tests for User Story 4 (write first, confirm they fail)

- [ ] T039 [P] [US4] Write `tests/integration/test_fiscal_stamping_http.py` (the `client` fixture, real rows; call `seed_cancellation_reasons` first) with the state cases: with documents in all six states, `GET /fiscal-documents?status=X` returns exactly those for each X, with paging as today; the `/cancel` response itself lists the request it just created (FR-027); a document rejected once and then cancelled returns both requests, newest first, with reason, substitute, requester, times and outcome; a document stamped by the current system reads `issued` with `stamp` filled, `simulated: false` and `cancellations: []`
- [ ] T040 [P] [US4] Extend `tests/unit/test_fiscal_document_service.py`: the detail builder maps cancellation rows to `cancellations` newest first, and its query sets `populate_existing` and fills `cancellation_substitution`

### Implementation for User Story 4

- [ ] T041 [US4] Add `FiscalDocumentCancellationResponse` (`id, reason, substitution, status, pac_code, pac_message, requested_by, requested_at, answered_at, resolved_at, last_checked_at`), plus `cancellation_substitution: str | None = None` and `cancellations: list[FiscalDocumentCancellationResponse] = []` on `FiscalDocumentResponse` (defaults, as `relations = []`, for the existing test rows), in `app/schemas/fiscal.py`
- [ ] T042 [US4] Load the document's cancellation rows newest first in the detail read in `app/services/fiscal_document_service.py` by assigning `document.cancellations` from one fresh query in `get_document`, as `lines` and `relations` are assigned there (fiscal_document_service.py:192–201), with no mapped relationship or `selectinload`, and with `.execution_options(populate_existing=True)` so request objects an earlier transaction loaded show their current row (R15 rule 11), so a response built right after `/cancel` lists the new request and fill the new fields. T039 and T040 pass

**Checkpoint**: US4 works.

---

## Phase 7: User Story 5 – Drive every PAC outcome in the simulator (P2)

**Goal**: every outcome reachable through the API alone.

**Independent test**: walk contracts/endpoints.md "Driving each scenario" through HTTP.

### Tests for User Story 5 (write first, confirm they fail)

- [ ] T043 [US5] Add the scenario walk to `tests/integration/test_fiscal_stamping_http.py`, with `pac_provider` set to `simulated` by `monkeypatch` and `get_pac_provider` overridden with `SimulatedPac(sessions)`: one test per row of "Driving each scenario" (13 rows), all through HTTP (SC-001). For "timeout, did not stamp", set `stamping_since` back instead of waiting

### Implementation for User Story 5

- [ ] T044 [US5] Extend `tests/unit/test_fiscal_schemas.py` first and confirm it fails: `PacSimulationUpdate` rejects an unknown `stamp` or `cancellation` outcome, a `pac_code` over 20 and a `pac_message` over 500 characters; `ReceiverAnswer` accepts only `accept`, `reject` and `expire`. Then add `PacSimulationResponse`, `PacSimulationUpdate` (the `stamp` and `cancellation` literals from endpoints.md, `pac_code` `max_length=20`, `pac_message` `max_length=500`, matching the `pac_simulation` columns) and `ReceiverAnswer` to `app/schemas/fiscal.py`
- [ ] T045 [US5] Write `tests/api/test_pac_simulator.py` first (patched simulator): GET, PUT and receiver return 200; 401; 403 without Update on 23; 404 for an unknown document; 409 `not_sealed` for a draft; 409 `nothing_to_refresh` for a receiver answer with nothing pending; 422 for an invalid body; every route answers 404 `request.route_not_found` when `settings.pac_provider` is `none` (monkeypatch), even without a token. Confirm they fail. Then create `app/api/v1/endpoints/pac_simulator.py` with `GET` and `PUT /pac-simulator/fiscal-documents/{id}` and `POST /pac-simulator/fiscal-documents/{id}/receiver`. Each depends on `require_simulator()` (404 unless `settings.pac_provider == 'simulated'`, checked per request, as research R7 and endpoints.md say) and Update on 23, and takes its `SimulatedPac` from `Depends(get_pac_provider)`, so T043's override reaches it. The routes build `keys` from the document (`stamp_uuid`, issuer, `batch or ''`, serial; R6) and call `set_next`, `get_record` and `answer_as_receiver`. Include the router in `app/api/v1/router.py` with prefix `/pac-simulator` and tag `pac-simulator`. T043 passes

**Checkpoint**: every story works on its own.

---

## Phase 8: Polish & cross-cutting

- [ ] T046 [P] Add a "New MBE: stamping and cancellation (spec 039)" section to `docs/specs/10-fiscal-documents.md`: the states, the simulator, the 5-minute grace, the job and its cron, and `APP_ENV`/`PAC_PROVIDER` (documentation, exempt from tests)
- [ ] T047 [P] Add a CHANGELOG `[Unreleased]` `Added` entry in `CHANGELOG.md` for the three endpoints, the simulator routes, the job, the two settings and migration 034, and a `Changed` entry: cancelling a credit note updates the paid flag of its invoice's orders (spec 033 FR-011); `status=issued` excludes documents with a cancellation in process, receipts and credit notes are refused on an invoice whose cancellation is in process, and reopen locks the document
- [ ] T048 Run the SC-007 scan from quickstart.md (`git grep -nIiE 'pac_(user|password)|FacturacionMexico(User|Password)' -- . ':!specs'`) and confirm it finds nothing
- [ ] T049 Check SC-006 and FR-031: only `app/services/pac/__init__.py` and `app/api/v1/endpoints/pac_simulator.py` import `app.services.pac.simulated` (`git grep -n 'pac.simulated' app/`); migration 034 only adds two columns and creates tables; and `fiscal_stamping.py` and `app/services/pac/` write only `fiscal_document`, `fiscal_document_xml`, `fiscal_document_cancellation`, `pac_simulation`, `incidence` and, through `customer_payment_service.reopen` when a credit note is cancelled, `sales_order.paid` (FR-022, FR-031)
- [ ] T050 Run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`, and `uv run ruff format --check` on only the files this branch adds or changes (`git diff --name-only origin/main -- '*.py'`); main already has unformatted files, which stay untouched (Surgical Changes). Fix anything failing
- [ ] T051 Re-read spec.md's acceptance scenarios and edge cases against the tests and mark any without one; add the missing test before closing
- [ ] T052 Ask the user to confirm the after-merge SC-002 measurement on test :8766 (quickstart.md §3) and record it as pending in this file; it cannot run on this branch because SQLite has no row locks. Then ask the user to confirm applying migration 034 to mbe_dev and adding `APP_ENV=development` and `PAC_PROVIDER=simulated` to dev's `.env`. After confirmation, apply them, deploy the branch to dev :8765 (pre-authorized) and walk quickstart.md §2 steps 1–4. Clean up the created documents only with confirmation

### After merge (each needs confirmation; not part of this branch)

- Test :8766: `APP_ENV=staging`, `PAC_PROVIDER=simulated`, migration 034, restart, then quickstart.md §3, which measures SC-002 (20 concurrent stamps → 1 UUID).
- Cron on dev and test: `*/10 * * * * cd <instance> && uv run python -m app.jobs.resolve_fiscal_documents`.
- Open the mbe-ui handoff for mbe-roadmap#52, #54 and #56 from contracts/endpoints.md.

---

## Dependencies & execution order

- **Phase 1 → Phase 2 → stories.** T002 needs T001; T005 needs T004; T007 needs T003; T009 needs T008; T010 needs T002 and T009; T013 needs T008; T011 needs T006 and T007.
- **US1** needs Phase 2. It is the MVP.
- **US2** needs Phase 2, and its integration tests stamp through US1's T018–T019. T027 depends only on Phase 2; T030 follows T029, and T026 passes once both are done.
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
- Phase 2: T008 alongside T006–T007; T012 after T006; T013 after T008.
- US1: tests T014–T017 together; T020 alongside T018–T019; T021 after T019.
- US2: tests T023, T024, T025 and T027 together, then T026 (same file as T025); T030 after T029.
- US3: tests T032–T035 together.
- US4/US5: T039 and T040 together; T043 after T039 (same file).
- Polish: T046 and T047 together.

### Example: US1

```text
Parallel: T014 tests/unit/test_pac_simulated.py
          T015 tests/unit/test_fiscal_stamping.py
          T016 tests/integration/test_stamp_flow.py
          T017 tests/unit/test_print_contexts.py, tests/integration/test_print_render.py
Then:     T018 SimulatedPac.stamp → T019 fiscal_stamping.stamp (+ reopen lock) → T021 response fields → T022 /stamp with its API tests
Parallel: T020 print context + banner
```

## Implementation strategy

1. **MVP**: Phases 1–3. A sealed document can be stamped against the simulator, read and printed. That unblocks the stamping step of #54 and #56.
2. **+ US2**: cancellation, which unblocks #52.
3. **+ US3**: refresh and the job, so nothing stays stuck.
4. **+ US4, US5**: the history in reads and the simulator controls the UI testers need; then the handoff.
5. Each phase ends with `uv run pytest -q` and `ruff check` green, and a commit on the spec branch.
