# Implementation Plan: Delivery Documents

**Branch**: `026-delivery-documents` | **Date**: 2026-10-25 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/026-delivery-documents/spec.md`

## Summary

This feature adds five PDFs, rendered with spec 019's pipeline. They are built from the current system's views, without the data changes those views make while rendering (mictlanix/mbe#54):

| Route | Document | Layout |
|---|---|---|
| `GET /delivery-orders/{id}/ticket?warehouse=` | Delivery ticket, or pickup ticket for fulfillment type PICKUP | 72 mm |
| `GET /delivery-orders/{id}/document` | The letter-size delivery order (DeliveryFormatRamos) | Letter |
| `GET /delivery-itineraries/{id}/ticket` | Itinerary ticket | 72 mm |
| `GET /delivery-itineraries/{id}/delivery-notes` | The driver's delivery notes, with detachable receipts | 72 mm |

How it is built:
- Each document is one context function in `print_contexts.py` and one Jinja template. Spec 019's routes follow the same shape.
- **States:**
  - a delivery order's documents are refused while it is DRAFT or PENDING_APPROVAL;
  - a cancelled order prints stamped CANCELADA;
  - an itinerary's documents print only once it is DEPARTED or CLOSED.
- **Read only:** the context functions load data and build plain dicts. They assign nothing to a loaded entity, and a test asserts that the session holds no changes after each render.

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, WeasyPrint and Jinja2 (spec 019), python-barcode and qrcode (spec 019's `barcode_data_uri` and `qr_data_uri`). No new dependency.

**Storage**: MariaDB `mbe_dev`, read only. No migration.

**Testing**: pytest, pytest-asyncio and httpx.
- **Route tests:** the services are patched, as in `tests/api/test_print_endpoints.py`.
- **Render tests on the SQLite schema:** text extraction with pypdf, as in `test_quote_document_render.py`, plus a session-clean assertion.
- **Unit tests:** the pure grouping and status helpers.

**Target Platform**: Linux server (xolotl test instance)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: one render of each document per print. In 2026 the busiest ticket ran about 40 a day. A context runs a few queries per sales order (`attach_derived` and `list_order_applications` run once per order) plus a constant handful. Orders in 2026 have at most 34 lines, and a delivery order rarely spans more than a few sales orders.

**Constraints**:
- Rendering never writes (FR-010, SC-002).
- The content must match the current system, with the corrections the spec lists (SC-001).
- The layouts are spec 019's.

**Scale/Scope**:
- 4 routes in 2 existing routers;
- 4 context functions plus small helpers in `print_contexts.py`;
- 5 templates;
- docs: `06-logistics.md`, CHANGELOG.

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | One context function and one template per document. No print log and no configuration: the template is chosen by fulfillment type, not by an appSetting. The warehouse filter is a single optional query value. | PASS |
| II. Think Before Coding | Three questions were asked before the spec was written. Two corrections from planning are written back into the spec: the sales order payment-status labels, and the point of sale warehouse the API can derive. | PASS |
| III. Surgical Changes | The existing code gains only the new routes, in `delivery_orders.py` and `delivery_itineraries.py`, and new functions appended to `print_contexts.py`. No existing context or template changes. | PASS |
| IV. Goal-Driven Execution | The quickstart lists, for each document, every element the text-extraction tests check. SC-004 adds real renders on the test deployment. | PASS |
| V. Reuse Over Rebuild | Reuses: `header_context`, `_ship_to_lines`, `render_pdf`/`pdf_response`, `formatting.*` (including the QR and barcode helpers), `customer_payment_service.list_order_applications` (the same payments-on-delivery rule as the sale receipt), `delivery_itinerary_service.stops_of`/`lines_of_stop`, `delivery_order_service.get_order`/`lines_of`, `require_privilege`, and the cancelled stamp markup from `sale_receipt.html`. Nothing new beyond templates and contexts. | PASS |
| VI. Async-First | The contexts are async. Rendering runs in spec 019's worker thread behind its limiter. | PASS |
| VII. Security by Default | Read on DeliveryOrders (71) or DeliveryItineraries (87). The current system only hid the buttons. | PASS |
| VIII. Ruff Compliance | Gate. | PASS |
| Testing | Route tests (200/401/403/404/409/422), render tests for each document, a read-only test and helper unit tests. Tests are written first. | PASS |
| Changelog | Added: the four routes. No upgrade note: no migration, no setting. | PASS |
| Migrations | None. | PASS |

Re-check after Phase 1 design: unchanged, all PASS.

## Project Structure

### Documentation (this feature)

```text
specs/026-delivery-documents/
├── spec.md
├── plan.md              # this file
├── research.md          # R1–R12
├── data-model.md        # the document views: source of every printed field
├── contracts/
│   └── delivery-documents.md
├── quickstart.md        # test matrix and the deployment checks
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── api/v1/endpoints/
│   ├── delivery_orders.py         # + GET /{id}/ticket, GET /{id}/document
│   └── delivery_itineraries.py    # + GET /{id}/ticket, GET /{id}/delivery-notes
├── services/
│   └── print_contexts.py          # + delivery_ticket_context, delivery_order_document_context,
│                                  #   itinerary_ticket_context, delivery_notes_context, helpers
└── rendering/templates/
    ├── delivery_ticket.html       # 72 mm
    ├── pickup_ticket.html         # 72 mm
    ├── delivery_order.html        # Letter (Ramos)
    ├── itinerary_ticket.html      # 72 mm
    └── delivery_notes.html        # 72 mm
tests/
├── api/test_delivery_documents.py
├── integration/test_delivery_documents_render.py
└── unit/test_delivery_print_helpers.py
docs/specs/06-logistics.md          # §1 "Print / Ticket" rewritten; §4 gains "Print"
CHANGELOG.md
```

**Structure Decision**: this follows spec 019 and spec 024. A context function sits beside the existing ones, with a template per document, and the routes sit in the module's own router.

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|---|---|---|
| 5 templates | One per document: their content differs (FR-001, FR-006, FR-007) | One template with flags would mix five layouts and the ticket/Letter split |
