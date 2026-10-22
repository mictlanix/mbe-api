# Feature Specification: Quotation PDF and Email

**Feature Branch**: `024-quote-pdf-email`

**Created**: 2026-10-22

**Status**: Draft

**Input**: Implements mictlanix/mbe-roadmap#30 (epic mictlanix/mbe-roadmap#3). The scope document is `docs/specs/02-sales.md`, Sales Quotes, "Print / PDF" and "Send Email". The request named "§2.2", but the section is numbered §2. There are two parts:
1. A letter-size PDF of a sales quote, built the way spec 019 builds the sales order document.
2. Sending that PDF by email to addresses the user chooses, through a small mail capability that the future invoice email (mictlanix/mbe-roadmap#50) can reuse. Each send is recorded.

## Findings that shape this spec

### The current system (`mictlanix/mbe`)

- **PDF** (`QuotationsController.Print`/`Pdf`, `Views/Quotations/Print.cshtml`):
  - It is rendered on Letter paper with a 6 mm margin.
  - The header carries the facility (store) name, address and logo. No company name or RFC appears.
  - The body carries:
    - the customer name and the contact (name, mobile or phone);
    - the title "Cotización {id}" (the record id, not the serial), the date and the due date (the validity);
    - the comment and the ship-to address;
    - the lines: code, name with the line comment, net price, quantity and subtotal;
    - the total in words, subtotal, taxes and total;
    - "Elaborada por" with the salesperson's name.
  - The payment terms are not printed. The PDF is offered only for confirmed or cancelled quotes, while the on-screen print works in any state.
- **Email** (`QuotationsController.SendEmail`):
  - **Recipient:** one, typed or picked from suggestions. The suggestions are the email addresses of the customer's contacts and taxpayer recipients (`CustomersController.ListEmails`).
  - **Subject:** "{company} Cotización {serial}".
  - **Body:** plain text: "Le enviamos la cotización {id}. Agradecemos su atención y esperamos servirle muy pronto. Atte: {salesperson}." ("We are sending you quote {id}. Thank you for your attention, and we hope to serve you very soon. Regards, {salesperson}.")
  - **Attachment and sender:** the PDF is attached, and the sender is `noreply@mictlanix.com`.
  - **State:** none is checked, so a draft can be emailed.
- **How the current system sends mail:**
  - It authenticates to SMTP with Google OAuth2 using a hard-coded placeholder refresh token (`NotificationsHelpers.cs:164`).
  - It sends in a background task and ignores the result, and the screen always reports success.
  - **No record is kept of any send.**
- **Invoices** use the same helper: one recipient plus a configured CC list, and the PDF and XML attached.

### This API today

- **Rendering:** spec 019's rendering pipeline produces three PDFs (sale ticket, sales order document, cash cut). The sales order document is `GET /sales-orders/{id}/document`: Letter, 6 mm margin, inline `pedido-{id:08d}.pdf`, `SALES_ORDERS` Read, any state. The OpenAPI 200 response is `application/pdf` only, so mbe-ui's generated client receives bytes.
- **No mail:** there is no mail sending and no mail setting.
- **Audit:** audit events go to `incidence`, keyed by `(source, instance_id)`, with the acting employee, a reason and a content line. `SourceType` mirrors the current system's values 1–9 and adds an API-only `FACILITY = 10`. It has no value for a quote.

### mbe-ui (`origin/main` at `2ab245e`)

- No screen or generated method refers to a quote PDF or quote email.
- Sales order PDFs go through a shared document module (`DocumentKind` → preview, print, save). A quote document would be one more kind there.

### Measured in mbe_dev (2026-10-22, quotes from the last 12 months)

| Measure | Count |
|---|---|
| Quotes (non-cancelled) | 7,045 |
| … whose own contact has an email | 2,141 |
| … whose customer has at least one contact with an email | 6,390 (91%) |
| … for the walk-in customer | 1,552 |
| By state: confirmed / draft / cancelled (all quotes) | 6,774 / 271 / 117 |
| Quotes without a serial | 2,502 of 7,162 (the PDF title uses the id, as the current system does) |

## Clarifications

### Session 2026-10-22

- Q: Which quotes can be emailed? → A: Confirmed only. Drafts and cancelled quotes are refused (FR-009).
- Q: Which privilege does sending require? → A: Read on sales quotes, the same as the PDF (FR-010).
- Q: How does the API authenticate to the mail server? → A: SMTP user and password over TLS (STARTTLS or SSL). OAuth2 is out of scope (FR-013).
- Q: Should a quote email carry a Reply-To so that customer replies reach a person? → A: Yes, a single optional Reply-To address from configuration (for example, a sales inbox). When it is unset, no Reply-To is sent (FR-006, FR-013).

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A salesperson gets the quote as a PDF (Priority: P1)

A salesperson opens a quote and downloads or prints it as a letter-size document with the facility's logo. They hand it to the customer or keep it.

**Why this priority**: The PDF is what the customer actually receives, by email or on paper, and the email in Story 2 attaches it.

**Independent Test**: Request the document for a confirmed quote with three lines. The response is a letter-size PDF. Its text holds:
- the facility name, the customer, "Cotización {id}", the date and the validity date;
- the payment terms;
- the three lines, the subtotal, the taxes, the total and the total in words;
- the salesperson.

**Acceptance Scenarios**:

1. **Given** a quote, **When** its document is requested by a user with Read on sales quotes, **Then** the response is a PDF displayed inline, named `cotizacion-{id:08d}.pdf`.
2. **Given** a quote in any state (draft, confirmed, cancelled), **When** its document is requested, **Then** it renders, as the sales order document does.
3. **Given** a quote whose facility has a logo, **When** it renders, **Then** the logo is in the header. Without one, the header still renders.
4. **Given** a quote id that does not exist, **Then** the response is 404 with the same message the quote's read route gives.
5. **Given** a user without Read on sales quotes, **Then** the response is 403.

---

### User Story 2 - A salesperson emails the quote to the customer (Priority: P1)

From a quote, a salesperson sends the PDF by email to one or more addresses. These are typically the customer contact's address, or one the salesperson types. The customer receives a message from the business with the quote attached. The salesperson learns at once whether the send succeeded.

**Why this priority**: It is the second legacy action of #30. Today the current system's email cannot be relied on: it ignores failures and keeps no record.

**Independent Test**: With mail configured to the test double:
- send a confirmed quote to two addresses: both are delivered, with the subject, the body and the PDF attached, and one audit entry names the sender, the time and both addresses;
- with the mail server failing, the response says so, and no audit entry is written.

**Acceptance Scenarios**:

1. **Given** a confirmed quote and one or more valid addresses, **When** the user sends it, **Then** one message is delivered to those addresses, carrying:
   - the subject "{facility name} Cotización {id}";
   - the body text of the current system;
   - the quote PDF attached, as in Story 1.
2. **Given** a successful send, **Then** one audit entry records the quote, the employee who sent it, the time and every recipient address.
3. **Given** an invalid address, or no address, **Then** the request is refused before anything is sent.
4. **Given** the mail server refuses or cannot be reached, **Then** the response reports that the email could not be sent, and nothing is recorded as sent.
5. **Given** mail is not configured in this deployment, **Then** the response says that email is not available, and the rest of the API works normally.
6. **Given** a draft or a cancelled quote, **When** the user sends it, **Then** it is refused (409), nothing is sent and nothing is recorded (FR-009).

---

### User Story 3 - Other documents can be emailed the same way later (Priority: P3)

A developer building the invoice email (mictlanix/mbe-roadmap#50) uses the same mail capability: recipients, subject, body and attachments, with nothing quote-specific in it.

**Why this priority**: It is a design constraint more than a user journey, and it is tested through the quote email.

**Independent Test**: The mail capability can be called with any subject, body and attachments, and its tests do not mention quotes.

**Acceptance Scenarios**:

1. **Given** the mail capability, **When** it is given recipients, a subject, a plain-text body and any number of attachments, **Then** it delivers exactly that message.
2. **Given** a test run, **Then** no real mail is sent: tests use a double that records messages.

---

### Edge Cases

- **No contact email:** a quote whose contact has no email (or that has no contact) can still be sent. The user supplies the address.
- **Repeated addresses:** they are sent once each (deduplicated, case-insensitively).
- **Many recipients:** a single request may name up to 10 addresses. More is refused, which guards against a pasted list.
- **Long audit text:** an audit entry has a 1,000-character limit for its content. Ten addresses fit in the common case; contact emails are at most 80 characters. A recipient list longer than 1,000 characters is truncated with an ellipsis in the audit entry, and the response always carries the full list.
- **No logo:** a facility without a logo renders without one.
- **Long quotes:** a quote with many lines paginates on letter pages, like the sales order document.
- **Sender:** the From address is the configured one. The salesperson's own address is not used, because it is not a verified sender. Replies go to the configured Reply-To, when set.
- **Concurrent sends:** two sends of the same quote both go out and both are recorded. There is no locking.

## Requirements *(mandatory)*

### Functional Requirements

**Quote document (PDF)**

- **FR-001**: The system MUST provide a quote document as a letter-size PDF (6 mm margin), returned inline as `cotizacion-{id:08d}.pdf`. It follows the conventions of spec 019's sales order document: the route shape `/sales-quotes/{id}/document`, a PDF-only OpenAPI 200 response, and the same rendering pipeline.
- **FR-002**: The document MUST show:
  - **Header:** the facility name, address and logo.
  - **Parties:** the customer name and the contact (name, mobile or phone).
  - **Title block:** "Cotización {id}", the date, and the validity date (the quote's due date).
  - **Terms:** the payment terms (cash, or credit with the customer's credit days).
  - **Notes:** the comment and the ship-to address, when present.
  - **Lines:** code, name and line comment, unit price net of discount, quantity, and line amount.
  - **Totals:** subtotal, taxes, total, and the total in words in the quote's currency.
  - **Footer:** "Elaborada por" and the salesperson's name.
- **FR-003**: The document MUST render for a quote in any state.
- **FR-004**: Access MUST require Read on sales quotes. An unknown quote is 404.

**Quote email**

- **FR-005**: The system MUST send a quote by email to 1–10 addresses given in the request. Addresses are validated and deduplicated case-insensitively. Invalid, missing or too many addresses are refused (422) before any send.
- **FR-006**: The message MUST be:
  - **Subject:** "{facility name} Cotización {id}".
  - **Body:** plain text: "Le enviamos la cotización {id}.\nAgradecemos su atención y esperamos servirle muy pronto.\nAtte: {salesperson name}."
  - **Attachment:** the FR-001 PDF, named `cotizacion-{id:08d}.pdf`.
  - **From:** the configured sender address.
  - **Reply-To:** the configured reply-to address, when one is set. Otherwise none is sent.
- **FR-007**: The send MUST be synchronous: the response says whether the mail server accepted the message. A refusal or a connection failure is reported as an error (502), and nothing is recorded.
- **FR-008**: Each successful send MUST write one audit entry:
  - **Keyed to:** the quote.
  - **Who:** the sending employee.
  - **When:** the time.
  - **What:** the reason "Quote emailed", plus a content line listing every recipient.

  It is written after the server accepts the message, so a recorded send always happened.
- **FR-009**: Only a confirmed quote MAY be emailed. A draft or a cancelled quote is refused (409) before any send. A draft could still change after the customer received it. This is stricter than the current system, which sends any state.
- **FR-010**: Sending MUST require Read on sales quotes, the same as the PDF. Whoever can see the quote can send it.
- **FR-011**: When mail is not configured, the email route MUST answer 503 "Email is not configured", and the API MUST still start and serve every other route.

**Mail capability**

- **FR-012**: A mail capability MUST send one message (recipients, subject, plain-text body, zero or more attachments with name, content type and bytes). It must not depend on quotes, so that invoices can reuse it.
- **FR-013**: Its connection settings MUST come from configuration, documented in `.env.example` and absent by default. They cover the server, port, security mode, credentials, the From address and an optional Reply-To address. Authentication is standard SMTP user and password over TLS (STARTTLS or implicit SSL, set in configuration). This works with Google Workspace through an app password and with most providers. OAuth2 is out of scope.
- **FR-014**: Tests MUST use a mail double that records messages instead of sending them. The real transport is tested with the SMTP client replaced, so no new test dependency is needed (research R8), and is exercised against a real server once, on the test deployment (SC-005).

**Documentation**

- **FR-015**: `docs/specs/02-sales.md` (Sales Quotes, Print/PDF and Send Email) MUST describe the document and the email as the API implements them.

### Key Entities

- **Sales quote** (existing): header, lines, totals, state, due date (validity), payment terms, contact, ship-to, salesperson and facility. Nothing new is stored on it.
- **Audit entry** (`incidence`, existing): one row per successful send. It needs a source value for quotes, which does not exist yet.
- **Mail message** (not stored): recipients, subject, body and attachments.
- **Mail settings** (configuration): server, port, security mode, credentials, sender and optional reply-to.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: A quote of 1 to 50 lines renders as a letter-size PDF carrying every FR-002 element. This is checked by text extraction in tests.
- **SC-002**: 100% of successful sends leave exactly one audit entry with every recipient. 0 failed sends leave one.
- **SC-003**: A send that the mail server refuses reports the failure to the user every time. None is reported as sent, unlike the current system, which reports every send as successful.
- **SC-004**: The test suite sends no real email.
- **SC-005**: On the test deployment, with real mail settings, a quote emailed to a test mailbox arrives with the PDF attached, and the audit entry exists.

## Assumptions

- **Route shape:** the quote's routes follow spec 019's, under `/sales-quotes/{id}/…`: `GET …/document` for the PDF and `POST …/email` for the send.
- **Title:** it uses the quote id, as the current system's PDF does. 2,502 quotes in the last year have no serial.
- **Payment terms on the PDF:** they are printed, as the request asks, although the current system's PDF leaves them out.
- **Recipient suggestions:** there is no suggestions endpoint. The client already reads the customer's contacts and taxpayer recipients, with their emails, and offers them.
- **Message text:** the subject and body text are fixed, as in the current system. Custom text is out of scope.
- **Audit source:** the audit entry uses a new source value for quotes. It is API-only, as `FACILITY = 10` is, because the current system's enum has no quote value.
- **Language:** the email is sent in Spanish, like the document.
- **Deployment database:** mbe_dev, whose data runs through 2026-10-21.

## Out of Scope

- UI (mictlanix/mbe-roadmap#31).
- Invoice email (mictlanix/mbe-roadmap#50); only the reusable mail capability is built here.
- ESC/POS printing (mictlanix/mbe-api#247).
- CC or BCC lists, custom subject or body, HTML email, a per-user or per-salesperson reply-to, scheduling and retries.
- OAuth2 mail authentication.
- Recording failed send attempts.
