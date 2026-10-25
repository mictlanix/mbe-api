# Tasks: Delivery Documents

**Input**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/delivery-documents.md](./contracts/delivery-documents.md), [quickstart.md](./quickstart.md)

**Tests**: the constitution requires them, so the template's "tests are optional" line does not apply. Each story's tests come first and must fail before the implementation. Each test task is marked **(commit with T0xx)**.

**Patterns to copy**:
- **Routes:** `sales_orders.py:156-183` (the ticket and the document), for `PDF_RESPONSE`, `response_class=Response`, `render_pdf` and `pdf_response`.
- **Contexts:** `print_contexts.sale_ticket_context` (the choice between two templates, the walk-in customer name, payments with no cash session) and `sales_order_context` (Letter layout, `_ship_to_lines`).
- **Templates:**
  - `sale_receipt.html` for the 72 mm structure, the barcode and the cancelled stamp; `#ticket-end` must stay the last element of `<body>`;
  - `sales_order.html` and `sales_quote.html` for the Letter layout.
- **Render tests:** `tests/integration/test_quote_document_render.py` (`_flat` text extraction, the pagination check). **Route tests:** `tests/api/test_print_endpoints.py`.

**Read only (FR-010)**:
- No context assigns to an attribute or relationship of a loaded entity. The only exception is `attach_derived`'s `__dict__` keys, which SQLAlchemy does not track.
- Filtered lines are a new list.
- Every render test ends with `assert not (db.dirty or db.new or db.deleted)`.

## Format: `- [ ] T### [P?] [US#] Description · path`

---

## Phase 1: Setup

- [X] T001 Record the baseline before any change: run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`, and write the counts under this task · `specs/026-delivery-documents/tasks.md`
  - **Baseline (2026-10-25)**: ruff clean; pytest 3727 passed, 0 failed.

---

## Phase 2: Foundational (blocks all stories)

- [X] T002 Write `tests/integration/delivery_seed.py`, the builders shared by the render tests. They insert ORM rows directly, in the style of `tests/integration/commission_seed.py`, over the `seeded` baseline:
  - `delivery_order(db, *, fulfillment_type, status, customer=1, ship_to=None, contact=None, lines=[(product, quantity, warehouse, sales_order_detail)], comment=None) -> int`;
  - `sales_order_for(db, *, terms, paid, due_date, customer=1, customer_name=None) -> (sales_order_id, detail_id)`;
  - `payment_on_delivery(db, sales_order_id, amount, method)`: a `customer_payment` with `cash_session=None` and a standing `sales_order_payment`;
  - `address(db, *, url_address=None, nickname=None) -> int` and `contact(db, *, mobile) -> int`;
  - `warehouse(db, facility=1) -> int`;
  - `pickup_event(db, delivery_order, employee)`: a `delivery_order_event` with `to_status = PICKED_UP`;
  - `itinerary(db, *, status, vehicle=True, operator=True, warehouse) -> int` and `stop(db, itinerary, sequence, [(delivery_order_detail, sent_quantity)])`.

  Reuse `tests/integration/seed.py` where it already builds a sales order. Check `tests/integration/test_itinerary_flow.py:36` for the rows an approved order needs.
- [X] T003 Add the shared pure helpers to `app/services/print_contexts.py`, with their unit tests first in `tests/unit/test_delivery_print_helpers.py` (commit together):
  - `_order_status_label(terms, paid, due_date, today) -> str | None` (research R4): "No Pagado", "Vencido", "Por Cobrar" or None;
  - `_unit_label(unit, *, symbol) -> str` (research R5): the symbol falling back to the name, or the name;
  - `_note_groups(lines) -> list[(key, lines)]` (research R6): groups in order of first appearance, keyed by `(customer, ship_to, contact, comment)`.

  Plus two async loaders:
  - `_delivery_sales_orders(db, details)`: the distinct sales orders, each through `sales_order_service.attach_derived`, by id;
  - `_payments_on_delivery(db, sales_orders)`: research R3, via `customer_payment_service.list_order_applications`, standing applications whose payment has `cash_session is None`, as `{order, amount, method}`.

---

## Phase 3: User Story 1 — The warehouse prints a delivery order's ticket (P1) 🎯 MVP

**Goal**: FR-001–FR-005, FR-009–FR-011 for `GET /delivery-orders/{id}/ticket`.

**Independent test**:
- Render an approved delivery and an approved pickup: each element in data-model.md's two ticket tables appears in the extracted text.
- DRAFT gives 409, CANCELLED is stamped, and the warehouse filter keeps only that warehouse's lines.

- [X] T004 [P] [US1] Write `tests/api/test_delivery_documents.py` (commit with T007). Patch the context and `render_pdf` as `test_print_endpoints.py` does, and cover:
  - 200 `application/pdf` with `inline; filename="entrega-00000042.pdf"`;
  - 401 without a token, and 403 without Read on 71 for a non-admin;
  - 404 `Delivery order not found`;
  - 409 `Delivery order is not approved` for DRAFT and PENDING_APPROVAL;
  - 422 for `warehouse=abc`;
  - 422 `Unknown warehouse` and `No line of this order comes from that warehouse`.
- [X] T005 [P] [US1] Write `tests/integration/test_delivery_documents_render.py`, ticket part (commit with T007), using `delivery_seed`:
  - **Delivery ticket:** two sales orders, one cash and unpaid, one credit and past due. A payment on delivery of 1,500.00 in cash, a contact with a mobile, a ship-to with `url_address` and nickname, a line at quantity 0, and a comment. Assert:
    - the taxpayer name and RFC, "Orden de Entrega", the padded id, the customer, the date;
    - each sales order id with its terms and "No Pagado" or "Vencido";
    - the contact and mobile, the ship-to lines, the comment, the product names and the sales order ids;
    - "Pagos Pendientes" with 1,500.00 and "Efectivo";
    - "Por cobrar" with the cash order's balance;
    - the link text, and an image XObject on the page for the QR (the `_xobjects` helper, `tests/integration/test_print_render.py:832`);
    - the signature text;
    - the product on the 0-quantity line is absent.
  - **Pickup ticket:** for the walk-in customer, assert "Entrega en mostrador", the sales order's customer name, and "Entregó" with the name of the PICKED_UP event's employee (seed that event with a different employee from `updater`). Also assert the payment on delivery and the padded id under the barcode. A READY_FOR_PICKUP order, which has no such event, prints the updater.
  - **No ship-to and no contact:** neither block appears, and the page has no image XObject beyond the logo.
  - **Warehouse filter:** an order with lines in two warehouses. Filtered, only that warehouse's product appears, and "Pagos Pendientes" is unchanged.
  - **CANCELLED:** the text contains "CANCELADA".
  - **SC-005, ticket half:** a 34-line delivery ticket is one page, and the first and last products both appear in the extracted text, which shows the page cut held.
  - **Read only:** every render ends with `db.dirty`, `db.new` and `db.deleted` empty, and the `delivery_order`, `delivery_order_detail` and `customer_payment` row counts unchanged.
- [X] T006 [US1] Write the templates `app/rendering/templates/delivery_ticket.html` and `pickup_ticket.html`, extending `_ticket_layout.html` with a header comment giving the context contract, as `sale_receipt.html` does. The content is data-model.md's two ticket tables. The delivery ticket prints `header.taxpayer_name` at the top of its `main` block, because the layout prints only the RFC (analysis U1). The CANCELADA stamp copies `sale_receipt.html:34-38`. Spanish labels follow the current system's resources:
  - "Orden de Entrega" and "Cliente";
  - "Fecha de entrega";
  - "Contacto";
  - "Pagos Pendientes", "Pedido", "Importe" and "Forma de pago";
  - "Importe por cobrar";
  - "Persona que recibe" and "Nombre y firma";
  - "Entrega en mostrador" and "Entregó".
- [X] T007 [US1] Implement the ticket:
  - **`delivery_ticket_context(db, order, *, warehouse=None)`** in `app/services/print_contexts.py`. It loads the lines (`delivery_order_service.lines_of`), the units, customer, contact and ship-to, then the sales orders and payments on delivery (T003). Each line's comment comes from the `attach_derived` lines of those sales orders, keyed by `sales_order_detail` id, with no extra query (analysis U2). "Entregó" is the employee of the order's latest `delivery_order_event` with `to_status = PICKED_UP`, falling back to the order's `updater` (research R7). It filters by warehouse into a new list, drops lines at quantity 0 on the delivery ticket only, and returns `pickup_ticket.html` when `fulfillment_type == PICKUP`, else `delivery_ticket.html`.
  - **`GET /delivery-orders/{id}/ticket`** in `app/api/v1/endpoints/delivery_orders.py`, gated with the existing DeliveryOrders Read dependency:
    - 404 for an unknown id;
    - 409 for DRAFT and PENDING_APPROVAL;
    - 422 for an unknown warehouse, and for a warehouse none of the lines come from;
    - otherwise render and return `pdf_response(pdf, f'entrega-{id:08d}.pdf')`.

  Run T004 and T005: green. Commit T004–T007.
  - **As built:**
    - WeasyPrint draws an SVG data URI (the QR) as vector paths, not as an image XObject, so the render tests count path segments: more than 100 with a link, few without one. The pickup barcode is counted as `re` rectangles.
    - The warehouse 422s are checked in the route, and the context assumes a valid warehouse.
    - The read-only tests call the context and `render_pdf` on the test's own session, because a route call would use another session and make `db.dirty` trivially empty. One more test goes through the route.
    - 34 new tests; full suite 3761 passed.

---

## Phase 4: User Story 2 — The dispatcher prints the trip's documents (P1)

**Goal**: FR-007–FR-011 for `GET /delivery-itineraries/{id}/ticket` and `/delivery-notes`.

**Independent test**: a departed itinerary with two stops, where the second holds two customers' orders.
- The ticket lists both stops in sequence, with sent quantities and unit symbols.
- The notes have three acknowledgements and three receipts.
- OPEN and CANCELLED give 409.

- [X] T008 [P] [US2] Extend `tests/api/test_delivery_documents.py` (commit with T011) for both itinerary routes:
  - 200 with `itinerario-000042.pdf` and `acuses-000042.pdf`;
  - 401, and 403 without Read on 87;
  - 404 `Itinerary not found`;
  - 409 `Itinerary has not departed` for OPEN and `Itinerary is cancelled` for CANCELLED.
- [X] T009 [P] [US2] Extend `tests/integration/test_delivery_documents_render.py` (commit with T011).
  - **Setup:** a DEPARTED itinerary from a warehouse of facility 1, with a vehicle (nickname and plate) and an operator. Stop 1 has customer A's order. Stop 2 has two orders for customer B at the same ship-to and contact, plus customer C's order. Sent quantities differ from the ordered ones.
  - **Ticket:** "Itinerario de Entrega", the padded id, the operator's name, the vehicle nickname and plate, "Parada 1" before "Parada 2", each sent quantity with its unit symbol, each delivery order id, and the operator signature text.
  - **Notes:**
    - "Acuses de Recibido";
    - exactly three acknowledgements: A; B, with both its orders' lines; and C;
    - each with its contact, ship-to and QR;
    - three "Recibo de Entrega" blocks, each showing sales order ids.
  - **Missing data:** an itinerary with no vehicle and no operator prints "—" for both. An itinerary with `warehouse=None` takes its header from its first delivery order's facility (research R7).
  - **CLOSED:** it also renders.
  - **Read only:** the same assertion as T005.
- [X] T010 [US2] Write `app/rendering/templates/itinerary_ticket.html` and `delivery_notes.html`, extending `_ticket_layout.html`, with the content of data-model.md's itinerary tables.
  - The notes print a dashed cut line before the receipts.
  - Each receipt repeats the store block: logo, name, RFC and address from the context's `header`, not a second layout.
- [X] T011 [US2] Implement `itinerary_ticket_context(db, itinerary)` and `delivery_notes_context(db, itinerary)` in `app/services/print_contexts.py`:
  - **Data:** stops via `delivery_itinerary_service.stops_of` and `lines_of_stop`. Each itinerary line joins its `delivery_order_detail` and its delivery order (customer, ship-to, contact, comment) and the sales order line's `sales_order`. Units come by symbol (T003).
  - **Header:** from `warehouse.facility`, falling back to the first delivery order's facility.
  - **Operator and vehicle:** "—" when missing.
  - **Notes groups:** `_note_groups`, within each stop.

  Add `GET /delivery-itineraries/{id}/ticket` and `/{id}/delivery-notes` to `app/api/v1/endpoints/delivery_itineraries.py`, with the DeliveryItineraries Read gate, a 404, the 409s for OPEN and CANCELLED, and the filenames of FR-011. Run T008 and T009: green. Commit T008–T011.
  - **As built:**
    - A stop heading lists every customer in the stop, since a stop can hold several.
    - The delivery notes print every acknowledgement first, then a dotted cut line and a receipt per group, in the current system's order. The worker had interleaved them; this was corrected in review, and a test asserts the order.
    - The trip comment prints on both documents.
    - An itinerary with neither a warehouse nor any line takes the lowest facility for its header.

---

## Phase 5: User Story 3 — The letter-size delivery format (P2)

**Goal**: FR-006 for `GET /delivery-orders/{id}/document`.

**Independent test**: an approved order renders every element in research R10 on Letter paper. A 34-line order paginates.

- [X] T012 [P] [US3] Extend `tests/api/test_delivery_documents.py` (commit with T014) for `/document`:
  - 200 with `orden-entrega-00000042.pdf`;
  - 401 and 403;
  - 404;
  - 409 for DRAFT;
  - `warehouse` is not a parameter, so it is ignored.
- [X] T013 [P] [US3] Extend `tests/integration/test_delivery_documents_render.py` (commit with T014).
  - **Setup:** an approved delivery with a credit sales order for a customer with `credit_days=15`, a contact with a mobile, a full ship-to, no comment, and a payment on delivery.
  - **Assert:**
    - the store name and address, and no RFC;
    - the customer and every ship-to line;
    - the contact's name and mobile, not ship-to fields;
    - "Ninguno";
    - "Orden de entrega" with the padded id, and the date exactly once;
    - "Condiciones de pago" with "Crédito – 15 días";
    - each line's quantity, code, name and comment;
    - the payment on delivery;
    - the legend "Recibí de conformidad. Antes de descargar, verifiqué las características del concreto solicitadas";
    - "Firma de conformidad".
  - **SC-005:** a 34-line order gives 2 or more Letter pages, with the last product on the last page.
  - **Other cases:** CANCELLED is stamped. The read-only assertion holds.
- [X] T014 [US3] Write `app/rendering/templates/delivery_order.html`, extending `_print_layout.html`, and implement `delivery_order_document_context(db, order)` in `app/services/print_contexts.py` (research R10).
  - **Header:** `header_context(..., letter=True)` with `rfc` and `taxpayer_name` blanked.
  - **Terms:** each distinct sales order term through `formatting.terms_name`, with "– N días" from `customer.credit_days` on credit, as `sales_quote_context:481` does.
  - **Route:** add `GET /delivery-orders/{id}/document` to `delivery_orders.py`, with the same gate, 404 and 409 as the ticket.

  Run T012 and T013: green. Commit T012–T014.

---

## Phase 6: Polish & Cross-Cutting

- [X] T015 [P] Rewrite `docs/specs/06-logistics.md`:
  - **§1 "Print / Ticket"** (FR-012):
    - the current system: the mini-printer setting, PickUpTicket and DeliveryTicket, PrintFormat with DeliveryFormatRamos, `only_current_warehouse`, the 2026 print counts, login-only access, and mictlanix/mbe#54;
    - the new routes, the warehouse option, the states, and the corrections (no commission factor; the Ramos contact, terms and date).
  - **§4, new "Print" subsection:** the itinerary ticket and the delivery notes, by stop and by group, DEPARTED or CLOSED only.
- [X] T016 [P] Add to `CHANGELOG.md` `[Unreleased]` `### Added`: delivery documents (mictlanix/mbe-roadmap#42, spec 026). The four routes, their privileges and states, the warehouse option, and rendering that writes nothing (mictlanix/mbe#54 not carried over). No upgrade note.
- [X] T017 Run the full gate: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. No new failures against T001. Record the counts here. **Result:** ruff clean; 3795 passed, 0 failed (baseline 3727).
- [X] T018 **Confirm with the user before deploying.** Deploy the branch to `/var/www/mbe-api-test` and run SC-004 per `quickstart.md`, read only:
  - a real DELIVERY with a link and two sales orders;
  - a PICKUP;
  - a two-warehouse order, filtered;
  - a CLOSED itinerary with a multi-customer stop;
  - the Letter document.

  Compare the extracted text with the database, and record the ids and results in `quickstart.md`.

---

## Dependencies

- T001 → T002–T003 → every story.
- **US1, US2 and US3 are independent of one another.** They touch different routes, templates and context functions, but the same three test files and `print_contexts.py`, so they run in sequence: US1, then US2, then US3.
- Polish comes after the stories. T018 needs the user.

## Parallel opportunities

- Within each story, the route-test and render-test tasks ([P]) can be written together. They are different files.
- T015 and T016 can run alongside each other.

## Implementation strategy

- **MVP:** US1, the delivery and pickup tickets, which make up 8,405 of the 2026 prints. It can ship alone.
- **Then:** US2, the driver's documents (7,399 prints).
- **Then:** US3, the Letter format, configured but unused in 2026.
- **One commit per story:** tests plus implementation.
