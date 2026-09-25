# Research: Document Printing

Sources:
- A throwaway spike run in a scratch venv on 2026-09-24, on macOS with an i7-4870HQ (4 cores / 8 threads) and Python 3.12. Numbers below come from that run.
- The legacy code in `../mbe`, cited by path.
- The current code in this repo.

## R1. Renderer: WeasyPrint 70, pinned exactly

**Decision**: `weasyprint==70.0`, `jinja2`, `python-barcode`, in-process. The WeasyPrint version is pinned exactly, not with `>=`.

**Rationale**:
- Issue #230's analysis holds. None of the legacy templates use JavaScript, and WeasyPrint implements CSS paged media, which is what both geometries need.
- The spike rendered both geometries correctly.
- Pinning matters because output bytes depend on the WeasyPrint version (R4).

**System libraries**:
- WeasyPrint loads pango, pangoft2, harfbuzz, fontconfig and gobject at runtime.
- Debian/Ubuntu: `apt install libpango-1.0-0 libpangoft2-1.0-0 libharfbuzz0b libharfbuzz-subset0 libfontconfig1`.
- macOS: `brew install pango`, which is already present on the dev machine.
- The repo has no Dockerfile and no CI, so the libraries are documented in `README.md` and `quickstart.md`.

**Alternatives rejected**: see issue #230 (Chromium/Playwright, Typst, ReportLab/fpdf2). Nothing in the spike changed that analysis.

## R2. Zero-network rendering: a local-only `URLFetcher`

**Decision**: Subclass `weasyprint.urls.URLFetcher`. WeasyPrint 70 replaced function fetchers with this class, and a plain function no longer works.
- Allowed schemes: `data` and `file`.
- A `file` URL is served only if it resolves inside the bundled assets directory or inside `settings.images_dir`.
- Every response sets `Content-Type`. Without it, stylesheets are rejected.
- Any other URL raises `ValueError`, and WeasyPrint logs it as a failed load.

Templates never contain `http(s)` URLs. The facility logo is passed as an absolute `file://` URL built from `settings.images_dir`, and only when the file exists (spec edge case: logo missing).

**Evidence**: `socket.connect` and `getaddrinfo` were patched to record calls. An `<img src="http://example.com/x.png">` went through the fetcher and was blocked, with zero socket or DNS activity. The SVG DOCTYPE did not trigger any fetch either.

**Test**: The same socket patch runs as a pytest fixture that fails on any call. Each document is rendered under it (SC-003).

## R3. Off the event loop: a single-slot thread limiter

**Decision**: `await anyio.to_thread.run_sync(_render, ..., limiter=_RENDER_LIMITER)` with `_RENDER_LIMITER = anyio.CapacityLimiter(1)`. `run_in_threadpool`, the call the issue named, is this same function using anyio's shared 40-thread limiter. Passing a dedicated limiter is the only difference.

**Evidence**: ten concurrent `/pdf` loops against `/ping` (idle p95 ≈ 4 ms):

| Mode | /ping p95 | tickets/s | /pdf p50 |
|---|---|---|---|
| shared limiter (plain `run_in_threadpool`) | 25 ms | 5.7 | 1743 ms |
| `CapacityLimiter(1)` | 19 ms | **7.8** | **1263 ms** |
| `CapacityLimiter(4)` | 17 ms | 5.1 | 1532 ms |
| `ProcessPoolExecutor(4)` | 5 ms | 18.9 | 444 ms |

**Rationale**:
- Rendering holds the GIL, so extra threads add no throughput. They only make every render finish late together.
- One slot gave the best throughput and latency, and is well within SC-005 (+100 ms budget).
- One slot also means only one render ever uses the shared `FontConfiguration` at a time, which removes the question of whether pango is thread-safe.

**Alternatives rejected**:
- A process pool scales about 2.4×, but costs spawn time, 70–140 MB RSS per worker and a warm-up path.
- Nothing needs more than about 8 tickets/s per instance today.
- It stays the documented upgrade path if load grows. It would be a change to one function.

## R4. Deterministic bytes

**Decision**:
- Use `write_pdf()` defaults, with no `pdf_identifier`.
- Emit no `dcterms.created` or `dcterms.modified` meta tags.
- Pin WeasyPrint (R1).

**Evidence**:
- Identical SHA-256 across renders in one process, and across separate processes with different `PYTHONHASHSEED`.
- WeasyPrint 70 writes no creation date unless the HTML asks for one.
- Font subset prefixes are content hashes.
- `/Info` holds only `/Producer`.

**Caveat**: Bytes differ across platforms, because pango and harfbuzz versions differ. Tests compare two renders on the same machine and never check a golden file into the repo.

## R5. Fonts: bundled static TTFs, one shared `FontConfiguration`

**Decision**:
- Bundle Open Sans 400/700, Roboto 700 and Roboto Mono 400 as static TTFs. Legacy uses Open Sans 400, Roboto 700 and Roboto Mono 400 (`ticket.css:1-18`). Open Sans 700 is added because the templates use `<b>` in body text.
- All three families are SIL OFL 1.1, verified in name table ID 13, so `OFL.txt` ships next to them. That is about 575 KB total.
- Sources: googlefonts/opensans, googlefonts/RobotoMono and the roboto-3-classic release. google/fonts now ships only variable fonts.
- One module-level `FontConfiguration` is shared by all renders.
- `font-family` is set on `html` and on `@page`. Page margin boxes do not inherit it, and the spike's page counter fell back to Times.

**Evidence**: The shared `FontConfiguration` cut the warm median from 255 ms to 116 ms for a 20-line ticket, and from 374 ms to 229 ms for a 30-line letter document. Per-render setup was re-reading fontconfig and rewriting every font file to a temp directory.

## R6. Barcodes: Code128 SVG as a `data:` URI

**Decision**:
- `python-barcode`'s `code128` with `SVGWriter`, written to a `BytesIO`.
- Options: `write_text=False` (the template prints the id as text, like legacy), `quiet_zone` 2.0, `compress=False`.
- Embedded as `<img src="data:image/svg+xml;base64,…">`.

**Payload**: the id as legacy displays it.
- A sales order id is zero-padded to 8 digits (`SalesOrder.cs:44-55`, `{0:D8}`).
- A cash session id is padded to 6 digits (`CashCountReport.cs:41`).
- Legacy encodes Code128 subset B (`Code128Content.cs:10,99`). python-barcode chooses its own subset, and the scanned value is the same string either way.

**Rationale**: An inline `<svg>` also renders, but WeasyPrint logs an "Ignored `fill:black`" warning per bar. The `data:` URI is silent, can be sized with CSS, and passes the fetcher as `data`. Output is deterministic.

## R7. Stylesheets: hand-written, not Bootstrap 3

**Decision**:
- Two small stylesheets, `ticket.css` and `print.css`, ported from legacy's own `ticket.css` / `print.css`.
- Legacy's use of the Bootstrap grid is replaced with plain tables, or with a two-column float for the letter header.
- `@font-face` URLs point at the bundled fonts.

**Evidence**: Bootstrap 3 under WeasyPrint dropped every `@media (min-width…)` rule (190 warnings), laid out `col-xs` floats wrongly and lost the striped rows. Parsing its 121 KB of CSS pushed one render to 742 ms.

## R8. Page geometry

**Decision** (width confirmed with the user 2026-09-25: 80 mm rolls, 72 mm printable; fitted height chosen in spec Clarifications):
- **Ticket**: a single page, 72 mm wide with zero margin, exactly as tall as its content.
  - The ticket layout lays out on a tall probe page, `@page { size: 72mm 5000mm; margin: 0 }`.
  - The layout ends `<body>` with an empty `<div id="ticket-end">`.
  - After `HTML(...).render(...)`, set `page.height = page.anchors['ticket-end'][1]`, then call `document.write_pdf()`.
  - `render_pdf` does this only for templates that extend the ticket layout. It keys on the presence of the `ticket-end` anchor, so no flag is needed.
- **Letter**: `@page { size: Letter; margin: 6mm }`. Letter pages paginate normally.

**Evidence** (spike, WeasyPrint 70):

| Lines | Page | Fitted render | Fixed 297 mm render |
|---|---|---|---|
| 1 | 72 × 44.0 mm | 41 ms | 41 ms |
| 20 | 72 × 206.4 mm | 113 ms | 116 ms |
| 200 | 72 × 1745.0 mm, one page | 873 ms | 906 ms (7 pages) |

- `Page.height` and `Page.anchors` are public attributes. Anchors are `(x0, y0, x1, y1)` in CSS px. The marker's y matched the laid-out `<html>` box height exactly at 1, 20 and 200 lines, so no private attribute is read.
- Resizing the page after layout is byte-identical to a true second layout at that size, and costs nothing extra. It works because the content starts at the top and nothing is anchored to the bottom of the page.
- The output is deterministic, the same as R4.
- The 5000 mm probe stays under the PDF viewer limit of 14,400 pt (5080 mm). At 72 mm width, the 200-line ticket used 1745 mm. Content longer than the probe page is not a realistic ticket and is not handled (constitution I).
- Legacy's jsreport options, which set the paper, are 6 mm for letter and 0 for tickets (`CustomController.cs:80-127`). Its CSS `@page` rules (10 mm / 0) were overridden by them. Legacy's fixed 297 mm ticket height is deliberately not carried over.

**Why fitted**: A fixed 297 mm page feeds about 20 cm of blank paper after a short ticket, unless the driver trims it. It also splits long tickets into pages, and drivers configured to cut per page then cut mid-ticket. A thermal printer feeds one continuous strip, and one page sized to the content matches that exactly.

**Printer-side risk**: The browser must print the page at actual size, not scaled to the driver's paper length. T037 checks this on the real printer.

## R9. Formats and wording (legacy culture `es-MX`, `Web.config:121`)

| Value | Format | Legacy source |
|---|---|---|
| Money | `$1,234.50` | `{0:C}` in es-MX |
| Date, due date | `2026-09-24` | `Resources.DateFormatString` |
| Date-time (cut start/end, cancellation stamp) | `2026-09-24 15:03:00` | `DateTimeFormatString` |
| Promise date | `jueves, septiembre 24, 2026` | `ToString("dddd, MMMM dd, yyyy")` in es-MX |
| Quantity | up to 4 decimals, trailing zeros trimmed (`0.####`) | `SalesOrderDetail.cs:56` |
| Discount rate | `10.00 %` | `{0:p}` in es-MX |
| Folio, order id, payment id, refund id | 8-digit zero-padded | `{0:D8}` |
| Cash session id | 6-digit zero-padded | `{0:000000}` |

- Spanish day and month names come from a fixed table in the formatting module, not from the process locale, so output does not depend on the host.
- Labels come from `mbe/Resources/Resources.resx`, for example Folio, Fecha, Vendedor, Cliente, IVA, Saldo, Cambio, Ticket de Venta, Corte de Caja, Faltante and Sobrante. The full table is in `data-model.md`.
- The cancellation stamp prints the raw `DateTime.ToString()` in legacy, which depends on the runtime. It uses the date-time format above instead.

**Payment method names**: mbe-api has no Spanish names for payment methods. `PaymentMethod` is an IntEnum of SAT codes, and the only name field is the free-text `PaymentMethodOption.name`. A module-level dict maps each `PaymentMethod` code to legacy's `[Display]` name (`Model/Constants/PaymentMethod.cs`), for example 1 → "Efectivo", 4 → "T. de Crédito" and 28 → "T. de Débito". The same applies to `PaymentTerms` (Contado / Crédito) and `PaymentType` (Contado, Pago de Crédito, Pago Anticipado, Nota de Crédito).

**display_on_ticket (spec clarification)**: For a payment with a payment option, the row label is the option's `name` when `display_on_ticket` is true, and the method's Spanish name when it is false. A payment with no option prints the method name, as legacy does. Non-immediate payments print "`{PaymentType} - {id:D8}`", as legacy does (`Payments/Print.cshtml:106-121`).

## R10. Amount in words

**Decision**: Port legacy's `CurrencyConverter` (`mbe/Web/Utils/CurrencyConverter.cs`) and keep its output format:
- MXN: `{WORDS} PESOS {cc}/100 M. N.`
- USD: `{WORDS} DÓLARES AMERICANOS {cc}/100 USD`
- EUR: `{WORDS} EUROS {cc}/100 EUR`
- Any other currency falls back to MXN.
- Rounding is half away from zero, to cents.
- Correct Spanish throughout, rather than legacy's literal output:
  - The currency noun is singular whenever the whole part is 1 (`UN PESO 50/100 M. N.`).
  - "uno" shortens to "un" or "ún" before a noun or "mil" (`VEINTIÚN PESOS`, `CIENTO UN MIL`).
  - An exact million or millions takes "DE" before the currency (`UN MILLÓN DE PESOS`, `DOS MILLONES DE PESOS`).

Legacy's grammar defects are fixed rather than reproduced, in line with the spec's treatment of the `IsPaid` bug. The user chose full correction on 2026-09-25. The defects include:

| Legacy output | This feature |
|---|---|
| `UN MIL  DOSCIENTOS…` (says "un mil", with a double space) | `MIL DOSCIENTOS…` |
| `UN MILLÓNCIEN MIL` for 1,100,000 (no space before "cien") | `UN MILLÓN CIEN MIL` |
| `UN MIL MILLÓN` for 1,000,000,000 | `MIL MILLONES` |
| `DOLÁRES` (misspelled) | `DÓLARES` |
| `VEINTIUNO MIL`, `CIENTO UNO MIL` | `VEINTIÚN MIL`, `CIENTO UN MIL` |
| `UNO PESOS 50/100` for 1.50, `CIENTO UNO PESOS` for 101 | `UN PESO 50/100`, `CIENTO UN PESOS` |
| `DOS MILLONES PESOS`, `UN MILLÓN PESOS` | `DOS MILLONES DE PESOS`, `UN MILLÓN DE PESOS` |

The format (currency names, `cc/100`, `M. N.`, `USD`, `EUR`, rounding) is unchanged. T025 pins the full rules.

**Rationale**: SC-001's fidelity check covers geometry, folio, lines and totals, not the prose of the words. Reproducing grammar bugs on a customer-facing document has no value. The user chose full grammatical correction over reproducing legacy's output.

## R11. Figures reused versus computed

**Reused unchanged**: `sales_order_service.attach_derived` supplies the lines, per-line amounts, subtotal, tax total, total, balance and status. The documents show exactly what `GET /sales-orders/{id}` shows.

**Where the balance differs from legacy**:
- Legacy adds credit notes back (`SalesOrder.cs:247-253`), and mbe-api does not.
- Using the API's balance keeps the printed receipt consistent with every screen in mbe-ui.
- The difference is recorded here and not "fixed" in this feature.

**Discount**: No discount total exists today. The documents compute it as the sum over lines of `quantity × price × discount_rate`, in the line's price basis, using `totals.line_amounts`' existing discount step. It is shown only when non-zero.

**Refunds and credit notes on the receipt**:
- `customer_refund_service` already filters by order.
- `CreditNote` has a `sales_order` column, but no service filters by it.
- The context builder queries directly: completed, non-cancelled `CustomerRefund` rows where `sales_order == id`, and `CreditNote` rows where `sales_order == id`. `CreditNote` has no status columns. That is two small `select`s, not a new service method on a service that does not need one.
- **Payment applications**: `list_order_applications` deliberately returns cancelled applications. The receipt and the cut use only non-cancelled ones.

**Cash cut**: computed by a new function, `cash_session_service.cut_figures(db, session_id)`. It ports `CashCountReport.cs`:
- **Refunds** are payments with `payment_type = CREDIT_NOTE` or `amount < 0`, reported as `abs(amount)` and grouped by `method`.
- **Sales** are every other payment with `amount > 0`, of any type including 0 (N/A), grouped by `method`, each net of the summed `SalesOrderPayment.amount_change`.
- **Why not legacy's filters**: they select by payment type only. That fails on three of the four refund and tender conventions found in `mbe_dev`, including mbe-api's own negative cash payouts. See data-model.md › CashCutFigures.
- Expense vouchers in the session with `completed` and not `cancelled`. Each total is the sum of its details' `amount`, and all count as cash.
- Starting cash is the existing `opening_amount`. Counted cash is the sum of `denomination × quantity` over COUNTED_CASH rows.
- `cash_in_drawer = starting + cash_sales − cash_expenses − cash_refunds`.
- The difference is the absolute value of counted minus in-drawer, labelled "Faltante" if in-drawer exceeds counted, else "Sobrante".

**Label deviation**: Legacy's cash-sales row is labelled "Efectivo Contado". This feature labels it "Ventas en Efectivo" (spec FR-032).

## R12. Access, errors, OpenAPI

- **Privileges**:
  - Tickets and documents use `_READ` in `sales_orders.py`, which is `SystemObject.SALES_ORDERS` + READ.
  - The cut uses `_READ` in `cash_sessions.py`, which is `SystemObject.POS` + READ.
  - Both are the same dependencies the `GET /{id}` routes already use.
- **404s**: raised with the existing `_order_or_404`-style helpers and the same detail strings. An open session returns 409 with "Cash session is not closed".
- **OpenAPI**:
  - One shared constant, `PDF_RESPONSE = {200: {'content': {'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}}}`.
  - Every route sets `response_class=Response` and `responses=PDF_RESPONSE`.
  - Setting `response_class=Response` stops FastAPI adding its default `application/json` 200 content.
  - A unit test walks `app.openapi()` and asserts that every route returning `application/pdf` declares exactly this (SC-006).
- **Response headers**: `Content-Disposition: inline; filename="<doc>-<id:D8>.pdf"` so browsers show the PDF inline with a sensible name. Examples: `ticket-00001234.pdf`, `pedido-00001234.pdf`, `corte-000123.pdf`.

## R13. Pagaré wording

**Decision**: A new setting, `promissory_note_template: str`, whose default is legacy's `Web.config:14` text with Python `str.format` fields `{customer}`, `{balance}`, `{due_date}` and `{issuer}`. The template fills them with already formatted strings.
- The issuer is the name of the facility's `TaxpayerIssuer`.
- If the facility has no taxpayer, the field is left blank.
