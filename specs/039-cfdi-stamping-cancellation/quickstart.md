# Quickstart: validating spec 039

## Prerequisites

- Worktree `../mbe-api-039` on branch `039-cfdi-stamping-cancellation`, with `uv sync`.
- Test environment:

  ```bash
  export DATABASE_URL='sqlite+aiosqlite:///:memory:' JWT_SECRET_KEY=test-secret-test-secret-test-secret-0123 \
         BUSINESS_TIMEZONE=America/Mexico_City
  ```

## 1. Automated checks

```bash
uv run pytest -q                       # whole suite, including the new files below
uv run ruff check app/ migrations/ tests/
```

| New test file | Proves |
|---|---|
| `tests/unit/test_pac_simulated.py` | TFD shape, one-shot outcomes, timeout with and without a stamp, receiver answers, the 24-hour default |
| `tests/unit/test_pac_settings.py` | Refuses to start with `simulated` + `production`; `none` answers `pac.not_configured` |
| `tests/unit/test_pac_provider.py` | `repr(Csd)` hides the key and password |
| `tests/unit/test_resolve_fiscal_documents.py` | The job's branches: no provider, refused in production, a failure doesn't stop the others |
| `tests/unit/test_invoice_service.py`, `test_print_contexts.py` (extended) | `_payable`/`_creditable` with an open cancellation; the simulated flag |
| `tests/integration/test_print_render.py` (extended) | The simulated banner on the rendered PDF |
| `tests/unit/test_fiscal_document_service.py` (extended) | Status order for stamping and cancellation_pending |
| `tests/unit/test_fiscal_stamping.py` | The service's branches with a mocked session: check order, each PAC outcome's effect, the 5-minute grace at 4:59 and 5:01 |
| `tests/integration/test_stamp_flow.py` | Stamp, idempotent repeat, reject/unavailable leave sealed, timeout → refresh, 5-minute grace, a second stamp on a stamping document makes no PAC call (SC-002's rule; the concurrent run is §3) |
| `tests/integration/test_cancel_flow.py` | Every row of contracts/endpoints.md `/cancel`, the dependents rule, reason 01, freeing refunds and receipt balances (SC-005), receipt/note blocked during an open request |
| `tests/integration/test_resolve_job.py` | The job resolves stamping and pending documents; one failure doesn't stop the others; SC-003 |
| `tests/api/test_fiscal_stamping.py`, `tests/api/test_pac_simulator.py` | Routes in the patched-service style: happy path, 401, 403, 404, and each error code's mapping |
| `tests/integration/test_fiscal_stamping_http.py` | Real database over HTTP: the status filter for all six states, the cancellation history, and every row of "Driving each scenario" (SC-001) |
| Flow tests parametrised over `SimulatedPac` and `ScriptedPac` | SC-006 |
| `tests/unit/test_contract_docs.py` (existing) | The new codes match spec 036's contract |

SC-007 credential scan: `git grep -nIiE 'pac_(user|password)|FacturacionMexico(User|Password)' -- . ':!specs'` finds nothing, and a test asserts no log record from a full flow contains the CSD password.

## 2. On dev (xolotl :8765), after migration 034 is applied with confirmation

`.env` on dev: `APP_ENV=development`, `PAC_PROVIDER=simulated`. Then, using a token for a user with Update on 23:

1. Seal an invoice (existing spec 027 flow), then `POST /fiscal-documents/{id}/stamp`. Expect 200 and `status: issued`, with `stamp.provider_rfc = SIM010101SIM` and `simulated: true`.
2. `GET /fiscal-documents/{id}/pdf` shows the UUID, the seals, the QR and the "DOCUMENTO SIMULADO" banner.
3. Walk every row of contracts/endpoints.md "Driving each scenario" and check the state and error code after each step.
4. `uv run python -m app.jobs.resolve_fiscal_documents` resolves the documents left stamping or pending by step 3.
5. With `APP_ENV=production` and `PAC_PROVIDER=simulated`, the service refuses to start. Check this locally, not on dev.

Clean up the dev documents you created afterwards (each deletion needs confirmation).

## 3. On test (xolotl :8766, 4 workers), after merge and with confirmation

SC-002 needs a database that enforces row locks, so it is measured here. With `APP_ENV=staging` and `PAC_PROVIDER=simulated` in test's `.env`, seal one invoice and send 20 stamp requests at once:

```bash
seq 20 | xargs -P20 -I{} curl -s -o /dev/null -w '%{http_code}\n' -X POST -H "Authorization: Bearer $TOKEN" \
  https://test.api.mbe.mictlanix.com/api/v1/fiscal-documents/$ID/stamp | sort | uniq -c
```

Expect the document to end issued with one UUID, `GET /pac-simulator/fiscal-documents/$ID` to show that same UUID, and every response to be 200, 409 `fiscal_document.stamping` or 409 `lock.busy`.
