# Contract: Quote Document and Email

Both routes are under `/api/v1`, authenticated, and require `SALES_QUOTES` (30) Read. A refusal is `403 {"detail": "Insufficient privileges"}`, and an unknown quote is `404 {"detail": "Sales quote not found"}` (the read route's string).

## `GET /sales-quotes/{sales_quote_id}/document`

| | |
|---|---|
| Geometry | Letter, margin 6 mm, paginates |
| States | any (draft, confirmed, cancelled) |
| 200 | PDF bytes, `Content-Type: application/pdf`, `Content-Disposition: inline; filename="cotizacion-{id:08d}.pdf"` |

The OpenAPI 200 publishes only `application/pdf` with `{"type": "string", "format": "binary"}`, exactly as spec 019's print routes do.

**Content** (FR-002):
- **Header:** the facility name, address and logo. There is no taxpayer block.
- **Customer and contact:** the customer name, and the contact's name and mobile (or phone and extension).
- **Title block:** "Cotización {id}", the date, and "Vigencia" (the due date).
- **Terms:** "Contado", or "Crédito – {credit_days} días".
- **Comment and ship-to,** when present.
- **Lines:** code, name and line comment, net unit price after discount, quantity, and line amount.
- **Totals:** the total in words, then subtotal, taxes and total.
- **Footer:** "Elaborada por {salesperson}".

## `POST /sales-quotes/{sales_quote_id}/email`

Request:

```json
{"to": ["compras@cliente.mx", "Ana@Cliente.mx"]}
```

`to` holds 1–10 addresses. Each is trimmed, has at most 254 characters, matches `^[^@\s]+@[^@\s]+\.[^@\s]+$`, and is deduplicated case-insensitively in first-seen order.

200:

```json
{"recipients": ["compras@cliente.mx", "Ana@Cliente.mx"], "sent_at": "2026-10-22T14:05:11"}
```

`sent_at` is local wall-clock time, as everywhere in this API.

The message sent:

| Field | Value |
|---|---|
| From | `MAIL_FROM` |
| Reply-To | `MAIL_REPLY_TO`, omitted when empty |
| To | the recipients |
| Subject | `{facility name} Cotización {id}` |
| Body (text/plain, UTF-8) | `Le enviamos la cotización {id}.\nAgradecemos su atención y esperamos servirle muy pronto.\nAtte: {salesperson name}.` |
| Attachment | `cotizacion-{id:08d}.pdf`, `application/pdf`, the document above |

Errors:

| Status | When | Body |
|---|---|---|
| 409 | the quote is a draft or cancelled | `{"detail": "Only a confirmed quote can be emailed"}` |
| 422 | `to` is empty, longer than 10, or holds an invalid address | the standard validation error |
| 502 | the mail server refused the message or could not be reached | `{"detail": "The email could not be sent: <reason>"}` |
| 503 | mail is not configured (`SMTP_HOST` or `MAIL_FROM` empty) | `{"detail": "Email is not configured"}` |

**Audit**: a 200 writes one `incidence` with these fields:

| Field | Value |
|---|---|
| `source` | 11 (SalesQuote) |
| `instance_id` | the quote id |
| `updater` | the sender's employee |
| `modification_time` | now |
| `comment` | `Quote emailed` |
| `content` | `To: a, b`, at most 1,000 characters |

No other status writes anything.
