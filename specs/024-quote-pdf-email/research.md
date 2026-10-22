# Research: Quotation PDF and Email

The spec's "Findings that shape this spec" records what the current system, this API and mbe-ui do today. This file records the design decisions.

## R1. Routes

**Decision**:
- `GET /sales-quotes/{sales_quote_id}/document`: the PDF. Requires `SALES_QUOTES` Read, works in any state, and is returned inline as `cotizacion-{id:08d}.pdf` with `rendering.PDF_RESPONSE`.
- `POST /sales-quotes/{sales_quote_id}/email`: the send. Requires `SALES_QUOTES` Read. The body is `{"to": [...]}`, and the response is 200 `{"recipients": [...], "sent_at": "..."}`.

**Rationale**:
- The document route mirrors spec 019's `GET /sales-orders/{id}/document`. Its OpenAPI 200 is PDF-only, so mbe-ui's generated client gets bytes and its shared document module can add one more kind.
- The email route is a POST because it causes an effect.
- A 200 with the deduplicated recipients and the time lets the client show what was sent.

**Alternatives considered**: `/print` or `/pdf` were rejected; spec 019 settled on `/document` for the letter-size document. A 202 with background sending was also rejected, because FR-007 requires the response to say whether the server accepted the message.

## R2. The document

**Decision**:
- A new template, `app/rendering/templates/sales_quote.html`, extends `_print_layout.html` as `sales_order.html` does.
- A new context function, `print_contexts.sales_quote_context(db, quote)`, takes a quote that has been through `sales_quote_service.attach_derived`. It reuses:
  - `header_context(..., letter=True)`, with the taxpayer blanked as the current system does on quotes;
  - `formatting` (money, quantities, dates, `terms_name`);
  - `words.amount_in_words`.
- **Lines:** `price + price_adjustment` with the line's discount applied, net of tax. The unit price comes from `totals.line_amounts(quantity=1, …)`, and the line amount is the line's `subtotal` from `attach_derived`.
- **Totals:** the quote's `subtotal`, `tax_total` and `total`, so the PDF matches the API's numbers.
- **Terms:** `terms_name(payment_terms)`. For NET_D, the customer's credit days are added ("Crédito – 30 días").
- **Validity:** the quote's `due_date`.

**Rationale**: the quote reuses the sales order document's helpers. Its arithmetic comes from the same `totals` module `attach_derived` uses, so the PDF can't disagree with the quote's own totals. The current system printed the net price and the subtotal, with no discount column. The discounted net unit price keeps the subtotal equal to quantity × unit price on every line.

## R3. The mail capability

**Decision**: one module, `app/services/mail.py`, with nothing quote-specific in it:
- **Data types:** `Attachment(filename, content_type, data)` and `Message(to, subject, body, attachments)` dataclasses.
- **`class Mailer(Protocol)`:** `async def send(self, message: Message) -> None`.
- **`SmtpMailer(settings)`:** builds an `email.message.EmailMessage`, with From and the optional Reply-To from settings, a plain-text body and the attachments. It sends with **stdlib `smtplib` inside `asyncio.to_thread`**:
  - security `ssl` uses `SMTP_SSL`, `starttls` uses `SMTP` + `starttls()`, and `none` uses plain `SMTP`, which is for local relays only;
  - it calls `login` when a user is set, and sends with a timeout from settings;
  - it raises `MailError` on `smtplib.SMTPException` or `OSError`.
- **`MailError(Exception)`**: the failure type the routes turn into 502.
- **`def get_mailer() -> Mailer | None`**: a FastAPI dependency that returns `SmtpMailer` when `smtp_host` and `mail_from` are set, else `None`. A route that needs mail answers 503 on `None`.

**Rationale**:
- **No new dependency**, as constitution V asks: `smtplib` and `email` are in the standard library.
- **Async-first (constitution VI):** the blocking SMTP conversation runs in a worker thread, never on the event loop.
- **Reusable:** the invoice email (#50) needs only `Message` and `Mailer`.
- **Testable:** tests override `get_mailer` with a recording double.

**Alternatives considered**:
- `aiosmtplib` is a new dependency, and it buys nothing that a thread doesn't, at one message per request.
- A global mailer singleton was rejected, because a dependency is what tests can override.
- OAuth2 is out of scope (spec clarification).

## R4. Configuration

**Decision**: new `Settings` fields, all empty or defaulted. A missing value means "not configured", and none of them stops the app from starting.

| Setting | Default | Meaning |
|---|---|---|
| `SMTP_HOST` | empty | Mail server; empty disables mail |
| `SMTP_PORT` | `587` | |
| `SMTP_SECURITY` | `starttls` | `starttls`, `ssl` or `none` |
| `SMTP_USER` | empty | Login skipped when empty |
| `SMTP_PASSWORD` | empty | |
| `SMTP_TIMEOUT` | `30` | Seconds per connection or command |
| `MAIL_FROM` | empty | From address; empty disables mail |
| `MAIL_REPLY_TO` | empty | Optional Reply-To (spec clarification) |

All of them are documented in `.env.example`, which notes that Google Workspace needs an app password.

## R5. Recording a send

**Decision**:
- **Source value:** add `SourceType.SALES_QUOTE = 11`. It is API-only, as `FACILITY = 10` already is, and `docs/constants.md` gains its row.
- **Entry:** after `await mailer.send(...)` returns, call `incidences.record(db, source=SALES_QUOTE, instance_id=quote.sales_quote_id, updater=current.employee_id, reason='Quote emailed', context='To: a@x.com, b@y.com')` and commit.
- **On failure:** `MailError` → 502, and nothing is recorded (FR-007, FR-008).
- **Size:** at most 10 addresses of up to 254 characters would exceed the `content` limit of 1,000. `incidences.record` takes `context` as written, so the service truncates it to 1,000 characters with a trailing `…`. The full list is in the response anyway.

**Rationale**: `incidence` is how this project records audit events (specs 011 and 021), and a send needs only who, when and to whom. Recording after acceptance means a recorded send always happened.

**Gap accepted**: if the commit fails after the server accepted the message, the email went out unrecorded. The alternative is to record before sending, which lets an entry claim a send that failed, and that breaks SC-002's "0 failed sends leave one". Losing a record to a commit failure is the rarer and more honest failure.

**Docs drift noticed**: `docs/constants.md` omits the existing `FACILITY = 10`. Adding row 11 directly below row 9 would leave the gap visible, so 10 is added in the same edit. That is one line, and it serves this change's own row.

## R6. Address validation

**Decision**: a Pydantic model, `QuoteEmailRequest`, with `to: list[str]` of length 1–10. Each address is stripped and must match `^[^@\s]+@[^@\s]+\.[^@\s]+$`, with at most 254 characters. The list is deduplicated case-insensitively, keeping first spellings and order. Any failure is a 422.

**Rationale**: `EmailStr` needs the `email-validator` package, which is a new dependency. The mail server is the final judge of deliverability. The API's job is to refuse obvious typos and header injection: no whitespace, so no CR or LF, and exactly one `@`.

## R7. The quote state

**Decision**:
- **Email:** only `completed and not cancelled` (spec FR-009). Otherwise 409 `Only a confirmed quote can be emailed`, checked before rendering or sending.
- **PDF:** any state (FR-003).

## R8. Testing

- **`tests/unit/test_mail.py`:**
  - `SmtpMailer` with `smtplib.SMTP`, and `SMTP_SSL`, patched to mocks;
  - the order of starttls, login and send_message, and that login is skipped without a user;
  - From, Reply-To, To, the subject, the body, and the attachment's name, type and bytes;
  - `SMTPException` and `OSError` → `MailError`;
  - `get_mailer` returns `None` when either `SMTP_HOST` or `MAIL_FROM` is unset.
- **`tests/unit/test_quote_email.py`:** the request model (lengths, regex, deduplication, header-injection attempts) and the message text (subject and body exactly as FR-006).
- **`tests/api/test_quote_document_email.py`:** the routes, with `get_mailer` overridden by a recording double:
  - document: 200 and a PDF signature, 404, 403;
  - email: 200 records one incidence; 409 draft and cancelled; 422; 502 when the double raises `MailError` (no incidence); 503 when `get_mailer` returns `None`.
- **`tests/integration/test_quote_document_render.py`:** a real render through the SQLite schema. `pypdf` text extraction checks every FR-002 element (SC-001) and a 50-line quote paginating.

**Spec wording**: FR-014 said the real transport is "exercised against a local fake SMTP server". That would need `aiosmtpd`, a new dev dependency. Patching `smtplib`'s client proves the same calls without one. FR-014 is reworded to "with the SMTP client replaced". The real server is exercised once, on the test deployment (SC-005).
