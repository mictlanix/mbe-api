# Research: Connect stamping and cancellation to the real PAC

**Spec**: [spec.md](spec.md) | **Date**: 2026-11-18 (simulator decision revised the same day)

Sources read for this research:
- **The library**: mictlanix/cfdi at `4b41821b7947adc71a32a535ed5bb87dbd325b77`: the `pac/` and `sat/` packages, including `pac/simulated.py` and `sat/simulated.py`, and its spec 003.
- **The legacy system**: the current system (`mbe/Web`) and its PAC client (`dfacture-client`).
- **mbe-api's own code**: spec 039 and its `contracts/endpoints.md`; `app/services/pac/`, `app/services/fiscal_stamping.py` and `app/api/v1/endpoints/pac_simulator.py`.
- **mbe_dev**, read-only.
- **xolotl** and mbe-ui's `origin/main`.

## R1. The dependency and how the servers reach it

**Decision**
- **The dependency:** declare `mictlanix-cfdi` in `pyproject.toml`, with a `[tool.uv.sources]` entry `{ git = "ssh://git@github.com/mictlanix/cfdi.git", rev = "4b41821b7947adc71a32a535ed5bb87dbd325b77" }`. `uv.lock` records the commit.
- **Server access:** the servers read the repository with a **read-only deploy key** on mictlanix/cfdi. On xolotl that key is the `mictlanix` user's existing `~/.ssh/id_ed25519.pub`, which today has no GitHub access (`ssh -T git@github.com` answers "Permission denied (publickey)", measured 2026-11-18).
- **Documentation:** the README records how to add the deploy key and how to move the pin (change `rev`, then `uv lock`).

**Rationale**
- **No PyPI, no tags.** The repository is private and not on PyPI, and it has no tags yet. A full commit SHA is the only pin that cannot move.
- **SSH for developers.** Developers already reach GitHub over SSH; the local check authenticates.
- **Least privilege.** A deploy key grants read access to this one repository and nothing else, with no token in the URL, in `uv.lock` or in `.env`.

**Alternatives considered**
- **HTTPS with a token:** rejected, because the token ends up in the lock file or in the environment.
- **Vendoring the built wheel:** rejected. The wheel loses its provenance, and every library fix would need a copied file.
- **A tag (`v0.1.0`):** better than a SHA once it exists. If the user tags the library first, the plan uses the tag; nothing else changes.

**Consequences**
- The library requires `cryptography>=50.0.2`. mbe-api locks 49.0.0, so the lock moves to 50.x. `lxml` 6.1.3 already satisfies it.
- Moving `cryptography` touches python-jose and the CSD sealing, so the full suite must pass on the new lock before anything else changes.

## R2. One adapter over the library's clients

**Decision**: `app/services/pac/adapter.py` holds `CfdiPacProvider`:

```python
CfdiPacProvider(pac: PacClient, sat: SatStatusClient, *, name: str, resolves_by_resubmitting: bool)
```

`get_pac_provider()` builds it from settings:

| `PAC_PROVIDER` | `pac` | `sat` | `name` | `resolves_by_resubmitting` |
|---|---|---|---|---|
| `simulated` | `SimulatedPac(DatabaseSimulatorStore())` | `SimulatedSatStatusClient(pac)` | `simulator` | False |
| `multifacturas` | `MultifacturasPac(MultifacturasSettings(...))` | `SatConsultaClient()` | `multifacturas` | True |

- **One translation path.** The adapter translates every value and error once, for both (R3).
- **The log line.** It logs `pac {name}: stamp call {issuer} {series}{folio}` at WARNING, one line per `stamp` call and never the XML. With `name='simulator'` that is exactly spec 039's line, which its quickstart counts (FR-027).
- **What goes away:** `app/services/pac/simulated.py` and its test `tests/unit/test_pac_simulated.py` are deleted.

**Rationale**
- **One PAC implementation.** The user decided the library is the API's only PAC implementation (2026-11-18). One adapter means the simulated path exercises the same translation as the real one.
- **The constructor is the seam.** Offline tests pass the library's simulators or a scripted fake. The library's only internal seam, `_soap._post`, is private, and patching it from mbe-api would couple the tests to the library's internals.

**Alternatives considered**
- **One adapter class per provider:** rejected. The two would be identical apart from their clients.
- **Keeping mbe-api's simulator beside the library:** rejected by the user's decision.

## R3. Translation table

| Contract (mbe-api) | Library | Rule |
|---|---|---|
| `stamp(sealed_xml: str)`, `find_stamp(sealed_xml: str)` | `stamp(bytes)`, `find_stamp(bytes)` | `sealed_xml.encode('utf-8')`. The stored text always encodes to the same bytes, which FR-009 relies on. |
| `Stamp.uuid` | `stamp.timbre.UUID` | already upper case |
| `Stamp.stamped_at` | `timbre.FechaTimbrado` | naive, Mexico City local time, as the TFD writes it and the library parses it; a `tzinfo`, if ever present, is converted to Mexico City and dropped |
| `Stamp.sat_seal`, `sat_certificate`, `provider_rfc` | `SelloSAT`, `NoCertificadoSAT`, `RfcProvCertif` | as is |
| `Stamp.xml: str` | `stamp.xml: bytes` | `.decode('utf-8')` |
| `Csd(certificate, key, password: bytes)` | `CsdCredentials(certificate, private_key, password: str)` | `password.decode('utf-8')`; all 17 stored passwords are printable ASCII (counted without reading them) |
| `CancellationRequest.substitution` | `substitute_uuid` | as is |
| `reason`, `issuer_rfc`, `recipient_rfc`, `uuid`, `total` | same names | as is. Multifacturas ignores `recipient_rfc` and `total`; the simulator checks them against the stamp (205 when they differ). |
| `CancellationAnswer` | `CancellationAnswer` | by name. Multifacturas returns only `ACCEPTED` (202) and `PENDING` (201, spec Clarifications); the simulator can also return `NOT_CANCELLABLE`. |
| `SatStatus.state` | `SatState` | value (`valid`, `cancelled`, `not_found`) |
| `SatStatus.cancellable` | `SatStatus.cancellability` | value (`without_acceptance`, `with_acceptance`, `not_cancellable`) |
| `SatStatus.cancellation` | `CancellationStatus` | value (`none`, `pending`, `accepted`, `rejected`, `expired`). The contract's `requested` is never produced. |

**Errors (FR-005)**

| Library | Contract |
|---|---|
| `pac.PacRejected(code, message)`, `sat.SatRejected(code, message)` | `PacRejected(code, message)` |
| `pac.PacUnavailable`, `sat.SatUnavailable` | `PacUnavailable` |
| `pac.PacTimeout`, `sat.SatTimeout` | `PacTimeout` |
| `ValidationError` while building `CancellationRequest` | `PacRejected(code=<first violation's rule>, message=<the library's summary>)`. The summary names rules and nodes, never values. |

- **How errors are raised:** each translated error is raised `from None`, so no library traceback text or payload rides along.
- **No catch-all:** the adapter adds no handler for other exceptions. The library documents only these (constitution I).

## R4. Resolving a document left `stamping` (FR-008 to FR-011)

**Decision**
- **A contract attribute.** The contract gains `resolves_by_resubmitting: bool`. The adapter sets it per provider (R2), and `ScriptedPac` lets tests set it.
- **The refresh's stamping branch** in `fiscal_stamping.refresh`:
  - **`False` (simulator):** unchanged. `find_stamp`, and after the grace, sealed again (spec 039).
  - **`True` (Multifacturas):**
    - **Within `STAMP_LOOKUP_GRACE` (5 minutes) of the marker:** no PAC call; the document is returned as it is, `stamping`.
    - **After the grace:** call `provider.stamp(xml)` with the stored sealed XML.
      - A stamp goes to `_store_stamp`, with the incidence reason `CFDI stamp resolved {uuid}`.
      - `PacRejected` clears the marker through `_clear_marker` (sealed again, only if the marker is still the one read) and raises `pac.rejected`.
      - `PacUnavailable` or `PacTimeout` leaves the marker and raises `pac.unavailable` or `pac.timeout`.

**Rationale**
- **The PAC call stays visible.** It stays in `fiscal_stamping`, beside every other PAC call and state change.
- **The contract stays honest.** `find_stamp` keeps its documented promise to never stamp.
- **The grace** avoids racing the first request inside the PAC.

**Alternatives considered**
- **Making Multifacturas' `find_stamp` stamp:** rejected. It hides a fiscal operation, and the service would still need to clear the marker on a rejection.
- **A new protocol method:** rejected as more surface for the same choice.

**Concurrency.** Two resubmissions get the same stamp from the PAC. `_store_stamp`'s write rules (spec 039 R15 rule 6) store it once. `_clear_marker` is conditioned on the marker, so a rejection racing a success leaves the document issued.

## R5. The 72-hour wait for a 201 (FR-012, FR-014)

**Decision**
- **201 is `PENDING`.** Multifacturas' 201 is `CancellationAnswer.PENDING`, as the user confirmed on 2026-11-18. Spec 039's `_apply_answer` already stores status `pending` with `answered_at`.
- **New constant:** `CANCELLATION_SAT_WAIT = timedelta(hours=72)` in `fiscal_stamping`.
- **The rule:** in `_apply_status`, SAT's valid/`none` closes the request as `failed` ("Cancellation not received by SAT") only when one of these holds:
  - the request is `requested` and 5 minutes have passed since `requested_at` (unchanged);
  - the request is `pending` and 72 hours have passed since `answered_at` (new).
- **Otherwise** only `last_checked_at` is written. Transaction 1 of refresh also reads `answered_at`.

**Rationale**
- **The user's answer.** The user chose 72 hours, SAT's deadline for the receiver.
- **The simulator is unaffected.** The library's simulator records every pending answer as pending at SAT, so it never answers valid/`none` for a pending request. Its behavior does not change.

**Accepted consequence.** The history reads "Cancellation pending the receiver" from the 201, even when SAT cancels at once. The user chose this over a new answer value.

**Tests that change:** spec 039's `test_a_result_that_closes_the_request[not_received]` and the integration test in `test_cancel_flow.py`. Each gains a case within 72 hours and one after.

## R6. Configuration and startup checks (FR-016 to FR-019)

**Decision**: the `Settings` fields.

| Setting | Type, default | Notes |
|---|---|---|
| `PAC_PROVIDER` | `none` \| `simulated` \| `multifacturas` \| None, None | must be set in `.env`; None (unset) refuses the start |
| `MULTIFACTURAS_USER` | `str \| None`, None | required with `multifacturas` |
| `MULTIFACTURAS_PASSWORD` | `SecretStr \| None`, None | required with `multifacturas`; never logged |
| `MULTIFACTURAS_PRODUCTION` | `bool`, False | must equal `APP_ENV == 'production'` |
| `MULTIFACTURAS_URL` | `str`, `https://ws.multifacturas.com` | the library refuses non-HTTPS |
| `MULTIFACTURAS_CONNECT_TIMEOUT` | `float`, 10 | seconds |
| `MULTIFACTURAS_READ_TIMEOUT` | `float`, 60 | seconds |

**Where the PAC is configured.** In each instance's `.env`, as the user required on 2026-11-18. An environment variable of the same name overrides it, as for every setting.
- **No default:** `PAC_PROVIDER` is typed `… | None = None` rather than a required field. A required field would fail at import, in every test, script and tool that loads `settings` without an `.env`.
- **Where unset refuses:** `check_pac_settings`, which the API's startup and the resolve job already call, refuses `None`.

**Startup refusals.** `check_pac_settings` refuses an unset `PAC_PROVIDER`, keeps the simulator's refusal in production, and gains three more. Each names settings, never values:
- `PAC_PROVIDER=multifacturas` without a user or password;
- `MULTIFACTURAS_PRODUCTION=true` with `APP_ENV` not production;
- `MULTIFACTURAS_PRODUCTION=false` with `APP_ENV=production` and `PAC_PROVIDER=multifacturas`.

**Keeping what spec 039's tests rely on:**
- the simulator refusal's text stays verbatim (`pac_provider=simulated is not allowed with app_env=production: …`), which `tests/unit/test_resolve_fiscal_documents.py:136` and `tests/integration/test_resolve_job.py:170` match;
- the check reads the `multifacturas_*` fields only when `pac_provider == 'multifacturas'`, because those tests pass settings as a `SimpleNamespace` with only `pac_provider` and `app_env`.

**Bad endpoint or timeouts.** `check_pac_settings` builds `MultifacturasSettings` once, so a non-HTTPS URL or a non-positive timeout refuses the start too, naming the variable.

**SAT's status client** keeps the library's defaults (SAT's public URL, 10 s connect, 60 s read), because the spec asks for settings for the PAC only.

**Alternatives considered**
- **Per-issuer credentials:** rejected, because nothing in the current system uses them (R-legacy).
- **A mode enum:** rejected, because the library takes a bool.

## R7. Migration 037: the simulator's store and its provider RFC

**Decision**: `migrations/037_pac_simulator_store.sql` and its rollback. Numbers 035 and 036 are taken on `origin/main` (`035_fiscal_document_usage_width.sql`, #342; `036_itinerary_stop_delivery_order.sql`).

**What the migration does**
1. **Creates `pac_simulator_store`:**
   - `store_key VARCHAR(100) NOT NULL PRIMARY KEY`; the library's keys are `seal:` + 64 hex characters, or `uuid:` + 36;
   - `value MEDIUMTEXT NOT NULL`; a stamped XML reaches 67,700 bytes on mbe_dev, beyond `TEXT`;
   - `updated_at DATETIME NOT NULL`.
2. **Drops `pac_simulation`.**
3. **Rewrites the old simulator's RFC** on the documents it stamped:
   - `fiscal_document_xml.data`: `REPLACE(data, 'RfcProvCertif="SIM010101SIM"', 'RfcProvCertif="SIM010101SI0"')`, for the documents whose `rfc_pac` is `SIM010101SIM`;
   - then `fiscal_document.rfc_pac`.

**The rollback** reverses step 3, recreates `pac_simulation` empty with migration 034's definition, and drops `pac_simulator_store`.

**Rationale**
- **The schema doesn't fit.** `pac_simulation` holds one typed row per document; the library needs string values under string keys. Reusing the table would mean replacing every column, which is a new table under an old name.
- **Converting the rows isn't worth it.** It would mean computing the library's private keys (`seal:` + SHA-256 of the issuer's seal) and record format in SQL, which couples the migration to the library's internals. Nothing is lost by dropping them. Measured on 2026-11-18:
  - no simulated document is `stamping`;
  - no cancellation request is open;
  - no outcome is queued (21 rows, `next_stamp` and `next_cancellation` all null).
- **The documents themselves keep their stamps and states.** A later cancellation of one of them is answered as for a UUID the simulator never stamped: pending, then the receiver's controls work (spec, Edge Cases).
- **The RFC must still be rewritten.** The mark follows the library simulator's RFC (FR-021), so documents from the old simulator need it to stay marked as simulated.

**Order of the walk**
- **Dev and test share mbe_dev.** The migration drops a table the old code reads.
- **So it runs in one step with the deploy of both:** migrate, then restart dev and test on the new code.
- **Confirmation:** both need the user's confirmation.

## R8. Live tests (FR-029)

**Decision**: `tests/integration/test_multifacturas_live.py`, with module-level `skipif` guards. It goes under `tests/integration/`, beside the other cross-component tests, rather than a new directory (analysis D1).

| Test | Variables | What it checks |
|---|---|---|
| Stamp and duplicate | `MULTIFACTURAS_USER`, `MULTIFACTURAS_PASSWORD` (the library's own names), `SAT_TEST_CSD_DIR`, `SAT_TEST_CSD_PASSWORD` | an invoice issued an hour ago, sealed with SAT's public test CSD (EKU9003173C9, read from the folder `SAT_TEST_CSD_DIR` names; mbe-api's fixtures carry only a self-signed certificate, which the PAC would refuse, and FR-007 keeps CSD material out of the repository), is stamped twice, with the same UUID |
| Cancellation | same | the cancellation's answer is recorded; the expected answer is a rejection with 205 in test mode, not a hard assertion |
| SAT status | `SAT_LIVE_VALID`, `SAT_LIVE_CANCELLED`, each `issuer|recipient|uuid|total` | the adapter reads `valid` and `cancelled` |

## R9. The walk on test (SC-001, SC-002)

**Prerequisites**, each confirmed with the user:
1. The deploy key on mictlanix/cfdi.
2. Migration 037 and the deploy of dev and test.
3. Test's `.env`: `PAC_PROVIDER=multifacturas`, the credentials and `MULTIFACTURAS_PRODUCTION=false`, keeping `APP_ENV=staging` (user's decision, 2026-11-18).

**Steps**
1. Stamp one invoice, one payment receipt and one credit note for a real issuer.
2. Stamp one more of each to serve as a substitute. Reason 01 requires a stamped, live document of the same issuer (spec 039).
3. Cancel each original with 02, then with 01 and its substitute.
4. Record every answer in quickstart.

**Open risk:** test mode may refuse a document sealed with a real issuer's CSD under the demo account.
- **If it does,** the walk records the answer and stamps with SAT's test issuer EKU9003173C9 instead.
- **That needs** a test issuer and its test CSD on mbe_dev, which are writes needing confirmation.
- **Decided at the walk,** not before.

**What the walk sends to the PAC:** the cancellations send the issuer's real CSD to the PAC's test endpoint. That is the same PAC that receives it in production, and the current system sends it on every cancellation.

## R10. The simulator's store on the database (FR-023)

**Decision**: `DatabaseSimulatorStore` in `app/services/pac/simulator_store.py` implements the library's `SimulatorStore` protocol (`async get(key) -> str | None`, `async set(key, value)`).
- **Model:** a `PacSimulatorStore` model in `app/models/fiscal.py`, replacing `PacSimulation`.
- **Sessions:** each `get` and `set` runs in its own session from the session factory (default `AsyncSessionLocal`, injectable for tests) and commits at once. A stamp the simulator issued survives even when the API never stores it, which a timeout needs (spec 039 FR-007, as its own simulator did).
- **Writes:** `set` writes with `merge` (insert or update by primary key) and sets `updated_at`.
- **Failures:** any exception escapes to the library, which turns it into `PacUnavailable` (`_get` and `_set` catch everything). The adapter then maps that to `pac.unavailable`.
- **The values are opaque.** mbe-api never parses them.

**Rationale**
- **One session per call** is the pattern spec 039's simulator used, for the same reason: a stamp issued during a timeout must survive.
- **`merge`** works the same on MariaDB and on the tests' SQLite.

**Alternatives considered**
- **A dialect-specific upsert** (`ON DUPLICATE KEY UPDATE`): it behaves differently under the tests' SQLite.
- **Advisory locks around each call:** unnecessary given R11.

## R11. Concurrency across instances (FR-024)

**Finding**: the library's `_locked` serializes calls within one `SimulatedPac` only. Each API worker process and the resolve job build their own instance (`get_pac_provider()` builds one per request). Spec 039's guard still prevents a double stamp, for three reasons:
1. **`stamp()` is guarded.** Under `_begin_locked` (`SELECT … FOR UPDATE`), it moves a sealed document to `stamping_since = marker` and commits before calling the PAC. A second request, in any process, locks the same row after that commit, reads `stamping`, and answers 409 `fiscal_document.stamping` without calling the PAC. Before the commit, it waits on the row lock.
2. **`refresh()` never stamps with the simulator.** It calls `find_stamp` (`resolves_by_resubmitting=False`), which only reads.
3. **A document returns to sealed only through `_clear_marker` or `_not_stamped`.** Both condition on the marker. A new stamp then needs a new request, which goes through point 1 again.

So at most one `stamp` call per document is in flight at any time, whatever the number of instances. Two instances of the library's simulator never stamp the same seal concurrently. The one other writer of a seal record, the `PUT {stamp}` route, refuses while the document is `stamping` (R12).

**Proof (SC-009), in two parts.** The offline suite can't race real transactions. Its database is one SQLite file on a single shared connection (`StaticPool`, `tests/integration/conftest.py`), and SQLite ignores `SELECT … FOR UPDATE`. A burst of concurrent requests there would not test the row lock. So:

1. **Offline, deterministic** (`tests/integration/test_stamp_flow.py`). It proves the marker logic across instances, which is the part that lives in code.
   - **Setup:** two `CfdiPacProvider`s over two `SimulatedPac` instances share one `DatabaseSimulatorStore`. The store is wrapped so that its first `set` of a `seal:` key waits on an `asyncio.Event`.
   - **The first request:** a stamp through provider A runs until it is held inside the simulator. The document is then `stamping`, committed.
   - **The second request:** a stamp through provider B answers 409 `fiscal_document.stamping` and never calls B's simulator.
   - **After release:** releasing the event lets A finish. Assert the document is issued with one UUID, the store holds one `seal:` and one `uuid:` record, and there is one `pac simulator: stamp call` line.
2. **Live on dev**, where MariaDB holds real row locks (T033, with the user's confirmation, since it stamps a dev document). Twenty concurrent stamp requests for one freshly sealed document come from two processes: half over HTTP to dev's API, half from a script in `/var/www/mbe-api-dev` that calls `fiscal_stamping.stamp` with its own simulator instance. Assert:
   - exactly one `pac simulator: stamp call` line across both processes' output;
   - one new `uuid:` row in `pac_simulator_store`;
   - one UUID on the document;
   - every request answered with the issued document or 409.

Spec 039's SC-002 was verified the same way, on test against MariaDB.

## R12. The simulator routes over the library (FR-025)

The routes in `app/api/v1/endpoints/pac_simulator.py` keep their paths, privileges, the per-request 404 when `PAC_PROVIDER` is not `simulated`, and the 409 `fiscal_document.not_sealed` for a draft.
- **Getting the simulator:** the routes take it from a new FastAPI dependency, `get_pac_simulator()`, which returns `SimulatedPac(DatabaseSimulatorStore())`. They build `SimulatedSatStatusClient(pac)` from it, over the same state as the provider's.
- **Why a dependency:** a route that built the store itself would always use the real database's session factory, and tests could not point it at theirs. Today the routes take the simulator from `get_pac_provider`, which `tests/integration/test_fiscal_stamping_http.py` overrides; the new dependency keeps that pattern.

| Route | The library call | Notes |
|---|---|---|
| `GET` | `SimulatedSatStatusClient.query_status(document.issuer, document.recipient, document.stamp_uuid, xml_total)` | For a stamped document: `uuid`, `sat_status` (`state`) and `cancellation_status` (`cancellation`). For one not yet stamped: `uuid` null, `sat_status` `not_found`, `cancellation_status` `none`, with no call. |
| `PUT {stamp}` | `SimulatedPac.configure_stamp(sealed_xml.encode(), outcome, code=, message=)` | 409 `fiscal_document.stamping` while the document is `stamping`, writing nothing. The library's instances don't lock each other, so writing the seal record while another process stores a stamp there could erase it (last write wins). Otherwise the stored XML of the document. For a document already stamped by the library, the outcome is accepted and has no effect, because the next stamp returns the existing stamp. For one stamped by the old simulator, the library answers 307. |
| `PUT {cancellation}` | `SimulatedPac.configure_cancellation(uuid, outcome, code=, message=)` | 409 `fiscal_document.not_stamped` when the document has no UUID |
| `PUT {stamp, cancellation}` | both | The stamp outcome is applied first. `pac_code` and `pac_message` go to whichever outcome is `rejected`. |
| `POST …/receiver` | `SimulatedPac.answer_as_receiver(uuid, answer)` | The library's `SIM01` ("no cancellation is pending") → 409 `fiscal_document.nothing_to_refresh`; no UUID → the same 409 |

- **Other library refusals** on these routes map through the adapter's error table (R3) to `pac.rejected`, `pac.unavailable` or `pac.timeout`.
- **The response:** `PacSimulationResponse` drops `next_stamp` and `next_cancellation` (user's decision, 2026-11-18). `PacSimulationUpdate`, the request body, does not change.

## R13. Every spec 039 scenario on the library

Each scenario of spec 039's "Driving each scenario" has an equivalent in the library. Two behave differently:
- **"Not cancellable"** is permanent for that UUID, not one-shot.
- **A stamp outcome** must be set after the last seal.

Both are accepted as closer to SAT, and listed in the spec's Overview table. No scenario is dropped, and nothing needs asking in mictlanix/cfdi. The full list, with how each is driven now, is in quickstart.md §5.

## R14. Checked against mbe-api's own sealed documents (2026-11-18)

**Where it ran:** read-only, outside the repository, with the library at `4b41821` (its own environment).

**Input:** three documents mbe-api sealed on mbe_dev, with their TFD removed:
- invoice 63907 (type I);
- credit note 63928 (type E);
- payment receipt 63931 (type P, Pagos 2.0).

**Results**
- **The input check:** the library's check accepts all three.
- **The library's `SimulatedPac` on each:**
  - it stamps with `RfcProvCertif` `SIM010101SI0`;
  - stamping the same sealed XML again returns the same UUID, and `find_stamp` returns it too;
  - `FechaTimbrado` is naive (no `tzinfo`);
  - SAT's simulated status reads `valid`, `without_acceptance` right after the stamp;
  - a reason-02 cancellation with nothing chosen is `ACCEPTED`, after which the status reads `cancelled`/`accepted`. This is spec 039's 24-hour rule, and it agrees with R3 and R12.

**Consequence:** no conversion of mbe-api's XML is needed before it reaches the library.

## R-legacy. The current system's configuration

- **One global account.**
  - `FacturacionMexicoUser`, `FacturacionMexicoPassword` and `FacturacionMexicoInProduction` live in `Web.config` and are read in `WebConfig.cs:197-211`.
  - The production flag is ignored: `DFactureClient40.cs` hard-codes `produccion="SI"` for both stamping (`:130`) and cancellation (`:249`).
- **The per-issuer column is unused.** `taxpayer_issuer.provider` (all 8 issuers = 4) is copied onto the document (`FiscalDocumentsController.cs:1555`) and never used to choose a PAC or credentials.
- **The CSD is per issuer.** Each issuer's certificate, key and password come from its own records (`CFDHelpers40.cs:60, 92`).
- **The switch was global.** All five issuers that stamp moved from CCF1011111K9 to LSO1306189R5 between 2025-05-13 and 2025-05-14 (mbe_dev).
- **No recovery and no status query.** The client has no retry, no handling of 307 and no query of SAT's status. `acuseSAT` exists (`GetStamp`) but is never called.
