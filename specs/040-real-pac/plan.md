# Implementation Plan: Connect stamping and cancellation to the real PAC

**Branch**: `040-real-pac` | **Date**: 2026-11-18 (revised the same day: the library's simulator replaces mbe-api's) | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/040-real-pac/spec.md`. Roadmap: mictlanix/mbe-roadmap#46, mictlanix/mbe-roadmap#51.

## Summary

The library from mictlanix/cfdi becomes the API's only PAC implementation. It is pinned to a commit and read over HTTPS from its public repository.
- **One adapter,** `CfdiPacProvider` in `app/services/pac/`, implements spec 039's contract over the library's clients (research R2, R3):
  - `PAC_PROVIDER=multifacturas`: `MultifacturasPac` and `SatConsultaClient`;
  - `PAC_PROVIDER=simulated`: the library's `SimulatedPac` and `SimulatedSatStatusClient`, with their state in a new database store (R10).
- **mbe-api's own simulator is deleted.** *(Amended 2026-11-24: the `/pac-simulator` routes were removed afterwards.)* Its `/pac-simulator` routes now drive the library's simulator (R12), without `next_stamp` and `next_cancellation`.

Outside the adapter, the plan changes three things the spec names:
- **Resolving a document left `stamping`:** refresh resubmits the stored sealed XML after the 5-minute grace when the provider resolves that way (R4).
- **The 72-hour wait:** a request the PAC answered with 201 (`PENDING`) closes as "not received by SAT" only after 72 hours (R5).
- **Migration 037** creates `pac_simulator_store`, drops `pac_simulation`, and rewrites the old simulator's provider RFC (R7).

Settings select the provider and refuse unsafe combinations at startup (R6). Production stays `PAC_PROVIDER=none`.

## Technical Context

- **Language/Version:** Python 3.12, which the library requires (≥3.12).
- **Primary Dependencies:**
  - FastAPI, SQLAlchemy 2.0 async, Pydantic v2;
  - **new:** `mictlanix-cfdi` (git over HTTPS, public repository, commit `4b41821b7947adc71a32a535ed5bb87dbd325b77`), which brings `cryptography` from 49.0.0 to ≥50.0.2.
- **Storage:** MariaDB `mbe_dev`. Migration 037 adds `pac_simulator_store`, drops `pac_simulation`, and rewrites stored values.
- **Testing:**
  - pytest and pytest-asyncio, offline;
  - the adapter is tested over the library's simulators, with a scripted fake for what they cannot produce;
  - the simulator store runs on the tests' SQLite;
  - opt-in live tests in `tests/integration/`.
- **Target Platform:** Linux on xolotl (dev :8765 in tmux, test :8766 under systemd), run with uv 0.12.21.
- **Project Type:** web service (an API).
- **Performance Goals:**
  - **Multifacturas' client is synchronous.** Its calls run in `asyncio.to_thread`, so they never block the event loop (constitution VI).
  - **Timeouts:** 10 s connect and 60 s read, for both the PAC and SAT.
  - **The simulator** does one short database transaction per store read or write.
- **Constraints:**
  - no credentials, CSD material or raw payloads in code, tests, logs or errors;
  - the PAC in use is set explicitly in each instance's `.env`, with no default; production's says `none`;
  - dev and test share mbe_dev, so migration 037 and the restart of both go in one step.
- **Verified:** the library accepts mbe-api's sealed invoices, credit notes and payment receipts, and its simulator stamps, deduplicates, looks up and cancels them as spec 039 expects (research R14).
- **Scale/Scope:** five issuers that stamp; about 3,000 CFDIs per issuer per year (mbe_dev, 2025–2026).

## Constitution Check

| Principle | Check | Result |
|---|---|---|
| I. Simplicity First | One adapter for both providers. One contract attribute. A key-value store with `get` and `set` only. No settings for SAT's client. No catch-all handler. The simulator's behavior is not rebuilt in mbe-api. | Pass |
| II. Think Before Coding | Assumptions A1–A6, the cutover checks, the behavior differences of the library's simulator (spec Overview) and the walk's fallback (R9) are stated. | Pass |
| III. Surgical Changes | `fiscal_stamping` changes only in the refresh's stamping branch and the `valid`/`none` case of `_apply_status`. The simulator routes keep their paths and bodies. | Pass |
| IV. Goal-Driven Execution | Each FR maps to an offline test. SC-001/002 map to the recorded walk, SC-008 to the scenarios on dev, and SC-009 to the deterministic offline test plus the live run on dev. | Pass |
| V. Reuse Over Rebuild | Reuses the library's clients and simulators, spec 039's guard, grace, `_clear_marker` and `_store_stamp`, and the existing error codes. The new dependency is declared in FR-001. It removes mbe-api's own simulator. | Pass; additions listed below |
| VI. Async-First | The adapter and store are `async` and use `AsyncSession`; the library offloads Multifacturas' blocking I/O to a thread. | Pass |
| VII. Security by Default | No new endpoint. The password is a `SecretStr`, and every `repr` hides it. Errors are raised `from None`. Startup ties the mode to `APP_ENV`, and the simulator stays refused in production. | Pass |
| VIII. Ruff Compliance | `uv run ruff check app/ migrations/ tests/`; format only the touched files that were already formatted. | Pass |

Re-checked after Phase 1: no change.

## Project Structure

### Documentation (this feature)

```text
specs/040-real-pac/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R14, R-legacy
├── data-model.md        # pac_simulator_store; dropped pac_simulation; values and transitions that change
├── quickstart.md        # offline suite, live tests, deploy, walk table, driving each scenario
├── contracts/
│   ├── pac-provider.md       # resolves_by_resubmitting, SIMULATED_PAC_RFC, CfdiPacProvider, DatabaseSimulatorStore
│   ├── configuration.md      # settings, startup refusals, per-instance values
│   └── simulator-controls.md # the /pac-simulator routes over the library
├── checklists/requirements.md
└── tasks.md
```

### Source Code (repository root)

```text
pyproject.toml, uv.lock                  # mictlanix-cfdi from git; cryptography ≥50.0.2
app/core/config.py                       # PAC_PROVIDER multifacturas, MULTIFACTURAS_*, check_pac_settings
app/models/fiscal.py                     # PacSimulatorStore replaces PacSimulation
app/schemas/fiscal.py                    # PacSimulationResponse without next_stamp, next_cancellation
app/services/pac/
├── __init__.py                          # get_pac_provider builds CfdiPacProvider per provider; get_pac_simulator for the routes
├── provider.py                          # resolves_by_resubmitting; SIMULATED_PAC_RFC from the library
├── adapter.py                           # NEW: CfdiPacProvider
├── simulator_store.py                   # NEW: DatabaseSimulatorStore
└── simulated.py                         # DELETED
app/api/v1/endpoints/pac_simulator.py    # routes over the library's simulator (R12)
app/services/fiscal_stamping.py          # refresh: resubmission branch (R4); _apply_status: 72 h (R5)
app/core/errors.py                       # resource name: pac_simulation → pac_simulator_store
migrations/037_pac_simulator_store.sql (+ _rollback.sql)
tests/fakes/pac.py                       # ScriptedPac gains resolves_by_resubmitting
tests/unit/test_pac_adapter.py           # NEW: translation and errors, both providers (R3)
tests/unit/test_simulator_store.py       # NEW: get/set, overwrite, failure → unavailable
tests/unit/test_pac_simulated.py         # DELETED with simulated.py
tests/unit/test_pac_settings.py          # refusals (SC-006)
tests/unit/test_fiscal_stamping.py       # resubmission outcomes; 72 h cases
tests/unit/test_fiscal_schemas.py, test_fiscal_document_service.py, test_print_contexts.py, test_pac_provider.py
tests/api/test_pac_simulator.py          # every route rule (contracts/simulator-controls.md)
tests/integration/test_stamp_flow.py, test_cancel_flow.py, test_resolve_job.py,
    test_fiscal_stamping_http.py, test_print_render.py      # over the library's simulator; deterministic two-instance test
tests/integration/test_multifacturas_live.py               # NEW, opt-in
.env.example, README.md, CHANGELOG.md, docs/specs/10-fiscal-documents.md, docs/data-dictionary.md
```

**Structure Decision**: this is the existing single FastAPI project. The adapter and store live in `app/services/pac/`, where spec 039 put the provider. The simulator's code moves out of mbe-api into the library.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| `CfdiPacProvider` | The contract over the library, for both providers | Calling the library from `fiscal_stamping` would spread translation and error mapping through the service |
| `DatabaseSimulatorStore` and `pac_simulator_store` | The library's simulator needs one state across worker processes and the job (FR-023) | Reusing `pac_simulation`'s typed columns would mean replacing all of them; in-memory state differs per process |
| `resolves_by_resubmitting` contract attribute | Refresh chooses between look-up and resubmission (FR-008, FR-011) | Making `find_stamp` stamp hides a fiscal operation and still needs the service change for rejections (R4) |
| New dependency `mictlanix-cfdi` | The PAC, SAT and simulator clients, built for this in step 2 (#160) | Writing them in mbe-api would duplicate the library |
