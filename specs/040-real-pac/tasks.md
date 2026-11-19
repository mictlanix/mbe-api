# Tasks: Connect stamping and cancellation to the real PAC

**Input**: Design documents from `specs/040-real-pac/`: plan.md, spec.md, research.md (R1–R13), data-model.md, contracts/pac-provider.md, contracts/configuration.md, contracts/simulator-controls.md and quickstart.md.

**Tests**: Required. FR-028 and FR-029 ask for them, and the constitution requires tests first for every change.

**Organization**: Tasks are grouped by user story (US1–US5 in spec.md), so each story can be built and tested on its own.

**Suite**: `DATABASE_URL='sqlite+aiosqlite:///:memory:' JWT_SECRET_KEY=test-secret-test-secret-test-secret-0123 BUSINESS_TIMEZONE=America/Mexico_City uv run pytest -q`.

**Lint**: `uv run ruff check app/ migrations/ tests/`. Run `ruff format` only on files you touch whose origin version was already formatted.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: The user story the task serves (US1–US5)

---

## Phase 1: Setup (Shared Infrastructure)

**Purpose**: Bring in the library and prove the new lock is safe.

- [ ] T001 Add `mictlanix-cfdi` to `dependencies` in `pyproject.toml`, with `[tool.uv.sources] mictlanix-cfdi = { git = "ssh://git@github.com/mictlanix/cfdi.git", rev = "4b41821b7947adc71a32a535ed5bb87dbd325b77" }`. Run `uv lock` and check:
  - `uv.lock` pins that commit;
  - `cryptography` resolved to ≥50.0.2;
  - `lxml` stayed on 6.1.3.
  (research R1)
- [ ] T002 Run the full offline suite and ruff on the new lock, before any other change. It is the gate for `cryptography` 49 → 50: python-jose, CSD sealing and the print tests must all pass. If anything fails, stop and report; don't patch around it. (research R1)
- [ ] T003 [P] Add a subsection "CFDI library (private)" to `README.md` under `## Setup`. It says:
  - the library comes from mictlanix/cfdi over SSH;
  - developers need GitHub SSH access;
  - each server needs its user's public key as a **read-only deploy key** on mictlanix/cfdi (xolotl: `mictlanix`'s `~/.ssh/id_ed25519.pub`), checked with `ssh -T git@github.com`;
  - to move the pin, change `rev` and run `uv lock`.
  (FR-002)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The adapter, the contract attribute and the settings that every story uses.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T004 In `app/services/pac/provider.py`:
  - add `resolves_by_resubmitting: bool` to the `PacProvider` protocol, with the docstring from contracts/pac-provider.md §1;
  - replace `SIMULATED_PAC_RFC = 'SIM010101SIM'` with a re-export of `mictlanix.cfdi.pac.SIMULATED_PAC_RFC`;
  - update the module docstring: the library is the one implementation, through `adapter.py`.

  In `tests/fakes/pac.py`, give `ScriptedPac` an instance attribute `resolves_by_resubmitting = False` that tests may set to True, and update its docstring's mention of `SIM010101SIM`. (research R2, R4)
- [ ] T005 Add the settings from contracts/configuration.md to `app/core/config.py`:
  - widen `pac_provider` to `Literal['none', 'simulated', 'multifacturas']`;
  - add `multifacturas_user: str | None = None`, `multifacturas_password: SecretStr | None = None`, `multifacturas_production: bool = False`, `multifacturas_url: str = 'https://ws.multifacturas.com'`, `multifacturas_connect_timeout: float = 10.0` and `multifacturas_read_timeout: float = 60.0`;
  - update the comment above `app_env`, whose "step 3 adds the real provider" is now done.
  (research R6)
- [ ] T006 [P] Document the new variables in `.env.example` next to `PAC_PROVIDER`. Use placeholders only, and comment that `MULTIFACTURAS_PRODUCTION` must be `true` exactly when `APP_ENV=production`. (contracts/configuration.md)
- [ ] T007 [P] Create `tests/unit/test_pac_adapter.py`, testing `CfdiPacProvider` (contracts/pac-provider.md §3) over the library's `SimulatedPac()` (in-memory store) and `SimulatedSatStatusClient(pac)`, plus an in-file scripted fake for what the simulators can't produce. It covers every row of research R3:
  - **Stamp:** str → bytes, with a byte check that the client received `sealed_xml.encode('utf-8')`; `uuid`, `sat_seal`, `sat_certificate` and `provider_rfc`; `stamped_at` naive in Mexico City time; `xml` as str.
  - **`find_stamp`:** the simulator's existing stamp, and None for an unknown document.
  - **Cancellation:** `request_cancellation` with `csd.password` decoded, `Csd.key` → `private_key`, and `substitution` → `substitute_uuid`. `ACCEPTED`, `PENDING` and `NOT_CANCELLABLE` come back by name. A `ValidationError` (reason `01` without a substitute) becomes `PacRejected` with the first violation's rule.
  - **Status:** `query_status` maps every `SatState`, `Cancellability` and `CancellationStatus` value.
  - **Errors:** the library's `PacRejected`, `PacUnavailable`, `PacTimeout`, `SatRejected`, `SatUnavailable` and `SatTimeout` map to the contract's classes, with code and message kept and `__cause__`/`__context__` suppressed.
  - **The log line:** exactly one WARNING per `stamp`, `pac {name}: stamp call {issuer} {series}{folio}`, without the XML.
  - **`repr` and messages:** no sentinel password or CSD password appears.

  Seal the input with the existing helpers and SAT test CSD (EKU9003173C9) from `tests/unit/test_cfdi_invoice.py`. For the simulator's outcomes, use `configure_stamp` and `configure_cancellation` (read `mictlanix/cfdi/pac/simulated.py` in the installed package for their signatures).
- [ ] T008 Create `app/services/pac/adapter.py` with `CfdiPacProvider(pac, sat, *, name, resolves_by_resubmitting)`, per contracts/pac-provider.md §3 and research R3:
  - every method translates values, and errors with `raise … from None`;
  - `stamp` logs the WARNING line, reading issuer, `Serie` and `Folio` from the sealed XML with lxml;
  - `__repr__` delegates to the clients.

  Make T007 pass.

**Checkpoint**: the adapter is complete and tested; nothing uses it yet.

---

## Phase 3: User Story 1 - Stamp with the real PAC (Priority: P1) 🎯 MVP

**Goal**: With `PAC_PROVIDER=multifacturas`, the API stamps through the library's Multifacturas client and stores the stamp as spec 039 does.

**Independent Test**: The stamp flow runs end to end through `CfdiPacProvider` over a scripted library client that issues stamps with a real-looking provider RFC: the document is issued, not marked simulated, and each failure maps to its code.

- [ ] T009 [US1] In `app/services/pac/__init__.py`, make `get_pac_provider()` build `CfdiPacProvider(MultifacturasPac(MultifacturasSettings(...)), SatConsultaClient(), name='multifacturas', resolves_by_resubmitting=True)` when `settings.pac_provider == 'multifacturas'`, exactly as in contracts/pac-provider.md. Import the library lazily inside the branch. The password comes from `settings.multifacturas_password.get_secret_value()`. Add a test in `tests/unit/test_pac_settings.py` that the factory returns a `CfdiPacProvider` with `resolves_by_resubmitting` True, without network.
- [ ] T010 [US1] Extend `tests/integration/test_stamp_flow.py` with the flow through `CfdiPacProvider(..., resolves_by_resubmitting=True)` over an in-file scripted library `PacClient`. It stamps with provider RFC `LSO1306189R5` and can be told to raise the library's `PacRejected`, `PacUnavailable` or `PacTimeout`. Cover an invoice, a payment receipt and a credit note:
  - **stamped:** issued; `rfc_pac` is `LSO1306189R5`; the detail's `stamp.simulated` is false; the stored XML is the client's.
  - **rejected:** `pac.rejected`, with `pac_code` and `pac_message` in params; sealed.
  - **unavailable:** `pac.unavailable`; sealed.
  - **timeout:** `pac.timeout`; `stamping`.

  (US1 scenarios 1–4)

**Checkpoint**: an instance configured with Multifacturas stamps.

---

## Phase 4: User Story 2 - Resolve a stamp left in flight without a second UUID (Priority: P1)

**Goal**: With a provider that resolves by resubmitting, refresh resends the stored sealed XML after the 5-minute grace and applies the stamp outcomes. The simulator's look-up is unchanged.

**Independent Test**: A stamp times out. A refresh within 5 minutes sends nothing; a refresh after resubmits byte-identical XML and stores one UUID. Every failure lands in FR-010's state, and concurrent refreshes leave one UUID.

- [ ] T011 [US2] In `tests/unit/test_fiscal_stamping.py`, using `ScriptedPac` with `resolves_by_resubmitting = True` and the file's existing session fakes (see `test_a_stamping_marker_that_changed_is_left_alone`), add tests for a document left `stamping`:
  1. **Within `STAMP_LOOKUP_GRACE`:** no PAC call, and the document comes back unchanged.
  2. **After it:** `stamp` is called once with exactly the stored XML, and `find_stamp` never.
  3. **A stamp:** stored with the incidence `CFDI stamp resolved {uuid}`.
  4. **`PacRejected`:** the marker is cleared only if unchanged, and `pac.rejected` is raised with params.
  5. **`PacUnavailable` and `PacTimeout`:** the marker stays, and the matching error is raised.
  6. **`touch=False`:** the updater and modification time stay as they were.

  The existing look-up tests (`resolves_by_resubmitting = False`) pass unchanged. (FR-008–FR-011)
- [ ] T012 [US2] In `app/services/fiscal_stamping.py` `refresh`, change the stamping branch (`request is None`) per research R4:
  - **If `provider.resolves_by_resubmitting`:** when `_now() - marker < STAMP_LOOKUP_GRACE`, return the document without calling the PAC. Otherwise call `provider.stamp(xml)`:
    - on success, `_store_stamp(..., reason=f'CFDI stamp resolved {issued.uuid}', touch=touch)`;
    - on `PacRejected`, `_clear_marker(db, document_id, marker)`, then raise the same `pac.rejected` DomainError that `stamp()` raises;
    - on `PacUnavailable` or `PacTimeout`, raise as the branch does today.
  - **Otherwise:** the `find_stamp` path is untouched.

  Make T011 pass.
- [ ] T013 [US2] In `tests/integration/test_stamp_flow.py`, test the recovery end to end with `CfdiPacProvider(..., resolves_by_resubmitting=True)` over T010's scripted client, configured to time out on the first call and to deduplicate by sealed XML afterwards:
  - **The sequence:** stamp → `pac.timeout` → age `stamping_since` past 5 min (as the file does near line 280) → refresh → issued with one UUID.
  - **Concurrent refreshes:** two concurrent refreshes leave one UUID, and the second finds the document issued.
  - **A rejection on resubmission:** the document is sealed again.

  (SC-003)

**Checkpoint**: US1 and US2 satisfy #46.

---

## Phase 5: User Story 3 - Cancel through SAT's two-step flow with the real PAC (Priority: P1)

**Goal**: 202 cancels at once. 201 (`PENDING`) leaves the request open until SAT settles it, and it closes as not received only after 72 hours.

**Independent Test**: Every PAC answer and SAT status reaches spec 039's final states, and the 72-hour rule holds on both sides of the boundary.

- [ ] T014 [US3] In `tests/unit/test_fiscal_stamping.py`, after T011 (same file):
  - change the `not_received` case of `test_a_result_that_closes_the_request`;
  - **a `pending` request with SAT valid/`none`:** within 72 hours of `answered_at`, only `last_checked_at` is written, with no status change and no incidence; at or after 72 hours, `failed` with "Cancellation not received by SAT";
  - **a `requested` request:** the 5-minute rule is unchanged on both sides.

  The status-session fake supplies `answered_at` in the request row. (FR-014, research R5)
- [ ] T015 [US3] In `app/services/fiscal_stamping.py`:
  - add `CANCELLATION_SAT_WAIT = timedelta(hours=72)` next to `STAMP_LOOKUP_GRACE`;
  - add `FiscalDocumentCancellation.answered_at` to transaction 1's request select in `refresh`, and unpack it in `_apply_status`;
  - close as `failed` on SAT valid/`none` only when `kept == 'requested'` and `now - requested_at >= STAMP_LOOKUP_GRACE`, or when `kept == 'pending'` and `now - answered_at >= CANCELLATION_SAT_WAIT`.

  Make T014 pass.
- [ ] T016 [US3] In `tests/integration/test_cancel_flow.py`:
  1. **Update the test near line 685** ("Only the fake can answer `valid`/`none`…"). A refresh right after the pending answer leaves the request `pending`. After moving `answered_at` back 72 hours, it closes `failed` with the same three incidence reasons.
  2. **Add the flow through `CfdiPacProvider(..., resolves_by_resubmitting=True)`** over a scripted library client and a scripted SAT client, for an invoice, a payment receipt and a credit note:
     - **201 then cancelled:** cancellation pending → SAT cancelled → cancelled, with spec 028's holds released (follow the file's existing refund and later-receipt assertions);
     - **201 then the receiver rejects:** SAT valid/`rejected` → issued;
     - **202:** cancelled at once;
     - **a refusal:** `pac.rejected`, the request `rejected` with code and message stored, and the document issued;
     - **reason 01:** the substitute reaches the client as `substitute_uuid`.

  (US3 scenarios 1–7, SC-004)

**Checkpoint**: with US1 and US2, this satisfies #51.

---

## Phase 6: User Story 4 - Configure an instance safely (Priority: P2)

**Goal**: The startup refusals of contracts/configuration.md.

**Independent Test**: Every combination starts or refuses as the table says, naming settings, never values.

- [ ] T017 [P] [US4] In `tests/unit/test_pac_settings.py`, following its env isolation (commit 0795063), parametrize every row of contracts/configuration.md "Startup refusals":
  - the valid combinations pass: `multifacturas` with test mode and `APP_ENV` staging or development, and with production mode and `APP_ENV=production`; `simulated` outside production;
  - every refusal raises, naming the listed settings;
  - no message contains the sentinel user or password;
  - a non-HTTPS URL or a timeout of 0 refuses, naming the variable.
- [ ] T018 [US4] Extend `check_pac_settings` in `app/core/config.py`:
  - keep the simulator's refusal in production;
  - add the Multifacturas refusals;
  - when `pac_provider == 'multifacturas'`, construct `MultifacturasSettings(...)` once (lazy import) and re-raise its `ValueError` as a message naming `MULTIFACTURAS_URL`, `MULTIFACTURAS_CONNECT_TIMEOUT` or `MULTIFACTURAS_READ_TIMEOUT`, without the value.

  Make T017 pass.

**Checkpoint**: US4 complete.

---

## Phase 7: User Story 5 - The simulator runs on the library (Priority: P2)

**Goal**: `PAC_PROVIDER=simulated` uses the library's simulator over a database store. mbe-api's simulator is gone. The routes drive the library's simulator, and two instances never double-stamp.

**Independent Test**: Every scenario of quickstart §5 passes through the routes. The two-instance test produces one UUID. Old simulated documents keep their mark after migration 036.

### Store and migration

- [ ] T019 [P] [US5] Create `migrations/036_pac_simulator_store.sql` and `migrations/036_pac_simulator_store_rollback.sql` per research R7, with a header comment in 034's style ("-- 036 … -- spec 040").
  - **The migration:** create `pac_simulator_store` (`store_key VARCHAR(100) NOT NULL PRIMARY KEY`, `value MEDIUMTEXT NOT NULL`, `updated_at DATETIME NOT NULL`), using the table options 034 used for `pac_simulation`. Drop `pac_simulation`. Then `UPDATE fiscal_document_xml … SET data = REPLACE(data, 'RfcProvCertif="SIM010101SIM"', 'RfcProvCertif="SIM010101SI0"')` for the documents whose `rfc_pac` is `SIM010101SIM` (read `app/models/fiscal.py` for the key), then `UPDATE fiscal_document SET rfc_pac = 'SIM010101SI0' WHERE rfc_pac = 'SIM010101SIM'`.
  - **The rollback:** the reverse, recreating `pac_simulation` exactly as 034 created it, empty.
  - Follow README "Writing a migration". Do not run it: T031 does, with confirmation.
- [ ] T020 [US5] In `app/models/fiscal.py`, replace `PacSimulation` with `PacSimulatorStore` (`store_key` String(100) primary key, `value` Text, `updated_at` DateTime), matching T019. Update the resource name in `app/core/errors.py:129`, and the `pac_simulation` mention in `PacSimulationUpdate`'s docstring in `app/schemas/fiscal.py`. Update `tests/unit/test_fiscal_document_service.py:73` to the new table.
- [ ] T021 [P] [US5] Create `tests/unit/test_simulator_store.py` for `DatabaseSimulatorStore`, on the tests' SQLite session factory:
  - a missing key reads None;
  - `set` then `get` returns the value;
  - a second `set` overwrites it and moves `updated_at`;
  - each call commits in its own session, visible to a fresh session;
  - a session factory that raises makes `SimulatedPac.stamp` raise the library's `PacUnavailable`, which the adapter maps to `PacUnavailable`.
- [ ] T022 [US5] Create `app/services/pac/simulator_store.py` with `DatabaseSimulatorStore(session_factory=AsyncSessionLocal)` per contracts/pac-provider.md §4 and research R10: `get` selects the value by key; `set` does `merge(PacSimulatorStore(store_key=…, value=…, updated_at=local_now()))` and commits. Each uses its own session. Make T021 pass.

### Provider and routes

- [ ] T023 [US5] In `app/services/pac/__init__.py`, make `simulated` build `pac = SimulatedPac(DatabaseSimulatorStore())` and `CfdiPacProvider(pac, SimulatedSatStatusClient(pac), name='simulator', resolves_by_resubmitting=False)`. Then:
  - delete `app/services/pac/simulated.py` and `tests/unit/test_pac_simulated.py`;
  - in `tests/unit/test_pac_settings.py:64`, assert a `CfdiPacProvider` with `resolves_by_resubmitting` False;
  - change `tests/unit/test_pac_provider.py:18` to `SIMULATED_PAC_RFC == 'SIM010101SI0'`.
- [ ] T024 [US5] Move the integration tests to the library's simulator. Wherever they build `SimulatedPac(session_factory=sessions)`, build `CfdiPacProvider` over `SimulatedPac(DatabaseSimulatorStore(sessions), clock=…)` and `SimulatedSatStatusClient`. Pass `clock` wherever a test moves time, for example the 24-hour rule. Replace `isinstance(pac, SimulatedPac)` checks with the provider's `name == 'simulator'` or a fixture flag. Replace direct `PacSimulation` queries with the store, or with the simulator's own reads (`find_stamp`, `query_status`). The files are:
  - `tests/integration/test_stamp_flow.py`;
  - `tests/integration/test_cancel_flow.py`, keeping lines 430–433's expectations;
  - `tests/integration/test_resolve_job.py`;
  - `tests/integration/test_fiscal_stamping_http.py`, whose fixture `pac` and `simulated` drive outcomes through the routes or the library's `configure_*`.

  Change the RFC literals in `tests/unit/test_fiscal_document_service.py:187`, `tests/unit/test_print_contexts.py:1109` and `tests/integration/test_print_render.py:761` to `SIM010101SI0`. Then run `grep -rn "SIM010101SIM\|PacSimulation\b\|pac\.simulated" app tests`; it must find only the migration.
- [ ] T025 [US5] Rewrite `tests/api/test_pac_simulator.py` per contracts/simulator-controls.md, with `PAC_PROVIDER=simulated` and the library's simulator on the test database (no `MagicMock(spec=SimulatedPac)`). Cover:
  - the 404 when not simulated;
  - the privilege;
  - 409 `not_sealed` for a draft;
  - GET on an unstamped document (`uuid` null, `not_found`, `none`) and on a stamped one;
  - PUT `stamp` for each outcome, applied once;
  - PUT `cancellation` before the stamp → 409 `fiscal_document.not_stamped`, and after it for each outcome, applied once;
  - `pac_code` and `pac_message` on `rejected`;
  - `receiver` accept, reject and expire, then refresh;
  - `receiver` with nothing pending → 409 `nothing_to_refresh`;
  - `not_cancellable` permanent across two cancels;
  - no `next_stamp` or `next_cancellation` in any response.

  Update `tests/unit/test_fiscal_schemas.py` for the response without them.
- [ ] T026 [US5] Rewrite `app/api/v1/endpoints/pac_simulator.py` per research R12:
  - keep `require_simulator`, `_UPDATE` and the draft check;
  - build `SimulatedPac(DatabaseSimulatorStore())` and `SimulatedSatStatusClient`;
  - GET calls `query_status` with the document's issuer, recipient, UUID and `fiscal_document_service.xml_total(get_xml(...))`;
  - PUT calls `configure_stamp` with the stored XML's bytes and/or `configure_cancellation` with the UUID (409 `fiscal_document.not_stamped` without one);
  - POST calls `answer_as_receiver`, mapping the library's `PacRejected` code `SIM01`, or no UUID, to 409 `fiscal_document.nothing_to_refresh`;
  - other library errors go through the adapter's mapping to `pac.rejected`, `pac.unavailable` or `pac.timeout`.

  Remove `next_stamp` and `next_cancellation` from `PacSimulationResponse` in `app/schemas/fiscal.py`. Make T025 pass.
- [ ] T027 [US5] In `tests/integration/test_stamp_flow.py`, add the two-instance test of research R11 (SC-009). Build two `CfdiPacProvider`s over two `SimulatedPac` instances sharing one `DatabaseSimulatorStore` on the test database, then fire 20 concurrent stamp requests for one sealed document, alternating providers, each in its own session. Assert:
  - exactly one simulator stamp call (count the adapter's log line, or wrap the store and count the `seal:` writes that create a stamp);
  - every request answered with the issued document or 409 `fiscal_document.stamping`;
  - one UUID on the document, and one `uuid:` key in the store.

  If SQLite can't hold the row locks the test needs, follow how spec 039's SC-002 test is written in this file. (FR-024)
- [ ] T028 [P] [US5] Update the docs:
  - `docs/specs/10-fiscal-documents.md`'s "The simulator" paragraph: the library's simulator, RFC `SIM010101SI0`, state in `pac_simulator_store`, outcomes set after the seal or stamp, the response without `next_*`, `not_cancellable` permanent;
  - `docs/data-dictionary.md`: replace the `pac_simulation` entry with `pac_simulator_store` (API-only, migration 036), and note that 036 dropped `pac_simulation`.

**Checkpoint**: all five stories complete offline.

---

## Phase 8: Live tests (FR-029)

- [ ] T029 [P] Create `tests/integration/test_multifacturas_live.py` per research R8.
  - **Multifacturas (skip unless `MULTIFACTURAS_USER` and `MULTIFACTURAS_PASSWORD` are set):** `CfdiPacProvider` over `MultifacturasPac` in test mode, with an invoice sealed with SAT's test CSD (EKU9003173C9) using the helpers in `tests/unit/test_cfdi_invoice.py`.
    - **Stamp twice:** the same UUID.
    - **Cancel with reason 02:** record the answer; 205 expected, not asserted hard.
  - **SAT status (skip unless `SAT_LIVE_VALID` and `SAT_LIVE_CANCELLED` are set):** each holds `issuer|recipient|uuid|total`, and the answers are `valid` and `cancelled` over `SatConsultaClient`.
  - **Never print** the variables.

  Confirm that the default suite skips the module.

---

## Phase 9: Polish, deploy and walk

- [ ] T030 Run the final gate:
  - `docs/specs/10-fiscal-documents.md` §2b: the adapter, Multifacturas' configuration, resubmission, the 72-hour wait, production stays `none` until #57, and the cutover checks;
  - a `CHANGELOG.md` entry for spec 040, in its existing format. Added: the Multifacturas provider, settings, startup checks and `pac_simulator_store`. Changed: the library's simulator replaces mbe-api's, the simulator routes' response, refresh resubmission and the 72-hour wait. Removed: `pac_simulation`;
  - the full offline suite and ruff;
  - format the touched files whose origin version was formatted;
  - search `git diff origin/main` for credentials and CSD material (SC-005).

  Commit on the spec branch.
- [ ] T031 **Deploy (each step needs the user's confirmation)**:
  1. Add xolotl `mictlanix`'s `~/.ssh/id_ed25519.pub` as a read-only deploy key on mictlanix/cfdi, and check `ssh -T git@github.com` from xolotl.
  2. Run migration 036 on mbe_dev. Afterwards, check read-only that no `fiscal_document` or `fiscal_document_xml` row carries `SIM010101SIM`, that `pac_simulator_store` exists, and that `pac_simulation` is gone.
  3. In the same step, deploy the branch to dev :8765 (pre-authorized) and test :8766. Both run `uv sync`.
  4. Check on dev that an old simulated document still reads `stamp.simulated` true.
- [ ] T032 **Test configuration (needs the user's confirmation)**:
  - in test's `.env`, set `PAC_PROVIDER=multifacturas`, `MULTIFACTURAS_USER`, `MULTIFACTURAS_PASSWORD` (from the user) and `MULTIFACTURAS_PRODUCTION=false`, keeping `APP_ENV=staging`, then restart;
  - check that a start with `MULTIFACTURAS_PRODUCTION=true` is refused;
  - check that the journal shows no credential;
  - check that the simulator routes answer 404 on test.

  (SC-005, SC-006, SC-007)
- [ ] T033 **Simulated scenarios on dev (SC-008)**: drive every row of quickstart.md §5 through dev's routes, and record each outcome there.
- [ ] T034 **Walk on test (SC-001, SC-002)**, per quickstart.md §4:
  - stamp an invoice, a payment receipt and a credit note, plus one substitute of each;
  - cancel each original with 02, then with 01 and its substitute;
  - fill the table.

  If test mode refuses a real issuer's CSD, record it and ask the user about research R9's fallback before any write.
- [ ] T035 Run the live tests (T029) with the user's credentials and two UUIDs of the current system, chosen read-only from mbe_dev. Record the outcome in quickstart.md §2 without the values (SC-004).
- [ ] T036 Update the UI handoff, mictlanix/mbe-ui#274 (FR-026), with a comment that lists:
  - the simulator routes' new response, without `next_stamp` and `next_cancellation`;
  - cancellation outcomes only after the stamp (409 `fiscal_document.not_stamped`);
  - stamp outcomes after the last seal;
  - `not_cancellable` now permanent;
  - the provider RFC `SIM010101SI0`;
  - that test now runs the real PAC in test mode, so the simulated scenarios run on dev.

  Post it only after the user confirms the text, because it's outward-facing.

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup:**
  - T001, then T002 (the gate);
  - T003 is independent.
- **Foundational:** after T002.
  - T004 and T005 come first;
  - then T007 and T008, the adapter, which every story needs;
  - T006 is independent.
- **US1:** after T008.
- **US2:** T011 and T012 need only T004. T013 needs T010's scripted client.
- **US3:** T014 comes after T011, in the same file. T016 needs T008.
- **US4:** after T005.
- **US5:**
  - T019, T020, T021 and T022 come first, with T022 needing T020;
  - then T023, which deletes the old simulator;
  - then T024 and T025/T026 (these two in that order);
  - then T027.
  - The other stories don't depend on US5. T023 breaks every test that still imports `app.services.pac.simulated` until T024 lands, so run T023 and T024 together.
- **Live tests:** after T008.
- **Polish:** T030 after all stories. T031–T036 in order, each with the user's confirmation where marked.

### Parallel Opportunities

| Phase | Tasks that can run together |
|---|---|
| 1 | T003 ∥ T001 |
| 2 | T006 ∥ T004 / T005; T007 ∥ T005 |
| US4 | all of it ∥ US2 and US3 |
| US5 | T019 ∥ T021 ∥ T028 |
| Live tests | T029 ∥ US2–US5 once T008 is done |
| Same file | T011 and T014 share `tests/unit/test_fiscal_stamping.py`; T010, T013, T024 and T027 share `tests/integration/test_stamp_flow.py`. Run each group one after the other. |

## Implementation Strategy

### MVP

1. **Phases 1–2:** the dependency and the adapter.
2. **US1 and US2:** Multifacturas stamping and its recovery, which completes #46 offline.

### Incremental delivery

1. **US3:** cancellation and the 72-hour wait, which completes #51.
2. **US4:** the startup checks, required before any server runs Multifacturas.
3. **US5:** the library's simulator, the store, the routes and migration 036. It must land before deploying, because dev runs the simulator.
4. **Then:** the live tests, the deploy (T031), test's configuration (T032), the scenarios on dev (T033), the walk (T034–T035) and the handoff update (T036).

### Notes

- **Commits:** commit after each phase on `040-real-pac`; spec-branch commits are pre-authorized.
- **Confirmation:** only deploying the branch to dev runs without confirmation. Migration 036, the test deploy, test's `.env`, the deploy key and the mbe-ui comment each need it.
- **Credentials:** never write credentials, CSD passwords or live UUIDs into files.
