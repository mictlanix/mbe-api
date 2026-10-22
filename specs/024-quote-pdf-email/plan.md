# Implementation Plan: Quotation PDF and Email

**Branch**: `024-quote-pdf-email` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/024-quote-pdf-email/spec.md`

## Summary

- **Document:** `GET /sales-quotes/{id}/document` renders a letter-size quote PDF through spec 019's pipeline, using a new `sales_quote.html` template and a `sales_quote_context`.
- **Email:** `POST /sales-quotes/{id}/email` sends a confirmed quote's PDF to 1–10 addresses, synchronously. It records one `incidence` (new source 11) per successful send.
- **Mail capability:** a quote-agnostic module (`app/services/mail.py`) sends mail over SMTP with standard-library `smtplib` in a worker thread, configured by `SMTP_*` and `MAIL_*` settings. It is overridable in tests and reusable by the invoice email.

The routes are specified in [contracts/quote-document-email.md](contracts/quote-document-email.md).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2, Jinja2 + WeasyPrint (spec 019). Mail uses the standard library (`smtplib`, `email`). **No new dependency.**

**Storage**: MariaDB `mbe_dev`. Writes `incidence` rows. No migration.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`; `pypdf` (already a test dependency) for text extraction; `unittest.mock` for `smtplib`.

**Target Platform**: Linux server (xolotl test instance)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**:
- **Document:** a render costs what the sales order document costs, under spec 019's single render slot.
- **Email:** the request waits for one SMTP conversation, bounded by `SMTP_TIMEOUT` (30 s).

**Constraints**:
- The event loop never blocks: renders and SMTP both run off-loop.
- Mail is optional: with no settings the app starts and the email route answers 503.

**Scale/Scope**: 2 routes, 1 template, 1 context function, 1 mail module, 8 settings, 1 enum value

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | One mail module: a protocol, an SMTP implementation and a dependency. Fixed message text, no templates for mail, no retries, no queue. | PASS |
| II. Think Before Coding | The clarifications are recorded in the spec. Research records the departures: the payment terms are printed, sending is synchronous, the record is written after the send, and FR-014 is reworded. | PASS |
| III. Surgical Changes | Adds new files. The existing ones gain only additions: two routes in `sales_quotes.py`, settings, an enum value, `.env.example`, `docs/constants.md`, and `02-sales.md`. | PASS |
| IV. Goal-Driven Execution | Success is the test matrix in quickstart.md plus SC-005 on the test deployment. | PASS |
| V. Reuse Over Rebuild | Reuses `rendering`, `header_context`, `formatting`, `words`, `totals`, `attach_derived` and `incidences.record`. No new dependency (stdlib `smtplib` instead of `aiosmtplib`, and a regex instead of `email-validator`). The new mail module is justified under Complexity Tracking. | PASS |
| VI. Async-First | SMTP runs in `asyncio.to_thread`, and rendering is already off-loop. | PASS |
| VII. Security by Default | Both routes are authenticated and privilege-gated. Address validation refuses whitespace, which blocks header injection. Credentials come only from the environment. | PASS |
| VIII. Ruff Compliance | Gate in quickstart.md. | PASS |
| Testing | New endpoints get happy path, 404, 401/403 and resource-specific cases (409, 422, 502, 503). The mail module gets unit tests, written first. | PASS |
| Changelog | A `CHANGELOG.md` `[Unreleased]` → Added entry, including the new settings. | PASS |

Re-check after Phase 1 design: unchanged, all PASS.

## Project Structure

### Documentation (this feature)

```text
specs/024-quote-pdf-email/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── quote-document-email.md
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── core/config.py                       # + SMTP_* / MAIL_* settings
├── enums.py                             # + SourceType.SALES_QUOTE = 11
├── services/
│   ├── mail.py                          # new: Message, Attachment, Mailer, SmtpMailer, MailError, get_mailer
│   ├── print_contexts.py                # + sales_quote_context
│   └── sales_quote_service.py           # + email_quote (state check, message, send, incidence)
├── schemas/sales_quote.py               # + QuoteEmailRequest, QuoteEmailResponse
├── rendering/templates/sales_quote.html # new
└── api/v1/endpoints/sales_quotes.py     # + GET /{id}/document, POST /{id}/email

tests/
├── unit/test_mail.py                    # new
├── unit/test_quote_email.py             # new
├── api/test_quote_document_email.py     # new
└── integration/test_quote_document_render.py   # new

.env.example, docs/constants.md, docs/specs/02-sales.md, CHANGELOG.md
```

**Structure Decision**: single web-service project. The mail capability lives in `app/services/mail.py`, beside the other services, and imports nothing from sales. The quote-specific part (message text, state check, audit) lives in `sales_quote_service.email_quote`, which is the only caller for now.

## Complexity Tracking

| Addition | Why needed | Simpler alternative rejected because |
|---|---|---|
| New service module `app/services/mail.py` (constitution V asks for justification) | No mail capability exists, and the invoice email (#50) must reuse it | Inlining SMTP in the quote service would make the invoice email copy it or import quote code |
| New `SourceType` value 11 | `incidence` has no key for a quote, and the request asks for sends to be recorded the way other audit events are | A dedicated mail-log table would need a migration and a second audit mechanism |
