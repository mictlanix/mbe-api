# Contract: Print Endpoints

All three are `GET`, authenticated, under `/api/v1`, and return a PDF body inline. None accepts query parameters or a body.

| Route | Geometry | Privilege | Filename |
|---|---|---|---|
| `/sales-orders/{sales_order_id}/ticket` | one page, 72 mm wide, content height, margin 0 | `SALES_ORDERS` READ | `ticket-{id:08d}.pdf` |
| `/sales-orders/{sales_order_id}/document` | Letter, margin 6 mm | `SALES_ORDERS` READ | `pedido-{id:08d}.pdf` |
| `/cash-sessions/{cash_session_id}/ticket` | one page, 72 mm wide, content height, margin 0 | `POS` READ | `corte-{id:06d}.pdf` |

## Responses

| Status | When | Body |
|---|---|---|
| 200 | success | PDF bytes, `Content-Type: application/pdf`, `Content-Disposition: inline; filename="…"` |
| 401 | no or invalid token | standard auth error (unchanged) |
| 403 | missing privilege | `{"detail": "Insufficient privileges"}` (unchanged) |
| 404 | record not found | `{"detail": "Sales order not found"}` / `{"detail": "Cash session not found"}`, the same strings as the read routes |
| 409 | cash session still open (cut only) | `{"detail": "Cash session is not closed"}` |

A ticket is always exactly one page, whose height grows with its content: about 44 mm for one line and about 206 mm for twenty. The letter document paginates normally, and every page is letter size.

## OpenAPI (normative)

Each route's 200 response MUST publish exactly:

```json
"200": {
  "description": "Successful Response",
  "content": {
    "application/pdf": {
      "schema": { "type": "string", "format": "binary" }
    }
  }
}
```

Its 200 response has no `application/json` entry. mbe-ui's dart-dio generator relies on this to emit `ResponseType.bytes`. Any other shape makes the client decode the PDF as UTF-8 and corrupt it silently (issue #230). Error responses keep FastAPI's default JSON declarations.

## Rendering guarantees

- Same stored data → same bytes, on the same platform and WeasyPrint version.
- No network I/O during a render.
- Renders run off the event loop, one at a time per process. Concurrent requests queue for the render slot, not for the event loop.
