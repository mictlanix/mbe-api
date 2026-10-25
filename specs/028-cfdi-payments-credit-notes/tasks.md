---
description: "Task list for CFDI payment receipts, credit notes and email"
---

# Tasks: CFDI Payment Receipts, Credit Notes and Email

**Input**: design documents in `specs/028-cfdi-payments-credit-notes/`: plan.md, spec.md, research.md (R1–R9), data-model.md, contracts/fiscal-receipts-credit-notes.md and quickstart.md.

**Tests**: required. Constitution v1.2.0 makes them mandatory and test-first, overriding the template's "optional" line. Every test task comes before its implementation and must fail first.

**Working tree**: `/Users/eddy/Projects/mictlanix/mbe-api-028`. Before each commit:
- check `git branch --show-current`;
- run `uv run ruff check app/ migrations/ tests/`.

No commit carries a Co-Authored-By line. Delegated test-writing goes to agents with absolute paths and no commits.

## Format: `[ID] [P?] [Story] Description`

---

## Phase 1: Setup

- [X] T001 [P] Write `migrations/025_fiscal_document_customer_payment.sql` and `migrations/025_fiscal_document_customer_payment_rollback.sql` (data-model.md). Follow the style and header of `migrations/024_supplier_code_unique.sql`. **Do not apply them to mbe_dev**; T037 does that, after confirmation. Extend `tests/unit/test_migrate.py` only if it enumerates migration files.
- [X] T002 [P] Vendor SAT's `Pagos20.xsd` and `catPagos.xsd` into `tests/fixtures/sat/`, from `http://www.sat.gob.mx/sitio_internet/cfd/Pagos/Pagos20.xsd` and the `catPagos.xsd` it imports. Rewrite their `schemaLocation` imports to the local files (`catCFDI.xsd.gz`, `tdCFDI.xsd`, `catPagos.xsd`), and add both to `tests/fixtures/sat/README.md`. Extend `tests/cfdi_schema.py` so `assert_valid_cfdi40(xml)` also validates the `pago20:Pagos` complement against `Pagos20.xsd` when present. Add a smoke test in `tests/unit/test_cfdi_schema.py`: `tests/fixtures/cfdi/payment.xml` without its TFD passes, and with `MontoTotalPagos` removed it fails.
- [X] T003 [P] Model and enum additions:
  - in `app/models/fiscal.py`, `FiscalDocument.customer_payment: Mapped[int | None]` (FK `customer_payment.customer_payment_id`), `payment_date: Mapped[date | None]` and `payment_amount: Mapped[Decimal | None]` (`Numeric(18, 2)`);
  - in `app/enums.py`, `SourceType.FISCAL_DOCUMENT = 13` with an "API-only" comment like 11 and 12.

  Run `uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py -q` and update the data dictionary or schema expectations if those tests require it.

---

## Phase 2: Foundational (blocks every story)

- [X] T004 Extend `tests/unit/test_cfdi_invoice.py` first, so it fails:
  - **Credit note XML:** an `InvoiceData` with `voucher_type='E'`, `relation_type='01'` and one UUID builds `TipoDeComprobante="E"`, `MetodoPago="PUE"` and `CfdiRelacionados TipoRelacion="01"`. It passes `assert_valid_cfdi40` and its seal verifies.
  - **Shared sealing:** `cfdi_invoice.seal_root(root, certificate_number=…, certificate_data=…, key_data=…, key_password=…)` seals any built root, and `seal(...)` gives the same result as before.
- [X] T005 In `app/services/cfdi_invoice.py`:
  - add `voucher_type: str = 'I'` to `InvoiceData` and write it to `TipoDeComprobante`;
  - split `seal` into `seal_root(root, …) -> str`, which sets `NoCertificado`/`Certificado`, computes the original string and signs, with `seal(data, …)` as `build` + `seal_root`;
  - add `seal_root_async`.

  Make T004 pass with the existing tests unchanged.
- [X] T006 Add the request and response schemas to `app/schemas/fiscal.py`, per the contract:
  - **requests:** `PaymentReceiptCreate`, `CreditNoteCreate {invoice}`, `PaidInvoiceAdd {invoice, amount?}`, `PaidInvoiceUpdate {amount}`, `InvoiceLineCopy {line, quantity?}`, `FiscalEmailRequest {to}` (reuse `QuoteEmailRequest`'s validator by subclassing or sharing it), `FiscalEmailResponse`;
  - **`InvoiceUpdate`:** gains `payment_date: date | None` (≤ today), `payment_amount` (> 0), `payment_reference` (≤ 50) and `ordering_bank` (≤ 25);
  - **`FiscalDocumentResponse`:** gains `payment_date`, `payment_amount`, `payment_reference`, `customer_payment` and `paid_invoices: list[PaidInvoiceResponse] = []`.

  Write the static-rule tests first in `tests/unit/test_fiscal_schemas.py`, one rejected value per rule.
- [X] T007 Add type dispatch to `app/services/invoice_service.py`. First write tests in `tests/integration/test_invoice_flow.py`:
  - `_require_type(document, *types)` gives 409 "Not available for this document type";
  - spec 027's invoice-only operations (`add_sales_order`, `add_relation`) refuse types 100 and 200;
  - `update_header` refuses invoice-only fields on a receipt and receipt fields on an invoice (422);
  - `_default_batch_for(db, issuer, facility, type)` generalises spec 027's choice of default batch to any type, with the per-type 422 message (contract).

  Then implement. Spec 027's tests must stay green.

**Checkpoint:** builders, schemas and dispatch are ready, and spec 027's suite is unchanged and green.

---

## Phase 3: User Story 1 — Payment receipts (P1) 🎯 MVP

**Goal:** a receipt is created from a customer payment or by hand. It relates PPD invoices with computed installments and balances, and is sealed as Pagos 2.0.

**Independent Test:** a PPD invoice of 10,000 gets two receipts:
- 4,000: installment 1, balance 10,000 → 6,000;
- 6,000: installment 2, balance 6,000 → 0.

Both validate against SAT's CFDI 4.0 and Pagos 2.0 schemas, and both seals verify.

### Tests (write first, must fail)

- [ ] T008 [P] [US1] Write `tests/unit/test_cfdi_payment.py` (R3):
  - **DR tax shares:**
    - a 16 %-only invoice gives `BaseDR = round6(amount/1.16)` and `ImporteDR = round6(base×0.16)`, legacy's values;
    - an invoice with 16 % and 0 % groups splits by `B×(1+r)` weights;
    - an exempt group gets a base and no importe;
    - an invoice without taxes gives `ObjetoImpDR="01"` and no `ImpuestosDR`.
  - **Totales:** always written; per-rate fields only for the rates present; `MontoTotalPagos` = the payment amount.
  - **Header:** zero-value Comprobante, `Moneda="XXX"`, `TipoDeComprobante="P"`, `UsoCFDI="CP01"`, the 84111506/ACT concept with `ObjetoImp="01"`, and no `FormaPago` or `MetodoPago`.
  - **Pago:** `FechaPago` at `T00:00:00`; `NumOperacion` and `NomBancoOrdExt` only when set.
  - **Fixture parity:** rebuilding `tests/fixtures/cfdi/payment.xml` from its values gives the same original string, apart from the corrections listed in R3, if the fixture shows any. Read the fixture first.
  - **Seal:** a sealed receipt validates (CFDI 4.0 and Pagos 2.0), and its seal verifies.
- [ ] T009 [P] [US1] Add pure balance tests to `tests/unit/test_invoice_service.py` (R2): `installment(earlier_count)`, `previous_balance(total, receipts, credit_notes)`, and the default amount `min(balance, unassigned)`.
- [ ] T010 [P] [US1] Write `tests/integration/test_receipt_flow.py`. Seed a stamped PPD invoice with an XML whose `Total` and `Impuestos` are known, a type-200 batch, an active CSD, and `SatCfdiUsage` CP01. Cover:
  - **Create by hand:** defaults (CP01, IMMEDIATE, series); no series gives 422.
  - **Create from a customer payment:**
    - the date, amount, form and reference are copied;
    - the link is stored;
    - PPD invoices holding lines of the payment's applied orders are proposed with `min(balance, unassigned)`;
    - a second live receipt for the same payment gives 409.
  - **Paid invoices:**
    - a PUE, unstamped, cancelled, other-recipient or other-issuer invoice gives 422;
    - an amount above the balance gives 422;
    - amounts summing above the payment amount give 422;
    - remove works.
  - **Header:**
    - form 99 gives 422;
    - a payment date in the future or before the invoice gives 422;
    - `payment_reference` and `ordering_bank` are stored.
  - **Confirm:**
    - amounts ≠ payment amount gives 422;
    - success seals with the next serial;
    - installment, previous balance and amount are stored on the relation;
    - the XML passes `assert_valid_cfdi40`, including Pagos;
    - the status is sealed.
  - **Second receipt:** installment 2 with the reduced balance. A sealed credit note on the invoice also reduces the balance (seed a type-100 doc with XML relating it).
  - **Reopen:** the receipt's amount is freed for a new receipt.
  - **Concurrency, simulated:** two drafts each claiming the full balance. Confirming the first succeeds; the second's confirm gives 422, because the balance was recomputed at confirm.
- [ ] T011 [P] [US1] Write `tests/api/test_payment_receipts.py`, delegated, in the patched-service style of `tests/api/test_invoices.py`. Cover `POST /fiscal-documents/payment-receipts` and `POST`, `PUT` and `DELETE /fiscal-documents/{id}/paid-invoices…`: success, 401, 403, 404, 409/422 mapping and body validation.

### Implementation

- [ ] T012 [US1] Implement `app/services/cfdi_payment.py` (R3):
  - `PaymentData`, `PaidInvoiceData` (UUID, series, folio, installment, previous balance, amount, and the invoice's transfer groups parsed from its XML) and `build(data, certificate_number, certificate) -> root`;
  - `seal`/`seal_async` through `cfdi_invoice.seal_root`;
  - `invoice_transfers(xml) -> list[(factor, rate, base)]`, read from a stored invoice XML.

  Make T008 pass.
- [ ] T013 [US1] Implement the receipts in `app/services/invoice_service.py`:
  - the pure balance helpers;
  - `create_payment_receipt(db, current, data)`, applying R7 for the from-payment proposals;
  - `add_paid_invoice`, `update_paid_invoice`, `remove_paid_invoice`;
  - `invoice_balance(db, invoice, *, exclude_document)` (R2);
  - the receipt branch of `confirm`:
    - lock the related invoices' rows `FOR UPDATE` in id order;
    - recompute, check the sum against the payment amount, then the serial lock;
    - build, seal, and store the relation values and the XML;
  - `update_header` support for the receipt fields.

  Make T009 and T010 pass.
- [ ] T014 [US1] Add the routes to `app/api/v1/endpoints/fiscal_documents.py`: `POST /payment-receipts` (Create 23) and `POST`, `PUT`, `DELETE /{id}/paid-invoices…` (Update 23). Extend `fiscal_document_service.get_document` to attach `paid_invoices` for receipts, with live values on drafts and stored values when sealed or issued. Make T011 pass.

**Checkpoint:** US1 works end to end. This is the MVP.

---

## Phase 4: User Story 2 — Credit notes (P1)

**Goal:** a credit note relates one stamped invoice with type 01. Its lines are copied from the invoice or added by hand, capped at the invoice's remaining total, and it is sealed as an egreso.

**Independent Test:** a credit note copying one invoice line at a smaller quantity seals. Its XML is E with `CfdiRelacionados` 01, it validates, and its seal verifies.

- [ ] T015 [P] [US2] Write `tests/integration/test_credit_note_flow.py`:
  - **Create from a stamped invoice:**
    - the issuer, customer, recipient, store, form and usage are copied;
    - IMMEDIATE, the type-100 series, and the relation type stored as `'1'`;
    - an unstamped or cancelled invoice, or a non-invoice, gives 422;
    - no series gives 422.
  - **Copied lines:**
    - product, keys, price, discount and tax are copied;
    - a quantity above the invoice line's gives 422;
    - a line of another invoice gives 404 or 422.
  - **Manual line:** with privilege 56, via spec 027's route.
  - **Confirm:**
    - success seals with the next serial;
    - the XML has E, 01 and PUE and validates;
    - a total above the invoice's remaining total gives 422. Seed an earlier sealed credit note.
  - **Reopen:** works.
- [ ] T016 [P] [US2] Write `tests/api/test_credit_notes.py`, delegated, for `POST /fiscal-documents/credit-notes` and `POST /{id}/invoice-lines`: success, 401, 403, 404, 409/422 mapping and validation.
- [ ] T017 [US2] Implement in `app/services/invoice_service.py`:
  - `create_credit_note(db, current, data)`;
  - `add_invoice_line(db, current, document, data)`;
  - the copied-line cap in `update_line` for credit notes;
  - the credit-note branch of `confirm`: lock the invoice row, check the cap (R4), then spec 027's path with `voucher_type='E'`, `relation_type='01'` and PUE.

  Make T015 pass.
- [ ] T018 [US2] Add `POST /credit-notes` (Create 23) and `POST /{id}/invoice-lines` (Update 23) to `app/api/v1/endpoints/fiscal_documents.py`. Make T016 pass.

---

## Phase 5: User Story 3 — Refunds of invoiced goods are refused (P2)

**Goal:** a refund may not take units on a live invoice. The refusal is a 409 naming the invoice, never a silent trim.

**Independent Test:**
- a refund of 2 units of a line whose 5 units are on a stamped invoice gives 409 naming the invoice;
- uninvoiced units of the same order refund as before.

- [ ] T019 [P] [US3] Extend `tests/unit/test_customer_refund_service.py` with `refundable_quantity(sold, refunded, invoiced_live)` cases: 10/0/6 gives 4; 10/2/6 gives 2; 10/0/10 gives 0; never negative.
- [ ] T020 [P] [US3] Write `tests/integration/test_refund_invoiced.py`. It uses spec 011's refund service and seeds a completed order and a stamped invoice of part of its line, using the helpers from `tests/integration/seed.py` and `test_invoice_flow.py`'s fixture pattern. Cover:
  - **Opening a refund:** `refundable_quantity` excludes the invoiced units.
  - **Over the limit:** updating a line above it gives 409, with `{series}{folio}` in the detail.
  - **At confirm:** a line that became over-limit because an invoice was sealed meanwhile gives 409, not a silent trim.
  - **Units that are allowed:**
    - a sealed but unstamped invoice also blocks;
    - a draft invoice does not;
    - a cancelled invoice does not;
    - the uninvoiced remainder refunds normally.
- [ ] T021 [P] [US3] Extend `tests/api/test_customer_refunds.py` (delegated): the 409 detail passes through on line update and confirm.
- [ ] T022 [US3] Implement in `app/services/customer_refund_service.py`:
  - `refundable_quantity` takes `invoiced_live`;
  - `line_refundable` subtracts the units on live invoices, reusing `invoice_service.remaining_by_order_line`'s live-invoice predicate (extract a shared `live_invoiced_quantities(db, detail_ids)` helper if needed);
  - `assert_quantity_refundable` gives 409 naming the first blocking invoice when the shortfall is due to invoiced units;
  - `confirm_refund`'s `reconcile_lines` raises that 409 instead of trimming invoiced units.

  Make T019 and T020 pass, with spec 011's existing refund tests green.

---

## Phase 6: User Story 4 — Email a stamped CFDI (P2)

**Goal:** a stamped CFDI 4.0's PDF and XML are mailed to 1–10 addresses, with the outcome reported and the send recorded.

**Independent Test:** emailing a stamped invoice to two addresses with a fake mailer sends one message with both attachments, named `{RFC}-{series}{folio:000000}`, with the current system's subject and body, and records an incidence.

- [ ] T023 [P] [US4] Write `tests/unit/test_fiscal_email.py`:
  - `fiscal_email_message(...)` builds the subject `Comprobante Fiscal - {RFC} - {series}{folio:06d}`;
  - the body carries the UUID, both RFCs, the total and the SAT link from `sat_qr_payload`;
  - the attachments are the PDF and the XML with legacy's names and content types;
  - spec 020's PDF context carries the QR payload for the invoice, credit note and payment fixtures (FR-014).
- [ ] T024 [P] [US4] Write `tests/integration/test_fiscal_email.py` with a fake `Mailer`:
  - a stamped invoice is sent and an incidence (source 13) is recorded;
  - a draft, sealed, cancelled or pre-4.0 document gives 409;
  - a `MailError` gives 502 and no incidence.
- [ ] T025 [P] [US4] Write `tests/api/test_fiscal_email.py`, delegated:
  - the success case;
  - 401, 403 (Read 23), 404;
  - 503 when no mailer;
  - 409 and 502 pass through;
  - address validation.
- [ ] T026 [US4] Implement `app/services/fiscal_email.py`:
  - the message builder;
  - `email_document(db, document, to, *, mailer, current) -> datetime`, which renders spec 020's PDF via `print_contexts.fiscal_document_context`, sends, and then records the incidence, in spec 024's order.

  Make T023 and T024 pass.
- [ ] T027 [US4] Add `POST /fiscal-documents/{id}/email` (Read 23, `mail.get_mailer`, 503 when none) to `app/api/v1/endpoints/fiscal_documents.py`. Make T025 pass.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T028 [P] Update `docs/specs/10-fiscal-documents.md` (FR-017): receipts (balances, Pagos XML and corrections), credit notes, the refund rule, and email. Mark stamping and cancellation pending. Rewrite spec 027's remaining-quantity paragraph: its sentence "A refund of already-invoiced goods needs a credit note; it does not free units to invoice again." becomes "Refunds cannot take units on a live invoice (spec 028)."
- [ ] T029 [P] Update `CHANGELOG.md` `[Unreleased]`:
  - **Added:** payment receipts, credit notes, CFDI email, and migration 025.
  - **Changed:** refunds of invoiced units are refused (409).
- [ ] T030 Run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`; everything must be green. Include `tests/integration/test_no_endpoint_returns_500.py` for the new routes.
- [ ] T031 Run SC-002 parity read-only (quickstart §3):
  - export the 21 live receipts and 3 credit notes of the last 12 months, with their rows and related invoices' XML;
  - rebuild each locally with `cfdi_payment.build` and `cfdi_invoice.build`;
  - compare the original strings, listing each difference and its FR-007 cause;
  - check the computed installments and balances against the stored ones.

  Record the result in spec.md under a "Verification" section.
- [ ] T032 **Ask the user first; DDL.** Apply migration 025 to mbe_dev (quickstart §2), and record it in spec.md "Verification".
- [ ] T033 **Ask the user first.** Deploy the branch to the test instance (spec 027's procedure). **Only after T032.** The model maps `fiscal_document.customer_payment`, so the deployed code fails every read of fiscal documents, spec 020's included, until migration 025 is on mbe_dev.
- [ ] T034 **Ask the user first; writes to mbe_dev.** Run SC-004 (quickstart §4) on a throwaway draft refund:
  - editing a line to invoiced units is refused (409);
  - editing a line to uninvoiced units is accepted;
  - the draft is then cancelled, **never completed**, so no stock or money moves.

  Cleanup is listed and approved before it runs. Record the result.
- [ ] T035 **Ask the user first; sends real mail.** Run SC-005 (quickstart §5) to an address the user names, and record the result. Remove scratch scripts from xolotl.

---

## Dependencies & Execution Order

- **Setup** (T001–T003) runs in parallel and comes first.
- **Foundational:** T004 → T005. T006 and T007 depend on T003 (and T007 on T006 for the schema fields). Phase 2 blocks every story.
- **US1** (T008–T014) needs Phase 2.
- **US2** (T015–T018) needs Phase 2 and can run in parallel with US1. Receipts' balance code reads credit notes, but only through the XML and relation rows the tests seed.
- **US3** (T019–T022) needs only T007's shared live-invoice predicate, and is independent of US1 and US2.
- **US4** (T023–T027) needs only T003 (SourceType) and T006 (schemas), and is independent of the others.
- **Polish:** T028–T030 after all stories. T031–T035 run in order, each marked "ask first" gated on the user.

### Parallel opportunities

- T001 ∥ T002 ∥ T003
- T008 ∥ T009 ∥ T010 ∥ T011
- T015 ∥ T016
- T019 ∥ T020 ∥ T021
- T023 ∥ T024 ∥ T025
- Stories US2, US3 and US4 in parallel with US1, by separate files: their service code touches `invoice_service.py` (US1, US2) and `customer_refund_service.py` (US3), so US1 and US2 serialise on that file.
- T028 ∥ T029

---

## Implementation Strategy

1. **MVP:** Setup, Foundational and US1 (payment receipts). PPD invoicing becomes compliant.
2. **Then:** US2 (credit notes), US3 (the refund rule) and US4 (email).
3. **Polish:** docs, CHANGELOG and the full suite. Then, each after confirmation: parity, the migration, the deploy, the refund check and the email check.
4. **PR:** it must close mictlanix/mbe-roadmap#53, #55 and #50.
