# Implementation Plan: CFDI Invoices up to a Sealed Document

**Branch**: `027-cfdi-invoices-sealing` | **Date**: 2026-10-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/027-cfdi-invoices-sealing/spec.md`

## Summary

This feature adds three things on top of spec 020's read-only fiscal documents:
- issuer batch maintenance (#44);
- an invoice editor: create, add sales orders or manual lines, edit, relate, confirm and reopen (#47);
- sealing: confirm builds the CFDI 4.0 XML and computes the original string with SAT's stylesheet. It seals with the issuer's CSD, stores the XML, and leaves the document **sealed**.

There is no PAC and no migration.

How it is built:
- **Sealed state:** "not completed, not cancelled, with an XML row". No existing row matches, so no schema change is needed (research R1).
- **Serial:**
  - The batch row is locked with `FOR UPDATE`, then `MAX(serial)` is read with a locking read in the same transaction. A failure rolls the serial back.
  - The locking read is needed because mbe_dev runs `REPEATABLE-READ` and the request session has already read before confirm (R2).
- **XML:** built from the current system's formulas (R5). The original string comes from cfdlib's XSLT 1.0 copy of SAT's stylesheet, run by lxml (R3). The RSA-SHA256 seal reuses spec 008's key loading (R4).
- **Remaining quantity:** the clarified, time-ordered refund rule, as a pure function over invoice and refund events (R6).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 and `cryptography` (spec 008). **New:** `lxml`, for the XSLT original string at runtime and XSD validation in tests. It is declared in the spec's Assumptions.

**Storage**: MariaDB `mbe_dev` (InnoDB, `REPEATABLE-READ`). The existing fiscal tables are written; there is no DDL and no migration.

**Testing**: pytest, pytest-asyncio, and httpx `ASGITransport` on the SQLite test schema.
- **Unit:**
  - the builder and sealer, against the fixture and SAT's XSD;
  - remaining quantity;
  - status;
  - serial sequencing.
- **API:** batch and invoice routes.
- **On xolotl:**
  - SC-002, a read-only parity script;
  - SC-003, concurrent confirms on MariaDB, after confirmation.

**Target Platform**: Linux server (xolotl test instance)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**:
- About 15 invoices a day: 10,698 in 24 months, at most 109 lines each.
- Confirm does one XSLT run and one RSA signature, in a worker thread, in tens of milliseconds.
- Remaining quantity costs two queries per added order.

**Constraints**:
- **Serials:** unique and consecutive per issuer and series under concurrency (SC-003).
- **XML:** XSD-valid, with a verifying seal (SC-001), and the same original string as the current system for the same data (SC-002).
- **Scope:** never contact a PAC. Write only fiscal tables (SC-004).

**Scale/Scope**:
- **New routes:** 4 batch routes and 10 invoice routes.
- **Services:**
  - `invoice_service.py`: the editor, confirm, reopen and remaining quantity;
  - `cfdi_invoice.py`: the XML builder, original string and seal;
  - `taxpayer_batch_service.py`.
- **Spec 020 updates:** `fiscal_document_service.document_status` and the list filter.
- **Vendored files:**
  - 36 XSLT files under `app/resources/cfdi40/`;
  - 3 SAT XSDs under `tests/fixtures/sat/`.
- **Docs:** `docs/specs/10-fiscal-documents.md` and the CHANGELOG.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-checked after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | No status column, counter or PAC interface. Only Invoice (0) is built (measured). Sealed is derived from existing columns. No draft delete or discard, because the spec does not ask for it. | PASS |
| II. Think Before Coding | The assumptions are in the spec, with 5 clarifications. The isolation-level subtlety and the relation targets were found and written down (R2, R5). The spec's FR-010 was corrected from the current system's code. | PASS |
| III. Surgical Changes | Spec 020 changes only `document_status`, the `status` filter, and the response's new fields and relations. The XML and PDF routes are unchanged. | PASS |
| IV. Goal-Driven | SC-001 to SC-004 each map to an automated test or a recorded xolotl run (quickstart). | PASS |
| V. Reuse Over Rebuild | Reused: the mapped models, `csd_service._load_key`, `cfdi.batch_settings`, `require_privilege`, `local_now`, spec 020's schemas, and cfdlib's stylesheet. The new services and lxml are justified below. | PASS, see Complexity Tracking |
| VI. Async-First | Every route is `async`. The CPU-bound XSLT and RSA work runs in `asyncio.to_thread`, as `parse_csd` does. | PASS |
| VII. Security by Default | Every route has `require_privilege`: 23 Create/Update, 56 Create for manual lines, 103 Update to change the store, 24 for batches. The CSD key never leaves the service. | PASS |
| VIII. Ruff | The gate runs before each commit. | PASS |
| Testing | Tests are written first. The API tests cover 401, 403, 404, 409 and 422. Unit tests cover the builder, remaining quantity and status branches. | PASS |
| Changelog | `[Unreleased]` Added: batches, invoice editor, sealing. Changed: the `sealed` status. | PASS |

Post-design re-check: there are no new violations. The design adds no DDL, no PAC, and no configuration.

## Project Structure

### Documentation (this feature)

```text
specs/027-cfdi-invoices-sealing/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/fiscal-invoices.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── api/v1/endpoints/
│   ├── fiscal_documents.py        # + create, header, lines, sales-orders, relations, confirm, reopen
│   └── taxpayer_issuers.py        # + /{rfc}/batches routes
├── resources/cfdi40/              # NEW: cadena_original_4_0.xslt + 35 includes (from cfdlib)
├── schemas/fiscal.py              # + batch, invoice create/update, line update, relation schemas
└── services/
    ├── cfdi_invoice.py            # NEW: build XML (R5), original_string (R3), seal (R4)
    ├── invoice_service.py         # NEW: defaults, lines, remaining qty, relations, confirm, reopen
    ├── taxpayer_batch_service.py  # NEW: batch CRUD and in-use check
    ├── fiscal_document_service.py # status gains 'sealed'; list filter
    └── csd_service.py             # unchanged; _load_key reused

tests/
├── fixtures/sat/                  # NEW: cfdv40.xsd, tdCFDI.xsd, catCFDI.xsd.gz
├── unit/test_cfdi_invoice.py      # NEW: formulas, formatting, XSD, seal, fixture parity
├── unit/test_invoice_service.py   # NEW: remaining qty, defaults, state rules, serial sequence
├── unit/test_fiscal_document_service.py  # + sealed status
├── api/test_taxpayer_batches.py   # NEW
├── api/test_invoices.py           # NEW
└── api/test_fiscal_documents.py   # + status=sealed filter, relations in detail

docs/specs/10-fiscal-documents.md  # FR-017
CHANGELOG.md
pyproject.toml / uv.lock           # + lxml
```

**Structure Decision**: this is the existing single-service layout. The invoice routes join spec 020's router, because they act on the same resource. The batch routes nest under the issuer, which owns them.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|---|---|---|
| `lxml` dependency | SAT defines the original string as an XSLT, and SC-001 needs XSD validation. The standard library has neither. | A hand-written original string would re-implement SAT's rules and make SC-002 compare our code with itself. |
| `cfdi_invoice.py` service | The XML builder, original string and seal are pure and CPU-bound. They are unit tested against the fixture and the XSD, and will be reused by stamping (#46). | Putting them inside `invoice_service` would mix DB access with pure formatting, and the stamping library could not reach them. |
| `invoice_service.py` service | The editor, remaining quantity, confirm and reopen are write logic. Spec 020's `fiscal_document_service` is read only by design. | Growing `fiscal_document_service` would mix spec 020's read rules with the write workflow. |
| `taxpayer_batch_service.py` | Batch CRUD and the in-use check. It follows the one-service-per-resource pattern of `taxpayer_issuer_service`. | Adding it to `taxpayer_issuer_service`: kept separate, as certificates are (`taxpayer_certificate_service`). |
| Vendored XSLT (352 KB) and XSD (6 MB, gzipped) | They must run offline, at the exact version. SAT's online XSLT 2.0 pulls includes over HTTP. | Fetching at runtime or test time is fragile and non-deterministic. |
