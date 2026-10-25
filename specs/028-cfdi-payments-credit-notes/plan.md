# Implementation Plan: CFDI Payment Receipts, Credit Notes and Email

**Branch**: `028-cfdi-payments-credit-notes` | **Date**: 2026-10-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/028-cfdi-payments-credit-notes/spec.md`

## Summary

Spec 027's invoice editor and sealing are extended to two more document types, a refund rule is added, and CFDIs can be emailed:

- **Payment receipts** (type 200, Pagos 2.0, #53):
  - created from a customer payment (pre-filled, PPD invoices proposed, link kept) or by hand;
  - each paid invoice's installment and previous balance are computed from earlier live receipts and credit notes, and recomputed under a row lock at confirm;
  - the XML follows legacy's, with the tax breakdown corrected to each invoice's own rates and `Totales` always written.
- **Credit notes** (type 100, egreso, #55):
  - one stamped invoice, related with type 01;
  - lines copied from it or added by hand;
  - capped at what the invoice still holds;
  - sealed by the invoice builder with `TipoDeComprobante` E.
- **Refund rule** (#55, clarified): a refund may not take units on a live invoice. The refusal is a 409 naming the invoice, and refunds never create credit notes.
- **Email** (#50): stamped CFDI 4.0 PDF and XML, sent through spec 024's mailer and recorded as an incidence. The QR is already on spec 020's PDF.

There is one migration, 025: a nullable `fiscal_document.customer_payment` column. It is gated on the user's confirmation.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, `cryptography` and `lxml` (spec 027). Spec 024's `mail` and spec 020's PDF rendering are reused. No new dependency.

**Storage**: MariaDB `mbe_dev`. Migration 025 adds one nullable column; it is DDL, so it is confirmed before it runs. Everything else uses the existing fiscal, refund and payment tables.

**Testing**: pytest with httpx `ASGITransport`.
- **Unit:** the builders, the tax share and balance formulas.
- **Integration:** receipt, credit-note, refund-rule and email flows, on the SQLite schema.
- **API:** route tests with patched services.
- **Schemas:** `Pagos20.xsd` and `catPagos.xsd` are vendored for the XSD checks.
- **xolotl:** read-only parity (SC-002), plus SC-004 and SC-005 after confirmation.

**Target Platform**: Linux server (xolotl test instance)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: about 2 receipts and well under 1 credit note a month (measured). Confirm locks a handful of invoice rows; the refund check adds one query per order line.

**Constraints**:
- **Balances:** installments and balances must be consistent under concurrent confirms (R2).
- **Schema validity:** the XML validates against SAT's CFDI 4.0 and Pagos 2.0 schemas (SC-001).
- **Parity:** original-string parity with the current system's receipts and credit notes, apart from the listed corrections (SC-002).
- **Scope:** no PAC and no stamping.

**Scale/Scope**:
- **Routes:** 5 receipt, 2 credit-note and 1 email route; refund routes change.
- **New module:** `cfdi_payment.py`.
- **Extended:** `cfdi_invoice.py` (voucher type, generic seal), `invoice_service.py` (types, balances, credit notes), `customer_refund_service.py` (refund rule).
- **Migration:** 1.
- **Docs:** `docs/specs/10-fiscal-documents.md` and the CHANGELOG.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | Receipts and credit notes reuse spec 027's editor, states, serial and seal. Credit notes relate one invoice, as all 112 legacy ones do. There is no per-line cross-credit-note tracking, no automatic receipts, and no type 101. | PASS |
| II. Think Before Coding | Two clarifications are recorded. Data-driven narrowings were made in the spec: one invoice per credit note, no discount concept, a 50-character operation number. The concurrency of installments is analysed (R2). | PASS |
| III. Surgical Changes | `cfdi_invoice` gains one field and a split seal function. The refund change sits in `line_refundable` and the confirm reconcile only. Invoice behaviour is unchanged. | PASS |
| IV. Goal-Driven | SC-001 to SC-005 map to tests or recorded xolotl runs (quickstart). | PASS |
| V. Reuse Over Rebuild | Reused: spec 027's machinery, spec 024's mailer and incidence recording, spec 020's PDF and QR, and the vendored stylesheet, which already includes `pagos20.xslt`. The new `cfdi_payment.py` is justified below. | PASS, see Complexity Tracking |
| VI. Async-First | Every route is `async`; the CPU work (XSLT and RSA) runs in a thread, as in spec 027. | PASS |
| VII. Security by Default | Every route has `require_privilege`: 23 Create/Update/Read and 56 for manual lines. Refunds keep their privileges. | PASS |
| VIII. Ruff | The gate runs before each commit. | PASS |
| Testing | Tests are written first; API tests cover 401, 403, 404, 409 and 422; unit tests cover every formula branch. | PASS |
| Changelog | `[Unreleased]` gets Added (receipts, credit notes, email) and Changed (the refund rule). | PASS |

Post-design: no new violations. Migration 025 is the only schema change, and it is required by the clarified link (R1).

## Project Structure

### Documentation (this feature)

```text
specs/028-cfdi-payments-credit-notes/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/fiscal-receipts-credit-notes.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
migrations/
├── 025_fiscal_document_customer_payment.sql            # NEW (confirm before applying)
└── 025_fiscal_document_customer_payment_rollback.sql   # NEW
app/
├── api/v1/endpoints/fiscal_documents.py   # + payment-receipts, credit-notes, paid-invoices,
│                                          #   invoice-lines, email; type checks on 027 routes
├── enums.py                               # SourceType.FISCAL_DOCUMENT = 13
├── models/fiscal.py                       # customer_payment, payment_date, payment_amount
├── schemas/fiscal.py                      # create bodies, paid-invoice and email schemas
└── services/
    ├── cfdi_invoice.py                    # voucher_type; seal_root shared with receipts
    ├── cfdi_payment.py                    # NEW: Pagos 2.0 builder and DR tax shares
    ├── invoice_service.py                 # receipts, balances, credit notes, type dispatch
    ├── fiscal_email.py                    # NEW: subject, body, attachments; send and record
    ├── fiscal_document_service.py         # paid_invoices on the detail
    └── customer_refund_service.py         # refundable quantity excludes live invoices
tests/
├── fixtures/sat/                          # + Pagos20.xsd, catPagos.xsd
├── unit/test_cfdi_payment.py              # NEW
├── unit/test_cfdi_invoice.py              # + credit note (E, 01, PUE)
├── unit/test_invoice_service.py           # + balance and installment formulas
├── unit/test_customer_refund_service.py   # + refundable quantity with invoiced units
├── integration/test_receipt_flow.py       # NEW
├── integration/test_credit_note_flow.py   # NEW
├── integration/test_refund_invoiced.py    # NEW
├── integration/test_fiscal_email.py       # NEW
├── api/test_payment_receipts.py           # NEW
├── api/test_credit_notes.py               # NEW
└── api/test_fiscal_email.py               # NEW
docs/specs/10-fiscal-documents.md
CHANGELOG.md
```

**Structure Decision**: the existing layout. The new routes join spec 027's fiscal documents router, and refunds keep their own.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| Migration 025 (`fiscal_document.customer_payment`) | Clarification Q2 keeps the receipt–payment link, and one live receipt per payment must be checked | No existing column fits; overloading `payment_reference` would corrupt the XML's `NumOperacion` (R1) |
| `cfdi_payment.py` | Pagos 2.0 is a different document: a complement, DR tax shares and totals. It is pure and unit tested against the fixture and the XSD | Growing `cfdi_invoice.py` would mix two SAT structures in one builder |
| `fiscal_email.py` | Subject, body and attachments are shared by every CFDI type, and the send-then-record order of spec 024 matters | Putting it in the route would duplicate spec 024's pattern untested |
