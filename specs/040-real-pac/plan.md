# Implementation Plan: Connect stamping and cancellation to the real PAC

**Branch**: `040-real-pac` | **Date**: 2026-11-18 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/040-real-pac/spec.md`. Roadmap: mictlanix/mbe-roadmap#46, mictlanix/mbe-roadmap#51.

## Summary

The plan adds a `MultifacturasProvider` to `app/services/pac/`. It composes the library's `MultifacturasPac` and `SatConsultaClient` from mictlanix/cfdi, pinned to a commit and read over SSH, and translates between their shapes and spec 039's contract (research R3).

Outside the provider, the plan makes three changes the spec names:
- **Resolving a document left `stamping`:** the refresh resolves it by resubmitting the stored sealed XML when the provider says it resolves that way, never by looking up and returning to sealed (R4).
- **The 72-hour wait:** a request the PAC answered with 201 closes as "not received by SAT" only after 72 hours (R5).
- **The simulator's RFC** becomes `SIM010101SI0`, with migration 035 for the 17 stored simulated documents (R7).

Settings select the provider and refuse unsafe combinations at startup (R6). Production stays `PAC_PROVIDER=none`.

## Technical Context

- **Language/Version:** Python 3.12, which the library requires (≥3.12).
- **Primary Dependencies:**
  - FastAPI, SQLAlchemy 2.0 async, Pydantic v2;
  - **new:** `mictlanix-cfdi` (git, SSH, commit `4b41821b7947adc71a32a535ed5bb87dbd325b77`), which brings `cryptography` from 49.0.0 to ≥50.0.2.
- **Storage:** MariaDB `mbe_dev`. There is no schema change, only data migration 035.
- **Testing:**
  - pytest and pytest-asyncio, offline;
  - the library's `SimulatedPac` and `SimulatedSatStatusClient` stand in for the library's PAC and SAT clients, plus a scripted fake;
  - opt-in live tests in `tests/live/`.
- **Target Platform:** Linux on xolotl (dev :8765 in tmux, test :8766 under systemd), run with uv 0.12.21.
- **Project Type:** web service (an API).
- **Performance Goals:**
  - **The library is synchronous.** Its calls run in `asyncio.to_thread`, so they never block the event loop (constitution VI).
  - **Timeouts:** 10 s connect and 60 s read, for both the PAC and SAT.
- **Constraints:**
  - no credentials, CSD material or raw payloads in code, tests, logs or errors;
  - production keeps `none`;
  - dev and test share mbe_dev, so migration 035 and the restart of both go in one step.
- **Scale/Scope:** five issuers that stamp; about 3,000 CFDIs per issuer per year (mbe_dev, 2025–2026).

## Constitution Check

| Principle | Check | Result |
|---|---|---|
| I. Simplicity First | One provider class. One contract attribute instead of a new method (R4). No settings for SAT's client. No catch-all error handler. | Pass |
| II. Think Before Coding | Assumptions A1–A5 and the cutover checks are in the spec. The test-mode fallback for the walk is in R9. | Pass |
| III. Surgical Changes | `fiscal_stamping` changes only in the refresh's stamping branch and in the `valid`/`none` case of `_apply_status`. Literals of the old RFC change only where tests assert them. | Pass |
| IV. Goal-Driven Execution | Each FR maps to an offline test, and SC-001/002 to the recorded walk. | Pass |
| V. Reuse Over Rebuild | Reuses the library's clients and simulators, spec 039's grace, `_clear_marker` and `_store_stamp`, and the existing error codes. The new dependency is declared in the spec (FR-001). | Pass; the new class is listed below |
| VI. Async-First | Provider methods are `async`, and the library offloads its blocking I/O to a thread. | Pass |
| VII. Security by Default | No new endpoint. The password is a `SecretStr`, and every `repr` hides it. Errors are raised `from None`. Startup ties the PAC's mode to `APP_ENV`. | Pass |
| VIII. Ruff Compliance | `uv run ruff check app/ migrations/ tests/`; format only the touched files that were already formatted. | Pass |

Re-checked after Phase 1: no change.

## Project Structure

### Documentation (this feature)

```text
specs/040-real-pac/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R9, R-legacy
├── data-model.md        # stored values and transitions that change
├── quickstart.md        # offline suite, live tests, deploy, walk table
├── contracts/
│   ├── pac-provider.md  # resolves_by_resubmitting, SIMULATED_PAC_RFC, MultifacturasProvider
│   └── configuration.md # settings, startup refusals, per-instance values
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
pyproject.toml, uv.lock                  # mictlanix-cfdi from git; cryptography ≥50.0.2
app/core/config.py                       # PAC_PROVIDER multifacturas, MULTIFACTURAS_*, check_pac_settings
app/services/pac/
├── __init__.py                          # get_pac_provider builds MultifacturasProvider
├── provider.py                          # resolves_by_resubmitting; SIMULATED_PAC_RFC = 'SIM010101SI0'
├── simulated.py                         # resolves_by_resubmitting = False
└── multifacturas.py                     # NEW: MultifacturasProvider
app/services/fiscal_stamping.py          # refresh: resubmission branch (R4); _apply_status: 72 h (R5)
migrations/035_simulated_pac_rfc.sql (+ _rollback.sql)
tests/fakes/pac.py                       # ScriptedPac gains resolves_by_resubmitting
tests/unit/test_pac_multifacturas.py     # NEW: translation and errors (R3)
tests/unit/test_pac_settings.py          # refusals (SC-006)
tests/unit/test_fiscal_stamping.py       # resubmission outcomes; 72 h cases
tests/integration/test_stamp_flow.py, test_cancel_flow.py   # resubmission and 72 h end to end
tests/live/test_multifacturas_live.py    # NEW, opt-in
.env.example, README.md (deployment), CHANGELOG.md,
docs/specs/10-fiscal-documents.md, docs/data-dictionary.md
```

**Structure Decision**: this is the existing single FastAPI project, and only the files above change. The provider lives beside the simulator in `app/services/pac/`, as spec 039 intended.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| `MultifacturasProvider` class | The spec's purpose: the contract implemented by the real PAC | Using the library's clients directly in the service would spread the translation and error mapping through `fiscal_stamping` |
| `resolves_by_resubmitting` contract attribute | Refresh must choose between lookup and resubmission (FR-008, FR-011) | Making `find_stamp` stamp hides a fiscal operation and still needs the service change for rejections (R4) |
| New dependency `mictlanix-cfdi` | The PAC and SAT clients, built for this in step 2 (#160) | Writing the clients in mbe-api would duplicate the library |
