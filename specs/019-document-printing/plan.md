# Implementation Plan: Document Printing

**Branch**: `019-document-printing` | **Date**: 2026-09-24 | **Spec**: [spec.md](./spec.md)

**Input**: Feature specification from `/specs/019-document-printing/spec.md`

## Summary

Issue #230 Phase 1 without CFDI. The feature adds an in-process PDF rendering core and three documents: the sale ticket (pre-payment or final receipt, by order state), the letter-size sales order document, and the cash session cut. All three are ported from legacy's Razor templates.

- **Rendering**: WeasyPrint 70 with Jinja2 templates and two hand-written stylesheets.
- **Zero-network**: a local-only `URLFetcher` blocks everything except `data:` URIs and bundled or `images_dir` files. Fonts are bundled OFL TTFs, and Code128 barcodes are SVG `data:` URIs.
- **Off the event loop**: renders run on a single-slot thread limiter. The spike measured +15–20 ms on an unrelated endpoint's p95 under 10 concurrent renders.
- **Figures**: sales order figures reuse `attach_derived` unchanged. The cash cut adds one function porting legacy `CashCountReport`.
- **Contract**: every route declares its 200 response as `application/pdf` with a binary string schema.
- **Storage**: nothing is persisted and there is no schema change.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**:
- Existing: FastAPI, SQLAlchemy async, anyio (via FastAPI).
- New runtime: `weasyprint==70.0` (pinned exactly, R1/R4), `jinja2`, `python-barcode`.
- New dev: `pypdf` (MediaBox and text assertions).
- New system: pango, harfbuzz and fontconfig (R1).

**Storage**: MariaDB, read-only for this feature. Logos are read from `settings.images_dir`. PDFs are never stored.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`. Integration renders run against the existing SQLite fixtures in `tests/integration/`.

**Target Platform**: a Linux server with the pango stack installed, and macOS for development.

**Project Type**: web service (REST API)

**Performance Goals**:
- One document under 1 s (spike: ticket ~116 ms, letter ~229 ms warm).
- About 8 tickets/s per process.
- An unrelated endpoint's p95 rises by at most 100 ms under 10 concurrent renders.

**Constraints**:
- No network I/O during a render.
- Byte-identical output for identical data on the same platform.
- Ticket PDFs are one page, 72 mm wide, as tall as their content (R8).
- The mbe-ui client generator depends on the binary schema.

**Scale/Scope**:
- Code: 3 routes, 1 rendering package (core + formatting + words), 4 templates + 2 layouts, 2 stylesheets, 5 font files, 1 context-builder module, 1 new service function, 1 setting.
- Tests: 6 new test files.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
|-----------|--------|-------|
| I. Simplicity First | Pass | Plain functions and dicts, no class hierarchy. One limiter, not a process pool (R3). Nothing is persisted. No abstraction beyond what the three documents share, which is the layouts, the filters and `render_pdf`. |
| II. Think Before Coding | Pass | Scope, the two-ticket mapping, `display_on_ticket` and cut contents were decided by the user. Deviations from legacy are listed, not silent: amount-in-words fixes (R10), the balance source (R11), the cash-sales label and the stamp format (R9). |
| III. Surgical Changes | Pass | Existing endpoint files gain one route each. `cash_session_service` gains one function. `attach_derived` and the totals module are untouched. |
| IV. Goal-Driven Execution | Pass | Each FR and SC maps to a check in quickstart.md. |
| V. Reuse Over Rebuild | Pass | Reused: `attach_derived`, `totals.line_amounts`, `list_order_applications`, `opening_amount`, the existing `_READ` dependencies and 404 helpers. New dependencies are declared in the spec. The new modules are justified below. |
| VI. Async-First | Pass | Handlers are `async def` and data loading uses `AsyncSession`. The synchronous render runs in a worker thread via `anyio.to_thread.run_sync`, so the event loop never blocks (measured, R3). |
| VII. Security by Default | Pass | All routes are behind `get_current_user` and the same privileges as the read routes. The fetcher cannot read outside the assets and images directories. No endpoint is public. |
| VIII. Ruff Compliance | Pass | Gate runs before commit. |
| Testing | Pass | Tests first: unit tests (formatters, words, fetcher, cut arithmetic), API tests (401/403/404/409/200, headers, OpenAPI) and integration renders (geometry, zero-network, determinism, concurrency, text content). |
| Changelog | Pass | `[Unreleased]` → Added: the three routes and the setting. It also notes the system-library requirement as an upgrade note. |

**Post-design re-check**: still passes. The design added no table or migration, and no dependency beyond those declared in the spec.

## Project Structure

### Documentation (this feature)

```text
specs/019-document-printing/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── print-endpoints.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── core/config.py                    # + promissory_note_template
├── rendering/                        # NEW package
│   ├── __init__.py                   # render_pdf(template, context) -> bytes (async, limiter, fitted ticket height), PDF_RESPONSE
│   ├── fetcher.py                    # LocalOnlyFetcher (R2)
│   ├── formatting.py                 # money, dates, qty, percent, padded ids, enum names (R9), barcode_data_uri (R6)
│   ├── words.py                      # amount_in_words (R10)
│   ├── templates/
│   │   ├── _ticket_layout.html       # ports _TicketLayout.cshtml
│   │   ├── _print_layout.html        # ports _PrintLayout.cshtml
│   │   ├── sale_ticket.html          # ports POS/Print.cshtml (not completed)
│   │   ├── sale_receipt.html         # ports Payments/Print.cshtml (completed)
│   │   ├── sales_order.html          # ports SalesOrders/Print.cshtml
│   │   └── cash_cut.html             # ports Payments/_CashCountTicket.cshtml
│   └── static/
│       ├── ticket.css, print.css     # hand-written from legacy (R7)
│       └── fonts/                    # OpenSans-{Regular,Bold}, Roboto-Bold, RobotoMono-Regular .ttf + OFL.txt
├── services/
│   ├── print_contexts.py             # NEW: builds the render dicts from rows (sale ticket/receipt, order doc, cut)
│   └── cash_session_service.py       # + cut_figures(db, session_id)
└── api/v1/endpoints/
    ├── sales_orders.py               # + GET /{id}/ticket, GET /{id}/document
    └── cash_sessions.py              # + GET /{id}/ticket

tests/
├── unit/test_rendering.py            # formatters, fetcher allow/deny, barcode determinism
├── unit/test_amount_in_words.py      # R10 rules incl. the corrected legacy cases
├── unit/test_cash_cut.py             # cut_figures arithmetic, shortage/overage, no counts
├── unit/test_print_contexts.py       # context builders: template choice, display_on_ticket, legacy conditions
├── api/test_print_endpoints.py       # 200/401/403/404/409, headers, OpenAPI schema for every PDF route
└── integration/test_print_render.py  # geometry, zero-network, determinism, concurrency, text content

pyproject.toml, uv.lock               # + weasyprint==70.0, jinja2, python-barcode; dev + pypdf
README.md                             # system library prerequisites
CHANGELOG.md
```

**Structure Decision**:
- **Why a top-level `app/rendering/`**: it is infrastructure with its own non-Python assets (templates, CSS, fonts), not a business service. It knows nothing about the models, and the future CFDI and ESC/POS specs reuse it.
- **Why a separate `print_contexts.py`**: the translation from rows to render dicts is business logic, so it lives in `app/services/`. It is not added to `sales_order_service`, which is already 1,100+ lines, and it keeps each endpoint to a load → build → render → respond sequence.
- **Why `cut_figures` sits in `cash_session_service`**: it reads session data, next to `opening_amount` and `payments_by_method`, which it reuses.

## Complexity Tracking

| Addition | Why Needed | Simpler Alternative Rejected Because |
|----------|------------|--------------------------------------|
| `app/rendering/` package (4 modules + assets) | Shared rendering core for three documents now, and CFDI next | Inlining WeasyPrint calls per endpoint duplicates the fetcher, fonts, limiter and filters three times |
| `app/services/print_contexts.py` | Row-to-document mapping with legacy's conditional logic (pagaré, card legend, on-delivery, `display_on_ticket`) | Putting it in endpoints breaks their existing thin style. Putting it in `sales_order_service` bloats an already large module with presentation concerns |
| `cut_figures` | The cut needs refunds, expenses, cash in drawer, counted cash and shortage/overage, none of which exist today | Computing in the template puts money arithmetic where it cannot be unit-tested (constitution: arithmetic needs `tests/unit/`) |
| 3 runtime dependencies + system libraries | No PDF, templating or barcode capability exists | Alternatives assessed in issue #230 and research R1 |
| ~575 KB of bundled fonts | Zero-network rendering (FR-003) with legacy's typefaces | System fonts differ per host and break byte-identical output |
