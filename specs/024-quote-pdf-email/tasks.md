# Tasks: Quotation PDF and Email

**Input**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/quote-document-email.md](./contracts/quote-document-email.md), [quickstart.md](./quickstart.md)

**Tests**: required by the constitution; the template's "tests are optional" line does not apply. Every story writes its tests first and watches them fail. Each test task is marked **(commit with T0xx)**.

**Story order**: US2 (email) uses the mail capability that US3 builds, so the phases run US1 → US3 → US2. US1 is independent of both.

**Patterns to follow**:
- **API tests:** `tests/api/test_print_endpoints.py` for `_auth`, `_deny_privileges` and `_client`, with services patched.
- **Render tests:** `tests/integration/test_print_render.py` for real renders with the `client` and `seeded` fixtures, and `pypdf.PdfReader(...).pages[i].extract_text()`.
- **Template:** `app/rendering/templates/sales_order.html`, a 100-line letter template extending `_print_layout.html`.

## Format: `- [ ] T### [P?] [US#] Description · path`

---

## Phase 1: Setup

- [X] T001 Record the baseline before any change: run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. Write the counts under this task · `specs/024-quote-pdf-email/tasks.md`
  - **Baseline (2026-10-22)**: ruff clean; pytest 3270 passed, 0 failed.

---

## Phase 2: User Story 1 — A salesperson gets the quote as a PDF (P1)

**Goal**: FR-001 to FR-004. `GET /sales-quotes/{id}/document`.

**Independent test**: a confirmed quote with 3 lines renders a letter PDF whose text holds every FR-002 element. A missing quote gives 404, and no privilege gives 403.

- [X] T002 [P] [US1] Write `tests/api/test_quote_document_email.py`, document part (commit with T004–T006). Patch `sales_quote_service.get_quote`, `sales_quote_service.attach_derived`, `print_contexts.sales_quote_context` and `rendering.render_pdf` (returning `b'%PDF-1.7 test'`). Cases:
  - 200, `content-type: application/pdf`, `content-disposition: inline; filename="cotizacion-00000007.pdf"`;
  - 404 `Sales quote not found` when `get_quote` returns None;
  - 401 with no token;
  - 403 with `_auth(administrator=False)` and `_deny_privileges()`;
  - the OpenAPI document's 200 response for this route has only `application/pdf` with `{"type": "string", "format": "binary"}`.
- [X] T003 [P] [US1] Write `tests/integration/test_quote_document_render.py` (commit with T004–T006). Use the `client` and `seeded` fixtures. Create a customer with `credit_days=30`, then through the API create a quote on credit terms (`payment_terms: 1`) with a comment, add 3 lines (one with `discount_rate: "0.1"`), and confirm it. Then `GET /sales-quotes/{id}/document` and extract the text. Assert:
  - the facility name, the customer name, `Cotización {id}`, and the date and validity date as `formatting.date_short` prints them;
  - `Crédito` and `30`;
  - the comment;
  - the 3 product codes;
  - the subtotal, taxes and total exactly as the quote JSON's `subtotal`, `tax_total` and `total`, formatted with `formatting.money`;
  - the total in words, from `words.amount_in_words(total, currency)`;
  - `Elaborada por` and the salesperson name.

  Second test: a draft quote with 50 lines renders more than one page, and the last product code is on the last page. Third test: a cancelled quote renders (200).
- [X] T004 [US1] Add `async def sales_quote_context(db, quote) -> tuple[str, dict]` to `app/services/print_contexts.py`, after `sales_order_context`. Follow it closely (research R2):
  - **Header:** `header_context(db, quote.facility, f'Cotización - {formatting.pad8(id)}', letter=True)`, with the taxpayer blanked.
  - **Parties:** the customer, and the contact: name, plus `mobile`, or `phone` and `phone_ext` when there is no mobile.
  - **Ship-to:** the lines built the same way as on the order.
  - **Lines:** unit price = `totals.line_amounts(quantity=1, price=price+price_adjustment, discount_rate=line.discount_rate, tax_rate, tax_included)[0]`; amount = `line.subtotal`.
  - **Totals:** `subtotal`, `taxes`, `total` and `amount_in_words` from the quote's derived fields.
  - **Terms:** `terms_name(payment_terms)`, which gives "Contado" or "Crédito". When NET_D, append ` – {customer.credit_days} días`, the exact contract wording.
  - **Validity:** `date_short(due_date)`.
  - **Footer:** the salesperson's name.

  Return `'sales_quote.html', context`. Give it a docstring citing legacy `Quotations/Print.cshtml` and spec 024.
- [X] T005 [US1] Create `app/rendering/templates/sales_quote.html` from `sales_order.html`:
  - title "Cotización {id}";
  - Fecha and Vigencia;
  - a "Condiciones de pago" line with the terms (and the credit days);
  - the customer and contact block, the comment and the ship-to;
  - the lines table: Código, Descripción (with the comment), Precio, Cantidad, Importe;
  - the totals block with the amount in words;
  - the "Elaborada por" footer.

  No promise date or savings line.
- [X] T006 [US1] Add `GET /{sales_quote_id}/document` to `app/api/v1/endpoints/sales_quotes.py`, mirroring `print_sales_order_document` in `sales_orders.py`:
  - **Decorator and guard:** `response_class=Response, responses=rendering.PDF_RESPONSE`, `Depends(_READ)`;
  - **Body:** `_quote_or_404` → `attach_derived` → `print_contexts.sales_quote_context` → `rendering.render_pdf` → `rendering.pdf_response(pdf, f'cotizacion-{id:08d}.pdf')`;
  - **Docstring:** "The letter-size cotización, for a quote in any state (spec 024)."

  Run T002 and T003: green. Commit T002–T006.
  - **As built:** the ship-to block of `sales_order_context` moved into a shared `_ship_to_lines(address)` helper, which both documents call. The existing print tests pass unchanged.

**Checkpoint**: the PDF works on its own.

---

## Phase 3: User Story 3 — A reusable mail capability (P3, built before US2)

**Goal**: FR-012 to FR-014. `app/services/mail.py` and the settings.

**Independent test**: `SmtpMailer` drives a mocked `smtplib` correctly for each security mode, maps failures to `MailError`, and `get_mailer` returns None when mail is unconfigured. Nothing here mentions quotes.

- [X] T007 [P] [US3] Write `tests/unit/test_mail.py` (commit with T008–T009). Patch `app.services.mail.smtplib.SMTP` and `.SMTP_SSL` with `MagicMock` classes, and build settings with `Settings(...)` overrides or `SimpleNamespace`. Cases:
  1. **`starttls`:** `SMTP(host, port, timeout=...)`, then `starttls()`, `login(user, password)`, `send_message(msg)`, `quit()`, in that order.
  2. **`ssl`:** `SMTP_SSL` is used, and no `starttls` is called.
  3. **`none` with no user:** no `starttls`, no `login`.
  4. **The message sent:**
     - `From` is `MAIL_FROM`, `To` lists every recipient, and `Subject` is the given subject;
     - `Reply-To` is set when `MAIL_REPLY_TO` is set and absent otherwise;
     - the body is text/plain UTF-8 and round-trips accents (`cotización`);
     - one attachment carries the given filename, `application/pdf` and the exact bytes.
  5. **Errors:** `smtplib.SMTPRecipientsRefused`, `smtplib.SMTPAuthenticationError` and `OSError('timed out')` each raise `MailError`, whose message carries the cause.
  6. **`get_mailer()`:** None when `smtp_host` is empty, None when `mail_from` is empty, and an `SmtpMailer` when both are set (patch `app.services.mail.settings`).
- [X] T008 [P] [US3] Add the settings to `app/core/config.py` `Settings`, in the style of the neighbouring fields:
  - `smtp_host: str = ''`, `smtp_port: int = 587`;
  - `smtp_security: Literal['starttls', 'ssl', 'none'] = 'starttls'`;
  - `smtp_user: str = ''`, `smtp_password: str = ''`, `smtp_timeout: int = 30`;
  - `mail_from: str = ''`, `mail_reply_to: str = ''`.

  Give them a comment block: mail is optional and disabled while `SMTP_HOST` or `MAIL_FROM` is empty. Add the same keys to `.env.example`, empty or defaulted, with comments: the security modes, that Google Workspace needs an app password, and that `MAIL_REPLY_TO` is optional (spec 024).
- [X] T009 [US3] Create `app/services/mail.py` (research R3), with a module docstring saying it is document-agnostic and that the invoice email (#50) reuses it:
  - `@dataclass(frozen=True) class Attachment: filename, content_type, data: bytes`;
  - `@dataclass(frozen=True) class Message: to: Sequence[str], subject, body, attachments: Sequence[Attachment] = ()`;
  - `class MailError(Exception)`;
  - `class Mailer(Protocol): async def send(self, message: Message) -> None`;
  - `class SmtpMailer`: holds the settings; `_build(message) -> EmailMessage`; `_send_sync(msg)`, which does the `smtplib` conversation by security mode and maps `(smtplib.SMTPException, OSError)` to `MailError(str(exc))`; `async def send` calls `await asyncio.to_thread(self._send_sync, self._build(message))`;
  - `def get_mailer() -> Mailer | None` for FastAPI `Depends`.

  Run T007: green. Commit T007–T009.

**Checkpoint**: mail can be sent by any feature.

---

## Phase 4: User Story 2 — A salesperson emails the quote to the customer (P1)

**Goal**: FR-005 to FR-011. `POST /sales-quotes/{id}/email` and the audit entry.

**Independent test**: with a recording mailer, a confirmed quote sent to two addresses delivers one message with the PDF attached and writes one incidence. A failing mailer writes none. Unconfigured mail gives 503.

- [ ] T010 [P] [US2] Write `tests/unit/test_quote_email.py` (commit with T012–T014).
  - **`QuoteEmailRequest`:** 0 addresses → ValidationError; 11 → error; `'no-at-sign'`, `'a@b'` (no dot) and `'a b@c.mx'` → error; `'a@b.mx\r\nBcc: x@y.mx'` → error (header injection); 255 characters → error; `[' Ana@X.mx ', 'ana@x.mx', 'b@y.mx']` → `['Ana@X.mx', 'b@y.mx']`.
  - **`sales_quote_service.quote_email_message(quote_id, facility_name, salesperson, pdf)`:** the subject, the body text exactly as FR-006 (with `\n` line breaks), and one attachment `cotizacion-{id:08d}.pdf`, `application/pdf`, holding the bytes.
  - **`_recipients_context(addresses)`:** `To: a, b`, and a long list is truncated to 1,000 characters ending in `…`.
- [ ] T011 [P] [US2] Extend `tests/api/test_quote_document_email.py` with an email part (commit with T012–T014).
  - **Recording double:** `class RecordingMailer: sent = []; async def send(self, m): self.sent.append(m)`, installed with `app.dependency_overrides[mail.get_mailer] = lambda: recorder`.
  - **Service:** run the real `sales_quote_service.email_quote`. Patch `sales_quote_service.get_quote`, `sales_quote_service.attach_derived`, `print_contexts.sales_quote_context`, `rendering.render_pdf` and `incidences.record`. Override `get_db` with a fake session whose `get(model, id)` returns a facility stub (`name`) or an employee stub (`first_name`, `last_name`) by model, and whose `commit` is an `AsyncMock`.
  - **Cases:**
    1. Confirmed quote, `{"to": ["a@x.mx", "b@y.mx"]}` → 200 with `recipients` and `sent_at`. One message, To holding both addresses, the PDF attached. `incidences.record` called once with `source=SourceType.SALES_QUOTE`, `instance_id=7` and `reason='Quote emailed'`.
    2. Draft quote → 409 `Only a confirmed quote can be emailed`; nothing sent or recorded.
    3. Cancelled quote → 409; nothing sent or recorded.
    4. Invalid address → 422; nothing sent.
    5. Mailer raising `MailError('550 refused')` → 502, detail starting `The email could not be sent`; `incidences.record` not called.
    6. `get_mailer` overridden to return None → 503 `Email is not configured`.
    7. Unknown quote → 404.
    8. No privilege → 403.
    9. No token → 401.
- [ ] T012 [US2] In `app/enums.py`, add `SALES_QUOTE = 11` to `SourceType`, after `FACILITY = 10`, with a short comment saying it is API-only, like `FACILITY`, and records a quote email (spec 024). In `app/schemas/sales_quote.py`, add:
  - **`QuoteEmailRequest`:** `to: list[str] = Field(min_length=1, max_length=10)`, with a `field_validator` that trims, checks the regex `^[^@\s]+@[^@\s]+\.[^@\s]+$` and a length of at most 254, and deduplicates case-insensitively keeping first spellings (research R6).
  - **`QuoteEmailResponse`:** `recipients: list[str]`, `sent_at: LocalDateTime` (the type the other schemas use).
- [ ] T013 [US2] In `app/services/sales_quote_service.py`, add:
  - **Helpers:** `quote_email_message(...) -> mail.Message` and `_recipients_context(addresses) -> str`, which truncates to 1,000 characters with `…`.
  - **`async def email_quote(db, quote, to, *, mailer, current) -> tuple[list[str], datetime]`:**
    1. Refuse anything other than `completed and not cancelled` with 409 `Only a confirmed quote can be emailed`.
    2. `attach_derived`, then render through `print_contexts.sales_quote_context` and `rendering.render_pdf`.
    3. Load the facility name and the salesperson's name.
    4. Build the message and `await mailer.send(...)`. On `mail.MailError`, raise 502 `f'The email could not be sent: {exc}'`.
    5. Then `incidences.record(db, source=SourceType.SALES_QUOTE, instance_id=quote.sales_quote_id, updater=current.employee_id, reason='Quote emailed', context=_recipients_context(to))` and commit.
    6. Return the recipients and `local_now()`.

  Its docstring names the ordering: record only after the server accepted the message (research R5).
- [ ] T014 [US2] Add `POST /{sales_quote_id}/email` to `app/api/v1/endpoints/sales_quotes.py`:
  - **Signature:** `response_model=QuoteEmailResponse`, with `data: QuoteEmailRequest`, `current: CurrentUser = Depends(_READ)` and `mailer: Mailer | None = Depends(mail.get_mailer)`;
  - **Body:** 503 `Email is not configured` when `mailer is None`; then `_quote_or_404`; then `email_quote(...)`; return the response.

  Run `uv run pytest tests/api/test_quote_document_email.py tests/unit/test_quote_email.py tests/unit/test_mail.py -q`: green. Commit T010–T014.

**Checkpoint**: quotes can be emailed and every send is recorded.

---

## Phase 5: Polish & Cross-Cutting

- [ ] T015 [P] In `docs/constants.md` § SourceType, add rows `10 | Facility | facility (API-only)` and `11 | SalesQuote | sales_quote (API-only, quote emailed — spec 024)` (research R5).
- [ ] T016 [P] Update `docs/specs/02-sales.md` §2 Sales Quotes, under Actions "Print / PDF" and "Send Email":
  - the route, letter size and any state;
  - the email route: confirmed quotes only, 1–10 addresses, the message, the 502 and 503 behaviour, and the incidence record;
  - the mail settings.

  Note that the current system always reported success and kept no record.
- [ ] T017 [P] Add an `### Added` entry to `CHANGELOG.md` `[Unreleased]`: **Quotation PDF and email** (mictlanix/mbe-roadmap#30, spec 024). Cover:
  - both routes;
  - the reusable mail service and the new settings;
  - the incidence source 11.
  - An **upgrade note**: mail is off until `SMTP_HOST` and `MAIL_FROM` are set.
- [ ] T018 Run the full gate: `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. No new failures against T001, and no test opens a socket to an SMTP server. Record the counts here.
- [ ] T019 **Confirm with the user before deploying, and ask for SMTP credentials and a test mailbox.** Deploy the branch to `/var/www/mbe-api-test`, set the mail settings in its `.env` as the user directs, and run quickstart's manual checks 2–4. Record the results, and whether mail settings were left on test, in `quickstart.md` under "Checks on the test deployment".

---

## Dependencies

- T001 → everything.
- **US1** (T002–T006) is independent.
- **US3** (T007–T009) is independent of US1.
- **US2** (T010–T014) needs US1 (the render) and US3 (the mailer).
- **Shared files:** US1 and US2 both edit `sales_quotes.py` and `test_quote_document_email.py`, so run US2 after US1.
- **Polish** comes after all stories. T019 waits for the user.

## Parallel opportunities

- **US1:** T002 and T003 (different test files).
- **Across stories:** US3 (T007–T009) can run alongside US1.
- **US3:** T007 and T008.
- **US2:** T010 and T011.
- **Polish:** T015–T017.

## Implementation strategy

1. **US1:** the PDF on its own is useful, and the UI can show and print it.
2. **US3:** the mail capability.
3. **US2:** the email, which together with US1 closes #30.
4. **Polish:** docs and the gate, then the test deployment with real SMTP once the user provides credentials.
5. **PR:** under "Roadmap", write `Closes mictlanix/mbe-roadmap#30`.
