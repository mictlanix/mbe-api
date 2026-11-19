# Research: Connect stamping and cancellation to the real PAC

**Spec**: [spec.md](spec.md) | **Date**: 2026-11-18

Sources read for this research:
- **The library**: mictlanix/cfdi at `4b41821b7947adc71a32a535ed5bb87dbd325b77`, the `pac/` and `sat/` packages and its spec 003.
- **The legacy system**: the current system (`mbe/Web`) and its PAC client (`dfacture-client`).
- **mbe-api's own code**: spec 039, `app/services/pac/` and `app/services/fiscal_stamping.py`.
- **mbe_dev**, read-only.
- **xolotl**.

## R1. The dependency and how the servers reach it

**Decision**
- **The dependency:** declare `mictlanix-cfdi` in `pyproject.toml`, with a `[tool.uv.sources]` entry `{ git = "ssh://git@github.com/mictlanix/cfdi.git", rev = "4b41821b7947adc71a32a535ed5bb87dbd325b77" }`. `uv.lock` records the commit.
- **Server access:** the servers read the repository with a **read-only deploy key** on mictlanix/cfdi. On xolotl that key is the `mictlanix` user's existing `~/.ssh/id_ed25519.pub`, which today has no GitHub access (`ssh -T git@github.com` answers "Permission denied (publickey)", measured 2026-11-18).
- **Documentation:** the README's deployment section records:
  - how to add the deploy key;
  - how to move the pin: change `rev`, then run `uv lock`.

**Rationale**
- **No PyPI, no tags.** The repository is private and not on PyPI, and it has no tags yet. A full commit SHA is the only pin that cannot move.
- **SSH for developers.** Developers already reach GitHub over SSH; the local check authenticates.
- **Least privilege.** A deploy key grants read access to this one repository and nothing else. It needs no token in the URL, in `uv.lock` or in `.env`.

**Alternatives considered**
- **HTTPS with a token:** rejected, because the token ends up in the lock file or in the environment.
- **Vendoring the built wheel from `dist/`:** rejected. The wheel loses its provenance, and every library fix would need a copied file.
- **A tag (`v0.1.0`):** better than a SHA once it exists. If the user tags the library first, the plan uses the tag; nothing else changes.

**Consequences**
- The library requires `cryptography>=50.0.2`. mbe-api locks 49.0.0, so the lock moves to 50.x. `lxml` 6.1.3 already satisfies the library.
- Moving `cryptography` touches python-jose and the CSD sealing, so the full suite must pass on the new lock (the T-gate in tasks).

## R2. The provider and its test seam

**Decision**
- **Constructor:** `MultifacturasProvider(pac: PacClient, sat: SatStatusClient)` in `app/services/pac/multifacturas.py`. It holds the library's two clients, which are its seam.
- **Construction from settings:** `get_pac_provider()` builds it from settings, with `MultifacturasPac(MultifacturasSettings(...))` and `SatConsultaClient()`.
- **Offline tests:** these pass the library's own `SimulatedPac` and `SimulatedSatStatusClient`. Those already reach stamped, rejected, unavailable, timeout, accepted, pending and the receiver's answers. A small scripted fake covers what the simulators cannot:
  - `SatRejected`, `SatUnavailable` and `SatTimeout`;
  - a `ValidationError` from `CancellationRequest`;
  - a byte-level check that the resubmission is identical.

**Rationale**
- **The constructor is the public seam.** The library's only internal seam is `mictlanix.cfdi._soap._post`, a private function. Patching it from mbe-api would couple our tests to the library's internals.
- **Reuse.** Using the library's simulators follows constitution principle V.
- **The wire is tested elsewhere.** The library's tests already cover the SOAP mapping, and the opt-in live tests (R8) cover the wire.

**Alternatives considered**
- **Monkeypatching `_soap._post`:** rejected as private coupling.
- **httpx mocking:** not possible, because the library uses `http.client`.

## R3. Translation table

| Contract (mbe-api) | Library | Rule |
|---|---|---|
| `stamp(sealed_xml: str)` | `stamp(bytes)` | `sealed_xml.encode('utf-8')`. The stored text always encodes to the same bytes, which FR-009 relies on. |
| `Stamp.uuid` | `stamp.timbre.UUID` | already upper case |
| `Stamp.stamped_at` | `timbre.FechaTimbrado` | naive, Mexico City local time, as the TFD writes it; any `tzinfo` is dropped after conversion to Mexico City |
| `Stamp.sat_seal`, `sat_certificate`, `provider_rfc` | `SelloSAT`, `NoCertificadoSAT`, `RfcProvCertif` | as is |
| `Stamp.xml: str` | `stamp.xml: bytes` | `.decode('utf-8')` |
| `Csd(certificate, key, password: bytes)` | `CsdCredentials(certificate, private_key, password: str)` | `password.decode('utf-8')`; all 17 stored passwords are printable ASCII (measured without reading them) |
| `CancellationRequest.substitution` | `substitute_uuid` | as is |
| `reason` | `reason` | as is |
| `issuer_rfc`, `recipient_rfc`, `uuid`, `total` | same names | as is; Multifacturas ignores `recipient_rfc` and `total` |
| `CancellationAnswer.ACCEPTED/PENDING` | same names | by name; Multifacturas never returns `NOT_CANCELLABLE` |
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
- **No catch-all:** the provider adds no handler for any other exception. The library documents only these (constitution principle I: no handling for impossible cases).

## R4. Resolving a document left `stamping` (FR-008 to FR-011)

**Decision**
- **A contract attribute.** The contract gains a class attribute, `resolves_by_resubmitting: bool`. The simulator sets `False`; Multifacturas sets `True`.
- **The refresh's stamping branch** in `fiscal_stamping.refresh` (for documents left `stamping`):
  - **`False` (simulator):** unchanged. `find_stamp`, and after the grace, sealed again (spec 039).
  - **`True` (Multifacturas):**
    - **Within the grace:** while the marker is younger than `STAMP_LOOKUP_GRACE` (5 minutes), the refresh does not call the PAC and returns the document as it is, still `stamping`. This avoids racing the first request, which the PAC may still be processing.
    - **After the grace:** it calls `provider.stamp(xml)` with the stored sealed XML.
      - A stamp goes to `_store_stamp`, with the incidence reason `CFDI stamp resolved {uuid}`.
      - `PacRejected` clears the marker through `_clear_marker`, so the document is sealed again (only if the marker is still the one read), and raises `pac.rejected`.
      - `PacUnavailable` or `PacTimeout` leaves the marker and raises `pac.unavailable` or `pac.timeout`.
- **No "not found" path.** With this provider, `_not_stamped` (sealed again on "not found") is never reached.

**Rationale**
- **The PAC call stays visible.** It stays in `fiscal_stamping`, where every other PAC call and every state change lives.
- **The contract stays honest.** `find_stamp` keeps its documented promise to never stamp.
- **One extra attribute** is the smallest change that lets the service choose.

**Alternatives considered**
- **Making Multifacturas' `find_stamp` stamp:** rejected. It breaks the contract's "never stamps", it hides a fiscal operation, and the service would still need to change to clear the marker on a rejection.
- **A new protocol method `resolve_stamp`:** rejected. It is more surface for the same choice.
- **Resubmitting at once, with no grace:** rejected. The first request may still be inside the PAC, where a concurrent duplicate could answer 307 without the document. The grace is spec 039's, reused.

**Concurrency**
- **Two resolutions.** Two refreshes, or a refresh and the job, can both resubmit. The PAC answers both with the same stamp.
- **One write wins.** `_store_stamp`'s write rules (spec 039 R15 rule 6) store it once, and the second finds the document issued.
- **A rejected resubmission racing a successful one:** `_clear_marker` is conditioned on the marker, and `_store_stamp` already cleared it, so the document stays issued.

## R5. The 72-hour wait for a 201 (FR-014)

**Decision**
- **New constant:** `CANCELLATION_SAT_WAIT = timedelta(hours=72)` in `fiscal_stamping`.
- **The rule:** in `_apply_status`, the case where SAT answers valid with cancellation `none` closes the request as `failed` ("Cancellation not received by SAT") only when one of these holds:
  - the request is `requested` (no PAC answer), and 5 minutes have passed since `requested_at` (unchanged);
  - the request is `pending` (the PAC answered 201), and 72 hours have passed since `answered_at` (new).
- **Otherwise** it only stamps `last_checked_at`.
- **Transaction 1 of refresh** also reads `answered_at`.

**Rationale**
- **The user's answer.** The user chose 72 hours, SAT's deadline for the receiver, on 2026-11-18.
- **No provider check needed.** The rule applies whatever the provider. The simulator never answers valid/`none` for a request it answered pending, so its behavior does not change. Only spec 039's two tests that assert the immediate closure change: `test_a_result_that_closes_the_request[not_received]` and the integration test in `test_cancel_flow.py`. Each gains a "within 72 hours, nothing changes" case and an "after 72 hours" case.

**Alternatives considered**
- **Applying the wait only for Multifacturas:** rejected. A provider attribute would add surface, and it would make no difference.

## R6. Configuration and startup checks (FR-016 to FR-019)

**Decision**: the `Settings` fields.

| Setting | Type, default | Notes |
|---|---|---|
| `PAC_PROVIDER` | `none` \| `simulated` \| `multifacturas`, `none` | |
| `MULTIFACTURAS_USER` | `str \| None`, None | required with `multifacturas` |
| `MULTIFACTURAS_PASSWORD` | `SecretStr \| None`, None | required with `multifacturas`; never logged |
| `MULTIFACTURAS_PRODUCTION` | `bool`, False | must equal `APP_ENV == 'production'` |
| `MULTIFACTURAS_URL` | `str`, `https://ws.multifacturas.com` | the library refuses non-HTTPS |
| `MULTIFACTURAS_CONNECT_TIMEOUT` | `float`, 10 | seconds |
| `MULTIFACTURAS_READ_TIMEOUT` | `float`, 60 | seconds |

`check_pac_settings` (already called at startup and by the resolve job) gains three refusals, each naming settings, never values:
- `PAC_PROVIDER=multifacturas` without a user or password;
- `MULTIFACTURAS_PRODUCTION=true` with `APP_ENV` not production;
- `MULTIFACTURAS_PRODUCTION=false` with `APP_ENV=production` and `PAC_PROVIDER=multifacturas`.

**Other settings decisions**
- **SAT's status client** keeps the library's defaults: SAT's public URL, with 10 s connect and 60 s read timeouts. The spec asks for settings only for the PAC.
- **Bad endpoint or timeouts.** `MultifacturasSettings` raises `ValueError` for a non-HTTPS URL or a non-positive timeout. `check_pac_settings` builds it once at startup, so a bad value refuses the start too. The message names `MULTIFACTURAS_URL` or the timeout.

**Rationale**
- **Global, as legacy.** The current system holds one global account (R-legacy below), and the spec follows it.
- **Mode must match the instance.** Tying the mode to `APP_ENV` closes the hole the current system has, where every request goes to production.

**Alternatives considered**
- **Per-issuer credentials in the database:** rejected, because nothing in the current system uses them.
- **A separate `MULTIFACTURAS_MODE` enum:** rejected, because the library takes a bool.

## R7. The simulator's RFC and migration 035

**Decision**
- **The constant:** `SIMULATED_PAC_RFC = 'SIM010101SI0'`.
- **Migration** `migrations/035_simulated_pac_rfc.sql` and its rollback, on mbe_dev only. It rewrites three places (the rollback swaps the values):
  - `fiscal_document.rfc_pac`, where it is `SIM010101SIM`;
  - `fiscal_document_xml.data` of those documents, with `REPLACE(data, 'RfcProvCertif="SIM010101SIM"', 'RfcProvCertif="SIM010101SI0"')`;
  - `pac_simulation.stamped_xml`, with the same `REPLACE`.
- **Tests:** they assert through the constant, except four literal cases that change to the new value: `test_pac_provider`, `test_fiscal_document_service`, `test_print_contexts` and `test_print_render`.

**Rationale**
- **Measured on 2026-11-18:** 17 documents (63907–63929, issuer DRA2110148A1) and 19 `pac_simulation` rows.
- **The simulator reads its stored XML.** `SimulatedPac.find_stamp` returns `pac_simulation.stamped_xml`, so it must carry the new RFC too, or a re-found stamp would bring back the old one.
- **The seal stays valid.** The simulated SAT seal is not a real signature, so rewriting the attribute invalidates nothing.

**Order of the walk**
- **Dev and test share mbe_dev.** Running the migration while either server runs the old code would make that server stop recognizing its simulated documents.
- **So the migration runs in one step with the deploy:** migrate, then restart dev and test on the new code.
- **Confirmation:** both the migration and the test deploy need the user's confirmation.

## R8. Live tests (FR-024)

**Decision**: `tests/live/test_multifacturas_live.py`, with a module-level `skipif` on the variables below.

| Test | Variables | What it checks |
|---|---|---|
| Stamp and duplicate | `MULTIFACTURAS_USER`, `MULTIFACTURAS_PASSWORD` (the library's own names) | a document sealed with SAT's public test CSD (EKU9003173C9, already in mbe-api's test fixtures) is stamped twice, with the same UUID |
| Cancellation | same | cancelling that stamp is rejected with SAT 205 in test mode, and the provider maps it to `PacRejected('205', …)` |
| SAT status | `SAT_LIVE_VALID`, `SAT_LIVE_CANCELLED`, each `issuer|recipient|uuid|total` | the provider reads `valid` and `cancelled` |

- **Where the SAT values come from:** they are the current system's own documents, chosen at walk time from mbe_dev and never committed.
- **Why it's offline by default:** live tests never run without their variables, so the default suite stays offline.

## R9. The walk on test (SC-001, SC-002)

**Prerequisites**, each confirmed with the user:
1. The deploy key on mictlanix/cfdi.
2. Test-mode credentials in test's `.env`, together with `PAC_PROVIDER=multifacturas` and `MULTIFACTURAS_PRODUCTION=false`. `APP_ENV=staging` is already set.
3. Migration 035 and the deploy of dev and test.

**Steps**
1. Seal and stamp one invoice, one payment receipt and one credit note for a real issuer.
2. Cancel each with 02, and with 01 plus a substitute.
3. Record each answer in quickstart.

**Open risk:** Multifacturas' test mode may refuse a document sealed with a real issuer's CSD under the demo account.
- **If it does,** the walk records that answer and stamps with SAT's test issuer EKU9003173C9 instead.
- **That needs** a test issuer and its test CSD on mbe_dev, which are writes needing confirmation.
- **Not settled in advance:** this is decided at the walk, not before.

**What the walk sends to the PAC:** the cancellation requests send the issuer's real CSD (certificate, key, password) to the PAC's test endpoint. That is the same PAC that receives them in production, and the current system sends them on every cancellation.

## R-legacy. The current system's configuration

- **One global account.**
  - `FacturacionMexicoUser`, `FacturacionMexicoPassword` and `FacturacionMexicoInProduction` live in `Web.config` and are read in `WebConfig.cs:197-211`.
  - The production flag is ignored: `DFactureClient40.cs` hard-codes `produccion="SI"` for both stamping (`:130`) and cancellation (`:249`).
- **The per-issuer column is unused.** `taxpayer_issuer.provider` (all 8 issuers = 4) is copied onto the document (`FiscalDocumentsController.cs:1555`) and never used to choose a PAC or credentials.
- **The CSD is per issuer.** Each issuer's certificate, key and password come from its own records (`CFDHelpers40.cs:60, 92`).
- **The switch was global.** All five issuers that stamp moved from CCF1011111K9 to LSO1306189R5 between 2025-05-13 and 2025-05-14 (mbe_dev).
- **No recovery and no status query.** The client has no retry, no handling of 307 and no query of SAT's status. `acuseSAT` exists (`GetStamp`) but is never called.
