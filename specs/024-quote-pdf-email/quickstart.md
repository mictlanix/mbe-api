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
