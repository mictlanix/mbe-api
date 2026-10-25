# Contract: Delivery Documents

Each route returns `200 application/pdf` inline, with `Content-Disposition: inline; filename="…"`, and declares spec 019's `PDF_RESPONSE`. Rendering never writes.

| Method | Path | Privilege | Filename |
|---|---|---|---|
| GET | `/api/v1/delivery-orders/{id}/ticket?warehouse=` | DeliveryOrders (71) Read | `entrega-{id:08d}.pdf` |
| GET | `/api/v1/delivery-orders/{id}/document` | DeliveryOrders (71) Read | `orden-entrega-{id:08d}.pdf` |
| GET | `/api/v1/delivery-itineraries/{id}/ticket` | DeliveryItineraries (87) Read | `itinerario-{id:06d}.pdf` |
| GET | `/api/v1/delivery-itineraries/{id}/delivery-notes` | DeliveryItineraries (87) Read | `acuses-{id:06d}.pdf` |

- **Ticket choice:** `/delivery-orders/{id}/ticket` returns the pickup ticket when `fulfillment_type` is PICKUP (0), and the delivery ticket otherwise.
- **`warehouse` (optional, integer):** prints only the lines from that warehouse. It is accepted on `/ticket` only.

## Errors

| Status | Detail | When |
|---|---|---|
| 401 | | No token, or an invalid one |
| 403 | | The module's Read privilege is missing (administrators pass) |
| 404 | `Delivery order not found` / `Itinerary not found` | Unknown id |
| 409 | `Delivery order is not approved` | The order is DRAFT or PENDING_APPROVAL |
| 409 | `Itinerary has not departed` | The itinerary is OPEN |
| 409 | `Itinerary is cancelled` | The itinerary is CANCELLED |
| 422 | `Unknown warehouse` | `warehouse` names no warehouse |
| 422 | `No line of this order comes from that warehouse` | The filter would leave no lines |
| 422 | (FastAPI validation) | `warehouse` is not an integer |

A CANCELLED delivery order is **not** an error. Its documents render stamped "CANCELADA".
