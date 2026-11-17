# Implementation Plan: CFDI stamping and cancellation against a simulated PAC

**Branch**: `039-cfdi-stamping-cancellation` | **Date**: 2026-11-17 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/039-cfdi-stamping-cancellation/spec.md`

## Summary

Sealed invoices, payment receipts and credit notes (specs 027 and 028) get the rest of their fiscal lifecycle: stamping, SAT's two-step cancellation, a way to resolve documents left in flight, and their state in lists and reads. All PAC traffic goes through a `PacProvider` Protocol that mbe-api owns ([contracts/pac-provider.md](contracts/pac-provider.md)). In this step a simulated provider, which keeps its state in the database, fulfils that Protocol.

**Approach:**
- **Stamping** runs in three steps (research R3): lock the document and mark it `stamping_since`, then commit; call the PAC with no lock held; then lock again and store the stamp. A timeout leaves the marker, which only a look-up resolves.
- **Cancellation** records a `fiscal_document_cancellation` row before calling the PAC, then follows what SAT reports (R8).
- **The `live()` predicate is unchanged**, so a stamping or pending document keeps holding units and balances until SAT cancels it.
- **Refresh**: an on-demand endpoint and a cron job resolve stamping documents and open cancellation requests.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, lxml (already used by `cfdi_invoice`). No new dependency.

**Storage**: MariaDB mbe_dev (shared with the current system). Migration 034 adds two columns (`fiscal_document.stamping_since`, `fiscal_document.cancelling_since`) and two tables (`fiscal_document_cancellation`, `pac_simulation`). Tests use SQLite via `Base.metadata.create_all`.

**Testing**: pytest, pytest-asyncio, httpx `ASGITransport`. Existing seal fixtures: `invoicing`, `receipts`, `notes` (module-local), and the helper `seed_fiscal_document` (tests/integration/seed.py).

**Target Platform**: Linux server (xolotl): dev on :8765 in tmux, test on :8766 under systemd with 4 workers.

**Project Type**: web service (REST API)

**Performance Goals**: stamp, cancel and refresh add one PAC round trip to two short transactions. The simulator answers in well under 100 ms. The job handles fewer than 100 waiting documents per run.

**Constraints**:
- No database lock is held during a PAC call.
- No document is stamped twice (SC-002, SC-004).
- No PAC credentials anywhere (FR-003).
- The simulator never runs with `APP_ENV=production`.
- Nothing writes to the current system (FR-031).

**Scale/Scope**:
- ~15k CFDIs stamped since 2024, about 25 a day, and ~525 cancelled since 2024.
- 3 new endpoints, 3 dev-only simulator endpoints, 1 job, 1 migration and 12 error codes.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|---|---|---|
| I. Simplicity First | Pass, with justified additions | Each new piece answers a stated requirement. The Protocol is the step-3 swap (FR-001, FR-004). The simulator is FR-005 to FR-009. The cancellation table is FR-023, and its status changes are not kept as separate rows (R8). The stamping marker is a column, not a table (R2) |
| II. Think Before Coding | Pass | The decisions and their alternatives are in research R1–R15. Open items for #160 are named: the look-up by document, the PAC timeout versus the 5-minute grace, the SAT acceptance rules |
| III. Surgical Changes | Pass | `live()` is untouched. Confirm changes only at the receipt and credit-note invoice locks, which gain `populate_existing`. Reopen gains a row lock and a check of the locked row (R15 rule 8), which FR-011 needs under concurrency. `_payable` and `_creditable` gain one condition each, with no signature change. Confirm and reopen don't get audit added (R10) |
| IV. Goal-Driven Execution | Pass | quickstart.md lists every check, mapped to SC-001 to SC-007 |
| V. Reuse Over Rebuild | Pass | Reuses `cfdi.parse`, `tfd_original_string`, the print context, `busy_as_conflict`, `incidences.record`, the job pattern, the `get_mailer`-style dependency and migration 033's virtual unique column. The new entities are justified below |
| VI. Async-First | Pass | Provider methods are async. The simulator uses its own `AsyncSessionLocal` session |
| VII. Security by Default | Pass | Every route is authenticated, with the existing fiscal-document privilege (no facility scope, as today). Simulator routes answer 404 unless `PAC_PROVIDER=simulated`, and need Update on 23. Credentials and CSD material stay out of errors and logs (R14) |
| VIII. Ruff Compliance | Pass | Gate in quickstart.md |
| Testing / Changelog | Pass | Test files are listed in quickstart.md. A CHANGELOG `Added` entry covers the endpoints, job, settings and migration 034 |

Re-checked after Phase 1 design: no new violations.

## Project Structure

### Documentation (this feature)

```text
specs/039-cfdi-stamping-cancellation/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R15
├── data-model.md        # migration 034, states, response fields
├── quickstart.md
├── contracts/
│   ├── pac-provider.md  # the interface #160 implements
│   ├── endpoints.md     # routes, outcomes, simulator scenarios (handoff source)
│   └── error-codes.md   # additions to spec 036
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
migrations/
├── 034_cfdi_stamping_cancellation.sql
└── 034_cfdi_stamping_cancellation_rollback.sql
app/
├── core/config.py                  # app_env, pac_provider (+ startup check helper)
├── core/error_codes.py             # 12 new codes
├── main.py                         # lifespan refuses simulated+production
├── api/v1/router.py                # includes the simulator router (404 per request unless simulated)
├── models/fiscal.py                # stamping_since, cancelling_since, FiscalDocumentCancellation, PacSimulation
├── schemas/fiscal.py               # status values, stamp, cancellations[], request bodies
├── services/pac/
│   ├── __init__.py                 # get_pac_provider()
│   ├── provider.py                 # Protocol, types, errors
│   └── simulated.py                # SimulatedPac
├── services/fiscal_stamping.py     # stamp, cancel, refresh (two-phase flows)
├── services/fiscal_document_service.py  # document_status order, status filter, detail fields
├── services/invoice_service.py     # _payable/_creditable check cancelling_since; confirm's invoice locks get populate_existing; reopen locks (R15)
├── services/print_contexts.py      # simulated banner flag
├── rendering/templates/fiscal_document.html  # banner
├── api/v1/endpoints/fiscal_documents.py      # /stamp, /cancel, /refresh
├── api/v1/endpoints/pac_simulator.py         # dev-only controls
└── jobs/resolve_fiscal_documents.py
tests/
├── fakes/pac.py                    # ScriptedPac
├── unit/test_pac_simulated.py, test_pac_settings.py, test_pac_provider.py, test_fiscal_stamping.py, test_resolve_fiscal_documents.py
├── unit/ (extended) test_fiscal_document_service.py, test_invoice_service.py, test_print_contexts.py, test_fiscal_schemas.py; integration/ (extended) test_print_render.py
├── integration/test_stamp_flow.py, test_cancel_flow.py, test_resolve_job.py, test_fiscal_stamping_http.py
└── api/test_fiscal_stamping.py, test_pac_simulator.py
docs/data-dictionary.md             # stamping_since, cancelling_since and the two new tables
docs/specs/10-fiscal-documents.md   # "New MBE: stamping and cancellation (spec 039)" section
specs/036-localizable-error-codes/contracts/error-codes.md  # new rows; lock.busy resource list gains fiscal_document
```

**Structure Decision**: These follow the existing single-service layout. The PAC code lives in its own package, `app/services/pac/`, because step 3 replaces what is inside it and nothing outside.

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|---|---|---|
| `PacProvider` Protocol and types, with `get_pac_provider`, `require_provider` and `check_pac_settings` | FR-001 and FR-004: #160 implements it, and step 3 swaps it without other changes | Calling the simulator directly would make step 3 touch every caller |
| `fiscal_document_cancellation` table | FR-023: a document can have several requests, each with an outcome. FR-020: "pending" must be stored | Columns on `fiscal_document` hold only one request and lose the history |
| `fiscal_document.stamping_since` and `cancelling_since` | FR-011 and FR-020a: durable in-flight markers on the row a lock delivers (R15) | No stamping marker means a timeout cannot be told apart from sealed, and the document could be stamped twice. Without `cancelling_since`, receipt and note confirm would have to query the request table under a lock, which takes gap locks and deadlocks with cancel (R15) |
| `pac_simulation` table | FR-007: the simulator's state must survive across 4 workers and the job process, and outlive the caller's rollback | In-memory state splits across workers and is lost on restart |
| Simulator routes | FR-006 / SC-001: every outcome is reachable through the API alone | A comment marker can't express receiver answers and pollutes the clerk's text (R6) |
| `app_env` setting | FR-008: no production flag exists today | `debug` doesn't mean "not production" (R5) |
| `services/fiscal_stamping.py` (stamp, cancel, refresh) | FR-010, FR-017, FR-024: two-phase flows that commit before and after a network call | Putting them in `invoice_service` would mix PAC calls into a module whose confirm holds the series lock; receipts and notes would need the same code three times |
| `SimulatedPac` | FR-005 to FR-007: the provider this step runs on | No existing provider exists; `ScriptedPac` is a test fake, not a dev tool |
| `jobs/resolve_fiscal_documents.py` | FR-025: periodic resolution | No existing job refreshes fiscal documents; it reuses the job pattern of `send_scheduled_reports` |
| Schemas: cancel request, `FiscalDocumentStamp` (the nested `stamp` object, in the style of `FiscalDocumentRelationResponse`), `FiscalDocumentCancellationResponse`, `PacSimulationResponse`, `PacSimulationUpdate`, `ReceiverAnswer` | FR-017, FR-027, FR-006: request and response shapes for the new routes and fields | The existing fiscal schemas have no cancellation or simulator fields; the nested `stamp` follows the file's one-class-per-nested-object style |

## Deployment notes (each needs confirmation)

1. Apply migration 034 to mbe_dev.
2. **dev :8765.** Add `APP_ENV=development` and `PAC_PROVIDER=simulated` to `.env`. Deploying the branch itself is pre-authorized.
3. **test :8766** (after merge). Add `APP_ENV=staging` and `PAC_PROVIDER=simulated`, then restart.
4. **Cron** on dev and test: `*/10 * * * * cd <instance> && uv run python -m app.jobs.resolve_fiscal_documents`.
5. **After merge.** Open the mbe-ui handoff issue for mbe-roadmap#52, #54 and #56. Its source is contracts/endpoints.md: routes, states, codes and how to drive each scenario.

## Open items for #160 (not blockers here)

- How the real PAC implements `find_stamp`: Facturación México looks up a stamp by UUID (`acuseSAT`), and a look-up by document must be confirmed.
- The PAC's timeout must stay under the 5-minute look-up grace (R3).
- SAT's receiver-acceptance rules, beyond spec 10's 24-hour simplification, come from the PAC's answers (FR-019).
