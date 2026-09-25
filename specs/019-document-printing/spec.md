# Feature Specification: Document Printing

**Feature Branch**: `019-document-printing`
**Created**: 2026-09-24
**Status**: Draft
**Input**: GitHub issue #230, Phase 1 without the CFDI invoice. The API has no way to produce a printable document. Legacy prints through a jsreport + PhantomJS sidecar, which upstream abandoned in 2018. mbe-ui has deferred printing in four specs and requires PDFs to be generated server-side (mictlanix/mbe-ui#178). This feature adds the rendering core and three documents: the sale ticket, the sales order document and the cash session cut.

## Clarifications

### Session 2026-09-24

- Q: Which part of issue #230 does this spec cover? → A: The rendering core, the sale ticket, the sales order document and the cash cut. The CFDI invoice PDF (and the `/fiscal-documents` router it needs), ESC/POS / CloudPRNT printing, email delivery and the other ~40 legacy documents are follow-up specs.
- Q: Legacy has two sale tickets. `POS/Print` is a pre-payment ticket for orders that are not completed. `Payments/Print` is the final receipt for completed orders. How does the one ticket route map to them? → A: One route that switches on the order's state. Before completion it prints the pre-payment ticket; after completion it prints the final receipt. This matches legacy, which redirects from one to the other.
- Q: No legacy template reads `display_on_ticket`. What does it do here? → A: When a payment was taken through a payment option whose flag is off, the receipt still prints the amount, but labels it with the base payment method name (e.g. "Tarjeta de crédito") instead of the option's own name (e.g. "3 MSI Banamex").
- Q: What does the cash cut include? mbe-api computes only the opening amount and payments by method today. → A: The full legacy report: sales, refunds and expenses by method, cash movements, counted cash, and shortage or overage, computed with the legacy formulas.
- Q: Legacy's cut selects refunds and sales by payment type only, which mis-sums three of the four refund and tender conventions in `mbe_dev`, including mbe-api's own negative cash payouts. How are payments classified? → A: By type and sign. A refund is a credit-note payment or a negative payment. A sale is any other positive payment (FR-031).

### Session 2026-09-25

- Q: What paper do the ticket printers use? → A: 80 mm thermal rolls with a 72 mm printable width. The ticket page stays 72 mm wide with zero margin.
- Q: Should the amount in words fix only legacy's listed defects, or all of its Spanish grammar? → A: All of it. Legacy's format stays, but the Spanish is correct: `UN PESO 50/100`, `CIENTO UN PESOS`, `VEINTIÚN MIL`, `UN MILLÓN DE PESOS` (research R10).
- Q: What page height should tickets use? → A: Fitted height. Each ticket is a single page, 72 mm wide and exactly as tall as its content, replacing legacy's fixed 297 mm pages. A short ticket feeds no blank paper, and a long ticket never has a page boundary (or a driver cut) in the middle.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - A cashier prints the sale ticket (Priority: P1)

A cashier finishes a sale at the point of sale and prints the ticket for the customer through the browser's print dialog on an 80 mm thermal printer (72 mm printable). Before the order is paid, the same button prints a pre-payment ticket the customer takes to the cash desk. Today mbe-ui has no ticket to print at all, so a store running on mbe-ui cannot hand a customer a receipt.

**Why this priority**: The sale ticket is the most frequently printed document, and it is the gap that blocks stores from moving off legacy.

**Independent Test**: Create an order, request its ticket, and check that the result is a one-page PDF 72 mm wide showing the store header, the lines, the totals and a barcode of the order number. Complete the order with a payment and request the ticket again; check that it is now the final receipt with the payment and change.

**Acceptance Scenarios**:

1. **Given** an order that is not completed, **When** its ticket is requested, **Then** a single-page PDF 72 mm wide and as tall as its content is returned. It shows the store header, the "Punto de Venta" title, the folio and date, the salesperson, the customer, each line (quantity × price, discount rate if any, product code, product name, line total), taxes and total, and a Code128 barcode of the order id. If the order is on immediate terms, it carries the legend "Este recibo no es un comprobante de pago."
2. **Given** a completed order paid in cash with change handed back, **When** its ticket is requested, **Then** the final receipt ("Ticket de Venta") is returned. It shows the lines with their comments, subtotal, discount (only when non-zero), taxes, total, one row per payment and a change row.
3. **Given** a completed order paid through a payment option whose "display on ticket" flag is off, **When** its ticket is requested, **Then** that payment's row shows the base payment method name and its amount, never the option's name.
4. **Given** a completed order that was later cancelled, **When** its ticket is requested, **Then** the receipt carries a visible "Cancelado" stamp with the cancellation time and order id.
5. **Given** a completed credit order with an outstanding balance, **When** its ticket is requested, **Then** the receipt carries the promissory note (pagaré) filled with the customer name, the balance, the due date and the issuing taxpayer's name, and a signature line.
6. **Given** a completed order with a card payment, **When** its ticket is requested, **Then** the receipt carries the card-payment legend and a signature line for the customer.
7. **Given** the store has a receipt message configured, **When** a final receipt is printed, **Then** the message appears at the bottom.
8. **Given** an order that does not exist, **When** its ticket is requested, **Then** the response is 404.

---

### User Story 2 - A supervisor prints the cash cut when closing a session (Priority: P1)

A supervisor closes a cash session with the counted denominations and prints the cut ticket ("corte de caja") on the same thermal printer. It shows what came in by payment method, what went out, what cash should be in the drawer, what was counted, and whether the drawer is short or over.

**Why this priority**: The cut is printed every shift, at the exact moment the session is closed, and it is how cash discrepancies are caught. mbe-ui can already close a session but has nothing to print.

**Independent Test**: Open a session with a starting amount, take cash and card payments and one cash refund, record an expense voucher, close the session with a count, request the cut, and check every figure against a hand calculation.

**Acceptance Scenarios**:

1. **Given** a closed session, **When** its cut is requested, **Then** a single-page PDF 72 mm wide and as tall as its content is returned. It shows the store logo and name (no address), the cash drawer, start and end time, the cashier, and a barcode of the session id.
2. **Given** a closed session with payments in several methods, **When** its cut is requested, **Then** it lists sales received by method (each net of change handed back) and their total, refunds by method, and expenses.
3. **Given** a closed session, **When** its cut is requested, **Then** it shows the cash movements (starting cash, cash sales, cash expenses, cash refunds), the counted cash, and a single "Faltante" (shortage) or "Sobrante" (overage) row with the absolute difference between counted cash and the cash that should be in the drawer.
4. **Given** a session that is still open, **When** its cut is requested, **Then** the request is rejected with 409 and no document is produced.
5. **Given** a session that does not exist, **When** its cut is requested, **Then** the response is 404.

---

### User Story 3 - A salesperson prints the sales order document (Priority: P2)

A salesperson prints a sales order (pedido) on letter paper to hand to a customer or to the warehouse. It carries the customer and ship-to details, the promise date, the terms, the lines and the totals with the amount in words.

**Why this priority**: It is printed less often than the ticket and the cut, and it is not tied to the cash flow of a shift.

**Independent Test**: Request the document for an order with a ship-to address, a discount and several lines, and check that the result is a letter-size PDF showing all of them and the correct totals.

**Acceptance Scenarios**:

1. **Given** any order, **When** its document is requested, **Then** a letter-size PDF is returned. It shows the store name, address and logo in the header; the customer, contact, ship-to address and comment; the order id, salesperson, date, promise date and terms, plus the due date when on credit terms; a lines table (quantity, code, name with comment, unit price, amount); and subtotal, discount (when non-zero), taxes and total.
2. **Given** an order with a total of 1,234.50 MXN, **When** its document is requested, **Then** the total is also written out in uppercase Spanish words, in legacy's format with the grammar corrections listed in research R10.
3. **Given** an order with a discount, **When** its document is requested, **Then** the savings line "¡Usted ahorró $X en esta compra!" appears.
4. **Given** an order that does not exist, **When** its document is requested, **Then** the response is 404.

---

### User Story 4 - Client code receives intact PDF bytes (Priority: P1)

A developer regenerates mbe-ui's API client from the published schema and calls a print endpoint. The client receives the PDF as raw bytes. If a print endpoint declares its response loosely, the generated client decodes the bytes as text. That corrupts every PDF silently, with no error.

**Why this priority**: A wrong schema breaks every document in a way nobody notices until a customer receives a blank or garbled receipt. It must be locked down before any client ships.

**Independent Test**: Fetch the published API schema and check each print route's success response.

**Acceptance Scenarios**:

1. **Given** the published API schema, **When** a developer reads any print route, **Then** its success response is declared as `application/pdf` with a binary string schema, and it declares no JSON success response.

---

### User Story 5 - Printing never degrades or slows the rest of the service (Priority: P2)

An operator runs the API on a host with no outbound internet access, under normal load. Documents still render identically, with fonts, logo and barcodes intact. Users on other screens do not notice when several tickets print at once.

**Why this priority**: Legacy's worst failure is silent: the renderer fetches fonts over plain HTTP, and the logo and barcode from the web app. When any fetch fails, the document prints degraded with no error. This feature exists partly to remove that.

**Independent Test**: Render each document with outbound network blocked and compare the output with a render made with network available. Fire a batch of concurrent renders and measure the response time of an unrelated endpoint.

**Acceptance Scenarios**:

1. **Given** outbound network access is blocked, **When** each document is rendered, **Then** it succeeds, and its output is byte-identical to the same render with network available.
2. **Given** ten documents rendering at once, **When** an unrelated endpoint is called, **Then** it responds within its normal time.

### Edge Cases

- **The store has no logo, or the logo file is missing from disk.** The document renders without a logo and without error. It never fetches the logo over the network.
- **The customer is the default walk-in customer** (`default_customer_id`) and the order carries a per-order customer name. The ticket prints that name after the customer's. For any other customer, only the customer's name is printed.
- **The order has no folio yet** (a draft). The ticket prints with the folio blank. The barcode carries the order id, which always exists.
- **The order has many lines.** The ticket is still one page. The page simply grows taller, so nothing is cut off and no page boundary splits the ticket.
- **A very short ticket** (one line). The page is only as tall as the content, with no blank space after the barcode.
- **A draft that was cancelled** (never completed). It prints the pre-payment ticket without the cancellation stamp, as legacy does. The stamp belongs to the final receipt.
- **A payment was taken with no cash session** (payment on delivery). The final receipt shows the "Contraentrega" block with each such payment's method and amount, as legacy does.
- **The order is completed on immediate terms but not paid.** The final receipt shows "Por cobrar: $X" with the balance.
- **A session closed with no counted cash rows.** Counted cash is 0, and the whole expected cash is reported as a shortage.
- **Order in a currency other than MXN.** Amounts print as stored, and the amount in words uses that currency's name. No conversion is applied.
- **The caller lacks the read privilege** for sales orders (tickets and documents) or for the point of sale (cuts). The response is 403, the same as reading the record.
- **Unauthenticated request.** The response is 401.

## Requirements *(mandatory)*

### Functional Requirements

**Rendering core**

- **FR-001**: The system MUST produce each document on demand, from data it already stores, and return it inline as a PDF. It MUST NOT persist generated documents.
- **FR-002**: The system MUST support two page geometries: a ticket geometry that is a single page, 72 mm wide with zero margin, whose height equals the height of its content; and a letter geometry. Each follows the matching legacy layout (`_TicketLayout`, `_PrintLayout`).
- **FR-003**: Rendering MUST NOT make any network request. Fonts are bundled with the service, the store logo is read from the service's image storage, and barcodes are generated in-process and embedded in the document.
- **FR-004**: Barcodes MUST be Code128 vector graphics embedded in the document, carrying the record id shown beneath them.
- **FR-005**: Rendering MUST NOT block other requests while a document is being produced.
- **FR-006**: The same input data MUST always produce the same document bytes.
- **FR-007**: Document text MUST be in Spanish, using legacy's wording for labels and legends. Dates and amounts use Mexican formatting: `$1,234.50`, and dates as legacy prints them.

**Sale ticket** — `GET /sales-orders/{id}/ticket`

- **FR-010**: For an order that is not completed, the ticket MUST reproduce legacy's pre-payment ticket (`POS/Print.cshtml`), with the fields and conditions in User Story 1, scenario 1.
- **FR-011**: For a completed order, the ticket MUST reproduce legacy's final receipt (`Payments/Print.cshtml`). Cancelled payment applications are excluded throughout. This includes:
  - the terms and, on credit, the due date;
  - refunds and credit notes applied to the order;
  - payment rows, with the change row when change is above zero;
  - the paid or balance row (see FR-012);
  - the payment-on-delivery block, the "Por cobrar" block, the card legend and signature, the pagaré and signature, the receipt message, and the cancellation stamp. Each appears under the same condition as in legacy.
- **FR-012**: A paid order's receipt MUST show a "Pagado" row. Legacy printed the literal boolean in that row, which is a bug and is not reproduced.
- **FR-013**: An immediate payment taken through a payment option whose "display on ticket" flag is off MUST be labelled with its base payment method name, not the option name. Its amount is unchanged. Other payment types print as "{type} - {id}", as legacy does, so the flag does not apply to them.
- **FR-014**: The pagaré wording MUST come from configuration, defaulting to legacy's text, with placeholders for customer name, balance, due date and issuing taxpayer name.

**Sales order document** — `GET /sales-orders/{id}/document`

- **FR-020**: The document MUST reproduce legacy's `SalesOrders/Print.cshtml` on letter paper, including the amount in words and the savings line. The fields are listed in User Story 3. The amount in words keeps legacy's format but corrects its Spanish grammar defects (for example "UN MIL  DOSCIENTOS" becomes "MIL DOSCIENTOS"), in the same way FR-012 and FR-032 fix legacy bugs.
- **FR-021**: The sales order document and the sale ticket MUST be available for an order in any state, as in legacy.

**Cash session cut** — `GET /cash-sessions/{id}/ticket`

- **FR-030**: The cut MUST reproduce legacy's `_CashCountTicket.cshtml` in the ticket geometry. The header shows only the logo and store name, with no address. The fields are listed in User Story 2.
- **FR-031**: The cut figures MUST follow legacy's structure. Each payment in the session is classified by type and sign, so the figures are correct under every refund convention in the shared database. Legacy's type-only filters are not used, because they mis-sum refunds paid out as negative payments.
  - A **refund** is a credit-note payment, or any payment with a negative amount. It is reported as its absolute amount.
  - A **sale** is any other payment with a positive amount, of any payment type, including the unset type that legacy wrote before 2025. Each is net of the change handed back.
  - Sales by method and refunds by method group these by payment method.
  - Cash sales and cash refunds are the cash-method subsets.
  - Expenses: completed, non-cancelled expense vouchers in the session. All of them count as cash.
  - Cash in drawer = starting cash + cash sales − cash expenses − cash refunds.
  - Counted cash: the sum of the session's counted-cash rows.
  - Difference = |counted − cash in drawer|. It is labelled "Faltante" when cash in drawer exceeds counted cash, otherwise "Sobrante".
- **FR-032**: Legacy labels the cash-sales row "Efectivo Contado", which is misleading. The cut MUST label it "Ventas en Efectivo" (legacy's own resource string for cash sales).
- **FR-033**: The cut MUST be available only for closed sessions. For an open session the response is 409.

**Access and contract**

- **FR-040**: Every print route MUST require authentication. Sale tickets and sales order documents MUST require the same privilege as reading a sales order. Cuts MUST require the same privilege as reading a cash session.
- **FR-041**: Every print route MUST declare its success response in the published API schema as `application/pdf` with a binary string schema, and MUST NOT declare a JSON success response.
- **FR-042**: A missing record MUST return 404 with the same shape as the corresponding read endpoint.

### Key Entities

No new persisted entities. The documents read existing records:

- **Sales order and its lines**: the folio, dates, terms, customer, salesperson, ship-to, currency, lines and derived totals.
- **Payment applications and customer payments**: the method, amount, change, cash session, and the payment option with its "display on ticket" flag.
- **Refunds and credit notes** applied to an order.
- **Facility**: the name, address, issuing taxpayer, logo and receipt message.
- **Cash session**: the drawer, cashier, start and end, starting and counted cash counts, its payments and its expense vouchers.

### Dependencies

These are new runtime dependencies, declared here per constitution principle V:

- An HTML/CSS-to-PDF renderer with CSS paged media support, and its system text-rendering libraries.
- A template engine.
- A Code128 barcode generator that produces vector output.
- Bundled font files: Open Sans, Roboto and Roboto Mono, the fonts legacy uses. Their licenses allow redistribution.
- `anyio`, already installed with the web framework, now imported directly for the render concurrency limit.

New development-only dependency:

- `pypdf`, used only by tests to read page sizes and text from generated PDFs.

The issue names WeasyPrint, Jinja2 and python-barcode. The plan confirms the choice. A QR generator is not needed until the CFDI spec.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a known legacy record of each type, the new document matches the legacy output in page width (and, for the letter document, page size), folio, every line and every total, except the balance of an order with credit notes (see Assumptions). It is checked against the legacy render of the same record.
- **SC-002**: 100% of ticket PDFs are one page, 72 mm wide, with a height within 1 mm of their content's height, whether the ticket has 1 line or 200. 100% of documents measure letter size.
- **SC-003**: With outbound network blocked, 100% of renders succeed and are byte-identical to their network-available counterparts.
- **SC-004**: A single document of up to 50 lines is returned in under 1 second on an otherwise idle instance.
- **SC-005**: While ten documents render concurrently, an unrelated read endpoint's response time rises by no more than 100 ms.
- **SC-006**: Every print route in the published schema declares a binary PDF success response. An automated check fails if one does not.
- **SC-007**: A ticket printed from Chrome on a real 80 mm thermal printer (72 mm printable) through the OS dialog is legible and fits the paper width, with nothing clipped. It prints at actual size, not shrunk to the driver's paper length, and feeds no blank paper beyond the printer's own feed before cutting.

## Assumptions

- Printing happens through the browser print dialog, exactly as in legacy. Silent printing, the cash-drawer kick and ESC/POS output are Phase 2 of issue #230 and out of scope.
- The ticket is 72 mm wide because that is the printable width of the 80 mm thermal rolls used in the field (576 dots at 203 dpi). Legacy uses the same width.
- Access follows the existing read endpoints: a privilege check, with no per-facility restriction on single-record reads. Printing is no stricter or looser than viewing the record.
- The facility whose header is printed is the order's facility for tickets and documents, and the cash drawer's facility for cuts.
- The amount in words covers MXN, USD and EUR, as legacy's `CurrencyToString` does, and falls back to MXN wording for any other currency. It keeps legacy's format, with the grammar corrections in research R10.
- The balance printed on a receipt is the one the API already returns. Unlike legacy's, it does not add credit notes back, so a printout always matches what mbe-ui shows for the same order.
- The legacy `_PrintLayout` auto-print script is browser-only behaviour and is not carried over.
- Where legacy sets page geometry in two places that conflict (jsreport options and CSS `@page`), the jsreport values govern: letter with a 6 mm margin, and the ticket with no margin. Those are what legacy actually printed. The ticket's fixed 297 mm height is the one geometry deliberately not carried over (see Clarifications).
- The deployment host can install the renderer's system libraries. The repository has no container definition today. The plan documents the libraries.
- CFDI invoice PDFs, the `/fiscal-documents` router, QR codes, email delivery, and the other ~40 legacy documents are follow-up specs. They reuse this rendering core.
