# Data Model: Delivery Documents

There are no new tables, columns or models. Each document is a read-only view built from existing models. The tables below give the source of every printed element, as the context functions must fill it. The keys are pre-formatted strings or flags (spec 019, research R9).

Common to all five: `header` comes from `header_context(db, facility, title, letter=…)`.

## Delivery ticket: `delivery_ticket.html` (72 mm, title "Orden de Entrega")

| Element | Source |
|---|---|
| Header | `header_context(order.facility, 'Orden de Entrega')`: store, RFC, address. `_ticket_layout.html` prints only the RFC, so the template also prints `header.taxpayer_name` at the top of its `main` block, as `DeliveryTicket.cshtml:11-16` does |
| Delivery order id | `pad8(order.delivery_order_id)` |
| Customer | `customer.name` |
| Delivery date | `date_short(order.date)` |
| Sales orders | For each distinct sales order of the lines, by id: id, `terms_name(payment_terms)`, status label (research R4) |
| Contact | `contact.name` and `contact.mobile`; omitted when there is no contact |
| Ship-to | `_ship_to_lines(ship_to)`; omitted when there is no ship-to |
| Comment | `order.comment`, when present |
| Lines | Lines with `quantity > 0`, after the warehouse filter: `qty(quantity)`, unit name, `product_name`, sales order id, and the sales order line's `comment`, taken from the `attach_derived` lines of the loaded sales orders |
| Pagos Pendientes | Payments on delivery (research R3): sales order id, `money(payment.amount)`, `method_name(method)` |
| Por cobrar | `money(sum of balance of unpaid cash-terms sales orders)`, when above 0 |
| Map link | `ship_to.nickname`, `ship_to.url_address` and `qr_data_uri(url_address)`, when there is a link |
| Signature | "Persona que recibe / Nombre y firma" |
| Cancelled stamp | `cancelled`, `cancelled_at` (research R8) |

## Pickup ticket: `pickup_ticket.html` (72 mm, title "Entrega en Mostrador")

| Element | Source |
|---|---|
| Header | `header_context(order.facility, …)`. The current system printed no RFC here, so `rfc` is set to '' as `sales_order_context` does |
| Folio, date | `pad8(order.serial)` or '' when it is null; `date_short(order.date)` |
| Customer | `customer.name`. For the walk-in customer, also `(first sales order's customer_name)` |
| Label | "Entrega en mostrador" |
| Lines | All lines, after the warehouse filter: `qty(quantity)`, unit name, `product_name`, sales order id |
| Entregó | The employee of the latest PICKED_UP `delivery_order_event`, else the `updater` (research R7): first and last name |
| Payments on delivery | Research R3: method and amount |
| Barcode | `barcode_data_uri(pad8(delivery_order_id))` and the id as text |
| Cancelled stamp | As on the delivery ticket |

## Letter document: `delivery_order.html` (Letter, title "Orden de Entrega {id}")

See research R10. Elements: header (store and address, no RFC), customer, `_ship_to_lines`, contact (name, mobile or phone), comment or "Ninguno", "Orden de entrega {id}", date, "Condiciones de pago", lines (quantity, code, name, comment), payments on delivery, the conformity legend, signature lines, and the cancelled stamp.

## Itinerary ticket: `itinerary_ticket.html` (72 mm, title "Itinerario de Entrega")

| Element | Source |
|---|---|
| Header | `header_context(warehouse.facility, …)`, with the fallback in research R7 |
| Itinerary id | `pad6(itinerary.deliveries_itinerary_id)` |
| Operator | The driver employee's first and last name, or "—" |
| Vehicle | `nickname · license_plate`, or "—" |
| Date | `date_short(itinerary.date)` |
| Comment | `itinerary.comment`, when present |
| Stops | In `sequence`: "Parada {n}", customer, first ship-to line; each line gives `qty(sent_quantity)`, unit symbol, product name and delivery order id |
| Signature | "Firma del Operador / Confirmación de Partidas y Cantidades descritas en el Despacho" |

## Delivery notes: `delivery_notes.html` (72 mm, title "Acuses de Recibido")

| Element | Source |
|---|---|
| Trip block | As on the itinerary ticket |
| Acknowledgement, per group (research R6) | customer; contact name and mobile; ship-to lines; order comment; QR and link of `url_address`; lines (sent quantity, symbol, product, delivery order id); "{contact name} / Firma de conformidad" |
| Receipt, per group, after the cut line | logo, store, RFC and address; date, operator, vehicle; customer, contact, ship-to; lines (sent quantity, symbol, product, **sales order** id) |

## States

| Document | Renders | Refused (409) |
|---|---|---|
| Delivery order ticket and document | APPROVED, READY_FOR_PICKUP, PICKED_UP, IN_PREPARATION, IN_TRANSIT, DELIVERED, PARTIALLY_DELIVERED, FAILED, CANCELLED (stamped) | DRAFT, PENDING_APPROVAL |
| Itinerary ticket and delivery notes | DEPARTED, CLOSED | OPEN, CANCELLED |
