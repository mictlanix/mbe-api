# Feature Specification: CFDI stamping and cancellation against a simulated PAC

**Feature Branch**: `039-cfdi-stamping-cancellation`

**Created**: 2026-11-16

**Status**: Draft

**Input**: User description: "Stamping and cancellation of CFDIs against a simulated PAC." Roadmap: mictlanix/mbe-roadmap#159. Step 1 of 3. Step 2 is the CFDI package and the real PAC client (mictlanix/mbe-roadmap#160). Step 3 connects them (mictlanix/mbe-roadmap#46, mictlanix/mbe-roadmap#51). The screens that use this are mictlanix/mbe-roadmap#52 (cancellation), #54 (payment receipts) and #56 (credit notes).

## Overview

Specs 027 and 028 take invoices, payment receipts and CFDI credit notes as far as a **sealed** document: the XML is built and signed with the issuer's certificate, but no PAC has stamped it. A sealed document has no fiscal validity. It cannot be downloaded, printed or emailed as a CFDI, and it cannot be cancelled before SAT.

This spec finishes the fiscal lifecycle in the API:
- it **stamps** a sealed document;
- it **cancels** a stamped document through SAT's two-step flow;
- it **reports** each document's fiscal state and cancellation history.

The API talks to the PAC only through a **PAC provider contract** that mbe-api owns. In this step a **simulated PAC** fulfils the contract. It behaves like the real one, including its failures and the receiver's acceptance, so the UI screens can be built and tested end to end now. In step 3 the real client from #160 replaces the simulator behind the same contract, and nothing else in the API changes.

### What the current system does (measured 2026-11-16)

- **Stamping.**
  - The current system stamps on Confirm through the PAC Facturación México.
  - It stores the UUID, the stamp date, the SAT seal, the SAT certificate number and the provider RFC on the document, and the stamped XML in `fiscal_document_xml`.
  - It never records that a stamp is in flight. A timeout leaves the document unconfirmed, and the next Confirm sends it again.
- **Cancellation.**
  - It sends the UUID, the reason and the substitute UUID to the PAC.
  - It marks the document cancelled as soon as the PAC accepts the request. It never queries SAT, so a cancellation still waiting for the receiver, or one the receiver rejected, shows as cancelled.
  - With no reason chosen it uses 03.
- **The PAC's other operation.** It can return a stamp's acknowledgement by UUID.
- **Reasons used** for stamped documents cancelled since 2024:

  | Reason | Documents |
  |---|---|
  | 02, errors without relation | 331 |
  | 03, operation not carried out | 194 |
  | 01, errors with relation | 2, neither with a substitute UUID recorded |

  Businesses cancel first and then reissue: the 2 substitution relations (04) since 2024 both relate an invoice that was already cancelled.
- **Volume.** Since 2024 the current system stamped 14,722 invoices, 67 payment receipts, 5 credit notes and 48 type-101 documents.

## Clarifications

### Session 2026-11-16

- Q: What does the API require of the substitute for reason 01? → A: The substitute must be a stamped, live document with the same issuer and recipient. The API does not require the 04 relation, and spec 027 is unchanged. If SAT rejects the substitute, the PAC's rejection is returned.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Stamp a sealed document (Priority: P1)

A billing clerk confirms an invoice, a payment receipt or a credit note, which leaves it sealed, and then stamps it. The document comes back **issued** (stamped), with its UUID, stamp date and SAT seal. From then on its XML, its PDF (with the stamp and the SAT verification QR) and its email (spec 028) are available. It can no longer be reopened or edited.

**Why this priority**: Nothing else in this spec works without a stamped document, and the payment-receipt and credit-note screens (#54, #56) need it to finish their flows.

**Independent Test**: Seal one document of each type, stamp it against the simulated PAC, and read it back. It is issued with a UUID, its XML carries the fiscal stamp, its PDF shows the stamp and says it is simulated, and reopening it is refused.

**Acceptance Scenarios**:

1. **Given** a sealed invoice, **When** the clerk stamps it, **Then** the invoice is issued and its UUID, stamp date, SAT seal, SAT certificate number and provider RFC are stored. The stored XML is the stamped XML.
2. **Given** an issued document, **When** anyone tries to reopen or edit it, **Then** the request is refused with a coded error.
3. **Given** a stamp request whose response was lost, **When** the clerk repeats it, **Then** the document is stamped once: the repeat returns the same UUID, or the state already reached, and never sends the document to the PAC a second time.
4. **Given** the PAC rejects the document, **When** the clerk stamps it, **Then** the document stays sealed and the error carries the PAC's own code and message. The clerk can reopen, fix and stamp again.
5. **Given** the PAC is unavailable, **When** the clerk stamps, **Then** the document stays sealed and the error says the PAC is unavailable and the stamp can be retried.
6. **Given** the PAC times out and the outcome is unknown, **When** the clerk stamps, **Then** the document is left **stamping**. It is not sealed and not issued, and it cannot be reopened or stamped again. It resolves only by asking the PAC whether that document was stamped (Story 3).
7. **Given** a stamped document, **When** it is downloaded, printed or emailed, **Then** the XML, PDF and email carry the stamp: UUID, the TFD original string, the SAT seal and the verification QR.

---

### User Story 2 - Cancel a stamped document (Priority: P1)

An authorised user cancels a stamped document with a SAT reason. Depending on what SAT reports, the cancellation is either accepted at once, so the document is **cancelled**, or it waits for the receiver's acceptance (**cancellation pending**). When it ends, the receiver has accepted it, so the document is cancelled, or rejected it, so the document is back to issued.

**Why this priority**: The cancellation screen (#52) is one of the three that this spec unblocks. Cancelling is also how units held by an invoice are freed for refunds and how a wrong receipt is undone.

**Independent Test**: Against the simulated PAC, cancel one document where the cancellation is accepted at once and one where it needs the receiver. Accept one pending cancellation and reject another. Check the document state and its cancellation history after each step.

**Acceptance Scenarios**:

1. **Given** an issued document that SAT cancels without acceptance, **When** a user cancels it with reason 02 or 03, **Then** it becomes cancelled. The reason, the request time and SAT's answer are recorded in its cancellation history.
2. **Given** an issued document whose cancellation needs the receiver, **When** a user cancels it, **Then** it becomes cancellation pending. It still counts as a live document: its units stay held and it still lowers the invoice balances that later payment receipts use.
3. **Given** a pending cancellation that the receiver accepts, **When** its status is refreshed, **Then** the document becomes cancelled.
4. **Given** a pending cancellation that the receiver rejects, **When** its status is refreshed, **Then** the document goes back to issued and the rejection is kept in its history. A new cancellation can be requested.
5. **Given** a document that SAT reports as not cancellable, **When** a user cancels it, **Then** the request is refused with a coded error, and the document stays issued.
6. **Given** an invoice with a live payment receipt or credit note related to it, **When** a user cancels it, **Then** the request is refused with a coded error naming those documents. Live means sealed, stamping, issued or cancellation pending.
7. **Given** an invoice that becomes cancelled, **Then** its units can be refunded (spec 028's refund rule) and it no longer counts in the previous balance that later payment receipts compute.
8. **Given** a payment receipt that becomes cancelled, **Then** its customer payment can back a new receipt (spec 028).
9. **Given** reason 01, **When** a user cancels, **Then** a substitute UUID is required, and it must be a stamped, live document (issued or cancellation pending) of the same issuer and recipient. Otherwise the request is refused with a coded error before the PAC is called. The API does not require the substitute to relate this document with 04, and spec 027's relation and remaining-quantity rules are unchanged. If SAT refuses the substitute, the PAC's rejection is returned (FR-014's rejection error) and the document stays issued.

---

### User Story 3 - Resolve documents left in flight (Priority: P2)

Some documents are left waiting: a stamp that timed out, or a cancellation that waits for the receiver. A clerk can refresh one document on demand. A periodic job refreshes every waiting document, so none stays waiting just because nobody looked.

**Why this priority**: Without this, a timeout or a pending cancellation leaves a document stuck. The flows in Stories 1 and 2 are still complete without it, as long as someone refreshes the document by hand.

**Independent Test**: With the simulated PAC, leave one document stamping (timeout) and one cancellation pending. Run the periodic job once. The first becomes issued with the UUID the PAC had assigned, or sealed if the PAC never stamped it. The second follows the receiver's answer.

**Acceptance Scenarios**:

1. **Given** a stamping document that the PAC did stamp, **When** it is refreshed, **Then** it becomes issued with the PAC's UUID and stamped XML. It is never stamped a second time.
2. **Given** a stamping document that the PAC did not stamp, **When** it is refreshed, **Then**, once 5 minutes have passed since the stamp request was sent, it returns to sealed and can be stamped again. Within those 5 minutes it stays stamping, so a request still waiting on the PAC is not undone.
3. **Given** the PAC is unavailable during a refresh, **Then** the document keeps its state and the next refresh tries again.
4. **Given** a pending cancellation, **When** the job runs, **Then** the document follows SAT's status: cancelled, back to issued, or still pending.

---

### User Story 4 - See and filter fiscal states and history (Priority: P2)

The UI lists fiscal documents and shows each one's fiscal state: draft, sealed, stamping, issued, cancellation pending, cancelled. It filters by state, and on a document's detail shows its stamp and every cancellation request with its outcome.

**Why this priority**: The three screens need it to show where each document stands. The data already exists once Stories 1–3 work.

**Independent Test**: Create documents in every state through the simulator. Filter the list by each state and get exactly those documents. Read a document that was rejected once and then cancelled; its history shows both requests in order.

**Acceptance Scenarios**:

1. **Given** documents in every state, **When** the list is filtered by one state, **Then** only documents in that state are returned, with the same paging as today.
2. **Given** a document with two cancellation requests, **When** it is read, **Then** both appear with reason, substitute UUID, who requested it, when, SAT's answers and the final outcome.

---

### User Story 5 - Drive every PAC outcome in the simulator (Priority: P2)

A developer or a UI tester reaches every PAC outcome through the API alone, with no code change or redeploy:
- stamped, rejected by the PAC, PAC unavailable, timeout with unknown outcome;
- cancellation accepted at once, pending then accepted, pending then rejected, not cancellable.

**Why this priority**: The screens must handle every outcome before the real PAC exists. The simulator is the only way to reach the failure paths on demand.

**Independent Test**: For each outcome, follow the documented way to select it, run the flow and observe the expected state and error.

**Acceptance Scenarios**:

1. **Given** the simulated PAC, **When** a tester selects an outcome by the documented means, **Then** the next stamp or cancellation of that document follows that outcome.
2. **Given** no outcome is selected, **Then** stamping succeeds, and cancellation follows the simulator's default acceptance rule (FR-019).
3. **Given** the application is configured for production, **When** it starts with the simulated PAC, **Then** it refuses to start and says why.

### Edge Cases

- **Two clerks stamp the same document at once.** Exactly one request reaches the PAC. The other gets the state the first one reached, or a coded "busy" error, and is never sent to the PAC.
- **Stale issue date.** The issue date of a sealed document passes SAT's 72 hours before it is stamped. Stamping is refused with the existing 72-hour error, and the clerk must reopen, update the issue date and seal again (confirm keeps a stored issue date). The PAC is not called.
- **Expired certificate.** The certificate that sealed the document expires before it is stamped. The PAC rejects the document, and Story 1's PAC-rejection path applies.
- **Cancelling a document that is not issued.** A draft, sealed, stamping, pending or cancelled document is refused with a coded error. A draft or sealed document is reopened or left alone, as today.
- **A credit note pending cancellation.** Its invoice cannot be cancelled until the note is cancelled, because a pending note is still live.
- **A cancellation that no one answers.** When SAT's period for the receiver lapses, the outcome is whatever SAT reports for the document. Cancelled means cancelled, valid means back to issued. The API keeps no clock of its own.
- **Documents stamped by the current system.** They are already issued. They can be read, printed, emailed and cancelled through the same flow, because cancellation needs only the UUID and the issuer's certificate.

## Requirements *(mandatory)*

### Functional Requirements

**PAC provider contract**

- **FR-001**: The API MUST define a PAC provider contract that the rest of the API uses for every PAC interaction. It has four operations:
  - **stamp** a sealed CFDI. Returns the stamped XML and the fiscal stamp (TFD 1.1): UUID, stamp date, SAT seal, SAT certificate number and certifying provider RFC.
  - **request cancellation** of a UUID with a SAT reason (01–04) and, for 01, the substitute UUID.
  - **query the SAT status** of a UUID. Returns:
    - valid, cancelled, or not found;
    - whether it can be cancelled: without acceptance, with acceptance, or not at all;
    - the cancellation status: none, requested, pending the receiver, accepted, rejected or expired.
  - **look up a stamp** of a given sealed CFDI (issuer, series, folio and seal). Returns the fiscal stamp and stamped XML if the PAC stamped it, or "not stamped". This is how a timeout is resolved without stamping again.
- **FR-002**: Every contract operation MUST report failure as one of three typed outcomes, never as text to parse:
  - **rejected**, with the PAC's own code and message;
  - **unavailable**, meaning nothing was done and the request can be retried;
  - **timeout**, meaning the outcome is unknown.
- **FR-003**: The contract MUST NOT carry or expose PAC credentials to its callers. Configuration supplies them to the provider. No credential appears in code, tests, fixtures or logs.
- **FR-004**: Which provider is active MUST be a configuration setting. Switching from the simulated PAC to a real one MUST NOT require changes anywhere else in the API.

**Simulated PAC**

- **FR-005**: The simulated provider MUST return a well-formed TFD 1.1 with a fresh random UUID, the current stamp date, and a SAT seal and SAT certificate number that look real. It MUST use a fixed provider RFC reserved for simulation.
- **FR-006**: The simulated provider MUST let a tester choose each outcome through the API alone, per document, with no code change or restart:
  - stamped, rejected (with a chosen code and message), unavailable, timeout;
  - cancellation accepted at once, pending, not cancellable;
  - for a pending cancellation, the receiver accepting or rejecting it.
  The way each outcome is chosen MUST be documented for the UI handoff.
- **FR-007**: The simulated provider MUST remember what it stamped and the status of each cancellation, so look-up and status queries return consistent answers. That includes a timeout in which it did stamp, and one in which it did not.
- **FR-008**: The application MUST refuse to start when the simulated provider is configured in production.
- **FR-009**: A document stamped by the simulated provider MUST say so: its PDF states that it has no fiscal validity, and the provider RFC identifies the simulator.

**Stamping**

- **FR-010**: The API MUST stamp a sealed document of type invoice, payment receipt or credit note. On success the document becomes issued, the fiscal stamp is stored in the document's existing stamp fields, and the stored XML is replaced by the stamped XML.
- **FR-011**: Before calling the PAC, the API MUST durably record that the document is **stamping**. A stamping document:
  - cannot be stamped, reopened, edited or cancelled;
  - is resolved only through the look-up operation (FR-001), on demand or by the periodic job.
- **FR-012**: A document MUST never be sent to the PAC for stamping more than once per seal:
  - concurrent stamp requests on the same document are serialised;
  - a repeated request on an issued document returns it unchanged;
  - a repeated request on a stamping document is refused with a coded error and does not call the PAC.
- **FR-013**: Stamping MUST re-check the 72-hour issue-date rule from spec 027 before calling the PAC.
- **FR-014**: When the PAC rejects or is unavailable, the document MUST stay sealed. The error MUST follow spec 036, and a rejection includes the PAC's code and message as parameters.
- **FR-015**: An issued document MUST NOT be reopened or edited.
- **FR-016**: The XML download and the PDF MUST be available for issued, cancellation-pending and cancelled documents. The email (spec 028) MUST be available for issued and cancellation-pending documents. All three MUST use the stored stamp: UUID, TFD original string, SAT seal and the SAT verification QR. A cancelled document's PDF MUST say it is cancelled.

**Cancellation**

- **FR-017**: The API MUST accept a cancellation request for an issued document with a SAT reason from the `sat_reason_cancellation` catalogue (01–04). For 01 a substitute UUID is required. It must be a stamped, live document (issued or cancellation pending) with the same issuer and recipient; the API does not check for a 04 relation, and a refusal by SAT comes back as the PAC's rejection.
- **FR-018**: Before requesting cancellation, the API MUST refuse it with a coded error when:
  - the document is not issued;
  - for an invoice, any payment receipt or credit note related to it is live (sealed, stamping, issued or cancellation pending). The error lists those documents.
- **FR-019**: Whether a cancellation needs the receiver's acceptance MUST come from the PAC's answer (FR-001's status query and the request's result), never from a rule computed by the API.
  - **Simulator default.** With no outcome chosen, the simulated provider applies the rule in `docs/specs/10-fiscal-documents.md` §2: no acceptance within 24 hours, acceptance after that. §2 counts from issuance; the simulator counts from its own stamp date (`stamped_at`), the only date it records, which is at most 72 hours after issuance.
  - **Real rules.** SAT's full list of exceptions (for example by amount, document type or the public-in-general RFC) belongs to SAT and the real PAC. It is not reproduced in the API.
- **FR-020**: A cancellation that needs acceptance MUST leave the document **cancellation pending**. A pending document still counts as live everywhere spec 027 and 028 count live documents:
  - the units it holds on order lines;
  - the refund rule;
  - invoice balances for payment receipts;
  - credit-note limits;
  - a payment receipt's link to its customer payment.
- **FR-020a**: While an invoice has an open cancellation request (requested or pending), no payment receipt or credit note can be added to it or confirmed against it (`invoice_not_payable` / `invoice_not_creditable`). Otherwise a receipt or note could be confirmed against the invoice while its cancellation is with the PAC. This is the one exception to FR-020's "live everywhere": the invoice still holds its units and balances, but it takes no new receipts or notes.
- **FR-021**: The document's state MUST follow SAT's status as the PAC reports it:
  - cancelled when SAT reports it cancelled;
  - back to issued when the cancellation is rejected, or SAT still reports it valid with no cancellation in process;
  - pending otherwise.
- **FR-022**: When a document becomes cancelled, the API MUST set its existing cancellation fields (`cancelled`, cancellation date, reason, substitute UUID). From that moment it stops counting as live, so spec 028's refund rule and the previous balance of later payment receipts no longer include it. When the cancelled document is a credit note, the orders on its invoice owe again, and their `paid` flag is updated as spec 033 FR-011 requires for a credit note's cancellation, in the same transaction, exactly as reopening a credit note already does.
- **FR-023**: Each cancellation request MUST be recorded in a cancellation history:
  - document, reason, substitute UUID;
  - requester, request time;
  - each change of SAT status, with its time, and the time of the last status query;
  - the final outcome: accepted, expired, rejected, not cancellable, or failed (the PAC was unavailable, or SAT never received it).
  A document can have several requests. At most one is open at a time.
- **FR-024**: The API MUST offer an on-demand refresh of a single document that is stamping or cancellation pending. A stamping document for which the PAC has no stamp returns to sealed only 5 minutes after the stamp request was sent.
- **FR-025**: A periodic job MUST refresh every stamping and cancellation-pending document. A PAC that is unavailable or times out leaves the document unchanged for the next run. One document's failure MUST NOT stop the others.

**Reads, lists and rules**

- **FR-026**: List and detail reads MUST report each document's fiscal state: draft, sealed, stamping, issued, cancellation pending, cancelled. The list MUST filter by any of them. The existing values and their meaning are unchanged: issued is the stamped state.
- **FR-027**: The detail read MUST include the fiscal stamp fields (UUID, stamp date, SAT certificate number, provider RFC, a simulated flag) and the cancellation history.
- **FR-028**: Stamping, cancelling and refreshing MUST use the existing fiscal-document privileges: Update on Fiscal Documents (23). Like the existing fiscal-document routes, they have no facility scope, since fiscal documents belong to an issuer. The periodic job runs as the system.
- **FR-029**: Each of these state changes MUST record an audit event like the existing fiscal-document actions do: stamped, stamp resolved (found, or sealed again), cancellation requested, and each way a cancellation request ends (pending, accepted, rejected, refused, failed). A stamp attempt that ends sealed or stamping in the same request is not audited (research R10).
- **FR-030**: Every error MUST follow spec 036, with a stable code and parameters. The new codes and their parameters MUST be added to spec 036's contract.
- **FR-031**: Nothing in this feature writes to the current system or depends on it. The current system keeps stamping its own documents until the cutover (#57). The API writes only the shared fiscal tables (`fiscal_document`, `fiscal_document_xml`), its own new tables and, when a credit note is cancelled, `sales_order.paid` through spec 033's existing paid-flag update (FR-022), never calls the current system, and writes `incidence` audit rows (FR-029), as spec 028's email does, and treats documents the current system stamped like its own.

### Key Entities

- **Fiscal document** (existing). Gains the stamping and cancellation-pending states. It keeps its existing stamp fields (UUID, stamp date, SAT seal, SAT certificate, provider RFC) and cancellation fields (cancelled, date, reason, substitute).
- **Fiscal document XML** (existing). Holds the sealed XML until stamping and the stamped XML after.
- **Cancellation request** (new). One per request:
  - document, reason, substitute UUID, requester, request time;
  - how the cancellation proceeds: at once, with acceptance, or not cancellable;
  - current status and final outcome.
- **Cancellation status check** (new, or folded into the request). Each SAT status answer received for a request, with its time.
- **Stamp attempt** (new or a marker on the document). Records that a document is stamping and since when (the audit events record who stamped it, or that the job resolved it), so a timeout is resolved by look-up.
- **Simulated PAC record** (simulator only). What the simulator stamped and each simulated cancellation's state and chosen outcome. It never exists with a real provider.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every outcome listed in User Story 5 can be reached through the API alone, on dev, by following the handoff's instructions: the 13 paths listed in contracts/endpoints.md "Driving each scenario", 0 code changes.
- **SC-002**: In a concurrency test of 20 simultaneous stamp requests on one sealed document, the PAC receives exactly 1 stamp call and the document ends with 1 UUID. Measured on the test deployment, whose database enforces row locks.
- **SC-003**: Every timeout left stamping is resolved by the first periodic run, after the 5-minute grace, in which the PAC answers. No document stays stamping after 2 such runs with the PAC available.
- **SC-004**: No document is ever stamped twice. Across the test suite and the dev scenarios, each sealed document maps to at most one UUID.
- **SC-005**: An invoice cancelled through this flow frees its units for refunds and drops out of later receipts' previous balance in the same request that marks it cancelled.
- **SC-006**: Replacing the simulated provider with another implementation of the contract changes only configuration and the provider itself. This is checked by a second test double of the contract running the same flows.
- **SC-007**: A credential scan of the repository, test fixtures and logs from a full test run finds no PAC credentials.

## Assumptions

- **"§2b"** refers to `docs/specs/10-fiscal-documents.md` §2 (Fiscal Documents): its Confirm, Cancel and Business Rules sections and the spec 027 and 028 notes. The file has no section numbered 2b.
- **Stamping is its own action on a sealed document**, after Confirm, which keeps sealing as specs 027 and 028 define it. The UI can call both from one button. In the current system Confirm stamped directly.
- **The stamped state keeps its existing name, `issued`**, which spec 020 and the UI already use. "Stamped" in this spec means issued.
- **The PAC is assumed to support the look-up operation** (FR-001). Facturación México returns a stamp's acknowledgement by UUID, but a look-up by sealed document (issuer, series and folio) must be confirmed in #160. If the PAC cannot do it, #160's provider must implement the look-up some other way that does not stamp twice. The contract does not change.
- **The receiver-acceptance rule** is SAT's, and arrives through the PAC. §2 states only the 24-hour rule, and the current system never tracked acceptance at all. The simulator defaults to §2's rule; the real rules are confirmed against the PAC in #160.
- **Choosing a simulated outcome** uses a per-document instruction that the simulator reads, for example a marker in the document's comment or a dev-only endpoint. The plan picks the means; the handoff documents it.
- **Retentions, payroll, transfer and global-invoice CFDIs are out of scope**, as are the CFDI package, the real PAC client, connecting them (#160, #46, #51) and the invoicing cutover (#57).
- **The mbe-ui handoff** for #52, #54 and #56 opens after this spec merges. It lists the endpoints, states, error codes and how to drive each simulated outcome.
- **SAT reversals are not detected.** Cancelled documents are not queried again, so a cancellation that SAT later reverses is not seen by the API.
- **Schema changes** go through numbered migrations on mbe_dev and are additive. The current system ignores the new tables and columns.
