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
| issued | `completed=1`, `cancelled=0`, `cancelling_since` NULL | yes |
| **cancellation_pending** | `completed=1`, `cancelled=0`, `cancelling_since` set (an open `fiscal_document_cancellation` exists) | yes, unchanged |
| cancelled | `cancelled=1` | no |

**Decision.**
- Add two nullable columns, `fiscal_document.stamping_since` and `fiscal_document.cancelling_since`, and one table, `fiscal_document_cancellation`. `cancelling_since` is set with the request row and cleared when the request closes, in the same transaction, under the document lock (R15 rule 2). It keeps every in-flight state on the document row, where a lock delivers it, so no reader has to query the request table under a lock.
- `document_status()` (fiscal_document_service.py:27) checks, in order: cancelled; completed with `cancelling_since` set (cancellation_pending); completed (issued); `stamping_since` (stamping); XML (sealed); otherwise draft.
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
- reopen (:983) refuses anything but sealed. It gains a row lock and a check of the locked row's columns (R15 rule 8), so a stamp in flight cannot be undone by a concurrent reopen.

So a stamping document can be neither edited nor reopened, with no new guard (FR-011, FR-015).

**Alternatives.**
- A `fiscal_document_stamping` table, one row while in flight. Rejected: it holds the same single fact as a nullable column, at the cost of a join on every status read.
- A status column. Rejected: legacy derives state from `completed` and `cancelled`, and the current system shares the table; a second source of truth could disagree with them.

The current system ignores the new column and table (spec FR-031).

## R3. Stamping never reaches the PAC twice

**Decision: three steps, with the PAC call outside any transaction.**
1. **Transaction A.**
   - Begin locked (R15 rule 1) and read the state from the locked row and its XML (rule 3).
   - Require sealed: a draft gets `fiscal_document.not_sealed`; issued, cancellation pending or cancelled return the document unchanged; stamping gets `fiscal_document.stamping`.
   - Re-check the 72-hour rule (`ISSUE_DATE_OUT_OF_RANGE`).
   - Set `stamping_since = local_now().replace(microsecond=0)`, then commit. (`DATETIME` columns drop fractions, so a value kept with microseconds would never equal the stored one; R15 rule 11.)
2. **Call** `provider.stamp(sealed_xml)`.
3. **Transaction B.** Lock the row again (R15 rule 1). If it is already completed, or its XML changed, apply R15 rule 5.
   - **Stamped:** if the document is still uncompleted and its XML is still the XML that was sent:
     - store `stamp_uuid`, `stamped`, `authority_digital_seal` (SAT seal), `authority_certificate_number` and `rfc_pac`;
     - replace the XML with the stamped XML;
     - set `completed=1` and clear `stamping_since`;
     - record the audit incidence.
   - **Rejected or unavailable:** clear `stamping_since` only if it still equals the value transaction A set (as R15 rule 6 does for refresh), so the document is sealed again, and raise `pac.rejected` or `pac.unavailable`.
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
| `query_status` | issuer and recipient RFC, UUID, total | `SatStatus`: valid, cancelled or not found; cancellability; cancellation status |

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
- the tests need neither: they run with the defaults and switch `pac_provider` with `monkeypatch` where a test needs the simulator routes.

**Alternatives.** Key the check on `debug`. Rejected: debug is about error output, and test runs without it.

## R6. The simulated PAC

**Decision.** `SimulatedPac` in `app/services/pac/simulated.py`.

- **State.** It keeps its state in a table, `pac_simulation`, and writes it in its **own** session, committed at once.
  - It is a table because test runs 4 workers and the job is a separate process, so in-memory state would be lost or split between them.
  - It uses its own session because a timeout in which the simulator did stamp must persist even though the API's transaction never stores that stamp (FR-007).
  - The session factory is a constructor argument, `SimulatedPac(session_factory=AsyncSessionLocal)`. The integration tests pass their per-test `sessions` fixture, so the simulator writes to the same test database as the code under test.
- **One row per document.**
  - `stamp` and `find_stamp` key it by issuer RFC, series (`''` when the CFDI has none) and folio, read from the sealed XML.
  - `request_cancellation` and `query_status` find it by UUID. A UUID the simulator never stamped, such as a document the current system stamped, gets a row created with the issuer and UUID only and no stamp date. With no stamp date, the default cancellation rule treats it as stamped over 24 hours ago, so `pending`.
  - The simulator routes find the row by the document's `stamp_uuid` when it has one, otherwise by issuer, series and folio, and create it when missing.
- A row created from the simulator routes' `keys` stores the document's `stamp_uuid` when it has one, so a later `request_cancellation` for that UUID finds it.
- The row holds:
  - UUID, stamped XML, stamp date;
  - SAT status (`valid` or `cancelled`) and cancellation status;
  - the next chosen stamp outcome and the next chosen cancellation outcome, with an optional PAC code and message.
- **TFD 1.1.**
  - `Version` 1.1, a fresh `uuid4` in upper case, `FechaTimbrado` now in Mexico City time, from `local_now()`;
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
- A document is simulated when its provider RFC is `SIMULATED_PAC_RFC`: the detail read checks `rfc_pac`; the print context checks the stamped XML's `RfcProvCertif`, as it reads every stamp value from the XML. No flag column is needed.

**Alternatives.**
- An RFC like `AAA010101AAA`. Rejected: it is SAT's generic test *taxpayer*, not a provider.
- Choosing outcomes with a marker in the document's comment. Rejected: the comment is the clerk's text and travels to the PDF, and a marker can't express "the receiver accepts".

## R7. Choosing a simulated outcome through the API

**Decision.** Dev-only routes under `/api/v1/pac-simulator`. The router is always included, and a dependency, `require_simulator()`, answers 404 `request.route_not_found` (no params, before authentication, as an unknown route would) on every request unless `settings.pac_provider == 'simulated'` at that moment. Checking per request, not at import, lets tests switch the setting with `monkeypatch` and keeps a plain `pytest` run independent of environment variables.

- `GET /pac-simulator/fiscal-documents/{id}`: the simulator's record for the document.
- `PUT /pac-simulator/fiscal-documents/{id}`: `{stamp?, cancellation?, pac_code?, pac_message?}` sets the next outcome. The document must be sealed or later.
- `POST /pac-simulator/fiscal-documents/{id}/receiver`: `{answer: accept | reject | expire}` answers a pending cancellation.

All three need Update on Fiscal Documents (23), like the routes they drive (no facility scope, as for every fiscal-document route). They take the document id, which the UI knows, and the simulator maps it to issuer, series and folio. The routes are listed in [contracts/endpoints.md](contracts/endpoints.md); the handoff for mbe-ui repeats them.

## R8. Cancellation

**Request** (`POST /fiscal-documents/{id}/cancel`, `{reason, substitution?}`):

1. **Transaction A.** Begin locked (R15 rule 1); the checks are plain reads after the lock and use `live()` for the related documents. Refuse in this order:
   - not issued, i.e. the recomputed state is draft, sealed, stamping or cancelled → `fiscal_document.not_issued` (a `cancellation_pending` document passes this check and gets the next one);
   - an open request (`cancelling_since` set) → `fiscal_document.cancellation_open`;
   - an unknown reason → `fiscal_document.unknown_cancellation_reason`;
   - reason 01 with no substitute → `fiscal_document.substitution_required`. With reason 02–04 a substitute is ignored: it is not checked, not sent to the PAC and not stored;
   - reason 01 with a substitute that is not a stamped, live document (issued or cancellation pending) of the same issuer and recipient → `fiscal_document.substitution_invalid` (clarification 2026-11-16, option C);
   - an invoice with a live payment receipt or credit note related to it → `fiscal_document.has_live_documents`, with `{documents: ["A123", …]}`. "Related" means a `fiscal_document_relation` whose `relation` is this invoice and whose `document` is of type 200 or 100.
   - no certificate valid now (`_certificate(db, issuer, local_now())`) → `fiscal_document.no_valid_certificate`. The CSD is kept for the PAC call.

   Then insert a `fiscal_document_cancellation` row with status `requested`, set `cancelling_since`, record `Cancellation requested ({reason})` and commit.
2. **Call** `provider.request_cancellation(...)` with the CSD found in transaction A by confirm's `_certificate(db, issuer, local_now())` (invoice_service.py:633).
3. **Transaction B.** Begin locked (R15 rule 1) and apply the answer only if the request is still `requested` (R15 rule 7). Every answer that closes the request also clears `cancelling_since`.

   | Answer | Request status | Document |
   |---|---|---|
   | `accepted` | `accepted` | cancelled: `cancelled=1`, `cancellation_date`, `cancellation_reason`, `cancellation_substitution` |
   | `pending` | `pending` | stays completed, so it reads as cancellation_pending |
   | `not_cancellable` | `not_cancellable` | issued; raise `fiscal_document.not_cancellable` |
   | `PacRejected` | `rejected`, with the PAC code and message | issued; raise `pac.rejected` |
   | `PacUnavailable` | `failed` | issued; raise `pac.unavailable` |
   | `PacTimeout` | stays `requested` | cancellation_pending; raise `pac.timeout`. A refresh resolves it |

**`_apply_cancelled`** (shared by transaction B and refresh) writes the cancellation fields, clears `cancelling_since` and records the incidence. For a credit note it also keeps spec 033 FR-011, with the update reopen uses (invoice_service.py:991–1002), applied to the orders locked per R15 rule 10 (ids read before the PAC call; `_credited_orders` is not reused): `order_balances` before the write, flush, `order_balances` after, and `customer_payment_service.reopen(order, before, after)` for each order. The note is still live when `before` is computed, because `cancelled` is not yet set.

**Keeping new receipts and notes off an invoice being cancelled.** The row lock in step 1 is released before the PAC call, so a payment receipt or credit note could be confirmed against the invoice meanwhile. To stop that:
- `_payable` (invoice_service.py:1076) and `_creditable` (:1425) each gain one condition, `invoice.cancelling_since is None`, and refuse otherwise with their existing codes `invoice_not_payable` and `invoice_not_creditable`. Their signatures and their five call sites do not change.
- Receipt and note confirms already lock their invoices (:1352–1355, :1609–1612) and re-run those checks. The two lock statements gain `.execution_options(populate_existing=True)`, so the invoice object carries the locked row's `cancelling_since` and `cancelled` even when the request had loaded it earlier. A locking read returns the latest committed row, so a cancellation committed while the confirm waited is seen.

**Refresh** (`POST /fiscal-documents/{id}/refresh`, and the job): applies to stamping documents and open requests. It runs in three steps, like stamp and cancel:
1. **Transaction 1.** Begin locked (R15 rule 1), recompute the state (rule 3), and read `stamping_since`, the open request's **id** and status, the sealed XML and, for a credit note, the ids R15 rule 10 needs. Commit. A state with nothing to refresh answers `nothing_to_refresh` here.
2. **Call** `find_stamp` or `query_status`, with no transaction open.
3. **Transaction 2** per R15 rules 6 and 10.
- **Locking:** R15 rules 1 and 6 (begin locked, compare before writing).
- **Stamping:** `find_stamp`. A stamp is stored as in R3 step 3. `None` returns the document to sealed only past the 5-minute grace.
- **Open request:** `query_status`. The document follows SAT (FR-021):
  - Every result that closes the request clears `cancelling_since`.
  - A result that cancels a credit note runs the paid-flag update of R8's `_apply_cancelled` (below).
  - SAT `cancelled` → document cancelled; the request becomes `accepted`, or `expired` when SAT says so.
  - SAT `valid` with cancellation `rejected` → request `rejected`; document issued.
  - SAT `valid` with cancellation `none` → request `failed`; document issued. SAT never received it. For a `requested` request under 5 minutes old: unchanged (R15 rule 7).
  - SAT `valid` with cancellation `pending` on a `requested` request (a cancel that timed out) → request `pending`, `answered_at` set, incidence `Cancellation pending the receiver`.
  - SAT `not_found` → unchanged.
  - Every `query_status` answer, including the ones that close the request, sets `last_checked_at`.
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
  - `fiscal_document.not_issued` when cancelling a draft, sealed, stamping or cancelled document;
  - `fiscal_document.no_valid_certificate` when cancelling and the issuer has no certificate valid now;
  - `fiscal_document.issue_date_out_of_range`;
  - `invoice_not_payable` and `invoice_not_creditable`;
  - `lock.busy`.
- **New `fiscal_document.*`:**
  - 409: `stamping`, `cancellation_open`, `has_live_documents`, `not_cancellable`, `nothing_to_refresh`;
  - 422: `unknown_cancellation_reason`, `substitution_required`, `substitution_invalid`.
- **New `pac.*`:**
  - `rejected` 422, with `{pac_code, pac_message}`;
  - `unavailable` 503;
  - `timeout` 504, with `{state}`: the document's current state, normally `stamping` or `cancellation_pending` (contracts/error-codes.md);
  - `not_configured` 503.

## R10. Audit

**Decision.** `incidences.record(source=SourceType.FISCAL_DOCUMENT, …)` for each state change, in the same transaction as the change. The reasons:
- `CFDI stamped {uuid}`;
- `CFDI stamp found {uuid}`;
- `CFDI stamp not found, sealed again`;
- `Cancellation requested ({reason})`;
- `Cancellation pending the receiver`;
- `CFDI cancelled`;
- `Cancellation rejected` (the receiver rejected it);
- `Cancellation not received by SAT`;
- `Cancellation refused: not cancellable`;
- `Cancellation refused by the PAC ({pac_code})`;
- `Cancellation failed: PAC unavailable`;
- `CFDI stamp {uuid} not stored: the document changed` (R15 rule 5).

The three `Cancellation refused/failed` reasons close a request, so they are audited; the stamp-not-stored one is audited because the PAC issued a UUID the API did not keep. On stamp, a rejection or unavailability only clears the marker the same request set, and is not audited. The job records as the system employee (`ensure_system_employee`).

Confirm and reopen record no audit today. This spec does not add it to them (surgical).

## R11. XML, PDF and email

- Already done: the XML download needs `completed`, and the PDF needs `stamp_uuid` and reads the TFD, original string and QR from the stored XML (print_contexts.py:821). Both work for issued, cancellation-pending and cancelled documents. The cancelled stamp already prints (template :17).
- **New:** when `is_simulated`, the PDF prints a banner, "DOCUMENTO SIMULADO — SIN VALIDEZ FISCAL", in the same style as the cancelled one.
- **Email** keeps spec 028's rule: stamped and **not cancelled**, so issued and cancellation pending. Spec FR-016 is corrected to say so.

## R12. The periodic job

**Decision.** `app/jobs/resolve_fiscal_documents.py`, run as `uv run python -m app.jobs.resolve_fiscal_documents`, in the pattern of the existing jobs.

- It refreshes every stamping document and every open request, oldest first.
- Each document gets its own transaction. A failure is printed and the job moves on (FR-025).
- The work is in `async def run(provider, session_factory=AsyncSessionLocal) -> int`; `main()` builds the provider, calls `run` and exits with its result. Tests call `run` with the `sessions` fixture.
- It exits 1 when any document failed.
- Suggested cron: `*/10 * * * *` on dev and test. Installing it is a deployment step that needs confirmation, as with `send_scheduled_reports`.

## R13. Proving the provider can be swapped (SC-006)

**Decision.** `tests/fakes/pac.py` holds `ScriptedPac`, an in-memory implementation of the Protocol that is not the simulator. The flow tests call the service with each provider directly; only the HTTP test (`test_fiscal_stamping_http.py`) overrides `get_pac_provider`. Nothing outside `app/services/pac/` imports the simulator, except the settings switch and the simulator routes.

## R14. Credentials (FR-003, SC-007)

The simulator needs no credentials. No PAC user, password or URL settings are added in this step; #46 adds them.

Two things never reach logs or errors: the issuer's CSD that is passed to `request_cancellation`, and the PAC's raw responses. `pac.rejected` carries only the PAC's code and message.

## R15. Locking rules for stamp, cancel, refresh and reopen

Requests run under MariaDB's REPEATABLE READ, and sessions keep loaded objects (`expire_on_commit=False`). The route has already read the document (`get_document`) and the auth dependency has already read `user` (deps.py:59), so a snapshot is open before the service runs. The code already has the answer to both consequences in `begin_locked` (inventory_documents.py:282): commit the transaction the route opened, take the lock, and only then read.

**Rules.**
1. **Begin locked.** Each transaction of stamp, cancel and refresh starts with `await db.commit()`, then locks the document with `select(FiscalDocument)….with_for_update().execution_options(populate_existing=True)`, under `busy_as_conflict(db, 'The fiscal document is busy, retry', resource='fiscal_document')`. InnoDB opens a transaction's read view at its first plain read, which now comes after the lock, so every plain read that follows sees what was committed before the lock was granted. No other read needs to lock anything, so no gap locks are taken.
2. **One lock: the document's row.** Every writer of a document's request rows and of its in-flight columns (`stamping_since`, `cancelling_since`) holds that document's lock, so those rows need no lock of their own. Stamp, cancel and refresh lock only that document, except one case: when a credit note becomes cancelled, `_apply_cancelled` then locks its invoice and the invoice's orders in the note → invoice → orders order that `reopen` already uses (R15 rule 10). Stamp, cancel and refresh never take those locks in the reverse order. Receipt and credit-note confirm can: they lock the invoice and then, in `_next_serial`, S-lock every `fiscal_document` row of the issuer (the only index it can use is on `issuer`), which includes the note being cancelled. That cycle is a rare deadlock (1213); see rule 9. Receipt and note confirm lock their invoices (invoice_service.py:1352–1355, :1609–1612) and read the invoice's own `cancelling_since` column from the locked row, with no read of the request table (R8).
3. **State comes from the locked row, never from `document.status`.** `status` is not a column. `get_document` sets it in memory (fiscal_document_service.py:202), and `populate_existing` does not refresh it. After the lock, the state is recomputed with `document_status(locked, has_xml=<plain read of its XML>)`. Every in-flight state is a column on the row itself.
4. **Commit, then raise.** `busy_as_conflict` rolls back on any exception, so a transaction that writes and then reports an error commits first and raises after leaving the context manager.
5. **Stamp, transaction B.** Begin locked (rule 1). Then:
   - **already completed** (a refresh stored the stamp): return the document as it is;
   - **uncompleted, XML unchanged:** store the stamp;
   - **XML changed:** record the incidence `CFDI stamp {uuid} not stored: the document changed`, leave the document as it is and raise `pac.timeout {state}` with the document's current state (which can be `sealed` or `draft` here). The XML can only change after a refresh reset the document past the 5-minute grace and a user reopened it. A PAC client whose timeout is under the grace never reaches this branch (R3).
6. **Refresh** begins locked (rule 1) after the PAC answers. It writes only if what it read before calling the PAC is still true. Otherwise it changes nothing except the kept request's `last_checked_at` (R8: every status answer sets it) and returns the document as it now is:
   - **clearing the stamping marker:** only if `stamping_since` still equals the value read before `find_stamp`;
   - **applying a cancellation result:** only to the request whose id transaction 1 kept, and only if that request's status, re-read by id with a column select, is still the one read before `query_status`. A different open request with the same status is left alone.
   Two job runs on dev and test, which share mbe_dev, can then overlap safely.
7. **A `requested` request within the grace.** SAT `valid` with cancellation `none` on a `requested` request whose `requested_at` is under 5 minutes old leaves it unchanged, since the cancel that sent it may still be waiting on the PAC (`STAMP_LOOKUP_GRACE`, R3). Cancel's transaction B begins locked and applies the answer only if the request is still `requested`. Otherwise a refresh has already resolved it, and the cancel returns the document as it now is.
8. **Reopen** (invoice_service.py:983) runs inside the route's open transaction, so it does not commit first. It locks the document as rule 1 does (`populate_existing`) and requires, on the locked row's columns, `completed` false, `cancelled` false and `stamping_since` NULL before deleting the XML. A locking read returns the latest committed row, so a stamp that committed its marker meanwhile is seen and the reopen is refused (`not_sealed`). This keeps FR-011 when requests race. The change is the lock and that check at the start of `reopen`.
9. **What can still answer `lock.busy`:** a lock wait timeout (1205) on a document another request holds, or a deadlock with an unrelated flow that locks the same invoice or orders (1213). Both are retryable 409s on stamp, cancel and refresh (contracts/endpoints.md). **Known limit:** when the deadlock victim is a receipt or credit-note confirm or a reopen, which are not wrapped in `busy_as_conflict` today, that request answers 500 and can be retried. It needs a credit note being cancelled or reopened (rule 8 locks the note before its invoice) at the same moment as any confirm of the same issuer that has already locked that invoice. Wrapping those existing flows is outside this spec; nothing is left half-written, because the victim's transaction is rolled back.
10. **Cancelling a credit note: every lock before any plain read.** `_apply_cancelled` updates the paid flags of the orders on the note's invoice (R8), and those balances must be read after the order locks (customer_payment_service.py:487–491; spec 033 research R4). `_credited_orders` (invoice_service.py:1006) does a plain read of the relation before its locks, so it is not reused here. Instead:
   - the transaction before the PAC call (cancel's A, refresh's 1) reads the note's invoice id and that invoice's order ids. Both are fixed once the note is sealed: a sealed note's relation and a completed invoice's lines never change;
   - the transaction after the PAC call commits, then locks the note, its invoice and its orders by primary key, in that order and with `populate_existing`, before any plain read. Only then does it read the request status (rule 7 / rule 6) and compute `order_balances` before and after.
11. **Reading what an earlier transaction loaded.** Sessions keep objects across commits (`expire_on_commit=False`), and `populate_existing` is set only on the statements that ask for it. So after a lock, the request status and the XML are read with a column select (`select(FiscalDocumentCancellation.status)…`, `fiscal_document_service.get_xml`), or with an entity select that sets `populate_existing`. They are never read from an object an earlier transaction loaded or inserted, which would show what that transaction saw, not what the row now holds. For the same reason every new timestamp (`stamping_since`, `cancelling_since`, `requested_at`, `answered_at`, `resolved_at`, `last_checked_at`) is written as `local_now().replace(microsecond=0)`, as confirm does (invoice_service.py:768): `DATETIME` drops fractions, and rule 6's and R3's equality checks compare the value kept in memory with the stored one.
