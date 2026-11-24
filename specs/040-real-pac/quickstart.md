# Quickstart: Connect stamping and cancellation to the real PAC

How to prove spec 040 works. The contracts are in [contracts/](contracts/), and the decisions are in [research.md](research.md).

## 1. Offline suite

```bash
DATABASE_URL='sqlite+aiosqlite:///:memory:' JWT_SECRET_KEY=test-secret-test-secret-test-secret-0123 \
BUSINESS_TIMEZONE=America/Mexico_City uv run pytest -q
uv run ruff check app/ migrations/ tests/
```

**Expected:** all tests pass, and the live tests are skipped. The new tests cover:
- the adapter's translation and error mapping, over both providers (R3);
- the resubmission in each outcome (R4, SC-003);
- the 72-hour wait (R5);
- each startup refusal (SC-006);
- the database store, and the deterministic two-instance test, where one stamp is held while a second instance is refused (R10, R11 part 1, SC-009);
- every simulator route rule (contracts/simulator-controls.md).

## 2. Live tests (opt-in)

```bash
# Multifacturas test mode: stamp twice with the same UUID; record the cancellation's answer
MULTIFACTURAS_USER=… MULTIFACTURAS_PASSWORD=… \
SAT_TEST_CSD_DIR=../cfdi/tests/fixtures/csd SAT_TEST_CSD_PASSWORD=… \
  uv run pytest tests/integration/test_multifacturas_live.py -q -s
# SAT's status service: a valid and a cancelled UUID of the current system (issuer|recipient|uuid|total)
SAT_LIVE_VALID='…' SAT_LIVE_CANCELLED='…' uv run pytest tests/integration/test_multifacturas_live.py -q
```

The values are given on the command line and never committed. SAT's public test CSD (EKU9003173C9) is read from the folder `SAT_TEST_CSD_DIR` names, such as mictlanix/cfdi's `tests/fixtures/csd`; its password is the one SAT publishes with it. No CSD material is kept in this repository.

## 3. Deploy (each step confirmed with the user)

1. **Migration 037** on mbe_dev, then restart dev and test on the new code at once (they share mbe_dev, and the old code reads the dropped `pac_simulation`). Afterwards:
   - no `fiscal_document` or `fiscal_document_xml` row carries `SIM010101SIM`;
   - `pac_simulator_store` exists;
   - `pac_simulation` is gone.
2. **Dev** keeps `PAC_PROVIDER=simulated` and `APP_ENV=development` in its `.env`. Every instance's `.env` must name `PAC_PROVIDER`, since there is no default. Check that a simulated document's detail still says `stamp.simulated` true.
3. **Test's `.env`:** set `PAC_PROVIDER=multifacturas`, `MULTIFACTURAS_USER`, `MULTIFACTURAS_PASSWORD` and `MULTIFACTURAS_PRODUCTION=false`, keep `APP_ENV=staging`, then restart.
4. **Check:**
   - test starts;
   - a start with `MULTIFACTURAS_PRODUCTION=true` is refused;
   - the journal shows no credential;
   - the simulator routes answer 404 on test.

**Done 2026-11-19** (commit 4903bd0 on dev and test):
- **Migration 037:** applied from test's checkout, 4 statements.
- **Before it:** no document stamping, no open request, nothing queued in `pac_simulation` (21 rows).
- **After it:** the 19 simulated documents carry `SIM010101SI0` in `rfc_pac` and in their XML, none carry `SIM010101SIM`, `pac_simulator_store` exists, and `pac_simulation` is gone.
- **Restarts:** dev (tmux) and test (systemd) restarted, both active.
- **Old simulated documents:** invoice 63907 reads `stamp.simulated` true on both. Its simulator view reads `valid`/`none`, because the old simulator's state was dropped, as accepted.

## 4. Walk on test (SC-001, SC-002)

| Document | Stamp: UUID / provider RFC / PDF mark | Substitute stamped | Cancel 02: answer | Cancel 01 + substitute: answer |
|---|---|---|---|---|
| Invoice | _pending_ | _pending_ | _pending_ | _pending_ |
| Payment receipt | _pending_ | _pending_ | _pending_ | _pending_ |
| Credit note | _pending_ | _pending_ | _pending_ | _pending_ |

**Expected**
- **Stamping:** each document is issued with a UUID from the PAC, provider RFC `LSO1306189R5` or the one test mode answers, and no "DOCUMENTO SIMULADO" in its PDF.
- **Cancelling:** in test mode the expected answer is `pac.rejected` with `pac_code` 205. The request closes as rejected, and the document stays issued. Record whatever code arrives.
- **Refresh** of a timed-out stamp is exercised offline only; test mode cannot be made to time out on demand.

**Fallback:** if test mode refuses a real issuer's CSD, record its answer here and see research R9.

## 5. Driving each scenario (dev, `PAC_PROVIDER=simulated`; for the mbe-ui handoff)

`PUT …` and `POST …/receiver` are `/pac-simulator/fiscal-documents/{id}` and its `/receiver`. `stamp`, `cancel` and `refresh` are spec 039's endpoints. Set a stamp outcome **after** the document's last seal, and a cancellation outcome **after** its stamp.

| Scenario | Steps now | What changed from spec 039 |
|---|---|---|
| Stamped | `stamp` | Provider RFC `SIM010101SI0` |
| Rejected by the PAC | `PUT …{stamp:"rejected"}` → `stamp` → 422; still sealed | — |
| PAC unavailable | `PUT …{stamp:"unavailable"}` → `stamp` → 503; still sealed | — |
| Timeout, did stamp | `PUT …{stamp:"timeout_stamped"}` → `stamp` → 504, stamping → `refresh` → issued | — |
| Timeout, did not stamp | `PUT …{stamp:"timeout_not_stamped"}` → `stamp` → 504 → `refresh` after 5 min → sealed | — |
| Cancelled at once | `stamp` → `PUT …{cancellation:"accepted"}` → `cancel` → cancelled | The outcome needs the stamp first (409 `fiscal_document.not_stamped` otherwise). With nothing chosen, a stamp under 24 hours old is also cancelled at once. |
| Pending, then accepted | `stamp` → `PUT …{cancellation:"pending"}` → `cancel` → pending → `POST …/receiver {accept}` → `refresh` → cancelled | The outcome needs the stamp first |
| Pending, then rejected | same, with `{reject}` → `refresh` → issued; history shows `rejected` | The same |
| Pending, then expired | same, with `{expire}` → `refresh` → cancelled; history shows `expired` | The same |
| Not cancellable | `stamp` → `PUT …{cancellation:"not_cancellable"}` → `cancel` → 409; issued | The outcome needs the stamp first. **Permanent:** every later `cancel` of this document answers 409 too. Use a new document to cancel afterwards. |
| Cancellation rejected by the PAC | `stamp` → `PUT …{cancellation:"rejected"}` → `cancel` → 422 `pac.rejected`; issued; history shows `rejected` | The outcome needs the stamp first |
| PAC unavailable on cancel | `stamp` → `PUT …{cancellation:"unavailable"}` → `cancel` → 503; issued; history shows `failed` | The same |
| Timeout on cancel | `stamp` → `PUT …{cancellation:"timeout"}` → `cancel` → 504, cancellation_pending → `refresh` → pending (SAT has it) | The same |

- **No scenario is dropped**, and none needs a change in mictlanix/cfdi.
- **Concurrency on MariaDB (SC-009, R11 part 2), 2026-11-19:** run on test rather than dev, because test serves 4 uvicorn worker processes on the same mbe_dev, a stronger multi-process check than dev's single process plus a script, and how spec 039 measured SC-002. Invoice 63948 (A5269) was sealed and stamped by 20 concurrent requests: 6 × 200 (the issued document) and 14 × 409. The journal holds exactly **one** `pac simulator: stamp call DRA2110148A1 A5269`, and the document ended issued with one UUID (`922EC73E-…`).

**Run on dev, 2026-11-19 (commit 4903bd0): 16 of 16 passed.** Each row used a fresh invoice of customer 1 (XAXX010101000), issuer DRA2110148A1, series A, one line of product 903, sealed through the API:

| Scenario | Invoice | Result |
|---|---|---|
| Stamped | 63933 | issued, `SIM010101SI0`, simulated |
| Rejected by the PAC | 63934 | 422 `pac.rejected`; sealed |
| PAC unavailable | 63935 | 503; sealed |
| Timeout, did stamp | 63936 | 504, stamping → refresh → issued |
| Timeout, did not stamp | 63937 | 504; refresh at once → stamping; after 5 min → sealed |
| Stamp outcome while stamping | 63937 | 409 `fiscal_document.stamping` |
| Cancellation outcome before the stamp | 63938 | 409 `fiscal_document.not_stamped` |
| Cancelled at once (chosen) | 63939 | cancelled |
| Cancelled at once (default, under 24 h) | 63940 | cancelled |
| Pending, then accepted | 63941 | cancellation_pending → receiver → refresh → cancelled; history `accepted` |
| Pending, then rejected | 63942 | → issued; history `rejected` |
| Pending, then expired | 63943 | → cancelled; history `expired` |
| Not cancellable, twice | 63944 | 409 `fiscal_document.not_cancellable` both times; issued |
| Cancellation rejected by the PAC | 63945 | 422 `pac.rejected`; issued; history `rejected` |
| PAC unavailable on cancel | 63946 | 503; issued; history `failed` |
| Timeout on cancel | 63947 | 504, cancellation_pending → refresh → still pending; history `pending` |

Invoice 63932 was stamped first to check the recipe. Invoices 63932–63948 stay on mbe_dev as sample data.
- **A stamp outcome while the document is `stamping`** answers 409 `fiscal_document.stamping`. Set it before the stamp, or after the refresh.
- **`GET …` and every `PUT` or `POST` response** now return only `uuid`, `sat_status` and `cancellation_status`, without `next_stamp` and `next_cancellation`.

## 6. Confirm at the cutover (#57)

These can't be verified in test mode:
- SAT's answer to a non-cancellable document;
- `acuseSAT` on SAT's 307 in production;
- a real 201 → pending or cancelled → the receiver's answer;
- the answer to wrong credentials;
- assumption A3: the duplicate check comes before validation.
