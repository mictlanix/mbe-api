# Feature Specification: Connect stamping and cancellation to the real PAC

**Feature Branch**: `040-real-pac`

**Created**: 2026-11-18

**Status**: Draft

**Input**: User description: "Connect stamping and cancellation to the real PAC." Roadmap: mictlanix/mbe-roadmap#46 (stamping), mictlanix/mbe-roadmap#51 (cancellation, SAT two-step). Step 3 of 3. Step 1 is spec 039 (mictlanix/mbe-roadmap#159), which stamps and cancels against a simulated PAC. Step 2 is the CFDI library in the private repository mictlanix/cfdi (mictlanix/mbe-roadmap#160).

## Overview

Spec 039 finished the fiscal lifecycle in the API: it stamps, cancels and refreshes CFDIs through a PAC provider contract that mbe-api owns. Until now only the simulator fulfils that contract. The library from mictlanix/cfdi can now talk to Facturación México (Multifacturas), the PAC the current system uses, and to SAT's CFDI status service.

This spec adds a **Multifacturas provider** behind the same contract. With configuration alone, an instance stamps and cancels for real, in the PAC's test mode or in production. The provider only translates between the library's shapes and the contract. Everything that decides a document's state stays where spec 039 put it, with three exceptions this spec names:
- how a document left `stamping` is resolved with a PAC that cannot look a stamp up (FR-008 to FR-011);
- how long a cancellation the PAC accepted waits for SAT before it counts as not received (FR-014);
- the simulator's provider RFC (FR-020 to FR-022).

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

## Clarifications

### Session 2026-11-18

- Q: How long does a cancellation the PAC answered with 201 wait for SAT before it may close as "not received by SAT"? → A: 72 hours from the PAC's answer, SAT's deadline for the receiver (FR-014).

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

A stamp request timed out, so the API does not know whether the PAC stamped the document. A user refreshes the document, or the resolve job reaches it. With Multifacturas the API cannot look the stamp up, so it sends the same sealed document again. The PAC answers with the stamp it already issued, or stamps it now if the first request never arrived. Either way the document ends with exactly one UUID.

**Why this priority**: A document issued twice at SAT is a fiscal error that cannot be undone silently. Without this story a document left `stamping` stays stuck with this PAC, or worse, returns to sealed and is stamped again under a new XML.

**Independent Test**: Offline, with the library's client replaced at its seam: time out a stamp, then refresh. The second request carries the same sealed document; the PAC's answer is stored; only one UUID is ever stored. Live in test mode: stamp the same sealed document twice and check that both answers carry the same UUID.

**Acceptance Scenarios**:

1. **Given** a document left `stamping` by a timeout, **When** it is refreshed and the PAC answers with a stamp, **Then** the document becomes issued with that stamp.
2. **Given** a document left `stamping`, **When** it is refreshed and the PAC times out again or cannot be reached, **Then** the document stays `stamping` and the answer is `pac.timeout` or `pac.unavailable`.
3. **Given** a document left `stamping`, **When** it is refreshed and the PAC rejects the document, **Then** the document returns to sealed and the answer is `pac.rejected` with the PAC's code.
4. **Given** the simulator is configured, **When** a document left `stamping` is refreshed, **Then** it is resolved by looking the stamp up, as in spec 039, and nothing is stamped again.

---

### User Story 3 - Cancel through SAT's two-step flow with the real PAC (Priority: P1)

A billing user cancels an issued document with a SAT reason (02, or 01 with a substitute UUID). The PAC accepts the request. The document waits as cancellation pending until SAT's status service settles it: cancelled, or rejected by the receiver (back to issued), or never received. Whether the receiver must accept comes from SAT's answer, never from the API.

**Why this priority**: Cancellation is the other half of #51, and the UI's cancellation screen (#52) depends on it.

**Independent Test**: Offline, with the PAC's and SAT's clients replaced at their seams, drive each answer (201, 202, rejection, unavailable, timeout) and each SAT status, and check every final state of spec 039. Live: query SAT's status service for UUIDs the current system issued (one valid, one cancelled) and check that the provider reads them correctly.

**Acceptance Scenarios**:

1. **Given** an issued document, **When** it is cancelled and the PAC answers 201, **Then** the document is cancellation pending and the request is open, waiting for SAT.
2. **Given** the PAC answers 202 "already cancelled", **When** a document is cancelled, **Then** the document is cancelled at once.
3. **Given** an open request, **When** it is refreshed and SAT reports the document cancelled (with or without acceptance, or after the receiver's deadline expired), **Then** the document is cancelled and spec 028's holds are released.
4. **Given** an open request, **When** SAT reports the cancellation pending the receiver, **Then** the request is marked pending the receiver and the document stays cancellation pending.
5. **Given** an open request, **When** SAT reports the receiver rejected it, **Then** the request closes as rejected and the document is issued again.
6. **Given** an open request whose cancellation SAT does not yet show, **When** it is refreshed within 72 hours of the PAC's answer (FR-014), **Then** nothing changes. **When** it is refreshed after that, **Then** the request closes as not received by SAT and the document is issued again.
7. **Given** the PAC refuses a cancellation (for example because SAT does not allow it), **When** a document is cancelled, **Then** the answer is `pac.rejected` with the PAC's or SAT's code and message, the request closes as rejected, and the document stays issued.

---

### User Story 4 - Configure an instance safely (Priority: P2)

An operator chooses an instance's PAC with settings only: none, the simulator, or Multifacturas with its account, mode, endpoint and timeouts. The instance refuses to start with a combination that would issue real CFDIs from a non-production instance, or test CFDIs from production.

**Why this priority**: The current system sends every request to production regardless of its flag. One wrong setting on dev or test would issue real CFDIs for real taxpayers.

**Independent Test**: Start the application with each combination of `APP_ENV`, `PAC_PROVIDER` and the Multifacturas mode, and check which start and which refuse, and that no log line contains a credential.

**Acceptance Scenarios**:

1. **Given** no PAC settings, **When** the application starts, **Then** the provider is `none` and stamp, cancel and refresh answer `pac.not_configured`.
2. **Given** `PAC_PROVIDER=multifacturas` without a user or password, **When** the application starts, **Then** it refuses to start, naming the missing settings but not their values.
3. **Given** `APP_ENV` other than production and Multifacturas in production mode, **When** the application starts, **Then** it refuses to start.
4. **Given** `APP_ENV=production` and Multifacturas in test mode, **When** the application starts, **Then** it refuses to start.

---

### User Story 5 - Simulated documents keep a valid provider RFC (Priority: P3)

The simulator's stamps carry the provider RFC `SIM010101SI0` instead of `SIM010101SIM`, which SAT's TFD schema rejects (an RFC must end in a digit or `A`). Documents the simulator already stamped keep their simulated mark.

**Why this priority**: The UI's screens (mbe-ui#274) depend on the simulator, and a simulated stamp should pass the same schema checks as a real one.

**Independent Test**: Stamp with the simulator and check the new RFC in the stamp and in the XML. After the migration, check that every previously simulated document on mbe_dev carries the new RFC and still shows as simulated in its detail and PDF.

**Acceptance Scenarios**:

1. **Given** the simulator, **When** a document is stamped, **Then** its provider RFC is `SIM010101SI0` and its detail reports `stamp.simulated` true.
2. **Given** documents stamped by the simulator before this change, **When** the migration runs, **Then** their stored provider RFC and the `RfcProvCertif` in their stored XML are `SIM010101SI0`, and they still show as simulated.

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

## Requirements *(mandatory)*

### Functional Requirements

**Dependency**

- **FR-001**: The API MUST depend on the CFDI library from mictlanix/cfdi, pinned to a fixed tag or commit, so every install resolves the same version.
- **FR-002**: The repository MUST document how the servers (dev, test, and later production) get read access to the private library repository, and how to move the pin.

**Provider**

- **FR-003**: A Multifacturas provider MUST implement spec 039's PAC provider contract by composing the library's Multifacturas client (stamp, cancellation) and its SAT status client (status queries).
- **FR-004**: The provider MUST translate every value between the library and the contract without loss:
  - the sealed and stamped XML;
  - the stamp's fields (UUID, stamp date, SAT seal, SAT certificate number, provider RFC) and the stamped document;
  - the issuer's certificate, key and key password;
  - the cancellation's reason and substitute UUID;
  - SAT's status: state, cancellability and cancellation status.
- **FR-005**: The provider MUST map the library's errors to the contract's three:
  - a refusal by the PAC or SAT, or the library's input checks, becomes a rejection carrying the code and message;
  - a request that never reached the service becomes unavailable;
  - an unknown outcome becomes a timeout.
  No other error from the library may escape the provider.
- **FR-006**: Error answers MUST use spec 036's existing codes (`pac.rejected`, `pac.unavailable`, `pac.timeout`, `pac.not_configured`), with the PAC's or SAT's code and message as params on a rejection.
- **FR-007**: No credential, CSD material (certificate, key, password) or raw PAC or SAT payload MAY appear in code, tests, fixtures, logs, exception messages or error answers.

**Stamping and its recovery**

- **FR-008**: A document whose stamp request timed out MUST be resolvable with this provider. The provider cannot look a stamp up, so a refresh (on demand or by the resolve job) MUST send the same sealed document again; the PAC answers with the existing stamp or stamps it for the first time.
- **FR-009**: A document MUST never receive a second UUID: the resubmission MUST be byte-for-byte the sealed document stored before the first request, and a document left `stamping` MUST NOT return to sealed with this provider except on a rejection (FR-010).
- **FR-010**: A resolving resubmission MUST apply the same outcomes as a stamp: a stamp makes the document issued; a rejection returns it to sealed with `pac.rejected`; unavailable or timeout leaves it `stamping`.
- **FR-011**: With the simulator, refresh MUST keep spec 039's behavior: it looks the stamp up and never stamps.

**Cancellation**

- **FR-012**: The PAC's 202 MUST cancel the document at once. Its 201 MUST leave the document cancellation pending with the request open, waiting for SAT's status. That status, not the PAC's answer, decides whether the receiver must accept.
- **FR-013**: Refreshing an open request with this provider MUST reach every final state of spec 039 from SAT's status:
  - cancelled, with or without acceptance, or after the receiver's deadline expired;
  - pending the receiver;
  - rejected by the receiver, back to issued;
  - not received by SAT, back to issued, under FR-014.
- **FR-014**: A request the PAC accepted with 201 MUST NOT close as "not received by SAT" while SAT may still be processing it. It closes only after 72 hours from the PAC's answer, SAT's deadline for the receiver. Until then a refresh that finds the document valid with no cancellation changes nothing. Spec 039's 5-minute wait for a request with no answer stays as it is.
- **FR-015**: A cancellation the PAC refuses MUST close the request as rejected and leave the document issued, with the code and message stored on the request and returned in the error's params (spec 039). The API MUST NOT infer "not cancellable" from PAC codes.

**Configuration**

- **FR-016**: `PAC_PROVIDER` MUST accept `none` (default), `simulated` and `multifacturas`. With `multifacturas`, these MUST come from settings, with none hard-coded:
  - the account (user and password);
  - the mode (test or production);
  - the endpoint;
  - the connection and read timeouts.
  Defaults are allowed for the endpoint and timeouts only.
- **FR-017**: One PAC account MUST serve every issuer of an instance, as in the current system. Each request MUST use the issuing taxpayer's own CSD.
- **FR-018**: The application MUST refuse to start when:
  - `PAC_PROVIDER=multifacturas` lacks a user or a password;
  - the Multifacturas mode is production and `APP_ENV` is not production;
  - the mode is test and `APP_ENV` is production.
  The simulator's existing refusal in production stays. A refusal names the settings, never their values.
- **FR-019**: Production MUST keep `PAC_PROVIDER=none` after this spec is deployed. Enabling Multifacturas in production is a step of the invoicing cutover (#57), because a function never runs in two systems at once.

**Simulator**

- **FR-020**: The simulator and its `/pac-simulator` controls MUST keep working as spec 039 defines, because the UI uses them (mbe-ui#274).
- **FR-021**: The simulator's provider RFC MUST be `SIM010101SI0`. A document is simulated when its provider RFC is the simulator's.
- **FR-022**: A numbered migration MUST rewrite the provider RFC on mbe_dev's stored simulated documents (17 on 2026-11-18, ids 63907–63929), in the document and in the `RfcProvCertif` of its stored XML, so they stay marked as simulated. Its rollback restores the old value. Tests and fixtures that assert the old value MUST change with it.

**Testing**

- **FR-023**: Tests MUST run offline against the provider, with the library's clients replaced at their seam. They MUST cover every translation in FR-004, every error in FR-005, the recovery in FR-008 to FR-010 and every final state in FR-013.
- **FR-024**: Live tests MUST be opt-in through environment variables and skipped without them:
  - against Multifacturas' test mode: stamp, the duplicate answer, cancellation;
  - against SAT's status service: a valid UUID and a cancelled one.

### Key Entities

- **PAC account (configuration)**: one per instance. It holds the user, password, mode, endpoint and timeouts. It is never stored in the database.
- **Stamp (existing, spec 039)**: the UUID, stamp date, SAT seal, SAT certificate number, provider RFC and the stamped XML. The provider RFC now tells real PAC stamps from simulated ones.
- **Cancellation request (existing, spec 039)**: an open request now also waits, after the PAC's 201, until SAT shows the cancellation or 72 hours pass (FR-014).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On test, with the PAC in test mode, one invoice, one payment receipt and one credit note are each stamped with a UUID from the PAC. Their PDFs carry no simulated mark. The results are recorded in quickstart.
- **SC-002**: On test, each of those documents is cancelled once with reason 02 and once with reason 01 plus a substitute. The PAC's answer to each is recorded in quickstart. In test mode that answer is SAT 205, and the document stays issued with the code shown.
- **SC-003**: In 100% of the offline recovery cases (timeout followed by a stamp, a rejection, unavailable, a timeout again, or two concurrent refreshes), every document ends with at most one UUID.
- **SC-004**: Every final state of spec 039's cancellation is reached from SAT's status in the offline tests. The live SAT query reads a valid and a cancelled UUID of the current system correctly.
- **SC-005**: A search of the test suite, the logs of the test walk and the error answers finds no credential, CSD material or raw PAC payload.
- **SC-006**: Every combination in FR-018 starts or refuses as specified.
- **SC-007**: After deploy, dev and test run on configuration only: the simulator on dev and Multifacturas test mode on test, or as the user decides. Production stays `none`.

## Assumptions

- **A1.** The PAC deduplicates by sealed document: stamping a byte-identical sealed XML again returns the existing stamp. This is verified in test mode, and the live test re-checks it.
- **A2.** The library is used as it stands at mictlanix/cfdi `4b41821` or a later pin. It reads no environment variables, and its only test seam is its transport function.
- **A3.** The PAC checks for a duplicate before it validates a document again. If it did not, a rejected resubmission of a document it already stamped would return that document to sealed (FR-010). Confirm at the cutover.
- **A4.** Test mode ignores the password. The wrong-credentials answer is only seen in production.
- **A5.** Credentials for test mode come from the user and go only in the test server's `.env`. Changing that file needs confirmation, as every change to test does.

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
