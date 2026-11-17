# Quickstart: validating spec 039

## Prerequisites

- Worktree `../mbe-api-039` on branch `039-cfdi-stamping-cancellation`, with `uv sync`.
- Test environment:

  ```bash
  export DATABASE_URL='sqlite+aiosqlite:///:memory:' JWT_SECRET_KEY=test-secret-test-secret-test-secret-0123 \
         BUSINESS_TIMEZONE=America/Mexico_City APP_ENV=development PAC_PROVIDER=simulated
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
| `tests/unit/test_fiscal_document_service.py` (extended) | Status order for stamping and cancellation_pending |
| `tests/integration/test_stamp_flow.py` | Stamp, idempotent repeat, reject/unavailable leave sealed, timeout → refresh, 5-minute grace, 20 concurrent stamps → 1 PAC call (SC-002) |
| `tests/integration/test_cancel_flow.py` | Every row of contracts/endpoints.md `/cancel`, the dependents rule, reason 01, freeing refunds and receipt balances (SC-005), receipt/note blocked during an open request |
| `tests/integration/test_resolve_job.py` | The job resolves stamping and pending documents; one failure doesn't stop the others; SC-003 |
| `tests/api/test_fiscal_stamping.py` | stamp, cancel, refresh and simulator routes: happy path, 401, 404, 409, 422, privilege and facility |
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
