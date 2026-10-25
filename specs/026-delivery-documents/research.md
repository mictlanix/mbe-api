# Research: Delivery Documents

Measured on mbe_dev (read only) on 2026-10-25, for 2026 data, unless stated otherwise.

## R1. One context function and one template per document

- **Decision:** add four context functions to `app/services/print_contexts.py`:
  - `delivery_ticket_context(db, order, *, warehouse=None)`, which returns `delivery_ticket.html` or `pickup_ticket.html` by `fulfillment_type`;
  - `delivery_order_document_context(db, order)`;
  - `itinerary_ticket_context(db, itinerary)`;
  - `delivery_notes_context(db, itinerary)`.

  Each returns `(template, dict)`, like spec 019's functions. There are five templates, two of which share one function.
- **Rationale:** this is the existing pattern, as in `sale_ticket_context`. It chooses between two templates the same way. The routes stay thin: load, check the state, build the context, `render_pdf`, then `pdf_response`.
- **Alternatives:** a `delivery_documents.py` module. It was rejected because every other document's context lives in `print_contexts.py` and shares its private helpers (`header_context`, `_ship_to_lines`, `_image_uri`).

## R2. Read only, mictlanix/mbe#54

- **Decision:** the contexts only read.
  - **Warehouse filter:** the lines are filtered into a new list, `[d for d in lines if d.warehouse == warehouse]`. Nothing is assigned to a relationship or an attribute of a loaded entity.
  - **Payment amounts:** used as loaded, with no commission factor.
  - **Sales orders:** they go through `sales_order_service.attach_derived` for their balance and paid flag, as spec 019 does. That writes only to instance `__dict__` keys, which SQLAlchemy does not track as changes.
- **Test (SC-002):** after rendering each document the test asserts `not db.dirty and not db.new and not db.deleted`, then compares a row count and a checksum of the touched tables before and after.
- **Rationale:** in the current system the defect is a view modifying a tracked entity. Checking the session state catches the same class of defect here.

## R3. Sales orders and payments on delivery

- **Decision:**
  - **Sales orders:** a delivery order's sales orders are the distinct `sales_order` of its lines' `sales_order_detail`, ordered by id. All 16,077 lines in 2026 have one.
  - **Payments on delivery:** for each sales order, `customer_payment_service.list_order_applications`, keeping applications that are not cancelled and whose payment has `cash_session IS NULL`. Each row prints the sales order id, the payment's `amount` and its method name.
  - **Amount to collect:** the sum of `balance` over the sales orders with cash terms that are not paid, printed when it is above 0.
- **Rationale:**
  - The current system's `GetDeliveryViewModel` selects "payments where `CashSession == null`", and it prints `Payment.Amount`.
  - The sale receipt already applies the same rule for its "Contraentrega" block (`print_contexts.py:235`).
  - The `payment_on_delivery` table is not the source: it has 1 row, from 2017.
- **Alternatives:** printing the application amount instead of the payment amount. Rejected, because the current system prints the payment amount, and a payment on delivery is applied to one order.

## R4. Sales order payment status on the delivery ticket

- **Decision:** port `DeliveryTicket.cshtml:33-50` exactly. Every sales order prints its id and terms name. If it is unpaid:
  - cash terms print "No Pagado";
  - credit terms print "Vencido" when `due_date < today` in business time, else "Por Cobrar".
- **Rationale:** the spec's Assumptions were corrected during planning to match this.

## R5. Units of measure

- **Decision:** the unit comes from `product.unit_of_measurement` via `sat_unit_of_measurement`:
  - the delivery and pickup tickets print its `name`, as the current system prints `UnitOfMeasurement.Name`;
  - the itinerary ticket and the delivery notes print its `symbol`, falling back to the name when the symbol is null.
- **Rationale:** these are the current system's two uses. `sales_order_service.attach_derived` already maps a product to its unit. The contexts load the units for the printed products in one query.

## R6. Itinerary lines by stop, and the delivery notes groups

- **Decision:**
  - **Itinerary ticket:** stops print in `sequence`, using `delivery_itinerary_service.stops_of` and `lines_of_stop`. Each stop prints a heading: "Parada {n}", the customer, and the first ship-to line. Its lines follow: sent quantity and symbol, product name, delivery order id.
  - **Delivery notes:** within each stop, lines are grouped by their delivery order's `(customer, ship_to, contact, comment)`, in order of first appearance. That is the current system's `GroupBy`, applied inside each stop.
  - **Sent quantities:** these are the quantities the documents print. They are fixed at departure, which is why only DEPARTED and CLOSED itineraries print (FR-008).
- **Rationale:**
  - 887 stops carry lines from more than one customer, a result of migration 008 giving each historical itinerary a single stop. So one acknowledgement per stop would merge customers.
  - The current system commented out the itinerary ticket's group header. The v2 stop heading puts that back in a simpler form, which the spec asks for ("its stops").
- **Alternatives:** one acknowledgement per delivery order. Rejected, because it would split two orders delivered to the same customer and address, which the current system groups together.

## R7. Who, where and the edge cases

- **Itinerary header:** the store is the one that owns the itinerary's dispatch warehouse (`warehouse.facility`). All 2026 itineraries have one. When it is NULL (the column allows it), the header falls back to the first delivery order's facility.
- **Vehicle and operator:**
  - The vehicle prints `nickname` and `license_plate`. The operator prints its driver employee's first and last name.
  - When either is missing, a dash prints instead. In 2026, 71 itineraries have no vehicle and 70 no operator.
- **Pickup ticket:**
  - "Entregó" is the employee of the order's latest `delivery_order_event` with `to_status = PICKED_UP`, the person who recorded the handover. Before pickup it falls back to the order's `updater`. The current system prints `Updater.Name`, but in v2 a later edit, such as a cancel after pickup, changes `updater`, while the event keeps who handed the goods over (analysis U3).
  - For the walk-in customer (`settings.default_customer_id`), the first sales order's `customer_name` prints beside the customer, as `sale_ticket_context` does.
- **Delivery ticket:**
  - **No ship-to** (118 deliveries in 2026): the address and QR blocks are omitted.
  - **No contact** (133): the contact line is omitted.
  - **Map link:** the QR prints `qr_data_uri(url_address)`, beside the link and nickname, when `url_address` is set.

## R8. States, and the CANCELADA stamp

- **Decision:**
  - **Delivery order documents:** 409 `Delivery order is not approved` for DRAFT and PENDING_APPROVAL. A CANCELLED order renders with the stamp markup `sale_receipt.html` uses (`cancelled`, `cancelled_at` = the order's `modification_time`), placed on both ticket templates and on the Letter template.
  - **Itinerary documents:** 409 `Itinerary has not departed` for OPEN, and `Itinerary is cancelled` for CANCELLED.
- **Rationale:** the clarification answers. The messages follow spec 025's style: a short sentence, no codes.

## R9. The warehouse filter

- **Decision:** `GET /delivery-orders/{id}/ticket?warehouse={id}`.
  - A warehouse that does not exist: 422 `Unknown warehouse`.
  - A warehouse none of the order's lines come from: 422 `No line of this order comes from that warehouse`.
  - The filter changes only the lines. The sales orders, payments on delivery and amount to collect still describe the whole order, as in the current system.
  - Lines at quantity 0 are dropped on the delivery ticket after filtering (`DeliveryTicket.cshtml:84`). The pickup ticket keeps every line.
- **Rationale:** the spec's assumption, written after checking `_pos_warehouse`. 391 orders in 2026 draw lines from more than one warehouse, and the filtered print was used 379 times.

## R10. The Letter document (DeliveryFormatRamos)

- **Decision:** `delivery_order.html` extends `_print_layout.html`, using `header_context(..., letter=True)` with the store's name and address and no taxpayer. That is what Ramos prints: `DeliveryFormatRamos.cshtml:7-12`. It prints:
  - the customer;
  - `_ship_to_lines(ship_to)`;
  - the contact's name and phone (`mobile`, else `phone`), which corrects the current system's copy-paste bug;
  - the comment, or "Ninguno";
  - "Orden de entrega {id:08d}" and the date, once;
  - "Condiciones de pago": the distinct terms of its sales orders, each as `formatting.terms_name`, plus "– N días" from the customer's `credit_days` on a credit term, as spec 024 prints it;
  - the lines (quantity, code, name, comment), the payments on delivery (R3), and the legend "Recibí de conformidad. Antes de descargar, verifiqué las características del concreto solicitadas";
  - the "Nombre" and "Firma de conformidad" lines.

  It paginates through the Letter layout. This document has no warehouse filter.
- **Rationale:** FR-006 and the clarification. Letter pagination is already proven by spec 024's 50-line quote test.

## R11. Filenames and the OpenAPI shape

- **Decision:**
  - The filenames are those of FR-011: `entrega-{id:08d}.pdf`, `orden-entrega-{id:08d}.pdf`, `itinerario-{id:06d}.pdf` and `acuses-{id:06d}.pdf`.
  - The routes declare `responses=PDF_RESPONSE` and `response_class=Response`, as `sales_orders.py` does.
  - The ids are padded like the current system's displays: 8 digits for orders, 6 for itineraries, as the cash cut pads its session.
- **Rationale:** spec 019 and spec 024 do the same.

## R12. Out of scope, confirmed in code

- The current system's `PrintFormat` ignored the state. Here the Letter document follows FR-003, per the spec's assumption.
- The itinerary's copy of `DeliveryFormatRamos` is dead: no action renders it.
- The PurchaseRequests copy belongs to another epic.
- Nothing records a print: the current system has no counter, and the spec adds none.
