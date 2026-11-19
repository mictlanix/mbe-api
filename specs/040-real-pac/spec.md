# Feature Specification: Connect stamping and cancellation to the real PAC

**Feature Branch**: `040-real-pac`

**Created**: 2026-11-18

**Status**: Draft

**Input**: User description: "Connect stamping and cancellation to the real PAC." Roadmap: mictlanix/mbe-roadmap#46 (stamping), mictlanix/mbe-roadmap#51 (cancellation, SAT two-step). Step 3 of 3. Step 1 is spec 039 (mictlanix/mbe-roadmap#159), which stamps and cancels against a simulated PAC. Step 2 is the CFDI library in the private repository mictlanix/cfdi (mictlanix/mbe-roadmap#160).

## Overview

Spec 039 finished the fiscal lifecycle in the API: it stamps, cancels and refreshes CFDIs through a PAC provider contract that mbe-api owns. Until now only mbe-api's own simulator fulfils that contract. The library from mictlanix/cfdi can now talk to Facturación México (Multifacturas), the PAC the current system uses, and to SAT's CFDI status service. It also ships its own simulated PAC and simulated SAT status.

This spec makes the library the API's **only PAC implementation**:
- **One adapter** in `app/services/pac/` implements the contract over the library's clients. With `PAC_PROVIDER=multifacturas` they are the library's Multifacturas client and SAT status client. With `PAC_PROVIDER=simulated` they are the library's simulated PAC and simulated SAT status.
- **mbe-api's own simulator goes away.** Its state moves to a store on the database that the library's simulator reads and writes.
- **The simulator controls keep their routes**, which the UI uses (mbe-ui#274). They now drive the library's simulator.

Everything that decides a document's state stays where spec 039 put it, with three exceptions this spec names:
- how a document left `stamping` is resolved with a PAC that cannot look a stamp up (FR-008 to FR-011);
- how long a cancellation the PAC accepted waits for SAT before it counts as not received (FR-014);
- the simulator itself: its implementation, its state, its provider RFC and its controls (FR-020 to FR-027).

### What the current system does (read 2026-11-18)

- **Configuration is global.** One PAC account (user, password, production flag) serves every issuer; it lives in the application's configuration. Each issuer's certificate (CSD) comes from that issuer's own records. The issuer table has a `provider` column, but nothing reads it to choose a PAC or credentials. Every issuer moved from the previous PAC to this one in the same week of May 2025.
- **It always sends production requests.** The production flag exists but is ignored: stamp and cancel calls are hard-coded to production.
- **No recovery.** A timeout or a duplicate gets no special handling. The PAC's operation that returns a stamp by UUID exists in the client but is never called.
- **Cancellation is one call.** Any answer other than OK fails. On OK the document is marked cancelled at once. SAT's status is never queried.
- **No endpoint or timeout settings.** The endpoints are fixed in the client, and the platform's default timeout of 100 s applies.

### What the PAC does (from the library's research, mictlanix/cfdi spec 003)

- **Duplicates.** Stamping the same sealed document again returns the existing stamp, with the same UUID. In test mode the PAC answers it as a fresh success. When it answers SAT's 307 "already stamped" instead, the library recovers the existing stamp, or reports an unknown outcome if it cannot.
- **No lookup.** There is no lookup of a stamp by sealed document.
- **Cancellation answers.** The PAC answers SAT's 201 "request received" or 202 "already cancelled". It never says whether the receiver must accept, and never says "not cancellable": a refusal is a rejection with the PAC's or SAT's code.
- **Test mode limits.** Test stamps never reach SAT, so cancelling a test stamp is rejected with SAT 205 "does not exist", and SAT's status service does not know test UUIDs.

### How the library's simulator compares with spec 039's (read 2026-11-18, mictlanix/cfdi `4b41821`)

| Behavior | Spec 039's simulator | The library's simulator |
|---|---|---|
| Provider RFC | `SIM010101SIM` (SAT's TFD schema rejects it) | `SIM010101SI0` |
| Next-call outcomes | per document: stamp and cancellation | stamp per sealed document; cancellation per UUID |
| A stamp outcome set before the document is resealed | kept (keyed by issuer, series, folio) | lost (keyed by the seal); set it after sealing |
| A cancellation outcome before the stamp | allowed | needs the UUID, so only after the stamp |
| Stamping a document it already stamped | returns the same stamp | same |
| `find_stamp` | finds the stamp of the same sealed document | same |
| Cancellation with nothing chosen | accepted under 24 hours from the stamp, pending after | same |
| Not cancellable | for the next request only | stays not cancellable for every later request, as SAT would answer |
| Timeout on cancel | SAT shows the request pending | same |
| The receiver's answer | accept, reject, expire | same |
| A UUID it never stamped | treated as valid, cancellable with acceptance | same |
| Reading back the queued outcomes | yes | no public read (FR-025) |
| State across processes | `pac_simulation` table | a caller-supplied store (FR-023) |
| Concurrent calls across instances | each call in its own transaction | serialized within one instance only (FR-024) |

## Clarifications

### Session 2026-11-18

- Q: How long does a cancellation the PAC answered with 201 wait for SAT before it may close as "not received by SAT"? → A: 72 hours from the PAC's answer, SAT's deadline for the receiver (FR-014).
- Q: The library cannot read back queued outcomes; how does `GET /pac-simulator/fiscal-documents/{id}` keep `next_stamp` and `next_cancellation`? → A: It drops them. GET and PUT return `uuid`, `sat_status` and `cancellation_status` only. mbe-ui#274 is told (FR-025).
- Q: How is the PAC's 201 recorded? → A: As `PENDING`, as planned. The request reads "pending" from the PAC's answer until SAT's status settles it (FR-012).
- Q: Which PAC does test (:8766) run after this spec? → A: Multifacturas in test mode, where the walk runs. Dev (:8765) stays on the simulator and serves the UI's simulated scenarios (SC-007).
- Q: What does "the PAC in use must be configured in the config file" require? → A: Each instance's `.env` names its PAC explicitly. `PAC_PROVIDER` has no default, and an instance whose configuration doesn't set it refuses to start. Production's `.env` says `PAC_PROVIDER=none` (FR-016, FR-018).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stamp with the real PAC (Priority: P1)

A billing user confirms an invoice, a payment receipt or a CFDI credit note. On an instance configured with Multifacturas, the API sends the sealed document to the PAC and stores the stamp it returns. The document is then issued, with a UUID the PAC assigned, and its PDF carries no "simulated" mark.

**Why this priority**: Stamping is what makes a document fiscally valid, and nothing else in this spec matters without it.

**Independent Test**: On test with the PAC in test mode, stamp one invoice, one payment receipt and one credit note through the existing stamp endpoint, and check that each is issued with the PAC's UUID and provider RFC and that its PDF has no simulated mark.

**Acceptance Scenarios**:

1. **Given** a sealed invoice on an instance with `PAC_PROVIDER=multifacturas` in test mode, **When** it is stamped, **Then** it becomes issued with the UUID, stamp date, SAT seal, SAT certificate number and provider RFC from the PAC's stamp, and the stored XML is the stamped document the PAC returned.
2. **Given** the PAC rejects a document, **When** it is stamped, **Then** the answer is `pac.rejected` with the PAC's code and message in the params, and the document stays sealed.
3. **Given** the PAC cannot be reached, **When** a document is stamped, **Then** the answer is `pac.unavailable` and the document stays sealed.
4. **Given** the stamp request times out with an unknown outcome, **When** a document is stamped, **Then** the answer is `pac.timeout` and the document stays `stamping` (resolved by User Story 2).

---

### User Story 2 - Resolve a stamp left in flight without a second UUID (Priority: P1)

A stamp request timed out, so the API does not know whether the PAC stamped the document. A user refreshes the document, or the resolve job reaches it. With Multifacturas the API cannot look the stamp up. Once 5 minutes have passed since the stamp request, so the first request is no longer in the PAC's hands, the API sends the same sealed document again. The PAC answers with the stamp it already issued, or stamps it now if the first request never arrived. Either way the document ends with exactly one UUID.

**Why this priority**: A document issued twice at SAT is a fiscal error that cannot be undone silently. Without this story a document left `stamping` stays stuck with this PAC, or worse, returns to sealed and is stamped again under a new XML.

**Independent Test**: Offline, with the library's client replaced at its seam: time out a stamp, then refresh after 5 minutes. The second request carries the same sealed document; the PAC's answer is stored; only one UUID is ever stored. Live in test mode: stamp the same sealed document twice and check that both answers carry the same UUID.

**Acceptance Scenarios**:

1. **Given** a document left `stamping` by a timeout less than 5 minutes ago, **When** it is refreshed, **Then** nothing is sent to the PAC and the document stays `stamping`.
2. **Given** a document left `stamping` more than 5 minutes ago, **When** it is refreshed and the PAC answers with a stamp, **Then** the document becomes issued with that stamp.
3. **Given** a document left `stamping` more than 5 minutes ago, **When** it is refreshed and the PAC times out again or cannot be reached, **Then** the document stays `stamping` and the answer is `pac.timeout` or `pac.unavailable`.
4. **Given** a document left `stamping` more than 5 minutes ago, **When** it is refreshed and the PAC rejects the document, **Then** the document returns to sealed and the answer is `pac.rejected` with the PAC's code.
5. **Given** the simulator is configured, **When** a document left `stamping` is refreshed, **Then** it is resolved by looking the stamp up, as in spec 039, and nothing is stamped again.

---

### User Story 3 - Cancel through SAT's two-step flow with the real PAC (Priority: P1)

A billing user cancels an issued document with a SAT reason (02, or 01 with a substitute UUID). The PAC accepts the request. The document waits as cancellation pending until SAT's status service settles it: cancelled, or rejected by the receiver (back to issued), or never received. Whether the receiver must accept comes from SAT's answer, never from the API.

**Why this priority**: Cancellation is the other half of #51, and the UI's cancellation screen (#52) depends on it.

**Independent Test**: Offline, with the PAC's and SAT's clients replaced at their seams, drive each answer (201, 202, rejection, unavailable, timeout) and each SAT status, and check every final state of spec 039. Live: query SAT's status service for UUIDs the current system issued (one valid, one cancelled) and check that the provider reads them correctly.

**Acceptance Scenarios**:

1. **Given** an issued document, **When** it is cancelled and the PAC answers 201, **Then** the document is cancellation pending and the request is open, waiting for SAT.
2. **Given** the PAC answers 202 "already cancelled", **When** a document is cancelled, **Then** the document is cancelled at once.
3. **Given** an open request, **When** it is refreshed and SAT reports the document cancelled (with or without acceptance, or after the receiver's deadline expired), **Then** the document is cancelled and spec 028's holds are released.
4. **Given** an open request, **When** SAT reports the cancellation pending the receiver, **Then** the request stays pending and the document stays cancellation pending.
5. **Given** an open request, **When** SAT reports the receiver rejected it, **Then** the request closes as rejected and the document is issued again.
6. **Given** an open request whose cancellation SAT does not yet show, **When** it is refreshed within 72 hours of the PAC's answer (FR-014), **Then** nothing changes. **When** it is refreshed after that, **Then** the request closes as not received by SAT and the document is issued again.
7. **Given** the PAC refuses a cancellation (for example because SAT does not allow it), **When** a document is cancelled, **Then** the answer is `pac.rejected` with the PAC's or SAT's code and message, the request closes as rejected, and the document stays issued.

---

### User Story 4 - Configure an instance safely (Priority: P2)

An operator chooses an instance's PAC in its configuration file (`.env`), explicitly: none, the simulator, or Multifacturas with its account, mode, endpoint and timeouts. An instance that does not name its PAC does not start. The instance refuses to start with a combination that would issue real CFDIs from a non-production instance, test CFDIs from production, or simulated CFDIs in production.

**Why this priority**: The current system sends every request to production regardless of its flag. One wrong setting on dev or test would issue real CFDIs for real taxpayers.

**Independent Test**: Start the application with each combination of `APP_ENV`, `PAC_PROVIDER` and the Multifacturas mode, and check which start and which refuse, and that no log line contains a credential.

**Acceptance Scenarios**:

1. **Given** a configuration without `PAC_PROVIDER`, **When** the application or the resolve job starts, **Then** it refuses to start, naming `PAC_PROVIDER`.
2. **Given** `PAC_PROVIDER=none`, **When** the application starts, **Then** stamp, cancel and refresh answer `pac.not_configured`.
3. **Given** `PAC_PROVIDER=multifacturas` without a user or password, **When** the application starts, **Then** it refuses to start, naming the missing settings but not their values.
4. **Given** `APP_ENV` other than production and Multifacturas in production mode, **When** the application starts, **Then** it refuses to start.
5. **Given** `APP_ENV=production` and Multifacturas in test mode, **When** the application starts, **Then** it refuses to start.
6. **Given** `APP_ENV=production` and `PAC_PROVIDER=simulated`, **When** the application starts, **Then** it refuses to start (spec 039, unchanged).

---

### User Story 5 - The simulator runs on the library (Priority: P2)

A tester on dev drives every simulated scenario through the same `/pac-simulator` routes as before (mbe-ui#274). Behind them, the library's simulated PAC and simulated SAT status now answer, with their state in the database, so the API's processes and the resolve job see the same simulated PAC. Simulated stamps carry the provider RFC `SIM010101SI0`, which SAT's TFD schema accepts. Documents the old simulator stamped keep their simulated mark.

**Why this priority**: The UI's screens are built against the simulator, and one PAC implementation means the simulated path exercises the same adapter as the real one.

**Independent Test**: Drive each scenario of quickstart's "Driving each scenario" through the routes with `PAC_PROVIDER=simulated`, and check each outcome. Run two simulator instances on the same store, and check that concurrent stamp requests for one document produce one UUID.

**Acceptance Scenarios**:

1. **Given** the simulator, **When** a document is stamped, **Then** its provider RFC is `SIM010101SI0` and its detail reports `stamp.simulated` true.
2. **Given** a stamp outcome set through `PUT /pac-simulator/fiscal-documents/{id}`, **When** the document is stamped, **Then** that outcome happens once, and the next stamp succeeds.
3. **Given** a cancellation outcome set through the same route on a stamped document, **When** it is cancelled, **Then** that outcome happens once.
4. **Given** a document that is not stamped yet, **When** a cancellation outcome is set, **Then** the answer is 409 `fiscal_document.not_stamped`.
5. **Given** a pending cancellation, **When** `POST …/receiver` accepts, rejects or lets it expire, **Then** the next refresh settles the document as spec 039 defines. With nothing pending, the answer is 409 `fiscal_document.nothing_to_refresh`.
6. **Given** two API processes with their own simulator instances, **When** both receive a stamp request for the same document at once, **Then** one reaches the simulator, the other answers 409 `fiscal_document.stamping`, and the document ends with one UUID.
7. **Given** documents stamped by the old simulator, **When** migration 036 runs, **Then** their stored provider RFC and the `RfcProvCertif` in their stored XML are `SIM010101SI0`, and they still show as simulated.

### Edge Cases

- **Timeout on resubmission.** The PAC times out on a resolving resubmission: the document stays `stamping`, and the next refresh or job run tries again.
- **Two resolutions at once.** Two refreshes, or a refresh and the job, resubmit the same document concurrently: the PAC answers both with the same stamp, and spec 039's write rules store it once.
- **SAT's 307 without the document.** The PAC answers "already stamped" without the stamped document, and the library cannot retrieve it: this counts as a timeout, and the document stays `stamping`.
- **Rejected resubmission.** A resolving resubmission is rejected, for example because the issue date is now outside SAT's 72-hour window. The PAC answers duplicates with the existing stamp, so a rejection means it holds no stamp for this document, and the document returns to sealed (assumption A3).
- **Document not cancellable.** A cancellation SAT does not allow (for example an invoice with live related documents, which spec 039 already refuses before calling the PAC) reaches the API as a rejection with a code. The API does not translate PAC codes into "not cancellable".
- **Test-mode cancellation.** Cancelling a test-mode stamp is rejected with SAT 205. This is the expected answer in test mode, not a defect.
- **Refreshing a test-mode request.** SAT does not know test UUIDs, so SAT reports "not found" and the document stays unchanged, as spec 039 defines.
- **Mismatched stamp.** The PAC returns a stamp whose seal does not match the submitted document: the library rejects it (`MF-ANSWER`), and the document stays sealed.
- **Library input error.** The library refuses an input, for example a malformed substitute UUID that slipped past the API's checks: this is a `pac.rejected` with the library's code, never a 500.
- **Simulated documents stamped before the switch.** Their old simulator state is gone (FR-023). A cancellation of one is answered as for a UUID the simulator never stamped: pending, waiting for the receiver, and the receiver controls then work on it.
- **A stamp outcome set before resealing.** The library keys a stamp outcome by the sealed document, so reopening and resealing discards it. Testers set outcomes after the last seal.
- **Not cancellable is permanent.** Once the simulator answers "not cancellable" for a UUID, every later cancellation of it gets the same answer.
- **The simulator's store fails.** A store read or write that fails is the library's "PAC unavailable", answered as `pac.unavailable`.

## Requirements *(mandatory)*

### Functional Requirements

**Dependency**

- **FR-001**: The API MUST depend on the CFDI library from mictlanix/cfdi, pinned to a fixed tag or commit, so every install resolves the same version.
- **FR-002**: The repository MUST document how the servers (dev, test, and later production) get read access to the private library repository, and how to move the pin.

**Provider**

- **FR-003**: One adapter MUST implement spec 039's PAC provider contract over the library's clients, for both providers:
  - **`multifacturas`:** the Multifacturas client for stamp and cancellation, and SAT's status client for status queries.
  - **`simulated`:** the library's simulated PAC for stamp, look-up and cancellation, and its simulated SAT status over that same simulated PAC.
- **FR-004**: The adapter MUST translate every value between the library and the contract without loss:
  - the sealed and stamped XML;
  - the stamp's fields (UUID, stamp date, SAT seal, SAT certificate number, provider RFC) and the stamped document;
  - the issuer's certificate, key and key password;
  - the cancellation's reason and substitute UUID;
  - SAT's status: state, cancellability and cancellation status.
- **FR-005**: The adapter MUST map every error the library documents to the contract's three:
  - a refusal by the PAC or SAT, or the library's input checks, becomes a rejection carrying the code and message;
  - a request that never reached the service becomes unavailable;
  - an unknown outcome becomes a timeout.
- **FR-006**: Error answers MUST use spec 036's existing codes (`pac.rejected`, `pac.unavailable`, `pac.timeout`, `pac.not_configured`), with the PAC's or SAT's code and message as params on a rejection.
- **FR-007**: No credential, CSD material (certificate, key, password) or raw PAC or SAT payload MAY appear in code, tests, fixtures, logs, exception messages or error answers.

**Stamping and its recovery**

- **FR-008**: A document whose stamp request timed out MUST be resolvable with Multifacturas. It cannot look a stamp up, so a refresh (on demand or by the resolve job) MUST send the same sealed document again once 5 minutes have passed since the stamp request (spec 039's grace). Before that, a refresh sends nothing and leaves the document `stamping`. The PAC answers with the existing stamp, or stamps it for the first time.
- **FR-009**: A document MUST never receive a second UUID: the resubmission MUST be byte-for-byte the sealed document stored before the first request, and a document left `stamping` MUST NOT return to sealed with Multifacturas except on a rejection (FR-010).
- **FR-010**: A resolving resubmission MUST apply the same outcomes as a stamp: a stamp makes the document issued; a rejection returns it to sealed with `pac.rejected`; unavailable or timeout leaves it `stamping`.
- **FR-011**: With the simulator, refresh MUST keep spec 039's behavior: it looks the stamp up and never stamps.

**Cancellation**

- **FR-012**: The PAC's 202 MUST cancel the document at once. Its 201 MUST be recorded as the contract's `PENDING`: the document is cancellation pending, and the request reads "pending" until SAT's status settles it. That status, not the PAC's answer, decides whether the receiver must accept.
- **FR-013**: Refreshing an open request MUST reach every final state of spec 039 from SAT's status:
  - cancelled, with or without acceptance, or after the receiver's deadline expired;
  - pending the receiver;
  - rejected by the receiver, back to issued;
  - not received by SAT, back to issued, under FR-014.
- **FR-014**: A request the PAC accepted with 201 MUST NOT close as "not received by SAT" while SAT may still be processing it. It closes only after 72 hours from the PAC's answer, SAT's deadline for the receiver. Until then a refresh that finds the document valid with no cancellation changes nothing. Spec 039's 5-minute wait for a request with no answer stays as it is.
- **FR-015**: A cancellation the PAC refuses MUST close the request as rejected and leave the document issued, with the code and message stored on the request and returned in the error's params (spec 039). The API MUST NOT infer "not cancellable" from Multifacturas' codes.

**Configuration**

- **FR-016**: The PAC in use MUST be configured in each instance's configuration file (`.env`; an environment variable of the same name may override it, as for every setting), never in the database or the code. `PAC_PROVIDER` MUST be set explicitly to `none`, `simulated` or `multifacturas`; it has no default. With `multifacturas`, these MUST come from the same configuration, with none hard-coded:
  - the account (user and password);
  - the mode (test or production);
  - the endpoint;
  - the connection and read timeouts.
  Defaults are allowed for the endpoint and timeouts only.
- **FR-017**: One PAC account MUST serve every issuer of an instance, as in the current system. Each request MUST use the issuing taxpayer's own CSD.
- **FR-018**: The application and the resolve job MUST refuse to start when:
  - `PAC_PROVIDER` is not set;
  - `PAC_PROVIDER=simulated` and `APP_ENV=production` (spec 039, unchanged);
  - `PAC_PROVIDER=multifacturas` lacks a user or a password;
  - the Multifacturas mode is production and `APP_ENV` is not production;
  - the mode is test and `APP_ENV` is production.
  A refusal names the settings, never their values.
- **FR-019**: Production MUST keep `PAC_PROVIDER=none` after this spec is deployed. Enabling Multifacturas in production is a step of the invoicing cutover (#57), because a function never runs in two systems at once.

**Simulator**

- **FR-020**: mbe-api's own simulator MUST be removed. `PAC_PROVIDER=simulated` MUST use the library's simulated PAC and simulated SAT status through the adapter of FR-003. No simulator behavior MAY be rebuilt in mbe-api. A scenario the library cannot reach is either dropped or asked for in mictlanix/cfdi.
- **FR-021**: A document is simulated when its provider RFC is the library simulator's, `SIM010101SI0`. The detail's `stamp.simulated` and the PDF's "DOCUMENTO SIMULADO — SIN VALIDEZ FISCAL" banner MUST follow it.
- **FR-022**: Migration 036 MUST rewrite the provider RFC of the documents the old simulator stamped on mbe_dev, in the document and in the `RfcProvCertif` of its stored XML, so they stay marked as simulated. Its rollback restores the old value. Tests and fixtures that assert the old value MUST change with it.
- **FR-023**: The simulator's state MUST live in the database, shared by every API process and the resolve job:
  - a new key-value store table, which the library's simulator reads and writes, replaces `pac_simulation`;
  - migration 036 creates the table and drops `pac_simulation` without converting its rows. On 2026-11-18 no simulated document was `stamping`, no request was open, and no outcome was queued;
  - a store read or write that fails is the library's "PAC unavailable".
- **FR-024**: Spec 039's guard MUST keep a double stamp impossible across processes, because the library serializes calls only within one instance:
  - the stamp request marks the document `stamping` and commits under the row lock;
  - the PAC is called with no lock held;
  - the stamp is stored under the lock again.
  A test MUST prove it with two simulator instances on the same store.
- **FR-025**: The simulator routes MUST keep their paths, privileges, the 404 when the simulator is not configured, and their bodies, except that the response no longer carries `next_stamp` and `next_cancellation` (the library cannot read them back):
  - **`GET /pac-simulator/fiscal-documents/{id}`** returns `uuid`, `sat_status` and `cancellation_status`. For a document not yet stamped, `uuid` is null, `sat_status` is `not_found` and `cancellation_status` is `none`.
  - **`PUT` the same route** sets the outcome of the next stamp, for a sealed document, or of the next cancellation, for a stamped one. A cancellation outcome for a document not yet stamped answers 409 `fiscal_document.not_stamped`.
  - **`POST …/receiver`** accepts, rejects or lets expire a pending cancellation. With nothing pending, it answers 409 `fiscal_document.nothing_to_refresh`.
  - A library refusal on these routes, for example SAT's 307 for a stamp outcome on a document the old simulator stamped, is `pac.rejected` with the library's code.
- **FR-026**: The UI handoff (mbe-ui#274) MUST be updated with the routes' new response, the not-stamped rule for cancellation outcomes, and the behavior differences in the Overview's table.
- **FR-027**: The log line `pac simulator: stamp call {issuer} {series}{folio}` that spec 039's quickstart counts MUST remain: one line per stamp call to the simulator, never with the XML.

**Testing**

- **FR-028**: Tests MUST run offline against the adapter, with the library's clients replaced at their seam. They MUST cover:
  - every translation in FR-004 and every error in FR-005;
  - the recovery in FR-008 to FR-010;
  - every final state in FR-013;
  - every scenario of the simulator routes.
- **FR-029**: Live tests MUST be opt-in through environment variables and skipped without them:
  - against Multifacturas' test mode: stamp, the duplicate answer, cancellation;
  - against SAT's status service: a valid UUID and a cancelled one.

### Key Entities

- **PAC configuration**: one per instance, in its `.env`. It names the PAC (`PAC_PROVIDER`, required) and, for Multifacturas, the user, password, mode, endpoint and timeouts. It is never stored in the database.
- **Stamp (existing, spec 039)**: the UUID, stamp date, SAT seal, SAT certificate number, provider RFC and the stamped XML. The provider RFC tells simulated stamps (`SIM010101SI0`) from real ones.
- **Cancellation request (existing, spec 039)**: an open request now also waits, after the PAC's 201, until SAT shows the cancellation or 72 hours pass (FR-014).
- **Simulator store (new, replaces `pac_simulation`)**: string values under string keys, written and read only by the library's simulator. One row per key; mbe-api never interprets the values.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On test, with the PAC in test mode, one invoice, one payment receipt and one credit note are each stamped with a UUID from the PAC. Their PDFs carry no simulated mark. The results are recorded in quickstart.
- **SC-002**: On test, each of those documents is cancelled once with reason 02, and once with reason 01 plus a substitute stamped for it. The PAC's answer to each is recorded in quickstart. In test mode the expected answer is SAT 205, and the document stays issued with the code shown.
- **SC-003**: In 100% of the offline recovery cases (timeout followed by a stamp, a rejection, unavailable, a timeout again, or two concurrent refreshes), every document ends with at most one UUID.
- **SC-004**: Every final state of spec 039's cancellation is reached from SAT's status in the offline tests. The live SAT query reads a valid and a cancelled UUID of the current system correctly.
- **SC-005**: A search of the test suite, the logs of the test walk and the error answers finds no credential, CSD material or raw PAC payload.
- **SC-006**: Every combination in FR-018 starts or refuses as specified.
- **SC-007**: After deploy, dev's `.env` names the library's simulator and test's names Multifacturas in test mode. Production's `.env` names `none` explicitly before any production deploy of this code.
- **SC-008**: On dev, every scenario of quickstart's "Driving each scenario" reaches its listed outcome through the simulator routes.
- **SC-009**: With two simulator instances on one store, 20 concurrent stamp requests for one document produce exactly one stamp call to the simulator and one UUID.

## Assumptions

- **A1.** The PAC deduplicates by sealed document: stamping a byte-identical sealed XML again returns the existing stamp. This is verified in test mode, and the live test re-checks it.
- **A2.** The library is used as it stands at mictlanix/cfdi `4b41821` or a later pin. It reads no environment variables, and its only test seam is its transport function.
- **A3.** The PAC checks for a duplicate before it validates a document again. If it did not, a rejected resubmission of a document it already stamped would return that document to sealed (FR-010). Confirm at the cutover.
- **A4.** Test mode ignores the password. The wrong-credentials answer is only seen in production.
- **A5.** Credentials for test mode come from the user and go only in the test server's `.env`. Changing that file needs confirmation, as every change to test does.
- **A6.** The UI does not yet read `next_stamp` or `next_cancellation`: mbe-ui's main branch has no call to the simulator routes (2026-11-18).

### To confirm at the cutover (not verifiable in test mode)

- How SAT answers a cancellation of a document that is not cancellable.
- That the PAC's "acuseSAT" returns the stamped document in production, the path of SAT's 307 "already stamped".
- A real cancellation end to end: 201, SAT pending or cancelled, the receiver's answer.
- The PAC's answer to wrong credentials.
- Assumption A3.

### Out of scope

- The invoicing cutover (#57), including enabling Multifacturas in production.
- Other PACs.
- SAT's bulk download service.
- Changes to the library's simulator. None is needed for this spec.
