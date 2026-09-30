# Implementation Plan: CFDI Invoice PDF

**Branch**: `020-cfdi-invoice-pdf` | **Date**: 2026-09-29 | **Spec**: [cfdi-invoice-pdf.spec.md](./cfdi-invoice-pdf.spec.md)

> **Scale note**: about 15 files across four areas: a new router with its service and schemas, a CFDI XML module, one template on spec 019's rendering core, and the tests with their seed data. Watch two things:
> - Every printed value comes from the stamped XML (research R1). A value read from a database column instead is a defect even when it happens to match.
> - The QR code's escaping is deliberately narrow (R3).

## Summary

The API gets a read-only `/fiscal-documents` router: a list, a detail, the stored XML as a download, and a letter-size PDF for stamped CFDI 4.0 documents. The PDF reproduces the content of legacy's `Print40T02Blue` for invoices, credit notes, applied advances and payment receipts, and marks cancelled documents on every page.

Every printed amount, code, seal and date is read from the stamped XML with the standard library's parser. The database supplies only catalog descriptions, line comments and the cancellation date. The SAT QR code is drawn by one new dependency, `segno`, as an inline SVG, and rendering reuses spec 019's core unchanged.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- Existing: FastAPI, SQLAlchemy async, WeasyPrint 70, Jinja2, and spec 019's `app/rendering/` package.
- New runtime: `segno` (research R3), declared in the spec's Assumptions.
- No new dev dependency. QR tests compare against `segno`'s own output for the expected payload, so no QR decoder is needed.

**Storage**: MariaDB, read-only for this feature. No schema change, no migration. Nothing is stored.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`. Integration renders use the existing SQLite fixtures in `tests/integration/`, which gain a fiscal-document seed with real stamped XML.

**Target Platform**: the same as spec 019.

**Project Type**: web service (REST API)

**Performance Goals**: a document of up to 50 lines in under 1 s, measured next to the database (SC-004). Spec 019's letter document already renders a 48-line order in 0.42 s there.

**Constraints**:
- Printed values are exactly the stamped XML's (R1).
- No network I/O during a render.
- Byte-identical output for identical data.
- The binary schema on both file routes, which mbe-ui's client generator depends on.

**Scale/Scope**:
- Code: 4 routes; 1 router, 1 service and 1 CFDI module; 3 schemas; 1 context builder, 1 template and its stylesheet rules; 1 dependency.
- Tests: 4 new test files and a fixtures directory, plus additions to 4 existing test files and the seed.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | Pass | Plain functions, dicts and one dataclass, with no parser classes per node. One template with two branches, not one per type. No stored PDFs and no totals column. |
| II. Think Before Coding | Pass | The user decided the cancellation mark (clarify) and a content-only comparison. The departures from legacy are listed in the spec and research: XML as the source, `&` escaping, refusing drafts, the privilege check. |
| III. Surgical Changes | Pass | Spec 019's rendering core is reused, not edited. `print_contexts.py` gains one function, `router.py` one line, `print.css` new rules. Nothing else in existing code changes. |
| IV. Goal-Driven Execution | Pass | Each FR and SC maps to an automated or manual check in quickstart.md. |
| V. Reuse Over Rebuild | Pass | Reused: `render_pdf`, `pdf_response`, `PDF_RESPONSE`, the fetcher, the fonts, `money`, `method_name`, `amount_in_words`, `_print_layout.html`, `require_privilege`, `ListResponse`, `LocalDateTime`, the models and SAT catalogs. The new service, schemas and module are justified below. The new dependency is declared in the spec. |
| VI. Async-First | Pass | Handlers are `async def` and use `AsyncSession`. XML parsing is microseconds of CPU in the handler. The render stays in spec 019's worker thread. |
| VII. Security by Default | Pass | Every route is behind `get_current_user` plus the `FISCAL_DOCUMENTS` READ privilege. The QR code is inlined, so there is no anonymous image route like legacy's. The XML is stored stamping output, parsed by expat, which has no external entities and limits entity expansion (R6). Batch settings are parsed with `ast.literal_eval`, which cannot execute code. |
| VIII. Ruff Compliance | Pass | The gate runs before commit. |
| Testing | Pass | Tests first. Unit: `cfdi.py` parsing, the original string, the QR payload, batch settings, and the context builder. API: 200/401/403/404/409/422, headers and OpenAPI. Integration: renders of four types, cancelled, multi-page, zero network, determinism. |
| Changelog | Pass | `[Unreleased]` → Added: the four routes and the `segno` dependency. The logo is noted as an operations step. |

**Post-design re-check**: still passes. The design added no table, migration or dependency beyond `segno`.

## Project Structure

### Documentation (this feature)

```text
specs/020-cfdi-invoice-pdf/
├── cfdi-invoice-pdf.spec.md
├── plan.md              # this file
├── research.md          # R1–R10
├── data-model.md
├── quickstart.md
├── contracts/
│   └── fiscal-documents.md
└── checklists/
    └── requirements.md
```

### Source Code (repository root)

```text
app/
├── api/v1/
│   ├── router.py                         # + include fiscal_documents at /fiscal-documents
│   └── endpoints/fiscal_documents.py     # new: list, detail, xml, pdf
├── schemas/fiscal.py                     # + FiscalDocumentSummary, FiscalDocumentLine, FiscalDocumentResponse
├── services/
│   ├── fiscal_document_service.py        # new: list_documents, get_document, get_xml, status rule
│   ├── cfdi.py                           # new: parse, tfd_original_string, sat_qr_payload, batch_settings
│   └── print_contexts.py                 # + fiscal_document_context
└── rendering/
    ├── __init__.py                       # + register the new filters
    ├── formatting.py                     # + qr_data_uri, quantity/price formats (R8)
    ├── templates/fiscal_document.html    # new, standalone letter template (its header is the CFDI's, not the store block)
    └── static/print.css                  # + CFDI sections, running footer, cancellation mark
pyproject.toml, uv.lock                   # + segno
CHANGELOG.md                              # [Unreleased] → Added
tests/
├── fixtures/cfdi/                        # new: stamped 4.0 XMLs (types 0, 100, 101, 200), anonymized, + README.md
├── unit/test_cfdi.py                     # new
├── unit/test_rendering.py                # + the new formatters
├── unit/test_fiscal_document_service.py  # new: status, search, xml_total
├── unit/test_print_contexts.py           # + fiscal_document_context
├── api/test_fiscal_documents.py          # new: list, detail, xml, pdf
├── api/test_print_endpoints.py           # + /fiscal-documents/{id}/pdf in _PDF_ROUTES
└── integration/
    ├── seed.py                           # + seed_fiscal_document from a fixture XML
    ├── test_fiscal_document_queries.py   # new: list, detail and XML queries
    └── test_print_render.py              # + CFDI renders; the route joins the zero-network list
```

**Structure Decision**:
- The router, service and schemas follow the taxpayer and sales-order patterns.
- XML handling gets its own module, `cfdi.py`, because the context builder, the list's totals and the tests all use it.
- The rendering core is reused as is.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|----------|------------|--------------------------------------|
| `fiscal_document_service.py` | The list's search, filters, status rule and per-page totals, plus the detail's lines. No fiscal-document service exists. | Queries inside the router break the codebase's router → service convention. |
| `app/services/cfdi.py` | Reading the stamped XML, which is the source of every printed value (R1), plus the original string and QR payload | Reading database columns reproduces legacy's recomputed totals, and the QR code's `tt` could drift from the stamp. |
| 3 schemas | The list, the detail and its lines have no existing schema | Returning ORM rows directly bypasses `LocalDateTime` and the response contract. |
| `segno` | No QR capability exists, and SAT requires the code (FR-007) | `qrcode` needs Pillow for images and emits less compact SVG. Serving an image from a route re-creates legacy's anonymous leak. |
| `tests/fixtures/cfdi/` | Real stamped structure for all four types, including the Pagos 2.0 complement | Hand-written minimal XML misses the attributes legacy prints. |
