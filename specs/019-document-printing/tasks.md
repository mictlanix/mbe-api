# Tasks: Document Printing

**Input**: Design documents from `/specs/019-document-printing/`
**Prerequisites**: [plan.md](./plan.md), [spec.md](./spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/print-endpoints.md](./contracts/print-endpoints.md), [quickstart.md](./quickstart.md)

**Tests**: Required. Constitution v1.2.0 (Development Workflow › Testing) makes tests mandatory and test-first for all work, which overrides the template's "tests are optional" text. Write each test task, run it, confirm it fails, then implement.

**Organization**: Grouped by user story. US4 (binary contract) is a single sweep test over every PDF route. It runs right after US1, so later routes are covered the moment they land. US5 (zero-network and concurrency) verifies the core against all three documents, so it comes after them.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US5 from spec.md

---

## Phase 1: Setup

- [X] T001 Add runtime dependencies to `pyproject.toml`: `weasyprint==70.0` (exact pin, research R1/R4), `jinja2>=3.1`, `python-barcode>=0.16`. Add `pypdf>=5` to the `dev` group. Run `uv lock && uv sync`, then check that `uv run python -c "import weasyprint; print(weasyprint.__version__)"` prints `70.0`.
- [X] T002 [P] Add the fonts under `app/rendering/static/fonts/`:
  - `OpenSans-Regular.ttf` and `OpenSans-Bold.ttf` from googlefonts/opensans;
  - `Roboto-Bold.ttf` from the roboto-3-classic release (`web/static`);
  - `RobotoMono-Regular.ttf` from googlefonts/RobotoMono;
  - `OFL.txt` holding the SIL OFL 1.1 text.
  All are static TTFs, not variable fonts (research R5). Check each file's name table ID 13 says OFL.
- [X] T003 [P] Add a "System libraries" section to `README.md`. It lists the brew and apt packages from research R1, and says the API cannot render documents without them.
- [X] T004 [P] Write `tests/unit/test_rendering.py::test_promissory_note_default` first and confirm it fails. It checks that `settings.promissory_note_template.format(customer='Ana', balance='$10.00', due_date='2026-10-01', issuer='Mictlanix')` contains all four values and no `{` or `}`. T005 extends this file. Then add `promissory_note_template: str` to `Settings` in `app/core/config.py`. Its default is legacy `Web.config:14`, with `{customer}`, `{balance}`, `{due_date}` and `{issuer}` in place of `{0}`, `{1:c}`, `{2:d}` and `{3}`. Add a `#` comment in the neighbouring style citing the legacy source (research R13).

---

## Phase 2: Foundational (blocks every story)

- [X] T005 [P] Extend `tests/unit/test_rendering.py` (created in T004), failing first:
  - **Formatters**:
    - `money(Decimal('1234.5'))` → `'$1,234.50'`, negative → `'-$1,234.50'`;
    - `date_short` → `'2026-09-24'`;
    - `date_time` → `'2026-09-24 15:03:00'`;
    - `date_long(date(2026,9,24))` → `'jueves, septiembre 24, 2026'`;
    - `qty(Decimal('2.5000'))` → `'2.5'` and `qty(Decimal('3'))` → `'3'`;
    - `percent(Decimal('0.1'))` → `'10.00 %'`;
    - `pad8(1234)` → `'00001234'` and `pad6(12)` → `'000012'`;
    - `method_name(1)` → `'Efectivo'`, `method_name(28)` → `'T. de Débito'`, `method_name(77)` → `'77'`;
    - `terms_name(0)` → `'Contado'`;
    - `payment_type_name(2)` → `'Pago de Crédito'`.
  - **Fetcher**:
    - `LocalOnlyFetcher` serves a file inside the assets root with its `Content-Type`, and a file inside a temp `images_dir`.
    - It serves a `data:` URI.
    - It raises for `http://`, `https://`, `file://` outside both roots, and a `../` traversal.
  - **Barcode**: `barcode_data_uri('00001234')` starts with `data:image/svg+xml;base64,`, and two calls return equal strings.
- [X] T006 Create `app/rendering/fetcher.py` with `LocalOnlyFetcher(URLFetcher)`, per research R2. `allowed_protocols={'data','file'}`. `file` URLs are served only under the assets root or `settings.images_dir`, resolved, with `Path.is_relative_to`. Everything else raises `ValueError`.
- [X] T007 Create `app/rendering/formatting.py`:
  - the formatters and enum-name tables from research R9 and data-model.md "Spanish names for enums", with Spanish day and month names from a fixed table, not the locale;
  - `barcode_data_uri(value: str) -> str` using python-barcode `code128` + `SVGWriter` into `BytesIO`, with options `write_text=False`, `quiet_zone=2.0`, `compress=False` (research R6).
  Run T005: it passes.
- [X] T008 Write `tests/integration/test_print_render.py::test_layouts_geometry` now, before the stylesheets, layouts and core exist, and confirm it fails (import error). T009–T011 make it pass. Render a minimal child of each layout through `render_pdf`. Supply the children with `monkeypatch.setattr` on the environment's `loader`, set to `ChoiceLoader([DictLoader({'_t.html': '{% extends "_ticket_layout.html" %}…', '_l.html': '{% extends "_print_layout.html" %}…'}), <original loader>])`. Then assert with pypdf:
  - Ticket: exactly one page, MediaBox width 204.09 pt ±0.1.
  - A 1-line ticket is one page shorter than 100 mm (283.5 pt). A 200-line ticket is one page, taller than the 1-line one, and the text of its last line is extracted from that page, so nothing fell outside it.
  - Letter: every page 612 × 792. A letter document long enough to overflow has more than one page, all letter size.
- [X] T009 Create `app/rendering/static/ticket.css` and `app/rendering/static/print.css`, hand-ported from legacy `mbe/Web/Content/ticket.css` / `print.css` without Bootstrap (research R7):
  - `@font-face` rules pointing at `fonts/*.ttf`;
  - the ticket probe page `@page { size: 72mm 5000mm; margin: 0 }` (the final height is set in T011) and `@page { size: Letter; margin: 6mm }`;
  - `font-family` set on `html` and inside `@page`;
  - body 7.5pt / 9pt Open Sans, `.total` in bold Roboto, `.signature`, the logo sizes, and the zebra rows on the letter lines table.
- [X] T010 Create `app/rendering/templates/_ticket_layout.html` and `app/rendering/templates/_print_layout.html`, porting `_TicketLayout.cshtml` / `_PrintLayout.cshtml`:
  - Ticket header: logo if set; store name; unless `skip_address`, the RFC and the address lines; then the title.
  - Letter header: store name, taxpayer if set, address; logo on the right.
  - Blocks: `main`, `footer`.
  - The ticket layout ends `<body>` with an empty `<div id="ticket-end"></div>`, after the footer (research R8).
  - No script. No absolute or `http` URLs; stylesheets are linked relative to `base_url`.
- [X] T011 Create `app/rendering/__init__.py`:
  - a Jinja `Environment` over `templates/` with `autoescape=True`, the formatters registered as filters, and `StrictUndefined`;
  - a module-level `FontConfiguration` and a `LocalOnlyFetcher`;
  - `_RENDER_LIMITER = anyio.CapacityLimiter(1)`;
  - `async def render_pdf(template: str, context: dict) -> bytes`, which renders the HTML and then `await anyio.to_thread.run_sync(_write, html, limiter=_RENDER_LIMITER)`. `_write` calls `HTML(string=…, base_url=<static dir>/, url_fetcher=…).render(font_config=…)`. If the first page has a `ticket-end` anchor, it sets `page.height = page.anchors['ticket-end'][1]`. Then it returns `document.write_pdf()` (research R3–R5, R8);
  - `PDF_RESPONSE` (research R12);
  - `pdf_response(content: bytes, filename: str) -> Response`, which sets `Content-Disposition: inline; filename="…"`;
  - the `weasyprint` logger left at its default level: a failed font, stylesheet or image load must stay visible as a warning (FR-003). With no Bootstrap (R7), a clean render logs nothing.
  Add a docstring stating the zero-network, off-loop and never-persist rules, and citing #230.

**Checkpoint**: `render_pdf` produces both geometries from bundled assets. No route exists yet.

---

## Phase 3: User Story 1 - A cashier prints the sale ticket (Priority: P1) 🎯 MVP

**Goal**: `GET /sales-orders/{id}/ticket` returns the pre-payment ticket for an order that is not completed, and the final receipt for a completed one.

**Independent Test**: seed an order, fetch the ticket, and check it is 72 mm wide with header, lines, totals and barcode. Complete it with a cash payment that has change, fetch again, and check it is now the "Ticket de Venta" with the payment and change rows.

### Tests (write first, confirm failing)

- [X] T012 [P] [US1] Write `tests/api/test_print_endpoints.py`, covering the sale ticket. Follow the pattern in `tests/api/test_cash_sessions.py`: patch `app.services.sales_order_service.get_order`, `app.services.sales_order_service.attach_derived`, `app.services.print_contexts.sale_ticket_context` and `app.rendering.render_pdf` with `AsyncMock`. The last two only take effect because T018 calls them through their modules. Assert:
  - 200 with `content-type: application/pdf`, the body equal to the mocked bytes, and `content-disposition` = `inline; filename="ticket-00000007.pdf"`;
  - 404 `{"detail": "Sales order not found"}` when `get_order` returns `None`;
  - 401 with no auth override;
  - 403 for a non-admin `CurrentUser` without the `SALES_ORDERS` READ privilege. `require_privilege` (`app/core/deps.py`) reads `(await db.execute(...)).scalar_one_or_none()`, so override `get_db` with a session whose `execute` is an `AsyncMock` returning a `MagicMock` with `scalar_one_or_none.return_value = None`.
- [X] T013 [P] [US1] Write the `sale_ticket_context` tests in `tests/unit/test_print_contexts.py`. Build `SimpleNamespace` rows and mock `db.execute`, following `tests/unit/` patterns. Cover:
  - a non-completed order selects `sale_ticket.html`, and a completed one `sale_receipt.html`;
  - the per-order `customer_name` is appended only when `customer == settings.default_customer_id` and it is non-blank;
  - salesperson: `first last` on the pre-payment ticket, `nickname` on the receipt;
  - `display_on_ticket=False` labels a payment with `method_name(method)`, `True` uses the option `name`, and no option uses `method_name`;
  - a non-immediate payment label is `'{payment_type_name} - {pad8(id)}'`;
  - cancelled applications (`SalesOrderPayment.cancelled`) never appear: they add no payment row, no Pagado amount and no Cambio. `list_order_applications` returns them deliberately;
  - `change` is Σ `amount_change` over non-cancelled applications and is shown only when > 0;
  - `discount` is tax-exclusive (the net at rate 0 minus the actual net, via `totals.line_amounts`), and the printed subtotal is `total − tax_total + discount`, so Subtotal − Descuento + IVA = Total. Descuento is shown only when non-zero (research R11);
  - flags:
    - `has_card_payment` for methods 4 and 28;
    - `on_delivery_payments` for payments with `cash_session is None`;
    - `por_cobrar` when completed, immediate terms, not paid and balance > 0;
    - `promissory_note` filled only when on credit terms and not paid;
  - `issuer` is blank when the facility has no taxpayer;
  - `receipt_message` is passed through;
  - `cancelled_at` = `date_time(modification_time)` only when cancelled;
  - a missing logo file gives `logo=None`;
  - `barcode` is `barcode_data_uri(pad8(order id))` (FR-004), checked by patching `barcode_data_uri` and asserting its argument;
  - a draft with no serial gives an empty folio string.
- [X] T014 [P] [US1] Add `test_sale_ticket_renders` to `tests/integration/test_print_render.py`. Use the `seeded` fixture, which asserts the template's baseline rows, plus `seed_sales_order(db, completed=…, paid=…)`. Do not call `seed_baseline` again: its rows are already in the template. Add payments and refunds per case, then call the route through `client`:
  - a draft: pypdf text contains "Punto de Venta", the 8-digit id text, each product name and the total, and no folio digits;
  - completed with a cash payment and change: "Ticket de Venta", "Efectivo" and "Cambio";
  - one render per conditional receipt block, each asserting its text:
    - Descuento;
    - a credit note;
    - Contraentrega (a payment with no cash session);
    - "Por Cobrar";
    - the card legend and "Acepto";
    - the pagaré (credit terms, unpaid);
    - the facility's receipt message;
  - a cancelled 1-line completed order: "Cancelado" is present, and with pypdf's `visitor_text`, every text fragment's y lies inside the MediaBox. That checks the stamp is not clipped on a short ticket.
  Each PDF is one page, 204.09 pt wide.

### Implementation

- [X] T015 [US1] Create `app/services/print_contexts.py`:
  - `header_context(db, facility_id, title, *, skip_address=False)`: loads the Facility, its Address and its TaxpayerIssuer, and builds the logo `file://` URL only if `Path(settings.images_dir, facility.logo)` exists.
  - `sale_ticket_context(db, order) -> tuple[str, dict]`. Its inputs:
    - `order`, already passed through `attach_derived`;
    - the customer, salesperson and applications via `customer_payment_service.list_order_applications`, plus the `CustomerPayment` rows and, through `CustomerPayment.payment_charge`, their `PaymentMethodOption` rows. Keep only applications that are not cancelled (see T013);
    - refunds as a direct `select` of completed, non-cancelled `CustomerRefund` where `sales_order == id`;
    - credit notes as a direct `select` of `CreditNote` where `sales_order == id`.
    It returns the template name and the dict described in data-model.md "Render contexts". Run T013: it passes.
- [X] T016 [P] [US1] Create `app/rendering/templates/sale_ticket.html`, extending `_ticket_layout.html` and porting `mbe/Web/Views/POS/Print.cshtml`:
  - header rows: Folio, Fecha, Vendedor, Cliente, and the due date if on credit;
  - each line as two rows: `qty × price [- discount%] code`, then `name | total`;
  - IVA and Total;
  - if there are payments: the refund label, Pagado and Saldo;
  - on immediate terms, the "Este recibo no es un comprobante de pago." legend;
  - a footer with the barcode `<img>` and the id text.
- [X] T017 [P] [US1] Create `app/rendering/templates/sale_receipt.html`, extending `_ticket_layout.html` and porting `mbe/Web/Views/Payments/Print.cshtml`:
  - the cancellation stamp (rotated −35°, "Cancelado", `cancelled_at`, and the raw id). Legacy places it at `top: 200px`, which lies below a short fitted ticket. Instead, position it absolutely inside a `position: relative` wrapper around the lines, at `top: 0`, so it always overlays the order and never extends past `ticket-end`;
  - the header plus Forma de Pago and the due date;
  - lines, with the comment shown bold italic;
  - Subtotal, Descuento if non-zero, IVA, Total;
  - refund rows, payment rows, the Cambio row and credit-note rows;
  - Pagado with a check mark (FR-012, legacy's `true.png`, bundled as `static/check.svg`) or Saldo;
  - the Contraentrega block;
  - "Por Cobrar : {balance}";
  - the card legend with an "Acepto" signature;
  - the pagaré with a signature;
  - the receipt message;
  - a barcode footer.
- [X] T018 [US1] Add `GET /{sales_order_id}/ticket` to `app/api/v1/endpoints/sales_orders.py`:
  - `response_class=Response`, `responses=PDF_RESPONSE`, and the `_READ` dependency;
  - `_order_or_404` → `sales_order_service.attach_derived` → `print_contexts.sale_ticket_context` → `rendering.render_pdf` → `rendering.pdf_response(…, f'ticket-{id:08d}.pdf')`;
  - import as `from app import rendering` and `from app.services import print_contexts`, and call through the module names. That matches the existing `from app.services import …` style, and T012's patches depend on it.
  Run T012 and T014: they pass.

**Checkpoint**: sale ticket end to end. This is shippable as the MVP.

---

## Phase 4: User Story 4 - Client code receives intact PDF bytes (Priority: P1)

**Goal**: No PDF route can publish a schema that makes the generated client decode bytes as text.

**Independent Test**: fetch `/openapi.json` and inspect each PDF route's 200.

- [X] T019 [US4] Add `test_pdf_routes_declare_binary_schema` to `tests/api/test_print_endpoints.py`. Collect every `APIRoute` whose `responses` contain `application/pdf`, and assert the set is not empty. It must include every path in contracts/print-endpoints.md that exists so far. For each, assert that `app.openapi()['paths'][path]['get']['responses']['200']['content']` equals exactly `{'application/pdf': {'schema': {'type': 'string', 'format': 'binary'}}}`, with no `application/json` key. Hard-code the expected route list from the contract so that a route that forgets `responses=` also fails. Extend the list in T024 and T030 as those routes land.

---

## Phase 5: User Story 2 - A supervisor prints the cash cut (Priority: P1)

**Goal**: `GET /cash-sessions/{id}/ticket` returns the "Corte de Caja" for a closed session, and 409 for an open one.

**Independent Test**: seed a session with starting cash, cash and card payments (one with change), an mbe-api cash refund payout (IMMEDIATE, cash, negative amount), a store-credit refund (CREDIT_NOTE, method N/A, positive), an expense voucher and counted cash. Close it, fetch the cut, and compare every figure with a hand calculation.

### Tests (write first, confirm failing)

- [X] T020 [P] [US2] Write `tests/unit/test_cash_cut.py` for `cash_session_service.cut_figures`, using a mocked `db.execute` in the style of the existing service unit tests. Cover:
  - sales by method net of change: positive, non-CREDIT_NOTE payments of any type, including type 0 (N/A), with `sales_total` summing them;
  - refunds by method, reported as `abs(amount)`: one case per convention in data-model.md › CashCutFigures:
    - a store-credit refund (CREDIT_NOTE, method N/A, positive) is a refund with no cash impact;
    - a legacy negative CREDIT_NOTE cash payment is a cash refund;
    - an mbe-api payout (IMMEDIATE, cash, negative) is a cash refund and never reduces `sales_by_method`;
  - change is subtracted only for non-cancelled applications;
  - expenses: completed, non-cancelled vouchers only, each total summed from its details, all counted as cash;
  - `cash_sales` counts cash-method sales only, and `cash_refunds` cash-method refunds only;
  - `cash_in_drawer = starting + cash_sales − expenses − cash_refunds`;
  - no counted rows → `counted_cash = 0`, `is_shortage=True` with `difference = cash_in_drawer`;
  - counted above in-drawer → `is_shortage=False`;
  - all amounts rounded to cents.
- [X] T021 [P] [US2] Extend `tests/api/test_print_endpoints.py` for the cut, patching `app.services.cash_session_service.get_session`, `app.services.cash_session_service.attach_derived`, `app.services.print_contexts.cash_cut_context` and `app.rendering.render_pdf`. Assert:
  - 200 with `filename="corte-000005.pdf"`;
  - 404 `{"detail": "Cash session not found"}`;
  - 409 `{"detail": "Cash session is not closed"}` when `end is None`, and that `render_pdf` was not awaited;
  - 401;
  - 403 without `POS` READ, using the same `get_db` override as T012.
- [X] T022 [P] [US2] Add `test_cash_cut_renders` to `tests/integration/test_print_render.py`. Seed a closed session with the scenario from the Independent Test and call the route. The pypdf text contains "Corte de Caja", the drawer name, "Ventas en Efectivo", "Efectivo Final", and "Faltante" or "Sobrante" with the expected amount. It must not contain "Efectivo Contado" (FR-032), or the facility's street or RFC (header without address). The PDF is one page, 204.09 pt wide.

### Implementation

- [X] T023 [US2] Add `async def cut_figures(db, session_id) -> CashCutFigures` to `app/services/cash_session_service.py`. `CashCutFigures` is a small `@dataclass` defined in the same module, with the fields and rules in data-model.md. Reuse the existing `opening_amount` query for starting cash. Run T020: it passes.
- [X] T024 [US2] Add to the cash cut:
  - **Context**: `cash_cut_context(db, session)` in `app/services/print_contexts.py`. It uses `header_context(…, skip_address=True)` for the drawer's facility, and builds the drawer name, `date_time` start and end, the cashier `first last`, the formatted `cut_figures` rows, the Faltante/Sobrante label and the barcode of `pad6(id)`.
  - **Template**: `app/rendering/templates/cash_cut.html`, extending `_ticket_layout.html` and porting `mbe/Web/Views/Payments/_CashCountTicket.cshtml`. Sections: Información de la Sesión, Ventas (with total), Devoluciones, Gastos, Movimientos en Caja (Efectivo Inicial, Ventas en Efectivo, Gastos, Devoluciones), and Balance de Caja (Efectivo Final plus the Faltante/Sobrante row). Then the barcode footer.
  - **Route**: `GET /{cash_session_id}/ticket` in `app/api/v1/endpoints/cash_sessions.py`, the same path-parameter name as the existing routes, calling `rendering` and `print_contexts` through their modules as in T018, with `response_class=Response`, `responses=PDF_RESPONSE` and `_READ`. It returns 404 if missing and 409 if `end is None`. Otherwise it attaches derived data, builds the context, renders and returns `pdf_response(…, f'corte-{id:06d}.pdf')`.
  - **Contract test**: add this path to T019's expected list.
  Run T021, T022 and T019: they pass.

**Checkpoint**: sale ticket and cash cut both work, independently.

---

## Phase 6: User Story 3 - A salesperson prints the sales order document (Priority: P2)

**Goal**: `GET /sales-orders/{id}/document` returns the letter-size pedido.

**Independent Test**: fetch the document for an order with a ship-to address, a contact, a discount and several lines, and check the text and the letter geometry.

### Tests (write first, confirm failing)

- [X] T025 [P] [US3] Write `tests/unit/test_amount_in_words.py` for `amount_in_words(amount, currency)`. Cover:
  - legacy's format with correct Spanish, per research R10:
    - `1234.50 MXN` → `'MIL DOSCIENTOS TREINTA Y CUATRO PESOS 50/100 M. N.'`;
    - `1.00` → `'UN PESO 00/100 M. N.'`;
    - `1.50` → `'UN PESO 50/100 M. N.'`;
    - `0.99` → `'CERO PESOS 99/100 M. N.'`;
    - `21` → `'VEINTIÚN PESOS 00/100 M. N.'`;
    - `100` → `'CIEN PESOS 00/100 M. N.'`;
    - `101` → `'CIENTO UN PESOS 00/100 M. N.'`;
    - `21000` → `'VEINTIÚN MIL PESOS…'`;
    - `101000` → `'CIENTO UN MIL PESOS…'`;
    - `1000000` → `'UN MILLÓN DE PESOS…'`;
    - `1100000` → `'UN MILLÓN CIEN MIL PESOS…'`;
    - `2000000` → `'DOS MILLONES DE PESOS…'`;
    - `1000000000` → `'MIL MILLONES DE PESOS…'`;
  - `1234.50 USD` → `'… DÓLARES AMERICANOS 50/100 USD'`;
  - EUR → `'… EUROS 50/100 EUR'`;
  - an unknown currency code falls back to MXN;
  - `0.005` rounds half away from zero to `0.01`;
  - no double spaces anywhere.
- [X] T026 [P] [US3] Extend `tests/unit/test_print_contexts.py` with the `sales_order_context` tests:
  - ship-to lines omit empty parts;
  - the contact appears only when set;
  - the due date appears only when on credit terms;
  - `promise_date` uses `date_long`;
  - savings text `'¡Usted ahorró $X en esta compra!'` appears only when the discount > 0;
  - `amount_in_words` receives the order's currency.
- [X] T027 [P] [US3] Extend `tests/api/test_print_endpoints.py` for `/sales-orders/{id}/document`: 200 with `filename="pedido-00000007.pdf"`, 404, 401 and 403.
- [X] T028 [P] [US3] Add `test_sales_order_document_renders` to `tests/integration/test_print_render.py`. The pypdf text contains "Pedido", the customer name, each line's code and name, the total and the amount in words. Every page is letter size. Repeat for a draft and for a cancelled order: both render (FR-021).

### Implementation

- [X] T029 [US3] Create `app/rendering/words.py` with `amount_in_words(amount: Decimal, currency: int) -> str`, porting `mbe/Web/Utils/CurrencyConverter.cs` with the research R10 corrections. Register it as a Jinja filter in `app/rendering/__init__.py`. Run T025: it passes.
- [X] T030 [US3] Add the sales order document:
  - **Context**: `sales_order_context(db, order)` in `app/services/print_contexts.py`. It uses `header_context` and adds the customer, contact, ship-to Address, salesperson `first last`, dates, terms, the lines with net price and amount, the totals, discount, amount in words and savings text.
  - **Template**: `app/rendering/templates/sales_order.html`, extending `_print_layout.html` and porting `mbe/Web/Views/SalesOrders/Print.cshtml`. The left column holds the customer, contact, ship-to and comment. The right column holds "Pedido {id}", Vendedor, Fecha, Fecha Promesa, Forma de Pago and the due date. Then the lines table (Cantidad, Código, Producto + comment, Precio, Importe) and a `tfoot` with the amount in words spanning the rows, Subtotal, Descuento, IVA and Total, plus the savings line.
  - **Route**: `GET /{sales_order_id}/document` in `app/api/v1/endpoints/sales_orders.py`, set up like T018, with filename `pedido-{id:08d}.pdf`.
  - **Contract test**: add this path to T019's expected list.
  Run T026, T027, T028 and T019: they pass.

**Checkpoint**: all three documents work.

---

## Phase 7: User Story 5 - Printing never degrades or slows the rest of the service (Priority: P2)

**Goal**: Prove FR-003, FR-005 and FR-006 against the real documents.

**Independent Test**: render every document with sockets blocked and compare the bytes. Measure `/health` latency under ten concurrent renders.

- [X] T031 [P] [US5] Add `test_zero_network_and_deterministic` to `tests/integration/test_print_render.py`. Use a fixture that monkeypatches `socket.socket.connect` and `socket.getaddrinfo` to raise `AssertionError('network access during render')`. For each of the three documents (seeded as in T014, T022 and T028), render once without the patch, then once with it. Assert that both succeed, that the byte strings are equal, and that the patched functions were never called. In addition:
  - Assert that each PDF embeds font subsets named for Open Sans and Roboto, read from the page `/Resources` `/Font` `BaseFont` names with pypdf, so a fetcher that wrongly blocked the bundled fonts cannot pass on fallback fonts.
  - With `caplog` at WARNING on the `weasyprint` logger, assert no record reports a failed load.
  - Render once with a real PNG copied into the temp `images_dir` as the facility's `logo`, and assert that the PDF contains an image XObject.
  - Render once with `logo` pointing to a missing file, and assert success with no network call (spec edge case).
  - Assert that no file was created under `settings.images_dir` or in `tempfile.gettempdir()` by the renders (FR-001: nothing persisted).
- [X] T032 [US5] Add `test_renders_do_not_block_event_loop` to `tests/integration/test_print_render.py`. Measure the p95 of 50 sequential `GET /api/v1/health` calls at idle through `client`. Build one sale-ticket context from seeded data first. Then start 10 concurrent `render_pdf('sale_receipt.html', ctx)` calls with `asyncio.gather` in a background task, and measure 50 more health calls while they run. Assert that the p95 rise is under 100 ms (SC-005) and that every render returns PDF bytes. Calling `render_pdf` directly, not the route, keeps 10 requests off the fixture's single shared SQLite connection, so the test measures render off-loading and not fixture contention. Mark it `@pytest.mark.slow` only if the suite already defines that marker; otherwise leave it unmarked.

---

## Phase 8: Polish & Cross-Cutting

- [X] T033 Run `uv run pytest tests/integration/test_no_endpoint_returns_500.py`. The three new routes are picked up automatically. The baseline template has no sales order or cash session at id `1`, so they reach their 404 path. That still proves the route and its lookup run. Confirm that none answers 500. If one does, fix the code, not the test.
- [X] T034 [P] Add an entry to `CHANGELOG.md` under `[Unreleased]`:
  - **Added**: the three PDF routes (#230) and `PROMISSORY_NOTE_TEMPLATE`.
  - An upgrade note: hosts need pango, harfbuzz and fontconfig (README).
  - A note on the deliberate deviations from legacy: amount-in-words fixes, "Ventas en Efectivo", and the API balance.
- [X] T035 [P] Add a commented `PROMISSORY_NOTE_TEMPLATE=` line to `.env.example`, stating that the default is the legacy pagaré text.
- [X] T036 Run `uv run ruff check app/ migrations/ tests/` and `uv run pytest`. Both must pass with zero failures.
- [ ] T037 Run the manual checks in `quickstart.md` against `mbe_dev`: fidelity against legacy output for one record of each type (SC-001), a 409 on an open session, the timing check (SC-004), and the thermal printer (SC-007). Record the results, including any mismatch, in `specs/019-document-printing/quickstart.md` under a "Results" heading. SC-007 needs the physical printer. If it is unavailable, record it as pending rather than passed.

---

## Dependencies & Execution Order

```text
Setup (T001–T004)
  └─► Foundational (T005–T011)
        ├─► US1 sale ticket (T012–T018) ─► US4 contract sweep (T019)
        ├─► US2 cash cut (T020–T024)          (T024 extends T019)
        └─► US3 order document (T025–T030)    (T030 extends T019)
                    └─ all three ─► US5 (T031–T032) ─► Polish (T033–T037)
```

- US1, US2 and US3 depend only on Foundational, and they touch different templates and context functions.
- They share `print_contexts.py`, `test_print_endpoints.py`, `test_print_contexts.py` and `test_print_render.py`. Run in parallel, each edit is additive to those shared files. Merge carefully or run the stories one at a time.
- T019 needs at least one PDF route (T018). T024 and T030 each add their path to it, so T019 must be done before T024 and T030. That is the one cross-story dependency.
- US5 needs all three documents.
- Within a story, tests come first and must fail, then context → template → route.

## Parallel Opportunities

- **Setup**: T002, T003 and T004 run alongside T001.
- **Foundational**: T005 and T008 (both tests) together. Then T006, T007, T009 and T010 together. T011 last, after all four, which turns T008 green.
- **US1**: T012, T013 and T014 together. Then T016 and T017 together, after T015.
- **US2**: T020, T021 and T022 together.
- **US3**: T025, T026, T027 and T028 together.
- **US5**: T031, then T032. They share a file.
- **Polish**: T034 and T035 alongside T033.

## Implementation Strategy

1. **MVP**: Setup, then Foundational, then US1 (sale ticket), then US4 (contract sweep). mbe-ui (#178) can ship ticket printing on this alone.
2. **Next**: US2 (cash cut). It is the other every-shift document.
3. **Then**: US3 (order document).
4. **Then**: US5 (verification of the non-functional guarantees across all three).
5. **Finally**: Polish, including the manual legacy fidelity pass on `mbe_dev`.
