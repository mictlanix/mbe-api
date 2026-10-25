# Quickstart: Delivery Documents

## Test matrix

| Requirement | Test |
|---|---|
| FR-001, FR-002 (delivery ticket) | `tests/integration/test_delivery_documents_render.py`: an approved DELIVERY with two sales orders (one on cash terms and unpaid, one on credit and past due), a payment on delivery, a contact, a ship-to with a map link, and a line at quantity 0. Text extraction finds every element in `data-model.md`: "No Pagado", "Vencido", "Pagos Pendientes", the amount to collect, the link and the signature. The QR image is present, and the line at quantity 0 is absent |
| FR-001 (pickup ticket) | A PICKUP for the walk-in customer: "Entrega en mostrador", the sales order's customer name, "Entregó", the payment on delivery and the barcode |
| FR-003 | DRAFT and PENDING_APPROVAL give 409 on both order routes. CANCELLED renders with "CANCELADA" |
| FR-004 | Two warehouses: the filter prints only that warehouse's lines, while the payments stay whole. 422 for an unknown warehouse and for a warehouse with no lines |
| FR-005 | No QR when the ship-to has no link; no address block without a ship-to; no contact line without a contact |
| FR-006, SC-005 | The Letter document: every element in research R10, the contact correction and the terms with credit days. A 34-line order paginates and its last line is on page 2 |
| FR-007, FR-008 | A departed itinerary with two stops, the second carrying two customers' orders. The ticket lists the stops in sequence with sent quantities and symbols. The notes have three acknowledgements and three receipts, and each receipt shows the sales order id. "—" prints for a missing vehicle. OPEN and CANCELLED give 409 |
| FR-009 | `tests/api/test_delivery_documents.py`: 401, and 403 without Read on 71 or 87, for each route. 404 for an unknown id |
| FR-010, SC-002 | After each render, `db.dirty`, `db.new` and `db.deleted` are empty, and the row counts are unchanged |
| FR-011 | The `Content-Disposition` filename and the media type, for each route |
| Helpers | `tests/unit/test_delivery_print_helpers.py`: the status label for each terms and due-date case, the stop grouping order, and the unit-symbol fallback |

Run: `uv run pytest -q tests/api/test_delivery_documents.py tests/integration/test_delivery_documents_render.py tests/unit/test_delivery_print_helpers.py`, then the full gate: `uv run ruff check app/ tests/` and `uv run pytest -q`.

## On the test deployment (SC-004)

The checks are read only, on `test.api.mbe.mictlanix.com`, as an administrator. Pick these from mbe_dev:
- a recent DELIVERY with a ship-to link and two sales orders;
- a recent PICKUP;
- an order whose lines come from two warehouses;
- a CLOSED itinerary whose stop carries two customers.

Render each document, extract its text, and compare the values with the database: customer, lines, quantities, sales order ids and payments. Record the ids and the results here.

Pre-v2 delivery orders were settled into PICKED_UP, DELIVERED or CANCELLED (migration 008), so they all render. Abandoned ones show the CANCELADA stamp.

### Results, 2026-10-25

Branch at af936c3 on `test.api.mbe.mictlanix.com`, as an administrator. All seven renders returned 200 with the FR-011 filename. Each printed value was checked against mbe_dev:

| Record | Document | Checked |
|---|---|---|
| Delivery order 9809 (DELIVERY, DELIVERED, two sales orders, map link) | Ticket and Letter document | Customer, contact name and mobile, ship-to, link, and the three lines with their quantities and sales orders 276806 and 276421. Both sales orders are cash and paid, so no status label prints. Their payments belong to cash sessions, so there is no "Pagos Pendientes" block. The Letter prints "Ninguno" for the empty comment and "Contado" as the terms |
| Delivery order 30206 (PICKUP, PICKED_UP) | Pickup ticket | Folio 00011988, date, customer, both lines of sales order 343985 and the barcode. The order has no events, so "Entregó" falls back to the updater, employee 76 |
| Delivery order 30136 (lines from warehouses 11 and 12) | Ticket, whole and filtered to 11 | Whole: both lines. Filtered: only the warehouse 11 line |
| Itinerary 4912 (CLOSED, one stop with three customers) | Ticket and delivery notes | Operator (through `vehicle_operator.driver`), vehicle, date, and the five lines with their sent quantities and delivery orders 30137, 30148 and 30143. The notes print three acknowledgements, then three receipts carrying sales orders 343766, 343795 and 343781 |

Read only: the `delivery_order` and `delivery_order_detail` row counts (30230 and 61831), and the latest `modification_time` of `delivery_order` and `deliveries_itinerary`, were the same before and after the renders.
