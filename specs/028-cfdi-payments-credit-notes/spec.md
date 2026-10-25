# Feature Specification: CFDI Payment Receipts, Credit Notes and Email

**Feature Branch**: `028-cfdi-payments-credit-notes`

**Created**: 2026-10-25

**Status**: Draft

**Input**: Implements three items of the CFDI invoicing epic (mictlanix/mbe-roadmap#5), within the §2b scope of `docs/specs/10-fiscal-documents.md`:
- mictlanix/mbe-roadmap#53, the payment complement;
- mictlanix/mbe-roadmap#55, CFDI credit notes;
- mictlanix/mbe-roadmap#50, email and QR.

It builds on spec 027, which delivered issuer batches, the invoice editor and sealing (mictlanix/mbe-roadmap#44, #47).

Payment receipts and credit notes stop at a **sealed, unstamped** document, as invoices do. Stamping (#46) and cancellation (#51) come later through a standalone PAC client library. Invoicing moves from the current system in one cutover (#57), and the two systems never issue CFDIs at the same time.

## Findings that shape this spec

### The current system (`mictlanix/mbe`)

- **Payment receipts (type 200, Pagos 2.0).** A clerk creates one by hand: terms "immediate", payment form "cash" and usage P01. The clerk then:
  - types the payment date, amount, payment form, operation number and ordering bank;
  - relates documents found by series, folio or UUID: stamped, not cancelled, same issuer and recipient, any type except credit notes and receipts;
  - types, for each related invoice, the installment number, the previous balance and the amount paid.
- **Nothing is computed for receipts.** Every new relation starts at installment 1 with a zero balance, and nothing checks the amount paid against the previous balance. The only rule is that the amounts paid may not exceed the payment amount.
- **The receipt XML** (`PaymentReceiptToCFDv40`) writes:
  - a zero-value Comprobante: currency XXX, type P, usage CP01, and one "Pago" concept 84111506/ACT;
  - one `Pago` with its date, form, MXN at rate 1, amount and operation number;
  - one `DoctoRelacionado` per invoice, with the installment, previous balance, amount paid and remaining balance.

  Its tax breakdown has defects:
  - It **back-computes the base from the amount paid at a fixed 16 % VAT**. A related invoice with 0 % or exempt lines therefore gets a 16 % tax.
  - It writes no `Totales` node when any related invoice has no taxes, although Pagos 2.0 requires that node.
  - It marks every related document as "subject to tax", whatever the invoice says.
- **Credit notes (type 100, egreso)** are built like invoices:
  - lines added by hand, as products at their list price;
  - relations to stamped invoices, written with relation type 01 whatever was stored;
  - payment method and form taken from the editor's defaults.

  No credit note was ever made from a refund. The current system's refunds never touch fiscal documents: they pay the customer back in cash or as **store credit**, which the current system and this API also call a "credit note" (`credit_note`, not a CFDI).
- **Email.** It sends a stamped CFDI to one address:
  - from the default sender, with an optional CC from configuration;
  - subject "Comprobante Fiscal - {RFC} - {series}{folio:000000}";
  - a Spanish body with the folio, both RFCs, the total and the SAT verification link;
  - the PDF and the XML attached, named `{RFC}-{series}{folio:000000}`.

  It sends in the background and always reports success.
- **QR.** The CFDI 4.0 print shows SAT's verification QR: `verificacfdi…?id&re&rr&tt&fe`.

### This API today

- **Spec 020** renders the CFDI 4.0 PDF **with** SAT's verification QR (`cfdi.sat_qr_payload`), and serves the stored XML. The QR part of #50 is therefore already done.
- **Spec 024's mail service** sends a message with attachments. The quote email uses it: the caller learns the outcome, and each send is recorded as an incidence.
- **Spec 011 refunds** pay back in cash or as store credit (`credit_note` + `customer_payment`). They check quantities against what was sold, but not against what was invoiced.
- **Spec 027** builds and seals Invoice CFDIs. Its "sealed" state, serial lock, certificate choice and original-string stylesheet apply to every type.

### Measured on mbe_dev (2026-10-25)

**Payment receipts (type 200), last 12 months**
- **Volume:** 23 issued, 2 of them cancelled, from 4 issuers, in 21 live receipts.
- **Relations:**
  - 29 relations, 27 at installment 1 and 2 at installment 2;
  - every amount is in MXN at rate 1;
  - 3 receipts relate PUE invoices, which SAT does not allow.
- **Coverage:** only 18 of the 42 PPD invoices issued in the period have a receipt.
- **How receipts map to customer payments:**
  - **no link** is stored;
  - matching by customer, amount and date (±10 days) finds a customer payment for only 3 of 21;
  - 9 of the 21 relate invoices with no sales order, so no customer payment exists for them;
  - for the others, the receipt's date or amount differs from the recorded payment. For example, receipt 63634 says 800.00 where the payment was 1,050.00, and receipt 58949 is dated 8 Oct where its payment is 5 Nov.

**Credit notes**
- **Fiscal (type 100):** 3 in 12 months, 123 since 2019. Their lines were typed by hand, often one concept ("DEVOLUCIÓN", 84111506), and none comes from a refund.
- **Advance payments applied (type 101):** 25 in the same period. They are not in this feature's scope.

**Refunds, last 12 months**
- 1,540 completed refunds.
- 210 touch order lines that were invoiced.
- In 75 refunds, the goods returned had **already been invoiced** when they were refunded.

Against those 75 refunds stand 3 fiscal credit notes. The rest of the returned invoiced goods never got one.

## Clarifications

### Session 2026-10-25

*(Open for `/speckit-clarify`. The request asks to settle there how refunds and credit payments relate to fiscal credit notes.)*

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Billing issues a payment receipt for a PPD invoice (Priority: P1)

A customer pays part or all of an invoice that was issued "in installments or deferred" (PPD). Billing creates a payment receipt for that payment, relates the invoices it pays, and confirms it. Each related invoice's installment number and previous balance are computed from the receipts already sealed or stamped for it. The receipt is sealed and waits to be stamped.

**Why this priority**: SAT requires a payment receipt for every payment of a PPD invoice. In the last 12 months, 24 of 42 PPD invoices received none. Without this, credit sales cannot be invoiced from the new MBE.

**Independent Test**:
1. Seal a PPD invoice for 10,000.
2. Create a receipt for a 4,000 payment and relate the invoice. The receipt shows installment 1, previous balance 10,000 and remaining 6,000. Confirm it.
3. Create a second receipt for 6,000. It shows installment 2, previous balance 6,000 and remaining 0.

Both sealed XMLs validate against SAT's CFDI 4.0 and Pagos 2.0 schemas, and their seals verify.

**Acceptance Scenarios**:

1. **Given** a customer payment, **When** billing creates a receipt from it, **Then**:
   - the payment date, amount, form and reference are filled in from the payment;
   - the PPD invoices covering the orders that payment was applied to are proposed as relations.
2. **Given** a draft receipt, **When** billing relates a stamped PPD invoice of the same issuer and recipient, **Then** the relation shows:
   - the next installment number;
   - the previous balance: the invoice total less what earlier sealed or stamped receipts paid;
   - an amount paid that defaults to the smaller of that balance and the unassigned payment amount.
3. **Given** a relation, **When** the amount paid exceeds the invoice's previous balance, **Then** the request is refused.
4. **Given** a draft receipt, **When** the relations add up to more than the payment amount, **Then** the request is refused. **When** they add up to less, **Then** confirm is refused until they match.
5. **Given** a PUE invoice, a credit note or an unstamped document, **When** it is related to a receipt, **Then** the request is refused.
6. **Given** an invoice whose lines carry 16 %, 0 % or exempt tax, **When** a receipt pays part of it, **Then** the receipt's tax breakdown splits the amount paid in the invoice's own proportions, never at a fixed 16 %.

---

### User Story 2 - Billing issues a credit note against invoices (Priority: P1)

Billing issues a fiscal credit note to return or discount part of what was invoiced. The credit note relates the invoice or invoices it adjusts with relation type 01, carries the returned products or a discount concept, and is sealed to wait for stamping.

**Why this priority**: the goods in 75 refunds in a year had already been invoiced, and only 3 credit notes were issued against them. Fiscal credit notes are what makes refunding invoiced goods correct.

**Independent Test**:
1. Seal a credit note related to a stamped invoice, with one line copied from that invoice at a smaller quantity.
2. Check that the sealed XML is an egreso (E) with `CfdiRelacionados` type 01 listing the invoice's UUID.
3. Check that it validates against SAT's schema and that its seal verifies.

**Acceptance Scenarios**:

1. **Given** a stamped invoice, **When** billing creates a credit note for it, **Then**:
   - the credit note takes the invoice's customer, recipient, issuer and store;
   - its series is the issuer's credit-note series;
   - it relates the invoice with type 01.
2. **Given** a draft credit note, **When** billing adds lines from a related invoice, **Then** each line keeps that invoice line's price, discount and tax, at a quantity no larger than what the invoice still holds after earlier credit notes.
3. **Given** a draft credit note, **When** billing adds a product or a discount concept by hand, **Then** it is kept within the invoice line rules of spec 027.
4. **Given** a credit note whose total exceeds what remains of the related invoices after earlier credit notes, **When** it is confirmed, **Then** confirm is refused.
5. **Given** a confirmed credit note, **Then** it has the next serial of its series, a sealed XML and the state "sealed". It can be reopened as an invoice can.

---

### User Story 3 - A refund of invoiced goods produces a fiscal credit note, or is refused (Priority: P2)

When a refund returns goods that were already invoiced, the system issues the fiscal credit note for them; otherwise the refund cannot be completed (the 22 Apr decision on mictlanix/mbe-roadmap#55).

**Why this priority**: today such refunds silently leave the invoice overstating the sale. The rule closes that gap, and it depends on stories 1–2 only for the credit note itself.

**Independent Test**:
1. Complete a refund of 2 units of an order line whose 5 units are on a stamped invoice. The system produces a draft credit note for 2 units, related to that invoice.
2. Refund units that were never invoiced. No credit note is produced.

**Acceptance Scenarios**:

1. **Given** an order line partly or fully on a stamped invoice, **When** a refund of that line is completed, **Then** a draft credit note is created:
   - for the refunded units that had been invoiced;
   - related to that invoice;
   - at the invoice's prices;
   - linked to the refund.
2. **Given** a refund of units that were never invoiced, **When** it is completed, **Then** no credit note is created and the refund behaves as today.
3. **Given** a refund of invoiced units that cannot have a credit note, **When** completion is attempted, **Then** it is refused with a message naming the reason. Such cases include an invoice that is sealed but not yet stamped, an issuer without a credit-note series, or a recipient without a regime or postal code.

---

### User Story 4 - Billing emails a stamped CFDI (Priority: P2)

Billing sends a stamped CFDI's PDF and XML to one or more addresses and learns whether the mail server accepted it.

**Why this priority**: customers receive their CFDIs by email today. It is a small change on top of spec 024's mail service and spec 020's PDF.

**Independent Test**:
1. Email a stamped CFDI 4.0 invoice to two addresses.
2. Check that the message carries the PDF and XML, named `{RFC}-{series}{folio:000000}`, with the current system's subject and body.
3. Check that the send is recorded.

**Acceptance Scenarios**:

1. **Given** a stamped CFDI 4.0 document of any type, **When** it is emailed to 1–10 addresses, **Then**:
   - one message goes to them with the PDF and the stored XML attached;
   - the response states the recipients and when the message was sent.
2. **Given** a draft, sealed or pre-4.0 document, **When** it is emailed, **Then** the request is refused.
3. **Given** a mail server that refuses the message, **When** it is emailed, **Then** the caller is told it was not sent and nothing is recorded.
4. **Given** no mail configuration, **When** a document is emailed, **Then** the request is refused as "email not configured".

---

### Edge Cases

- **Several invoices in one receipt:** one payment can settle several invoices. Each gets its own installment and balance, and their amounts must add up to the payment amount.
- **Earlier receipts:** an invoice's earlier receipts count when they are sealed or stamped and not cancelled. Drafts do not count, and a cancelled receipt frees its amount.
- **Receipts from the current system:** they count toward installments and balances exactly like new ones, so the first new receipt after the cutover continues the sequence.
- **Payment date:**
  - it cannot be in the future;
  - it may predate the receipt by more than the 72-hour rule, which applies only to the receipt's issue date;
  - it cannot precede the related invoice's date.
- **A payment with no customer payment record:** this covers payments for standalone invoices (9 of 21 receipts). Billing creates the receipt by hand with the same rules.
- **Credit notes and receipts on the same invoice:** a credit note lowers what the invoice still owes, so a later receipt's previous balance counts sealed or stamped credit notes too.
- **A refund that spans an invoiced and an uninvoiced line:** only the invoiced units go on the credit note.
- **A refund touching two invoices:** one credit note relates both, if they share issuer and recipient. Otherwise there is one credit note per issuer and recipient.
- **A cancelled draft credit note created from a refund:** the refund stays completed, and the credit note can be recreated.
- **Store credit or cash:** the refund's payout (cash or store credit) is unchanged. The fiscal credit note records the reduction of the sale; it does not move money.
- **Email of a cancelled CFDI:** it is refused.

## Requirements *(mandatory)*

### Functional Requirements

**Payment receipts (#53)**

- **FR-001**: The system MUST create payment receipt drafts (type 200):
  - for a customer and recipient, under the issuer's payment-receipt series;
  - optionally from a customer payment, whose date, amount, form and reference it copies.
- **FR-002**: When created from a customer payment, the system MUST propose relations to the stamped PPD invoices that hold lines of the orders that payment was applied to. Billing may remove them, or add others.
- **FR-003**: A receipt MUST accept relations only to documents that:
  - are stamped, not cancelled, PPD and of type Invoice;
  - share the receipt's issuer and recipient;
  - still have a balance.
- **FR-004**: For each relation the system MUST compute:
  - the installment number: 1 plus the earlier sealed or stamped, non-cancelled receipts relating that invoice;
  - the previous balance: the invoice total less what those receipts paid and less its sealed or stamped credit notes;
  - the remaining balance after this payment.

  Billing sets only the amount paid. It MUST be greater than 0 and no more than the previous balance.
- **FR-005**: The amounts paid MUST NOT exceed the payment amount, and confirm MUST require that they equal it.
- **FR-006**: The receipt header MUST be editable on a draft:
  - payment date: not in the future, and not before the related invoices;
  - payment form: a SAT form other than 99;
  - amount;
  - operation number (≤ 100 characters);
  - ordering bank name.
- **FR-007**: The receipt XML MUST follow Pagos 2.0 as the current system writes it:
  - a zero-value Comprobante, type P, currency XXX, usage CP01, one "Pago" concept;
  - one `Pago`;
  - one `DoctoRelacionado` per relation.

  Corrected from the current system:
  - each related document's taxes are the invoice's own transfer breakdown, by rate and exempt, scaled to the amount paid;
  - a document whose invoice carried no taxes is marked "not subject to tax";
  - `Totales` is always written.

**Credit notes (#55)**

- **FR-008**: The system MUST create credit note drafts (type 100, egreso):
  - for one or more stamped, non-cancelled invoices of the same issuer and recipient, related with type 01;
  - under the issuer's credit-note series.
- **FR-009**: A credit note's lines MUST come from three sources:
  - its related invoices' lines, at a quantity no larger than what the invoice line still holds after earlier sealed or stamped credit notes;
  - products added by hand (Create on StandaloneFiscalDocuments, as in spec 027);
  - a discount concept (SAT 84111506, unit ACT) at a stated amount.
- **FR-010**: Confirm MUST refuse a credit note whose total exceeds what its related invoices still hold after earlier credit notes. Otherwise it seals the credit note exactly as spec 027 seals an invoice: serial, XML, original string, seal and the sealed state. The XML differs in these values:
  - `TipoDeComprobante` E;
  - `CfdiRelacionados` type 01;
  - `MetodoPago` PUE.

**Refunds of invoiced goods (#55, 22 Apr decision)**

- **FR-011**: Completing a refund MUST determine, for each refunded order line, how many of the refunded units were already invoiced on stamped invoices. If any were, it MUST create a draft credit note for those units, related to those invoices and linked to the refund.
- **FR-012**: A refund of invoiced units MUST be refused when no credit note can be produced, with a message naming the reason. This covers an invoice that is sealed but not stamped, an issuer with no credit-note series, or a recipient missing its regime or postal code.

**Email and QR (#50)**

- **FR-013**: The system MUST email a stamped CFDI 4.0 document of any type to 1–10 addresses:
  - with its PDF (spec 020) and its stored XML attached, named `{RFC}-{series}{folio:000000}`;
  - with the current system's Spanish subject and body.

  It MUST report whether the server accepted the message, and record each accepted send.
- **FR-014**: The PDF MUST keep showing SAT's verification QR, which spec 020 already renders. No change is required beyond a test that asserts it for each type emailed.

**Common**

- **FR-015**: Receipts and credit notes MUST reuse spec 027's machinery:
  - the draft, sealed and reopen states;
  - the serial lock per issuer and series;
  - the 72-hour issue-date rule;
  - the certificate choice;
  - sealing.

  Spec 020's reads MUST report them like invoices.
- **FR-016**: Privileges MUST follow spec 027:
  - FiscalDocuments (23) to create, edit, confirm and reopen;
  - StandaloneFiscalDocuments (56) for hand-added products;
  - FiscalDocuments Read to email.

  A refund keeps its own privileges. The credit note it creates needs no extra privilege.
- **FR-017**: `docs/specs/10-fiscal-documents.md` MUST describe what is built: payment receipts, credit notes, the refund rule and email. It MUST mark stamping and cancellation as pending.

### Key Entities

- **Payment receipt:** a fiscal document of type 200. It has a payment (date, form, amount, reference, ordering bank) and relations to the invoices it pays. Each relation has an installment, a previous balance, the amount paid and the remaining balance.
- **Fiscal credit note:** a fiscal document of type 100 with lines and type-01 relations to the invoices it reduces, optionally linked to the refund that produced it.
- **Customer payment:** money received, applied to sales orders (spec 011). It is the optional source of a receipt.
- **Refund:** goods returned by a customer (spec 011). It is the optional source of a credit note.
- **Store credit:** spec 011's non-fiscal "credit note" (`credit_note`). It is unrelated to the fiscal credit note except through the refund both may come from.
- **Email send:** a record that a CFDI was mailed: to whom, by whom and when.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every receipt and credit note sealed in tests validates against SAT's CFDI 4.0 schema, and receipts also against the Pagos 2.0 schema. Every seal verifies with the issuer's certificate.
- **SC-002**: For the 21 live payment receipts and 3 credit notes issued on mbe_dev in the last 12 months:
  - rebuilding each from its stored data gives the stored original string, apart from the differences the corrections in FR-007 require, which are listed;
  - the computed installments and previous balances equal the stored ones wherever the stored ones are consistent.
- **SC-003**: Two receipts paying one PPD invoice in two installments give installments 1 and 2, and the second's previous balance equals the first's remaining balance.
- **SC-004**: On the test deployment:
  - a refund of invoiced units on a real order produces a related draft credit note, which seals;
  - a refund of uninvoiced units produces none;
  - no table outside fiscal documents, refunds and their payments changes.
- **SC-005**: An emailed stamped invoice arrives with both attachments. A refused send returns an error and records nothing.

## Assumptions

- **Advance payments applied (type 101)** are out of scope: there were 25 in 12 months, but they are not requested.
- **MXN only.** Every receipt relation in 12 months was in MXN at rate 1. Receipts and credit notes are MXN at rate 1, and the XML writes `MonedaP`/`MonedaDR` MXN, `EquivalenciaDR` 1 and `TipoCambioP` 1.
- **Credit note payment terms:** PUE. The payment form is the related invoice's, unless that is 99, in which case billing chooses it. Usage defaults to G02 (returns, discounts or bonuses).
- **No schema change is expected.** Receipts use the existing `payment_date`, `payment_amount`, `payment_reference` and `reference` columns of `fiscal_document`, and the existing relation columns. The links from a receipt to its customer payment and from a credit note to its refund need a place to live, which `/speckit-plan` decides.
- **Receipts from the current system** are read as stored. Their typed installments and balances are trusted for computing the next ones, even where inconsistent.
- **Email CC** is not configured in the current system, so none is added. The sender is spec 024's.
- **The refund rule** applies to refunds completed in the new MBE after this feature ships. Past refunds of invoiced goods are not revisited.

## Out of Scope

- Stamping (#46) and cancellation (#51), including SAT cancellation of a credit note or receipt.
- Advance payments applied (type 101), foreign currency, and retentions.
- The UI (mbe-ui), including mictlanix/mbe-roadmap#56.
- Automatic receipts without billing's confirmation; reminders for PPD invoices without receipts.
- Changes to how refunds pay back (cash or store credit).
