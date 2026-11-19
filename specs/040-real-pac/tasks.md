# Tasks: Connect stamping and cancellation to the real PAC

**Input**: Design documents from `specs/040-real-pac/` — plan.md, spec.md, research.md (R1–R9), data-model.md, contracts/pac-provider.md, contracts/configuration.md, quickstart.md

**Tests**: Required. FR-023 and FR-024 ask for them, and the constitution's testing rule requires them for every change.

**Organization**: Tasks are grouped by user story (US1–US5 in spec.md), so each story can be built and tested on its own.

**How to run the suite**: `DATABASE_URL='sqlite+aiosqlite:///:memory:' JWT_SECRET_KEY=test-secret-test-secret-test-secret-0123 BUSINESS_TIMEZONE=America/Mexico_City uv run pytest -q`.

**Lint**: `uv run ruff check app/ migrations/ tests/`. Run `ruff format` only on the files you touch, and only if origin's version of that file was already formatted.

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
- [ ] T002 Run the full offline suite and ruff on the new lock, before any other change. It is the gate for the `cryptography` 49 → 50 move: python-jose, CSD sealing and the print tests must all pass. If anything fails, stop and report; don't patch around it. (research R1)
- [ ] T003 [P] Add a subsection "CFDI library (private)" to `README.md` under `## Setup`. It says:
  - the library comes from the private repository mictlanix/cfdi over SSH;
  - developers need GitHub SSH access;
  - each server needs its user's public key added as a **read-only deploy key** on mictlanix/cfdi (xolotl: `mictlanix`'s `~/.ssh/id_ed25519.pub`), checked with `ssh -T git@github.com`;
  - to move the pin, change `rev` in `[tool.uv.sources]` and run `uv lock`.
  (FR-002)

---

## Phase 2: Foundational (Blocking Prerequisites)

**Purpose**: The contract attribute and the settings that every story reads.

**⚠️ CRITICAL**: No user story work can begin until this phase is complete.

- [ ] T004 In `app/services/pac/provider.py`, add `resolves_by_resubmitting: ClassVar[bool]` to the `PacProvider` protocol, with the docstring from contracts/pac-provider.md §1. Then:
  - set `resolves_by_resubmitting = False` on `SimulatedPac` in `app/services/pac/simulated.py`;
  - in `tests/fakes/pac.py`, give `ScriptedPac` an instance attribute `resolves_by_resubmitting = False` that tests may set to True.
  - Update the module docstring of `provider.py`: the real client is now `MultifacturasProvider`.
  (research R4)
- [ ] T005 Add the settings from contracts/configuration.md to `app/core/config.py`:
  - widen `pac_provider` to `Literal['none', 'simulated', 'multifacturas']`;
  - add `multifacturas_user: str | None = None`, `multifacturas_password: SecretStr | None = None`, `multifacturas_production: bool = False`, `multifacturas_url: str = 'https://ws.multifacturas.com'`, `multifacturas_connect_timeout: float = 10.0` and `multifacturas_read_timeout: float = 60.0`;
  - update the comment above `app_env`. Its "step 3 adds the real provider" is now done.
  (research R6)
- [ ] T006 [P] Document the new variables in `.env.example` next to `PAC_PROVIDER`. Use placeholder values only, and comment that `MULTIFACTURAS_PRODUCTION` must be `true` exactly when `APP_ENV=production`. (contracts/configuration.md)

**Checkpoint**: the suite still passes, and nothing has changed in behavior yet.

---

## Phase 3: User Story 1 - Stamp with the real PAC (Priority: P1) 🎯 MVP

**Goal**: With `PAC_PROVIDER=multifacturas`, a sealed invoice, payment receipt or credit note is stamped by the library's Multifacturas client, and the stamp is stored as spec 039 stores any stamp.

**Independent Test**: The stamp flow runs end to end with `MultifacturasProvider` built over the library's `SimulatedPac`: the document becomes issued with the PAC's UUID, it is not marked simulated, and each failure maps to its error code.

### Tests for User Story 1

- [ ] T007 [P] [US1] Create `tests/unit/test_pac_multifacturas.py`. Build `MultifacturasProvider(pac, sat)` with `mictlanix.cfdi.pac.SimulatedPac()` and `mictlanix.cfdi.sat.SimulatedSatStatusClient(pac)`, and add a small in-file scripted fake for the cases those can't produce. The tests cover the `stamp` rows of research R3:
  - the str → bytes encoding, and a byte check that the library received `sealed_xml.encode('utf-8')`;
  - `uuid`, `sat_seal`, `sat_certificate` and `provider_rfc` from the TFD;
  - `stamped_at` naive, in Mexico City local time;
  - `xml` decoded to str;
  - `find_stamp` returning None without calling the library;
  - `resolves_by_resubmitting is True`;
  - the library's `PacRejected(code, message)`, `PacUnavailable` and `PacTimeout` arriving as the contract's classes with the same code and message, and with `__cause__` and `__context__` suppressed (`from None`);
  - `repr(provider)` containing no password (configure the library settings with a sentinel password and assert it is absent).

  Use `SimulatedPac.configure_stamp(...)` outcomes `stamped`, `rejected`, `unavailable` and `timeout_not_stamped`; read the library's `pac/simulated.py` for their exact signatures. Any sealed XML the library accepts will do, for example one produced by the existing sealing fixtures with SAT's test CSD (EKU9003173C9) in `tests/unit/test_cfdi_invoice.py`.

### Implementation for User Story 1

- [ ] T008 [US1] Create `app/services/pac/multifacturas.py` with `MultifacturasProvider` per contracts/pac-provider.md §3:
  - `resolves_by_resubmitting = True`;
  - `__init__(self, pac, sat)` stores both clients;
  - `stamp` encodes, calls `self._pac.stamp`, and maps the result to `provider.Stamp` per R3. If `FechaTimbrado` has a tzinfo, convert it to `America/Mexico_City` and drop the tzinfo; if it is naive, keep it.
  - `find_stamp` returns None.
  - Translate errors per R3 with `raise … from None`.
  - `__repr__` delegates to the clients' reprs.

  Write the cancellation and status methods in T015. Until then, they raise `NotImplementedError`, which T015 removes. Make T007 pass.
- [ ] T009 [US1] In `app/services/pac/__init__.py`, make `get_pac_provider()` build `MultifacturasProvider(MultifacturasPac(MultifacturasSettings(...)), SatConsultaClient())` from settings when `settings.pac_provider == 'multifacturas'`, exactly as in contracts/pac-provider.md. Import the library lazily inside that branch, like the simulator. The password comes from `settings.multifacturas_password.get_secret_value()`.
- [ ] T010 [US1] Extend `tests/integration/test_stamp_flow.py` with the stamp flow through `MultifacturasProvider` over the library's `SimulatedPac`, for an invoice, a payment receipt and a credit note:
  - **stamped:** the document becomes issued; `rfc_pac` equals the library simulator's RFC, not `SIMULATED_PAC_RFC`; the detail's `stamp.simulated` is false; the stored XML is the library's stamped XML.
  - **rejected:** `pac.rejected`, with `pac_code` and `pac_message` in params, and the document sealed.
  - **unavailable:** `pac.unavailable`, and the document sealed.
  - **timeout:** `pac.timeout`, and the document `stamping`.

  Follow the file's existing helpers and parametrization style. (US1 scenarios 1–4)

**Checkpoint**: US1 is complete. An instance configured with Multifacturas stamps.

---

## Phase 4: User Story 2 - Resolve a stamp left in flight without a second UUID (Priority: P1)

**Goal**: With a provider that resolves by resubmitting, a refresh of a document left `stamping` resends the stored sealed XML after the 5-minute grace and applies the stamp outcomes. The simulator's lookup stays unchanged.

**Independent Test**: A stamp times out, then a refresh resubmits byte-identical XML and stores one UUID. Rejected, unavailable and timeout each land in the state FR-010 gives them, and concurrent refreshes still store one UUID.

### Tests for User Story 2

- [ ] T011 [P] [US2] In `tests/unit/test_fiscal_stamping.py`, using `ScriptedPac` with `resolves_by_resubmitting = True` and the file's existing session fakes (see `test_a_stamping_marker_that_changed_is_left_alone` and its neighbours), add tests for a document left `stamping`:
  1. **Within `STAMP_LOOKUP_GRACE`:** no PAC call at all, and the document is returned unchanged.
  2. **After the grace:** `stamp` is called once with exactly the stored sealed XML, and `find_stamp` is never called.
  3. **A stamp:** stored via `_store_stamp`, with the incidence reason `CFDI stamp resolved {uuid}`.
  4. **`PacRejected`:** the marker is cleared only if unchanged (sealed again), and `pac.rejected` is raised with params.
  5. **`PacUnavailable` and `PacTimeout`:** the marker stays, and `pac.unavailable` or `pac.timeout` is raised.
  6. **The resolve job (`touch=False`):** it leaves `updater` and `modification_time` alone on a resolved stamp.

  The existing simulator-path tests (`resolves_by_resubmitting = False`) must pass unchanged. (FR-008–FR-011)

### Implementation for User Story 2

- [ ] T012 [US2] In `app/services/fiscal_stamping.py` `refresh`, change the stamping branch (`request is None`) as research R4 describes:
  - **If `provider.resolves_by_resubmitting`:** when `_now() - marker < STAMP_LOOKUP_GRACE`, return the document as it is without calling the PAC. Otherwise call `provider.stamp(xml)`:
    - on success, `_store_stamp(..., reason=f'CFDI stamp resolved {issued.uuid}', touch=touch)`;
    - on `PacRejected`, `_clear_marker(db, document_id, marker)`, then raise the same `pac.rejected` DomainError that `stamp()` raises;
    - on `PacUnavailable` or `PacTimeout`, raise as the branch already does.
  - **Otherwise:** keep the `find_stamp` path untouched.

  Read `_clear_marker` and `_store_stamp` first. Don't change their rules. Make T011 pass.
- [ ] T013 [US2] In `tests/integration/test_stamp_flow.py`, test the end-to-end recovery with `MultifacturasProvider`:
  - **The sequence:** stamp → `pac.timeout` → age `stamping_since` past 5 min (the file already does this near line 280) → refresh → issued.
  - **One UUID:** exactly one stamp, whose UUID equals what the PAC holds for that XML.
  - **Concurrent refreshes:** two concurrent refreshes leave one UUID, and the second finds the document issued.
  - **A rejection on resubmission:** the document is sealed.

  Use the library's `SimulatedPac` if its `timeout_stamped` outcome followed by a resubmission returns the same stamp (check in its `pac/simulated.py`); otherwise use `ScriptedPac` with `resolves_by_resubmitting = True`. (SC-003)

**Checkpoint**: US1 and US2 together satisfy #46.

---

## Phase 5: User Story 3 - Cancel through SAT's two-step flow with the real PAC (Priority: P1)

**Goal**: Cancellation through `MultifacturasProvider`: 202 cancels at once, and 201 leaves the request open until SAT's status settles it. A pending request closes as "not received by SAT" only 72 hours after the PAC's answer.

**Independent Test**: Every PAC answer and every SAT status reaches spec 039's final states, and the 72-hour rule holds on both sides of the boundary.

### Tests for User Story 3

- [ ] T014 [P] [US3] Extend `tests/unit/test_pac_multifacturas.py` with the cancellation and status rows of research R3:
  - **The request:** `request_cancellation` builds the library's `CancellationRequest`, with `csd.password` decoded from bytes, `private_key` from `Csd.key`, and `substitution` → `substitute_uuid`.
  - **Answers and errors:**
    - `ACCEPTED` and `PENDING` come back by name;
    - the library's `PacRejected`, `PacUnavailable` and `PacTimeout` map to the contract's;
    - a `ValidationError` raised while building the request (reason `01` without a substitute) becomes `PacRejected` with the first violation's rule as code;
    - no secret appears in any message (use a sentinel CSD password).
  - **`query_status`:**
    - every `SatState`, `Cancellability` and `CancellationStatus` value maps to the contract's literals, with `cancellability` → `cancellable`;
    - `SatRejected`, `SatUnavailable` and `SatTimeout` map to `PacRejected`, `PacUnavailable` and `PacTimeout`.

### Implementation for User Story 3

- [ ] T015 [US3] Implement `request_cancellation` and `query_status` in `app/services/pac/multifacturas.py` per research R3, removing T008's `NotImplementedError`. Build `CsdCredentials(certificate=csd.certificate, private_key=csd.key, password=csd.password.decode('utf-8'))`, and catch the library's `ValidationError` around building `CancellationRequest`. Make T014 pass.
- [ ] T016 [P] [US3] In `tests/unit/test_fiscal_stamping.py`, change the `not_received` case of `test_a_result_that_closes_the_request`, and add cases to show:
  - **A `pending` request** with SAT valid/`none`:
    - **within 72 hours of `answered_at`:** only `last_checked_at` is written, with no status change, no incidence, and the document still cancellation pending;
    - **at or after 72 hours:** `failed`, with "Cancellation not received by SAT".
  - **A `requested` request:** the 5-minute rule is unchanged on both sides.

  The status-session fake must now supply `answered_at` in the request row. (FR-014, research R5)
- [ ] T017 [US3] In `app/services/fiscal_stamping.py`:
  - add `CANCELLATION_SAT_WAIT = timedelta(hours=72)` next to `STAMP_LOOKUP_GRACE`;
  - add `FiscalDocumentCancellation.answered_at` to transaction 1's request select in `refresh`, and unpack it in `_apply_status`;
  - change the `sat.state == 'valid' and sat.cancellation == 'none'` branch to close as `failed` only when `kept == 'requested'` and `now - requested_at >= STAMP_LOOKUP_GRACE`, or when `kept == 'pending'` and `now - answered_at >= CANCELLATION_SAT_WAIT`.

  Make T016 pass.
- [ ] T018 [US3] In `tests/integration/test_cancel_flow.py`:
  1. **Update the test near line 685** ("Only the fake can answer `valid`/`none`…"). Refreshing right after the pending answer leaves the request `pending` and the document cancellation pending. After moving `answered_at` back 72 hours, a refresh closes it `failed` with the same three incidence reasons.
  2. **Add the flow through `MultifacturasProvider`** over the library's `SimulatedPac` and `SimulatedSatStatusClient`, for invoice, payment receipt and credit note:
     - **Pending, then cancelled:** the PAC answers pending (201) → cancellation pending → the SAT simulator reports cancelled → cancelled, with spec 028's holds released (follow the file's existing assertions for refunds and later receipts).
     - **Pending, then rejected:** pending → the receiver rejects → issued.
     - **Accepted:** the PAC answers accepted (202) → cancelled at once.
     - **Refused:** the PAC refuses → `pac.rejected`, the request `rejected` with `pac_code` and `pac_message` stored, and the document issued.
     - **Reason 01:** a substitute reaches the library as `substitute_uuid`.

  (US3 scenarios 1–7, SC-004)

**Checkpoint**: US3 complete; with US1 and US2 it satisfies #51.

---

## Phase 6: User Story 4 - Configure an instance safely (Priority: P2)

**Goal**: The startup refusals of contracts/configuration.md.

**Independent Test**: Every combination starts or refuses as the table says, and the refusal messages name settings, never values.

- [ ] T019 [P] [US4] In `tests/unit/test_pac_settings.py`, follow the file's pattern (it already isolates itself from the shell's env; see commit 0795063). Parametrize over every row of contracts/configuration.md "Startup refusals":
  - each refusal raises, naming the listed settings;
  - the valid combinations pass: `multifacturas` with test mode and `APP_ENV=staging` or `development`, and with production mode and `APP_ENV=production`;
  - a message never contains the sentinel user or password;
  - a non-HTTPS `MULTIFACTURAS_URL` or a timeout of 0 refuses, naming the variable.
- [ ] T020 [US4] Extend `check_pac_settings` in `app/core/config.py` with the refusals. When `pac_provider == 'multifacturas'`, construct `MultifacturasSettings(...)` once (lazy import) and re-raise its `ValueError` as a message naming `MULTIFACTURAS_URL`, `MULTIFACTURAS_CONNECT_TIMEOUT` or `MULTIFACTURAS_READ_TIMEOUT`, without the value. Keep the simulator's refusal as it is. Make T019 pass. `app/main.py` and `app/jobs/resolve_fiscal_documents.py` already call it.

**Checkpoint**: US4 complete.

---

## Phase 7: User Story 5 - Simulated documents keep a valid provider RFC (Priority: P3)

**Goal**: The simulator stamps with `SIM010101SI0`, and stored simulated documents keep their mark.

**Independent Test**: A simulator stamp carries the new RFC in the stamp and the XML. After migration 035, the previously simulated documents carry it too and still read as simulated.

- [ ] T021 [US5] In `app/services/pac/provider.py`, set `SIMULATED_PAC_RFC = 'SIM010101SI0'`, and update the docstrings that spell the old value: `app/services/pac/simulated.py` line 5 and `tests/fakes/pac.py` line 5. Then change the four test literals of research R7:
  - `tests/unit/test_pac_provider.py:18`;
  - `tests/unit/test_fiscal_document_service.py:187`;
  - `tests/unit/test_print_contexts.py:1109`;
  - `tests/integration/test_print_render.py:761`.

  Also check `tests/unit/test_pac_simulated.py:98`. Run `grep -rn SIM010101SIM app tests` afterwards: only the migration may still contain it.
- [ ] T022 [P] [US5] Create `migrations/035_simulated_pac_rfc.sql` and `migrations/035_simulated_pac_rfc_rollback.sql`, with a header comment in the style of 034 ("-- 035 … -- spec 040", what and why). The migration:
  - `UPDATE fiscal_document_xml x JOIN fiscal_document d ON …` → `SET x.data = REPLACE(x.data, 'RfcProvCertif="SIM010101SIM"', 'RfcProvCertif="SIM010101SI0"') WHERE d.rfc_pac = 'SIM010101SIM'`. Read `app/models/fiscal.py` for how `fiscal_document_xml` keys to the document.
  - Then `UPDATE fiscal_document SET rfc_pac = 'SIM010101SI0' WHERE rfc_pac = 'SIM010101SIM'`.
  - Then `UPDATE pac_simulation SET stamped_xml = REPLACE(...) WHERE stamped_xml LIKE '%SIM010101SIM%'`.

  The rollback does the reverse in the reverse order. Follow README "Writing a migration". Do not run it: T027 does, with confirmation.
- [ ] T023 [P] [US5] Update the simulator's RFC in `docs/specs/10-fiscal-documents.md` (the "The simulator" paragraph). In `docs/data-dictionary.md`, add to the `pac_simulation` entry that migration 035 rewrote the stored RFC.

**Checkpoint**: All five stories complete offline.

---

## Phase 8: Live tests (FR-024)

- [ ] T024 [P] Create `tests/live/__init__.py` and `tests/live/test_multifacturas_live.py`, per research R8.
  - **Multifacturas (module-level skip unless `MULTIFACTURAS_USER` and `MULTIFACTURAS_PASSWORD` are set):** build `MultifacturasProvider` in test mode (`production=False`), and seal an invoice with SAT's test CSD (EKU9003173C9) using the existing sealing fixtures and helpers of `tests/unit/test_cfdi_invoice.py`.
    - **Stamp twice:** the second answer has the same UUID.
    - **Cancel with reason 02:** `PacRejected` with code `205`.
  - **SAT status (skip unless `SAT_LIVE_VALID` and `SAT_LIVE_CANCELLED` are set):** each holds `issuer|recipient|uuid|total`, and `query_status` returns `valid` and `cancelled` respectively.
  - **Never print** the variables' values.

  Confirm that the default suite (no variables) collects and skips these tests.

---

## Phase 9: Polish, deploy and walk

- [ ] T025 [P] Update `docs/specs/10-fiscal-documents.md` §2b (the PAC section):
  - the Multifacturas provider and its configuration;
  - resolution by resubmission;
  - the 72-hour wait;
  - production stays `none` until #57;
  - the cutover checks of spec.md.

  Add a `CHANGELOG.md` entry for spec 040 (Added: the provider, settings and startup checks; Changed: the simulator RFC, refresh resubmission and the 72-hour wait), in the file's existing format.
- [ ] T026 Run the final gate:
  - the full offline suite;
  - `uv run ruff check app/ migrations/ tests/`;
  - format the touched files that origin had formatted;
  - search `git diff origin/main` for credentials and CSD material (SC-005): no password, no key or certificate bytes, and no PAC payload in fixtures.

  Commit on the spec branch.
- [ ] T027 **Deploy (needs the user's confirmation for each step)**:
  1. Add xolotl `mictlanix`'s `~/.ssh/id_ed25519.pub` as a read-only deploy key on mictlanix/cfdi, and check `ssh -T git@github.com` from xolotl.
  2. Run migration 035 on mbe_dev, read-only check before and after: 17 documents and the `pac_simulation` rows carry `SIM010101SI0`, and none carries the old value.
  3. Deploy the branch to dev :8765 (pre-authorized) and to test :8766 in the same step. Both run `uv sync` successfully.
  4. Check a simulated document's detail still says `stamp.simulated` true.
- [ ] T028 **Test configuration (needs the user's confirmation, and the user's answer on whether the walk runs on test or dev; mbe-ui#274 uses the simulator's controls)**:
  - set `PAC_PROVIDER=multifacturas`, `MULTIFACTURAS_USER`, `MULTIFACTURAS_PASSWORD` (from the user) and `MULTIFACTURAS_PRODUCTION=false` in that instance's `.env`, then restart;
  - check that a start with `MULTIFACTURAS_PRODUCTION=true` is refused;
  - check that the journal shows no credential (SC-006, SC-005).
- [ ] T029 **Walk (SC-001, SC-002)**, following quickstart.md §4:
  - stamp an invoice, a payment receipt and a credit note;
  - cancel each with 02, and with 01 plus a substitute;
  - fill quickstart's table with each UUID, provider RFC, PDF mark and the PAC's answer.

  If test mode refuses a real issuer's CSD, record the answer and ask the user about the fallback in research R9 before any write.
- [ ] T030 Run the live tests (T024) with the user's credentials and two UUIDs of the current system chosen read-only from mbe_dev. Record the outcome in quickstart.md §2 without the values (SC-004).

---

## Dependencies & Execution Order

### Phase Dependencies

- **Setup (Phase 1):**
  - T001, then T002 (the gate);
  - T003 is independent.
- **Foundational (Phase 2):** after T002. It blocks every story.
- **User stories:**
  - **US1:** after Phase 2.
  - **US2:** after US1, because it needs `MultifacturasProvider.stamp` (T008) for T013. T011 and T012 need only T004.
  - **US3:** after T008, since it shares the provider file. T016 and T017 are independent of the provider.
  - **US4:** after T005.
  - **US5:** after Phase 2, independent of the others.
- **Live tests (Phase 8):** after US1 and US3.
- **Polish:** T025 and T026 come after all stories. T027–T030 run in order, each with the user's confirmation.

### Within each story

The tests are written first and fail, then the implementation makes them pass.

### Parallel Opportunities

| Phase | Tasks that can run together |
|---|---|
| 1 | T003 ∥ T001 |
| 2 | T006 ∥ T004 / T005 |
| US1 | T007 ∥ T004's follow-ups |
| US2 | T011 ∥ T013's setup. T011 and US3's T016 both edit `tests/unit/test_fiscal_stamping.py`, so run them one after the other |
| US3 | T014 ∥ T016 |
| US4 | all of it ∥ US2 and US3 |
| US5 | T022 ∥ T023 ∥ T021 |

---

## Parallel Example: User Story 3

```text
Task: "T014 Extend tests/unit/test_pac_multifacturas.py with cancellation and status translation"
Task: "T016 72-hour cases in tests/unit/test_fiscal_stamping.py"
```

---

## Implementation Strategy

### MVP

1. **Phase 1–2.**
2. **US1 and US2:** stamping with Multifacturas, and recovery without a second UUID. That completes #46 offline.

### Incremental delivery

1. **US3:** cancellation and the 72-hour wait, which completes #51.
2. **US4:** the startup safety checks. These are required before any server is configured with Multifacturas.
3. **US5:** the simulator's RFC and migration 035.
4. **Then:** the live tests, the deploy (T027), test's configuration (T028) and the walk (T029–T030).

### Notes

- **Commits:** commit after each phase on `040-real-pac`. Spec-branch commits are pre-authorized.
- **Confirmation:** nothing in T027–T030 runs without the user's confirmation, except deploying the branch to dev.
- **Credentials:** never write credentials, CSD passwords or live UUIDs into files.
