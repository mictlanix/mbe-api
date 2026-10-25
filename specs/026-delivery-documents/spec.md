# Feature Specification: Delivery Documents

**Feature Branch**: `026-delivery-documents`

**Created**: 2026-10-23

**Status**: Draft

**Input**: Implements mictlanix/mbe-roadmap#42, the pending API work of the Logistics epic (mictlanix/mbe-roadmap#4). The scope document is `docs/specs/06-logistics.md`: "Print / Ticket" under §1 Delivery Orders, which spec 012 left out of scope, and §4 Delivery Itineraries, read with the delivery flow v2 (`06a-delivery-flow-v2.md`). The request numbered them §6.1 and §6.4. The delivery documents are rendered as PDFs with spec 019's printing pipeline:
1. **For a delivery order:**
   - the delivery ticket;
   - the pickup ticket for counter pickups, chosen by the order's fulfillment type;
   - the letter-size delivery format;
   - an option to print only one warehouse's lines.
2. **For an itinerary:** the itinerary ticket and the driver's delivery notes.

Rendering must be read only (mictlanix/mbe#54 is not carried over).

## Findings that shape this spec

### The current system (`mictlanix/mbe`)

- **Delivery order, `DeliveryOrdersController.Print(id, only_current_warehouse)`:**
  - It redirects to Edit unless the order is completed.
  - The live configuration has `DeliveryOrdersUseMiniPrinter=true`, which selects a 72 mm ticket:
    - `PickUpTicket` when the order is picked up in store;
    - `DeliveryTicket` otherwise.
  - `only_current_warehouse=true` keeps only the lines from the warehouse of the user's point of sale. It is offered on the list row when one of the lines comes from that warehouse.
- **Letter format, `PrintFormat(id)`:**
  - It renders the configured `DeliveryOrderTemplate`. On the live site that is `DeliveryFormatRamos`.
  - `DeliveryOrderFormat` and the default `DeliveryFormat` are not configured anywhere: one is unreferenced and the other doesn't exist.
  - There is no state check and no warehouse filter.
  - The Print button replaces the PrintFormat button when the mini printer is on. PrintFormat is then reachable only by URL.
- **Itinerary, `DeliveryItinerariesController.Print` and `PrintDeliveryNotes`:** each redirects to Edit unless the itinerary is completed, then renders a 72 mm ticket: `ItineraryTicket` or `DeliveryNotesTicket`.
- **What each document prints:**

  | Document | Content |
  |---|---|
  | **DeliveryTicket** | Store header, taxpayer name and RFC, store address. Delivery order id, customer, delivery date. For each sales order: id, terms, and status ("No Pagado", "Vencido" or "Por Cobrar"). Contact and mobile, ship-to with its comment, order comment. Lines with quantity above 0: quantity and unit, product name, sales order id, line comment. "Pagos Pendientes": the sales orders' payments with no cash session, giving order, amount and method. "Por cobrar": the balance of unpaid cash orders. The ship-to's nickname and map link with a QR code. A receiver signature line |
  | **PickUpTicket** | Store header and address. Serial, date, customer (for the walk-in customer, the sales order's customer name too), "Entrega en mostrador". All lines: quantity, unit, product name, sales order id. Who handed it over (the updater). The payments on delivery. A Code128 barcode of the delivery order id. No signature |
  | **DeliveryFormatRamos** (Letter) | Store header and address. Customer, ship-to (street, numbers, neighborhood, borough, state, country, postal code, comment). Contact. Comment, or "Ninguno". "Orden de entrega {id}", date. A "Condiciones de pago" label with no value. Lines: quantity, code, name and line comment. The payments on delivery. "Recibí de conformidad. Antes de descargar, verifiqué las características del concreto solicitadas". Name and signature lines |
  | **ItineraryTicket** | Header from the dispatch warehouse's store. Itinerary id, operator, vehicle (nickname, plate), date, comment. Lines: sent quantity and unit symbol, product name, delivery order id. "Firma del Operador / Confirmación de Partidas y Cantidades descritas en el Despacho" |
  | **DeliveryNotesTicket**: "Acuses de Recibido" | The itinerary block, then one acknowledgement per (ship-to, contact, customer, comment) group: customer, contact and mobile, ship-to and its comment, order comment, a QR of the ship-to link, lines (sent quantity, unit, product, delivery order id), and a contact signature. After a cut line, a detachable "Recibo de Entrega" for each group: logo, store and taxpayer, date, operator, vehicle, customer, contact, ship-to, and lines with the sales order id |

- **Access:** only a login is required. Privileges are enforced only by hiding the buttons: Read on DeliveryOrders or DeliveryItineraries.
- **Rendering modifies data (mictlanix/mbe#54):**
  - `DeliveryTicket` and `DeliveryFormatRamos` do `Payment.Amount *= (Commission + 1)` on a tracked entity.
  - `Print` replaces the order's mapped `Details` collection with the filtered list.
  - Today nothing is flushed and `Commission` is never set, so neither has stored a change yet.
  - No document records that it was printed.

### This API today

- **Rendering:** spec 019's pipeline is WeasyPrint plus Jinja.
  - `app/rendering`, with a 72 mm ticket layout cut at `#ticket-end` and a Letter layout.
  - Context builders live in `app/services/print_contexts.py`, with `header_context` and `_ship_to_lines` already there.
  - Formatting helpers include `barcode_data_uri` and `qr_data_uri`.
  - Routes return `pdf_response`: inline, with an id-based filename.
  - The sale receipt already builds the "contraentrega" rows: payments with no cash session.
- **No delivery or itinerary documents** exist: no templates, no contexts, no routes.
- **Logistics (spec 012, 06a):**
  - `delivery_order` has `fulfillment_type`, with PICKUP = 0 and DELIVERY = 1, and the v2 status lifecycle. Each line carries a `warehouse`.
  - An itinerary has a vehicle, an operator, a dispatch warehouse and a status (OPEN, DEPARTED, CLOSED, CANCELLED). It has numbered stops, and each stop has details with sent quantities, fixed at departure.
  - Routes are gated on DELIVERY_ORDERS (71) and DELIVERY_ITINERARIES (87).
- **`payment_on_delivery`:** the table is unused. It has 1 row, from 2017.

### Measured (2026-10-22)

- **Prints per document in 2026:** taken from the legacy server's access log, all sites on xolotl, January to October.

  | Document | Prints |
  |---|---:|
  | Delivery order ticket (`Print`) | 8,026 |
  | … with `only_current_warehouse` | 379 |
  | Itinerary ticket | 3,846 |
  | Delivery notes | 3,553 |
  | Letter format (`PrintFormat`) | 0 (35 since August 2024) |

- **Delivery orders in mbe_dev, 2026:**
  - 5,690 deliveries and 2,391 counter pickups.
  - 391 orders, about 5%, draw lines from more than one warehouse.
  - Lines per order average 1.5 for pickups and 2.2 for deliveries; the most on one order is 34.
- **Ship-to map links:** 5,557 of the 5,690 deliveries have one, so the QR is worth keeping.
- **Itineraries in 2026:** 3,263.
  - Migration 008 gave each historical itinerary one stop.
  - 795 of those stops carry the lines of 2 to 9 delivery orders.
- **Statuses tell nothing about printing.** Migration 008 settled pre-v2 orders into terminal statuses, and every delivery never marked delivered became CANCELLED. The dataset ends around 2026-09-30, apart from a few API drafts.

## Clarifications

### Session 2026-10-23

- Q: The letter-size delivery format. The live site is configured for DeliveryFormatRamos, but its entry point logged no prints in 2026. What is built? → A: **The Ramos variant**, as the delivery order's letter document (FR-006).
- Q: From which delivery order states do its documents print? → A: **From approved onward.** DRAFT and PENDING_APPROVAL are refused. A CANCELLED order prints marked "CANCELADA", so its history can be reprinted (FR-003).
- Q: From which itinerary states do the itinerary ticket and the delivery notes print? → A: **DEPARTED or CLOSED**, as the current system prints only after confirming. The quantities are the sent quantities fixed at departure. OPEN and CANCELLED are refused (FR-008).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - The warehouse prints a delivery order's ticket (Priority: P1)

A warehouse clerk opens an approved delivery order and prints its ticket. A delivery gets the delivery ticket, which travels with the goods and is signed by the receiver. A counter pickup gets the pickup ticket, with a barcode to scan at the counter.

**Why this priority**: It is the most printed delivery document: 8,026 prints in 2026. Delivering without it would mean going back to the current system for every delivery.

**Independent Test**: Request the ticket of an approved delivery and of an approved pickup. Each is a 72 mm PDF with the content its table row in the Findings lists.

**Acceptance Scenarios**:

1. **Given** an approved delivery order of type DELIVERY, **When** its ticket is requested, **Then** the delivery ticket is returned with the store header, the order and customer data, each sales order with its terms and payment status, the contact, the ship-to, the lines, the pending payments and the amount to collect, the ship-to QR, and the receiver signature line.
2. **Given** an order of type PICKUP, **When** its ticket is requested, **Then** the pickup ticket is returned with "Entrega en mostrador", the lines, who handed it over, the payments on delivery and the order's barcode.
3. **Given** an order whose lines come from two warehouses, **When** the ticket is requested for one warehouse, **Then** only that warehouse's lines print, and the rest of the ticket is unchanged.
4. **Given** a DRAFT or PENDING_APPROVAL order, **When** its ticket is requested, **Then** the request is refused with a conflict and nothing is rendered.
5. **Given** a CANCELLED order, **When** its ticket is requested, **Then** it prints with a visible "CANCELADA" mark.

---

### User Story 2 - The dispatcher prints the trip's documents for the driver (Priority: P1)

Once an itinerary has departed, the dispatcher prints two documents. The itinerary ticket lists the trip and every stop's lines, and the driver signs it at dispatch. The delivery notes give one acknowledgement per destination for the receiver to sign, with a detachable receipt to leave with the customer.

**Why this priority**: 7,399 prints in 2026, and the v2 trip flow has no paper without them.

**Independent Test**: Depart an itinerary with two stops and print both documents. The trip header, the stops in sequence and the sent quantities all appear.

**Acceptance Scenarios**:

1. **Given** a departed itinerary, **When** its ticket is requested, **Then** a 72 mm PDF shows the header of the dispatch warehouse's store, the itinerary id, the operator, the vehicle (nickname and plate), the date and the comment. Each stop follows in sequence, with its customer and ship-to and its lines (sent quantity and unit, product, delivery order id). Last comes the operator's signature line.
2. **Given** the same itinerary, **When** its delivery notes are requested, **Then** the PDF shows the itinerary block. One acknowledgement follows for each destination group in each stop, with the customer, contact, ship-to, comment, ship-to QR, lines and contact signature. After a cut line comes one detachable "Recibo de Entrega" per group.
3. **Given** an OPEN or CANCELLED itinerary, **When** either document is requested, **Then** the request is refused with a conflict.

---

### User Story 3 - A branch prints the letter-size delivery format (Priority: P2)

A branch that delivers concrete prints the delivery order on Letter paper. The receiver signs that the concrete's characteristics were checked before unloading.

**Why this priority**: The live site is configured for it, but it logged no prints in 2026, so the tickets come first.

**Independent Test**: Request the letter document of an approved delivery order and check every element of the DeliveryFormatRamos row in the Findings.

**Acceptance Scenarios**:

1. **Given** an approved delivery order, **When** its document is requested, **Then** a Letter PDF shows the store header and address, the customer, the full ship-to, the contact, the comment ("Ninguno" when empty), "Orden de entrega {id}", the date, the payment terms, the lines (quantity, code, name and comment), the payments on delivery, the conformity legend, and name and signature lines.
2. **Given** an order with more lines than fit on one page, **When** its document is requested, **Then** it continues on further pages.

---

### Edge Cases

- **No ship-to:** a delivery without a ship-to prints without the address block and without the QR. A pickup never prints an address.
- **No map link:** a ship-to without one prints no QR and no link.
- **No contact or mobile:** the contact line is omitted.
- **Warehouse filter matching nothing:** the request is refused as invalid (422) rather than printing an empty ticket. A warehouse that does not exist is also 422.
- **Walk-in customer on a pickup:** the sales order's customer name is printed beside it, as in the current system.
- **Lines at quantity 0** are left out of the delivery ticket, as in the current system. The pickup ticket and the letter document print every line.
- **No vehicle or operator:** the itinerary prints a dash for the missing one.
- **Unknown id:** 404, as in spec 019.
- **Concurrent edits:** a document reflects the data at the moment it is rendered. Printing never changes any record.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST return a delivery order's ticket as a 72 mm PDF, using spec 019's ticket layout. It MUST choose the pickup ticket for fulfillment type PICKUP and the delivery ticket for DELIVERY.
- **FR-002**: The delivery ticket and the pickup ticket MUST print the content listed for them in the Findings, with these differences from the current system:
  - The amount of a payment on delivery is the payment amount as stored. No commission factor is applied (mictlanix/mbe#54).
  - "Payments on delivery" are the live payments of the order's sales orders that have no cash session.
- **FR-003**: A delivery order's documents MUST be refused with 409 while the order is DRAFT or PENDING_APPROVAL. Every later state renders. A CANCELLED order renders with a visible "CANCELADA" mark.
- **FR-004**: The ticket MUST accept an optional warehouse. When it is given, only the lines from that warehouse print. A warehouse that does not exist, or that none of the order's lines come from, is refused with 422.
- **FR-005**: The delivery ticket and the delivery notes MUST print a QR code of the ship-to's map link, with the link's text and nickname, when the ship-to has one.
- **FR-006**: The system MUST return a delivery order's letter-size document as a PDF on spec 019's Letter layout. Its content MUST be that of DeliveryFormatRamos in the Findings, with three corrections:
  - the contact block prints the contact's name and phone, not ship-to fields;
  - "Condiciones de pago" prints the sales orders' terms ("Contado", or "Crédito – N días");
  - the date prints once.

  The document pages when its lines exceed one page.
- **FR-007**: The system MUST return an itinerary's ticket and its delivery notes as 72 mm PDFs with the content listed for them in the Findings, with these adaptations to v2:
  - Lines are organized by stop, in stop sequence.
  - The delivery notes group each stop's lines by the delivery order's customer, ship-to, contact and comment, as the current system groups them.
  - Quantities are the sent quantities.
- **FR-008**: An itinerary's documents MUST be refused with 409 unless the itinerary is DEPARTED or CLOSED.
- **FR-009**: Each route MUST require the Read privilege of its module:
  - delivery order documents: DeliveryOrders (71);
  - itinerary documents: DeliveryItineraries (87).

  Administrators pass, as elsewhere. An unauthenticated request is 401, and a missing privilege is 403.
- **FR-010**: Rendering MUST be read only. No route writes, flushes or changes any record, and none records the print.
- **FR-011**: Each document MUST be returned inline, with a filename built from its id, following spec 019's naming:
  - `entrega-{id:08d}.pdf` for a delivery order ticket;
  - `orden-entrega-{id:08d}.pdf` for the letter document;
  - `itinerario-{id:06d}.pdf` for the itinerary ticket;
  - `acuses-{id:06d}.pdf` for the delivery notes.

  The OpenAPI response is `application/pdf`.
- **FR-012**: `docs/specs/06-logistics.md` MUST describe the documents:
  - "Print / Ticket" under §1: the current system's behaviour, the new routes, the warehouse option, the states and the correction for mictlanix/mbe#54;
  - a new "Print" subsection under §4 for the itinerary ticket and the delivery notes.

### Key Entities

- **Delivery order document:** a read-only rendering of a delivery order, its lines (optionally one warehouse's), its sales orders' terms, payment status and payments on delivery, its customer, contact and ship-to. It comes in three forms: delivery ticket, pickup ticket and letter document.
- **Itinerary document:** a read-only rendering of a departed or closed itinerary: its vehicle, operator and dispatch warehouse, and its stops in sequence with their delivery orders' lines at sent quantities. It comes in two forms: itinerary ticket and delivery notes.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: Each of the five documents carries every content element the current system prints for it, as listed in the Findings with the stated corrections. A test checks this by extracting the PDF text.
- **SC-002**: Rendering any document changes no row. A test compares the database before and after rendering every document, including the warehouse-filtered ticket.
- **SC-003**: Every route refuses as specified: 401, 403, 404, 409 for each excluded state, and 422 for a bad warehouse. Each refusal has a test.
- **SC-004**: On the test deployment, a real delivery, a real pickup and a real departed or closed itinerary from mbe_dev each render, and their content matches the data.
- **SC-005**: The largest delivery order in 2026 (34 lines) renders as a complete ticket and as a paginated letter document.

## Assumptions

- **Templates:** the documents reuse spec 019's layouts, header block and formatting helpers. Content must match the current system; layout need not.
- **Data under the v2 model:** the pickup ticket's "picked up in store" is fulfillment type PICKUP, as migration 008 mapped it. The itinerary's operator and vehicle come from the v2 columns.
- **Letter document states:** it follows FR-003's states, like the ticket. The current system's PrintFormat checked no state, but printing an unapproved order on Letter paper would bypass the approval rule the ticket keeps.
- **Warehouse option:** the warehouse is passed explicitly. The API could derive the current point of sale's warehouse, as `delivery_itinerary_service._pos_warehouse` does. But a warehouse clerk may have no point of sale, and the client already knows which warehouse it shows, so an explicit value covers both.
- **Filtered lines:** the warehouse option applies to the delivery ticket and the pickup ticket only, as in the current system. With the filter, the pending payments and the amount to collect still describe the whole order, as they do in the current system.
- **The sales order's payment status** on the delivery ticket uses the current system's rule (`DeliveryTicket.cshtml:33-50`). Every sales order is listed with its id and terms. An unpaid one gets one of these labels:
  - "No Pagado": cash terms;
  - "Vencido": credit terms, due before today;
  - "Por Cobrar": credit terms, not yet due.

  A paid order gets no label. "Paid" is the order's own paid flag.

## Out of Scope

- **UI:** mictlanix/mbe-roadmap#43.
- **ESC/POS direct printing:** mictlanix/mbe-api#247.
- **The deliveries summary report:** Reports epic.
- **The current system's other letter formats:** `DeliveryOrderFormat`, the missing `DeliveryFormat`, and the itinerary's dead `DeliveryFormatRamos` copy.
- **Recording prints:** no print counter or log.
- **Emailing the documents.**
- **The PurchaseRequests delivery format:** it also carries mictlanix/mbe#54, but purchase requests are another epic.
