# Quickstart: validating Quotation PDF and Email

The routes are specified in [contracts/quote-document-email.md](contracts/quote-document-email.md), and the decisions are in [research.md](research.md).

## Automated gate

```bash
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

Expected: zero ruff violations and a green suite. No test opens a real SMTP connection (SC-004).

| Requirement | Covered by |
|---|---|
| FR-001–FR-004 (document) | `tests/api/test_quote_document_email.py` (200/404/403, PDF signature, filename, any state); `tests/integration/test_quote_document_render.py` (every FR-002 element by text extraction, a 50-line quote paginates, no-logo facility) |
| FR-005–FR-011 (email) | `tests/api/test_quote_document_email.py` (200 + one incidence, 409 draft/cancelled, 422, 502 with no incidence, 503); `tests/unit/test_quote_email.py` (address rules, message text) |
| FR-012–FR-014 (mail capability) | `tests/unit/test_mail.py` (SMTP conversation by security mode, headers, attachment, error mapping, configured or not) |

## Manual checks on the test deployment (SC-005)

Deploy only after the user confirms (`/var/www/mbe-api-test`). No migration is needed.

1. **Configure mail.** Set `SMTP_*`, `MAIL_FROM` and optionally `MAIL_REPLY_TO` in the test instance's `.env`, using credentials the user provides. Restart the service.
2. **Document.** `GET /sales-quotes/{id}/document` for a confirmed quote with several lines. Open the PDF and check the header, terms, validity, lines and totals against the quote's JSON.
3. **Email.**
   - Send `POST /sales-quotes/{id}/email` `{"to": ["<a test mailbox>"]}`. The message arrives with the subject, the body and the PDF, and Reply-To is set if it was configured.
   - The newest `incidence` with `source = 11` names the quote, the employee and the address:

     ```sql
     SELECT * FROM incidence WHERE source = 11 ORDER BY incidence_id DESC LIMIT 1;
     ```
4. **Refusals.** A draft quote gives 409. An invalid address gives 422.
5. **Clean up.** Leave the mail settings in place only if the user wants them on test.

## Checks on the test deployment: 2026-10-22 (branch `024-quote-pdf-email` at `e102003`)

Deployed to `/var/www/mbe-api-test` (`git checkout`, `uv sync`, `systemctl restart mbe-api-test`). No migration. The checks called the local service on xolotl against `mbe_dev` with a short-lived admin token minted on the box. **Mail was left unconfigured, at the user's choice:** the real-server email check (SC-005) was not run.

1. **Document** ✅: `GET /sales-quotes/30358/document` (a confirmed credit quote with a contact) returned 200 `application/pdf`, `inline; filename="cotizacion-00030358.pdf"`, as one Letter page (612 pt wide). The text reads:
   - "CASA MAESTRA" with its address;
   - customer "INMOBILIARIA UKLA" and contact "JUAN MANUEL CONTRERAS 4775648982";
   - "COTIZACIÓN 30358", Fecha 2026-09-29, Vigencia 2026-10-14, and "Crédito – 15 días";
   - the concrete line at $2,143.10 × 7 = $15,001.72, the quote's own subtotal;
   - "Elaborada por".
2. **Email without mail settings** ✅: `POST /sales-quotes/30358/email` returned 503 `{"detail": "Email is not configured"}`. Nothing was sent or recorded.
3. **Missing quote** ✅: `GET /sales-quotes/999999999/document` returned 404 `Sales quote not found`.

**Still open (SC-005):** a real send to a test mailbox, checking the PDF attachment, the Reply-To and the `incidence` row (source 11). It needs `SMTP_*` and `MAIL_FROM` in the instance's `.env`.
