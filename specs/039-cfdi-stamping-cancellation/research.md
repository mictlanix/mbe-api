# Research: CFDI stamping and cancellation against a simulated PAC

Spec: [spec.md](spec.md). Code references are to `origin/main` at ab19e5e.

## R1. Stamping is its own action after Confirm

**Decision.** `POST /fiscal-documents/{id}/stamp` stamps a **sealed** document. `confirm` keeps sealing, exactly as specs 027 and 028 define it (`invoice_service.confirm`, :753).

**Rationale.**
- A sealed document is already a state the API exposes, filters and reopens. Stamping it is the one step that can fail in ways the clerk must see apart from sealing failures: the PAC's rejection, unavailability or a timeout.
- A confirm that also stamped would have to call the PAC inside the confirm transaction, which holds the series lock (`_next_serial`, :655) for the length of a network call. Or it would need the same two-phase handling as a separate stamp anyway.
- The UI can call both from one button.

**Alternatives.**
- Stamp inside confirm, as the current system does. Rejected: it either holds locks during a network call, or duplicates R3's two-phase handling inside confirm.

## R2. States, and what stores them

| State | Stored as | `live()` (spec 027) |
|---|---|---|
| draft | `completed=0`, no XML | no |
| sealed | `completed=0`, XML, `stamping_since` NULL | yes |
| **stamping** | `completed=0`, XML, `stamping_since` set | yes, unchanged |
| issued | `completed=1`, `cancelled=0`, no open cancellation request | yes |
| **cancellation_pending** | `completed=1`, `cancelled=0`, an open `fiscal_document_cancellation` | yes, unchanged |
| cancelled | `cancelled=1` | no |

**Decision.**
- Add one nullable column, `fiscal_document.stamping_since`, and one table, `fiscal_document_cancellation`.
- `document_status()` (fiscal_document_service.py:27) checks, in order: cancelled; completed with an open request (cancellation_pending); completed (issued); `stamping_since` (stamping); XML (sealed); otherwise draft.
- The list filter (:237) gains the two new values.
- The `live()` predicate (:96) and every rule built on it need **no change**. A stamping document has XML and a pending one is completed, so both already count as live. That covers:
  - refunds (`customer_refund_service._invoiced_live`);
  - remaining quantity;
  - receipts' previous balance (`invoice_settlements`);
  - credit-note limits.
- Both stay live until `cancelled=1`, which is FR-020.
- The value `issued` keeps its name (spec Assumptions).

**Rationale.** The existing guards fall out of the status order:
- `_require_draft` (invoice_service.py:131) refuses anything but draft;
- reopen (:983) refuses anything but sealed.

So a stamping document can be neither edited nor reopened, with no new guard (FR-011, FR-015).

**Alternatives.**
- A `fiscal_document_stamping` table, one row while in flight. Rejected: it holds the same single fact as a nullable column, at the cost of a join on every status read.
- A status column. Rejected: legacy derives state from `completed` and `cancelled`, and the current system shares the table; a second source of truth could disagree with them.

The current system ignores the new column and table (spec FR-031).

## R3. Stamping never reaches the PAC twice

**Decision: three steps, with the PAC call outside any transaction.**
1. **Transaction A.**
   - Lock the document row (`SELECT … FOR UPDATE`, with `busy_as_conflict` → `lock.busy`).
   - Require sealed: a draft gets `fiscal_document.not_sealed`; issued, cancellation pending or cancelled return the document unchanged; stamping gets `fiscal_document.stamping`.
   - Re-check the 72-hour rule (`ISSUE_DATE_OUT_OF_RANGE`).
   - Set `stamping_since = now`, then commit.
2. **Call** `provider.stamp(sealed_xml)`.
3. **Transaction B.** Lock the row again.
   - **Stamped:** if the document is still uncompleted and its XML is still the XML that was sent:
     - store `stamp_uuid`, `stamped`, `authority_digital_seal` (SAT seal), `authority_certificate_number` and `rfc_pac`;
     - replace the XML with the stamped XML;
     - set `completed=1` and clear `stamping_since`;
     - record the audit incidence.
   - **Rejected or unavailable:** clear `stamping_since`, so the document is sealed again, and raise `pac.rejected` or `pac.unavailable`.
   - **Timeout:** leave `stamping_since` set and raise `pac.timeout`.

**Rationale.**
- The commit in step 1 is the durable "in flight" record that FR-011 requires: a crash or timeout after it leaves a document that only a look-up resolves.
- Concurrent requests serialize on the row lock. The second one finds `stamping_since` set and never calls the PAC (FR-012, SC-002).
- No lock is held during the network call.

**Look-up grace.** A refresh that hears "not stamped" returns the document to sealed only when `stamping_since` is older than **5 minutes**. Otherwise it leaves the document stamping. This keeps the periodic job from resetting a document while the request that stamps it is still waiting on the PAC.

If the PAC's answer arrives after a reset, step 3 still applies the stamp, because the document is uncompleted and its XML unchanged. 5 minutes is longer than any PAC timeout the real client is expected to use; #160 confirms that.

**Alternatives.**
- Hold the row lock across the PAC call. Rejected: it holds a row lock for seconds per stamp, and a timeout still leaves the outcome unknown.
- Rely on the PAC rejecting duplicates (SAT error 307, "previously stamped"). Rejected as the mechanism, because the spec forbids stamping again. #160 may still map a 307 to the existing stamp as a safety net.

## R4. The PAC provider contract

**Decision.** A `PacProvider` Protocol in `app/services/pac/provider.py`, with typed results and three exception classes. The full contract is in [contracts/pac-provider.md](contracts/pac-provider.md).

| Operation | Input | Result |
|---|---|---|
| `stamp` | sealed XML | `Stamp` (UUID, stamp date, SAT seal, SAT certificate number, provider RFC, stamped XML) |
| `find_stamp` | sealed XML | `Stamp` or `None` |
| `request_cancellation` | `CancellationRequest`: issuer and recipient RFC, UUID, total, reason, substitute, the issuer's CSD | `CancellationAnswer`: `accepted`, `pending` or `not_cancellable` |
| `query_status` | issuer and recipient RFC, UUID, total | `SatStatus`: valid or cancelled, cancellability, cancellation status |

Errors: `PacRejected(code, message)`, `PacUnavailable`, `PacTimeout`.

**Notes.**
- `find_stamp` takes the sealed XML because issuer, series, folio and the issuer's seal are all in it. The real PAC finds the stamp by whichever of those it supports.
- Facturación México's `acuseSAT` takes a UUID. #160 must confirm a look-up by document, or implement it by another means that does not stamp twice (spec Assumptions).
- `request_cancellation` takes the issuer's CSD (certificate, key, password) because the current PAC needs them to sign the request (`DFactureClient40.Cancel`). These are the issuer's certificate, not PAC credentials, and the caller reads them from `taxpayer_certificate` as confirm does. PAC credentials (user and password) live only in the provider's settings (FR-003).
- `query_status` takes what SAT's status service needs: issuer RFC, recipient RFC, total and UUID.

**Choosing the provider.**
- Settings: `pac_provider: Literal['none', 'simulated'] = 'none'`. Step 3 adds the real value.
- `get_pac_provider()` is a FastAPI dependency in the style of `get_mailer()` (app/services/mail.py:89). It returns `None` when no provider is configured, and stamp, cancel and refresh then answer `pac.not_configured` (503), as mail does.
- The job builds the provider with the same function.

## R5. Refusing the simulator in production

**Decision.**
- Add `app_env: Literal['development', 'staging', 'production'] = 'production'`.
- `lifespan` (app/main.py:47) refuses to start when `pac_provider == 'simulated'` and `app_env == 'production'`, raising an error that names both settings.
- The job runs the same check before it starts.

**Rationale.** The default fails closed: an instance that forgets `APP_ENV` is treated as production, and the simulator cannot run there.

**Deployment changes** (each needs confirmation):
- dev: `APP_ENV=development`, `PAC_PROVIDER=simulated` in `.env`;
- test: `APP_ENV=staging`, `PAC_PROVIDER=simulated`;
- the test suite sets `APP_ENV=development`.

**Alternatives.** Key the check on `debug`. Rejected: debug is about error output, and test runs without it.

## R6. The simulated PAC

**Decision.** `SimulatedPac` in `app/services/pac/simulated.py`.

- **State.** It keeps its state in a table, `pac_simulation`, and writes it in its **own** session, committed at once.
  - It is a table because test runs 4 workers and the job is a separate process, so in-memory state would be lost or split between them.
  - It uses its own session because a timeout in which the simulator did stamp must persist even though the API's transaction never stores that stamp (FR-007).
- **One row per document**, keyed by issuer RFC, series and folio (all read from the sealed XML). The row holds:
  - UUID, stamped XML, stamp date;
  - SAT status (`valid` or `cancelled`), cancellation status, cancellability;
  - the next chosen stamp outcome and the next chosen cancellation outcome, with an optional PAC code and message.
- **TFD 1.1.**
  - `Version` 1.1, a fresh `uuid4` in upper case, `FechaTimbrado` now in Mexico City time;
  - `SelloCFD` copied from the sealed document;
  - `SelloSAT` 344 random base64 characters, the size of an RSA-2048 seal;
  - `NoCertificadoSAT` `00001000000000000000`;
  - `RfcProvCertif` **`SIM010101SIM`**.
  - It is inserted into the sealed XML's `cfdi:Complemento` and parsed back by the existing `cfdi.parse()`, so the PDF, QR and email paths stay as they are.

**Outcomes** apply once to the next call, then revert to the default:

| Operation | Outcomes | Default |
|---|---|---|
| stamp | `stamped`, `rejected` (code, message), `unavailable`, `timeout_stamped`, `timeout_not_stamped` | `stamped` |
| cancellation | `accepted`, `pending`, `not_cancellable`, `rejected` (code, message), `unavailable`, `timeout` | spec 10's 24-hour rule (below) |
| receiver answer, for a pending cancellation | `accept` → cancelled; `reject` → valid, cancellation `rejected`; `expire` → cancelled, cancellation `expired` | – |

- **Default cancellation rule.** Accepted at once when the stamp is less than 24 hours old, otherwise pending (`docs/specs/10-fiscal-documents.md` §2, Business Rules).
- **The cancellation `timeout` outcome** records the request as received: SAT status valid, cancellation `pending`. A later status query then shows a request in process, so the refresh path is exercised.

**The simulated provider RFC.**
- `SIM010101SIM` has the shape of a company RFC (3 letters, a date, 3 characters), so every field validator accepts it.
- It spells "SIM" twice, so anyone reading the XML or PDF sees it is not a real provider.
- The RFC of a real PAC, even one found in SAT's examples, would mislead a reader of the document.
- `is_simulated(document)` is `rfc_pac == SIMULATED_PAC_RFC`. No flag column is needed.

**Alternatives.**
- An RFC like `AAA010101AAA`. Rejected: it is SAT's generic test *taxpayer*, not a provider.
- Choosing outcomes with a marker in the document's comment. Rejected: the comment is the clerk's text and travels to the PDF, and a marker can't express "the receiver accepts".

## R7. Choosing a simulated outcome through the API

**Decision.** Dev-only routes under `/api/v1/pac-simulator`, mounted only when `pac_provider == 'simulated'`. Otherwise they return 404 because the routes do not exist.

- `GET /pac-simulator/fiscal-documents/{id}`: the simulator's record for the document.
- `PUT /pac-simulator/fiscal-documents/{id}`: `{stamp?, cancellation?, pac_code?, pac_message?}` sets the next outcome. The document must be sealed or later.
- `POST /pac-simulator/fiscal-documents/{id}/receiver`: `{answer: accept | reject | expire}` answers a pending cancellation.

All three need Update on Fiscal Documents (23) and the document's facility in scope, like the routes they drive. They take the document id, which the UI knows, and the simulator maps it to issuer, series and folio. The routes are listed in [contracts/endpoints.md](contracts/endpoints.md); the handoff for mbe-ui repeats them.

## R8. Cancellation

**Request** (`POST /fiscal-documents/{id}/cancel`, `{reason, substitution?}`):

1. **Transaction A.** Lock the document row and refuse in this order:
   - not issued (draft, sealed, stamping, cancelled) → `fiscal_document.not_issued`;
   - an open request → `fiscal_document.cancellation_open`;
   - an unknown reason → `fiscal_document.unknown_cancellation_reason`;
   - reason 01 with no substitute → `fiscal_document.substitution_required`;
   - reason 01 with a substitute that is not a stamped, live document (issued or cancellation pending) of the same issuer and recipient → `fiscal_document.substitution_invalid` (clarification 2026-11-16, option C);
   - an invoice with a live payment receipt or credit note related to it → `fiscal_document.has_live_documents`, with `{documents: ["A123", …]}`. "Related" means a `fiscal_document_relation` whose `relation` is this invoice and whose `document` is of type 200 or 100.

   Then insert a `fiscal_document_cancellation` row with status `requested` and commit.
2. **Call** `provider.request_cancellation(...)`, with the CSD of the issuer's latest active certificate, as confirm chooses it.
3. **Transaction B.**

   | Answer | Request status | Document |
   |---|---|---|
   | `accepted` | `accepted` | cancelled: `cancelled=1`, `cancellation_date`, `cancellation_reason`, `cancellation_substitution` |
   | `pending` | `pending` | stays completed, so it reads as cancellation_pending |
   | `not_cancellable` | `not_cancellable` | issued; raise `fiscal_document.not_cancellable` |
   | `PacRejected` | `rejected`, with the PAC code and message | issued; raise `pac.rejected` |
   | `PacUnavailable` | `failed` | issued; raise `pac.unavailable` |
   | `PacTimeout` | stays `requested` | cancellation_pending; raise `pac.timeout`. A refresh resolves it |

**Keeping new receipts and notes off an invoice being cancelled.** The row lock in step 1 is released before the PAC call, so a payment receipt or credit note could be confirmed against the invoice meanwhile. To stop that:
- `_payable` (invoice_service.py:1076) and `_creditable` (:1425) also refuse an invoice with an open cancellation request, using their existing codes `invoice_not_payable` and `invoice_not_creditable`.
- Receipt and note confirms already lock their invoices (:1355, :1612) and re-run those checks.

**Refresh** (`POST /fiscal-documents/{id}/refresh`, and the job): applies to stamping documents and open requests.
- **Stamping:** `find_stamp`. A stamp is stored as in R3 step 3. `None` returns the document to sealed only past the 5-minute grace.
- **Open request:** `query_status`. The document follows SAT (FR-021):
  - SAT `cancelled` → document cancelled; the request becomes `accepted`, or `expired` when SAT says so.
  - SAT `valid` with cancellation `rejected` → request `rejected`; document issued.
  - SAT `valid` with cancellation `none` → request `failed`; document issued. SAT never received it.
  - Anything else → still pending; `last_checked_at` updated.
- **Errors:** unavailable or timeout leave the document as it was; the on-demand refresh then raises the error.
- **Refused:** any other state → `fiscal_document.nothing_to_refresh` (409).

**Status answers.** FR-023's "each SAT status answer" is kept as each **change** of status:
- the request row's timestamps: `requested_at`, `answered_at`, `resolved_at`, `last_checked_at`;
- one audit incidence per change (R10).

Recording every unchanged poll would add a row every 10 minutes per pending document and say nothing new.

**Expired.** SAT closes a cancellation the receiver never answered as cancelled. The API takes whatever SAT reports and keeps no clock of its own (spec Edge Cases).

## R9. Error codes (spec 036)

New members in `app/core/error_codes.py`, added to spec 036's contract (checked by `tests/unit/test_contract_docs.py`). The full table is in [contracts/error-codes.md](contracts/error-codes.md).

- **Reused:**
  - `fiscal_document.not_sealed` when stamping a draft;
  - `fiscal_document.not_issued` when cancelling anything but an issued document;
  - `fiscal_document.issue_date_out_of_range`;
  - `invoice_not_payable` and `invoice_not_creditable`;
  - `lock.busy`.
- **New `fiscal_document.*`:**
  - 409: `stamping`, `cancellation_open`, `has_live_documents`, `not_cancellable`, `nothing_to_refresh`;
  - 422: `unknown_cancellation_reason`, `substitution_required`, `substitution_invalid`.
- **New `pac.*`:**
  - `rejected` 422, with `{pac_code, pac_message}`;
  - `unavailable` 503;
  - `timeout` 504, with `{state}`: `stamping` or `cancellation_pending`;
  - `not_configured` 503.

## R10. Audit

**Decision.** `incidences.record(source=SourceType.FISCAL_DOCUMENT, …)` for each state change, in the same transaction as the change. The reasons:
- `CFDI stamped {uuid}`;
- `CFDI stamp found {uuid}`;
- `CFDI stamp not found, sealed again`;
- `Cancellation requested ({reason})`;
- `Cancellation pending the receiver`;
- `CFDI cancelled`;
- `Cancellation rejected`;
- `Cancellation not received by SAT`.

Rejections and unavailability change nothing and are not audited. The job records as the system employee (`ensure_system_employee`).

Confirm and reopen record no audit today. This spec does not add it to them (surgical).

## R11. XML, PDF and email

- Already done: the XML download needs `completed`, and the PDF needs `stamp_uuid` and reads the TFD, original string and QR from the stored XML (print_contexts.py:821). Both work for issued, cancellation-pending and cancelled documents. The cancelled stamp already prints (template :17).
- **New:** when `is_simulated`, the PDF prints a banner, "DOCUMENTO SIMULADO — SIN VALIDEZ FISCAL", in the same style as the cancelled one.
- **Email** keeps spec 028's rule: stamped and **not cancelled**, so issued and cancellation pending. Spec FR-016 is corrected to say so.

## R12. The periodic job

**Decision.** `app/jobs/resolve_fiscal_documents.py`, run as `uv run python -m app.jobs.resolve_fiscal_documents`, in the pattern of the existing jobs.

- It refreshes every stamping document and every open request, oldest first.
- Each document gets its own transaction. A failure is printed and the job moves on (FR-025).
- It exits 1 when any document failed.
- Suggested cron: `*/10 * * * *` on dev and test. Installing it is a deployment step that needs confirmation, as with `send_scheduled_reports`.

## R13. Proving the provider can be swapped (SC-006)

**Decision.** `tests/fakes/pac.py` holds `ScriptedPac`, an in-memory implementation of the Protocol that is not the simulator. The flow tests run against both the simulator and the fake, through `app.dependency_overrides[get_pac_provider]`. Nothing outside `app/services/pac/` imports the simulator, except the settings switch and the simulator routes.

## R14. Credentials (FR-003, SC-007)

The simulator needs no credentials. No PAC user, password or URL settings are added in this step; #46 adds them.

Two things never reach logs or errors: the issuer's CSD that is passed to `request_cancellation`, and the PAC's raw responses. `pac.rejected` carries only the PAC's code and message.
