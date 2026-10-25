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

- Q: What happens when a refund returns goods that were already invoiced (75 refunds in 12 months, 3 credit notes)? → A: The refund is refused until the invoice is cancelled. Refunds never create credit notes; a fiscal credit note stays a billing document (US2).
- Q: Should a payment receipt be tied to a recorded customer payment? → A: Either. It is created from a customer payment (pre-filled, invoices proposed, link stored) or by hand with the same rules, for payments with no record, such as for standalone invoices.

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
   - the previous balance: the invoice total less what earlier sealed or stamped receipts paid and less its sealed or stamped credit notes;
   - an amount paid that defaults to the smaller of that balance and the unassigned payment amount.
3. **Given** a relation, **When** the amount paid exceeds the invoice's previous balance, **Then** the request is refused.
4. **Given** a draft receipt, **When** the relations add up to more than the payment amount, **Then** the request is refused. **When** they add up to less, **Then** confirm is refused until they match.
5. **Given** a PUE invoice, a credit note or an unstamped document, **When** it is related to a receipt, **Then** the request is refused.
6. **Given** an invoice whose lines carry 16 %, 0 % or exempt tax, **When** a receipt pays part of it, **Then** the receipt's tax breakdown splits the amount paid in the invoice's own proportions, never at a fixed 16 %.

---

### User Story 2 - Billing issues a credit note against invoices (Priority: P1)

Billing issues a fiscal credit note to return or discount part of what was invoiced. The credit note relates the one invoice it adjusts with relation type 01, carries the returned products or a discount line, and is sealed to wait for stamping.

**Why this priority**: billing needs credit notes for returns and discounts on invoiced sales. Refunds of invoiced goods are refused (US3), so a credit note is how such a sale is reduced without cancelling the invoice.

**Independent Test**:
1. Seal a credit note related to a stamped invoice, with one line copied from that invoice at a smaller quantity.
2. Check that the sealed XML is an egreso (E) with `CfdiRelacionados` type 01 listing the invoice's UUID.
3. Check that it validates against SAT's schema and that its seal verifies.

**Acceptance Scenarios**:

1. **Given** a stamped invoice, **When** billing creates a credit note for it, **Then**:
   - the credit note takes the invoice's customer, recipient, issuer and store;
   - its series is the issuer's credit-note series;
   - it relates the invoice with type 01.
2. **Given** a draft credit note, **When** billing adds lines from the related invoice, **Then** each line keeps that invoice line's product, SAT keys, price, discount and tax, at a quantity no larger than the invoice line's.
3. **Given** a draft credit note, **When** billing adds a product by hand, for example a service product renamed "Descuento" at the discount's amount, **Then** it is kept within the invoice line rules of spec 027.
4. **Given** a credit note whose total exceeds what remains of the related invoices after earlier credit notes, **When** it is confirmed, **Then** confirm is refused.
5. **Given** a confirmed credit note, **Then** it has the next serial of its series, a sealed XML and the state "sealed". It can be reopened as an invoice can.

---

### User Story 3 - A refund of invoiced goods is refused until the invoice is cancelled (Priority: P2)

A refund may return only units that are not on a live invoice. Units on a sealed or stamped invoice that is not cancelled cannot be refunded until that invoice is cancelled (the 22 Apr decision on mictlanix/mbe-roadmap#55, clarified 2026-10-25).

**Why this priority**: today such refunds silently leave the invoice overstating the sale: 75 of them in 12 months, mostly paid back as store credit. The rule stops new ones. It is independent of stories 1, 2 and 4.

**Independent Test**:
1. Refund 2 units of an order line whose 5 units are on a stamped invoice. The refund is refused with a message naming the invoice.
2. Refund units of the same order that were never invoiced. The refund goes through as today.

**Acceptance Scenarios**:

1. **Given** an order line whose units are on a sealed or stamped invoice that is not cancelled, **When** a refund of those units is created, edited or completed, **Then** it is refused (409). The message names the invoice's series and folio.
2. **Given** an order line of 10 with 6 on a live invoice, **When** a refund of up to 4 is completed, **Then** it goes through. A refund of 5 is refused.
3. **Given** a refund of units that were never invoiced, or whose invoice was cancelled, **When** it is completed, **Then** it behaves as today.
4. **Given** units on a draft invoice, **When** they are refunded, **Then** the refund goes through, because a draft reserves nothing (spec 027). Confirming that invoice later refuses the quantity the refund took.

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
- **Credit notes and receipts on the same invoice:** a credit note lowers what the invoice still owes, so a later receipt's previous balance counts the related invoice's sealed or stamped credit notes too.
- **One live receipt per customer payment:** a customer payment can back only one receipt that is not cancelled. A second one is refused.
- **A refund that spans an invoiced and an uninvoiced line:** it is refused as a whole, and the message names the invoiced line and its invoice. The clerk can refund the uninvoiced line alone.
- **A sealed, unstamped invoice:** its units are refused too. Billing can reopen it and remove the line (spec 027), which frees the units.
- **Cancellation is not built yet** (#51): until it is, units on a stamped invoice issued by the new MBE cannot be refunded at all. Invoices cancelled in the current system before the cutover free their units.
- **Store credit or cash:** a refund that is allowed pays back as today. A fiscal credit note issued by billing records a reduction of the sale; it does not move money.
- **Email of a cancelled CFDI:** it is refused.

## Requirements *(mandatory)*

### Functional Requirements

**Payment receipts (#53)**

- **FR-001**: The system MUST create payment receipt drafts (type 200):
  - for a customer and recipient, under the issuer's payment-receipt series;
  - optionally from a customer payment, whose date, amount, form and reference it copies. The receipt keeps a link to that payment.
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
  - operation number (≤ 50 characters, the column's size; the longest stored is 28);
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
  - for exactly one stamped, non-cancelled Invoice, related with type 01, taking its issuer, customer, recipient and store;
  - under the issuer's credit-note series.

  Every credit note on mbe_dev relates one invoice (112) or none (11).
- **FR-009**: A credit note's lines MUST come from two sources:
  - the related invoice's lines, at a quantity no larger than the invoice line's;
  - products added by hand (Create on StandaloneFiscalDocuments, as in spec 027), whose name, code and price billing may edit, e.g. a discount or a returned service.
- **FR-010**: Confirm MUST refuse a credit note whose total exceeds the related invoice's total, less its earlier sealed or stamped, non-cancelled credit notes. Otherwise it seals the credit note exactly as spec 027 seals an invoice: serial, XML, original string, seal and the sealed state. The XML differs in these values:
  - `TipoDeComprobante` E;
  - `CfdiRelacionados` type 01;
  - `MetodoPago` PUE.

**Refunds of invoiced goods (#55, 22 Apr decision, clarified 2026-10-25)**

- **FR-011**: A refund MUST NOT return more of an order line than is sold and not on a live invoice. A live invoice is sealed or stamped and not cancelled, so the refundable quantity is the line's quantity, less the units refunded before, less the units on live invoices. Creating, editing or completing a refund over that limit MUST be refused (409), naming the invoice's series and folio.
- **FR-012**: Refunds MUST NOT create fiscal credit notes. A fiscal credit note is always issued by billing (FR-008 to FR-010).

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

  Refunds keep their own privileges.
- **FR-017**: `docs/specs/10-fiscal-documents.md` MUST describe what is built: payment receipts, credit notes, the refund rule and email. It MUST mark stamping and cancellation as pending.

### Key Entities

- **Payment receipt:** a fiscal document of type 200. It has a payment (date, form, amount, reference, ordering bank) and relations to the invoices it pays. Each relation has an installment, a previous balance, the amount paid and the remaining balance.
- **Fiscal credit note:** a fiscal document of type 100 with lines and a type-01 relation to the one invoice it reduces. Billing issues it; refunds never do.
- **Customer payment:** money received, applied to sales orders (spec 011). It is the optional source of a receipt.
- **Refund:** goods returned by a customer (spec 011). It may only return units that are not on a live invoice.
- **Store credit:** spec 011's non-fiscal "credit note" (`credit_note`), a refund's payout. It is unrelated to the fiscal credit note.
- **Email send:** a record that a CFDI was mailed: to whom, by whom and when.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Every receipt and credit note sealed in tests validates against SAT's CFDI 4.0 schema, and receipts also against the Pagos 2.0 schema. Every seal verifies with the issuer's certificate.
- **SC-002**: For the 21 live payment receipts and 3 credit notes issued on mbe_dev in the last 12 months:
  - rebuilding each from its stored data gives the stored original string, apart from the differences the corrections in FR-007 require, which are listed;
  - the computed installments and previous balances equal the stored ones wherever the stored ones are consistent.
- **SC-003**: Two receipts paying one PPD invoice in two installments give installments 1 and 2, and the second's previous balance equals the first's remaining balance.
- **SC-004**: On the test deployment, against real orders:
  - a refund of units on a stamped invoice is refused, naming the invoice;
  - a refund of uninvoiced units of the same order is accepted when its line is edited on a draft refund;
  - the draft is then cancelled, never completed, so no stock or money moves on mbe_dev and nothing changes outside that draft refund.
- **SC-005**: An emailed stamped invoice arrives with both attachments. A refused send returns an error and records nothing.

## Assumptions

- **Advance payments applied (type 101)** are out of scope: there were 25 in 12 months, but they are not requested.
- **MXN only.** Every receipt relation in 12 months was in MXN at rate 1. Receipts and credit notes are MXN at rate 1, and the XML writes `MonedaP`/`MonedaDR` MXN, `EquivalenciaDR` 1 and `TipoCambioP` 1.
- **Credit note defaults:** PUE, with the related invoice's payment form and CFDI usage. Billing chooses the form when the invoice's is 99.

  Measured on mbe_dev: all 16 credit notes since 2023 are PUE with the invoice's form, 03 on 15 of them. Their usages are G03 on 11, G01 on 4 and G02 on 1. None relates a PPD invoice.
- **No schema change is expected.** Receipts use the existing `payment_date`, `payment_amount`, `payment_reference` and `reference` columns of `fiscal_document`, and the existing relation columns. The link from a receipt to its customer payment needs a place to live, which `/speckit-plan` decides.
- **Receipts from the current system** are read as stored. Their typed installments and balances are trusted for computing the next ones, even where inconsistent.
- **Email CC** is not configured in the current system, so none is added. The sender is spec 024's.
- **The refund rule** applies to refunds created, edited or completed in the new MBE after this feature ships. Past refunds of invoiced goods are not revisited, and no credit notes are issued for them.

## Verification

### SC-002: parity with the current system's receipts and credit notes (2026-10-25)

**Method.** Every payment receipt (23, 2 of them cancelled) and every credit note (3, 1 cancelled) issued on mbe_dev since 2025-10-25 was exported read-only, with its rows, its relations and the related invoices' stored XML. Each was rebuilt locally from its rows only: receipts with `cfdi_payment.build`, credit notes with `cfdi_invoice.build` (`E`, relation 01). `NoCertificado` and `Certificado` were taken from the stored XML. The original strings were compared through the vendored stylesheet.

**Result:** 25 of 26 are identical.
- **CGR 10:** the only difference is the order of its two paid invoices. The current system reads relations without an ORDER BY, so its XML follows MariaDB's plan:
  - RHP 12 and RHP 23 list them in row order, as the new MBE does;
  - CGR 10 lists them by invoice.

  The values are the same.
- **No tax correction showed:** every receipt in the period pays 16 %-only invoices, so the tax-share correction of FR-007 changes nothing here. The other cases are covered by unit tests.

**Balances.** For all 29 receipt relations, the stored, hand-typed installment and previous balance equal the formulas of FR-004, computed from the receipts issued before each one. There are 29 consistent and 0 inconsistent.

### Migration and deployment (2026-10-25)

- **Migration:** 025 was applied to mbe_dev from the test checkout, with approval. `fiscal_document.customer_payment` exists (`int(11)`, nullable), and every existing row is NULL.
- **Deployment:** the branch, at 952f9a0, runs on the test instance. Health returns 200, and the new routes are listed in the OpenAPI schema.

### SC-004: refunds of invoiced goods on the test deployment (2026-10-25)

Run through the API with approval:
- **Fully invoiced order 344007:** opening a refund gives 409 "Units of line ARENATRIAZ are on invoice GR4776; cancel the invoice to refund them", and nothing is written.
- **Order 343389,** with two lines on invoice A5204 and two not invoiced:
  - the draft refund (9610) leaves out the fully invoiced line;
  - it offers 9 of the line with 270 sold and 261 invoiced;
  - editing that line to 10 gives 409 naming A5204;
  - editing an uninvoiced line to 1 is accepted.
- **Cancellation:** the draft was cancelled, never completed.

A snapshot of payments, store credit, sales orders, fiscal documents and completed refunds was identical before and after. The cancelled draft refund 9610 and its 3 lines were then deleted, with approval, in one counted transaction.

### SC-005: email

Covered by the tests, with a fake mailer and real PDF rendering. A real send was skipped at the user's choice.

## Out of Scope

- Stamping (#46) and cancellation (#51), including SAT cancellation of a credit note or receipt.
- Advance payments applied (type 101), foreign currency, and retentions.
- The UI (mbe-ui), including mictlanix/mbe-roadmap#56.
- Automatic receipts without billing's confirmation; reminders for PPD invoices without receipts.
- Changes to how refunds pay back (cash or store credit).
