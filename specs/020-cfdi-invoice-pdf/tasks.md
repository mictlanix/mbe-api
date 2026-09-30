# Tasks: CFDI Invoice PDF

**Input**: [plan.md](./plan.md), [cfdi-invoice-pdf.spec.md](./cfdi-invoice-pdf.spec.md), [research.md](./research.md), [data-model.md](./data-model.md), [contracts/fiscal-documents.md](./contracts/fiscal-documents.md), [quickstart.md](./quickstart.md)

> **Scale note**: 30 tasks over about 15 files, across the CFDI XML module, the router with its service and schemas, one template on spec 019's core, and the tests with fixtures.
> - Every printed value must come from the parsed XML (research R1).
> - The story phases share the router and the print files, so run them one at a time. Each edit only adds to a shared file.

**Tests**: required by the constitution. In every phase, the tests come first and must fail before the implementation. Route tests live in the same task as their routes (T018, T019).

## Format: `- [ ] **T###** [P?] [US#] Description · path`

- `[P]`: independent of the other tasks in its wave (different file, no incomplete dependency).
- `[US#]`: the user story from the spec.

## Phase 1: Setup

**Wave 1: independent (different files):**

- [x] **T001** [P] Add `segno` to the runtime dependencies with `uv add segno`, then check that `uv run python -c "import segno"` works · `pyproject.toml`, `uv.lock`
- [x] **T002** [P] Create the XML fixtures, each a copy of a stamped 4.0 document from `mbe_dev`:
  - `invoice.xml`: type 0, with a discount and at least 2 concepts;
  - `invoice_retention.xml`: one of the 21 documents with `Retenciones`;
  - `credit_note.xml`: type 100, `TipoRelacion` 01;
  - `advance.xml`: type 101, `TipoRelacion` 07;
  - `payment.xml`: type 200, Pagos 2.0 with `NumOperacion`.

  Anonymize each consistently. Replace every name, RFC, postal code and UUID with synthetic values, and keep all structure, codes and amounts. Seals stay as they are, since nothing verifies them. Give `payment.xml`'s recipient an RFC containing `&`, and `invoice.xml`'s recipient one containing `Ñ`. Add a `README.md` stating their origin and the anonymization rule · `tests/fixtures/cfdi/`

## Phase 2: Foundational (blocks every story)

Files: `app/services/cfdi.py`, `app/rendering/formatting.py`, `app/services/fiscal_document_service.py`, `app/schemas/fiscal.py`, `tests/integration/seed.py`, `tests/unit/test_cfdi.py`, `tests/unit/test_rendering.py`, `tests/unit/test_fiscal_document_service.py`, `tests/integration/test_fiscal_document_queries.py`

### Tests (write first, confirm failing)

**Wave 1: independent (different files):**

- [x] **T003** [P] Write `tests/unit/test_cfdi.py`:
  - `parse` of every fixture returns the root, issuer, recipient, concepts, tax totals, relations, payments and TFD attributes verbatim.
  - `CfdiError` for malformed text, for XML missing `Comprobante`, and for XML missing `TimbreFiscalDigital`.
  - `tfd_original_string` equals `||1.1|UUID|FechaTimbrado|RfcProvCertif|SelloCFD|NoCertificadoSAT||` for the invoice fixture. A copy with `Leyenda` gets `Leyenda` in its place, and whitespace is normalized.
  - `sat_qr_payload`:
    - the URL shape from R3;
    - `tt` is the XML's `Total` string (`0` for `payment.xml`);
    - `fe` is the last 8 characters of `Sello`;
    - `&` in an RFC becomes `%26`, and `Ñ` stays raw.
  - `batch_settings`:
    - the real single-quoted template gives `logo_name='casamaestra.png'`, 15 mm and no accounts;
    - a template with `ExtraInfo` accounts;
    - `None`, malformed text, a negative `FooterHeight` and a non-list `ExtraInfo` each give the defaults.
- [x] **T004** [P] Add to `tests/unit/test_rendering.py`:
  - `qr_data_uri(payload)` equals a `data:image/svg+xml` URI of `segno.make(payload, error='m')` saved as SVG with `border=0`;
  - `qty4` formats `3.0000` as `3` and `24.0530` as `24.053`;
  - `price4` formats `474.137931` as `474.1379` and `1422.41` as `1,422.41`;
  - `fiscal_type_title` covers 0, 100, 101 and 200, and falls back for an unknown code;
  - `relation_label` covers 01, 04, 07, and an unknown code alone;
  - `payment_method_label` gives `PUE : Pago en una sola exhibición` and `PPD : Pago en parcialidades o diferido`;
  - `payment_form_label('03')` gives `03 : Transferencia Electrónica`.
- [x] **T005** [P] Write `tests/unit/test_fiscal_document_service.py`:
  - `document_status` from `completed`/`cancelled`: draft, issued, cancelled;
  - `classify_search`: `GR4776` and `GR 4776` give batch and serial, `4776` gives serial, a UUID gives `stamp_uuid`, and `TOMAS` gives text;
  - `xml_total` reads the root's `Total` (CFDI 3.3/4.0), falls back to lowercase `total` (CFD 2.0/2.2, CFDI 3.2), and gives `None` for `None` or unparseable text.
- [x] **T006** [P] Write `tests/integration/test_fiscal_document_queries.py` against the SQLite seed, using `seed_fiscal_document` from T011:
  - `list_documents`: newest first; each search kind; the `issuer`, `type`, `status` and `date_from`/`date_to` filters; `total` taken from the XML, including a 3.2-style document with lowercase `total`, and `None` without XML; `skip`/`limit` and `total` count;
  - `get_document`: returns the document with its lines in order, or `None`;
  - `get_xml`: returns the stored text, or `None` when there is no row.

**⟶ Wait for Wave 1 to finish, then:**

### Implementation

**Wave 2: independent (different files):**

- [x] **T007** [P] Create `app/services/cfdi.py`, following data-model.md and research R2, R3, R5 and R6:
  - `parse(data) -> Cfdi`: ElementTree with local-name matching, and `CfdiError` on failure;
  - `tfd_original_string`;
  - `sat_qr_payload`;
  - `batch_settings(template) -> BatchSettings`, using `ast.literal_eval`.

  T003 passes · `app/services/cfdi.py`
- [x] **T008** [P] Add to `app/rendering/formatting.py`: `qr_data_uri`, `qty4`, `price4`, `fiscal_type_title`, `relation_label`, `payment_method_label` and `payment_form_label` (R8). Register the ones templates use as filters in `app/rendering/__init__.py`'s environment. T004 passes · `app/rendering/formatting.py`, `app/rendering/__init__.py`
- [x] **T009** [P] Create `app/services/fiscal_document_service.py` (R9, R10):
  - `document_status`, `classify_search` and `xml_total`;
  - `get_document(db, id)`, with its lines ordered by `fiscal_document_detail_id` and attached as `document.lines`;
  - `get_xml(db, id) -> str | None`;
  - `list_documents(db, *, search, issuer, type, status, date_from, date_to, skip, limit)`, using the `both()` count pattern from `sales_order_service`. It sets `status` and `total` on each row.

  T005 passes · `app/services/fiscal_document_service.py`
- [x] **T010** [P] Add `FiscalDocumentSummary`, `FiscalDocumentLine` and `FiscalDocumentResponse` to `app/schemas/fiscal.py`, per data-model.md, with `from_attributes=True` and `LocalDateTime` for every datetime. `tests/unit/test_datetime_contract.py` still passes · `app/schemas/fiscal.py`
- [x] **T011** [P] Add `seed_fiscal_document(db, fixture, **overrides)` to `tests/integration/seed.py`. It creates the `fiscal_document` from the fixture's values (issuer, recipient, batch, serial, type, version 4.0, completed, stamp fields), its detail rows (one per concept, with an optional comment), its `fiscal_document_xml`, and the `taxpayer_batch` with the real single-quoted template. It also seeds the `sat_cfdi_usage` and `sat_tax_regime` rows the fixtures use. Once T009 is in, T006 passes · `tests/integration/seed.py`

**Checkpoint**: parsing, formats, queries and schemas are done and tested. No route exists yet.

## Phase 3: User Story 1 - Print a stamped invoice (Priority: P1) 🎯 MVP

**Goal**: `GET /fiscal-documents/{id}/pdf` returns the letter PDF of a stamped 4.0 invoice, with the SAT QR code, the stamp block, the footer on every page, and the cancellation mark when cancelled.

**Independent Test**: seed `invoice.xml`, fetch its PDF, and check the extracted text for every section's values. Decode nothing: the QR code is checked through its payload (T003) and its SVG (T004).

Files: `app/services/print_contexts.py`, `app/rendering/templates/fiscal_document.html`, `app/rendering/static/print.css`, `app/api/v1/endpoints/fiscal_documents.py` (created here), `app/api/v1/router.py`, `tests/unit/test_print_contexts.py`, `tests/api/test_fiscal_documents.py` (created here), `tests/api/test_print_endpoints.py`, `tests/integration/test_print_render.py`

### Tests (write first, confirm failing)

**Wave 1: independent (different files):**

- [x] **T012** [P] [US1] Add `fiscal_document_context` tests to `tests/unit/test_print_contexts.py`, with `SimpleNamespace` rows and a mocked `db`:
  - the template is `fiscal_document.html`, and the title comes from `fiscal_type_title(type)`;
  - issuer, recipient and every total come from the parsed XML, and a stale database value is never printed;
  - the recipient regime description comes from `sat_tax_regime`, the issuer's from `issuer_regime_name`, and the use description from `sat_cfdi_usage`;
  - comments are paired by position, and dropped when the counts differ;
  - `Descuento` only when present, `IVA Retenido` only when `TotalImpuestosRetenidos` is present;
  - `Moneda`, with `Tipo de Cambio` only when not MXN;
  - `amount_in_words` from `Total` in the XML's currency. A USD copy of `invoice.xml` gives `… DÓLARES AMERICANOS …/100 USD` and shows `Tipo de Cambio`;
  - the logo is a `file://` URL only when `images_dir/<logo_name>` exists;
  - footer accounts and height come from `batch_settings`, with defaults when no batch row exists;
  - `cancelled` is `None`, `{'date': '2026-09-29'}`, or `{'date': None}` when no date is recorded;
  - the stamp block values, and `qr_data_uri` built from `sat_qr_payload`.
- [x] **T013** [P] [US1] Add `/api/v1/fiscal-documents/{id}/pdf` to `_PDF_ROUTES` in `tests/api/test_print_endpoints.py` · `tests/api/test_print_endpoints.py`
- [x] **T014** [P] [US1] Add to `tests/integration/test_print_render.py`, using `seed_fiscal_document`:
  - `invoice.xml` renders letter pages, 612 × 792 pt. Its pypdf text contains the folio `GR 004776`, UUID, both certificate numbers, every concept's description and code, `Subtotal`/`Descuento`/`IVA`/`Total`, the amount in words, `Fecha de Certificación`, the original string, both seals, `Este documento es una representación impresa de un CFDI.` and `Página 1 de 1`;
  - `invoice_retention.xml` shows `IVA Retenido`;
  - a copy of `invoice.xml` with 80 concepts spans at least 2 pages, and every page has the legend and `Página N de M`;
  - a cancelled copy has `CANCELADO` and the date on every page;
  - add the route to the zero-network and determinism URL list.

**⟶ Wait for Wave 1 to finish, then:**

### Implementation

- [x] **T015** [US1] Add `fiscal_document_context(db, document, xml) -> tuple[str, dict]` to `app/services/print_contexts.py`, per data-model.md › Render context and research R1, R5 and R8, for the non-payment sections. Map the XML's `Moneda` to `CurrencyCode` for `amount_in_words` (`MXN`/`USD`/`EUR`, anything else MXN, per data-model.md). Leave `related` and `payments` as empty lists. T012 passes · `app/services/print_contexts.py`

**⟶ Wait for T015, then Wave 3: independent (different files):**

- [x] **T016** [P] [US1] Create `app/rendering/templates/fiscal_document.html`, extending `_print_layout.html`, with the header, Receptor, Emisor with the QR code, concepts, payment and totals, amount in words, and stamp block (contract §PDF content 1–4, 6, 8). Add the running footer (9) and the fixed cancellation mark (10). Leave `{% if related %}` and `{% if payments %}` slots empty for US3 and US4 · `app/rendering/templates/fiscal_document.html`
- [x] **T017** [P] [US1] Add the CFDI rules to `app/rendering/static/print.css` (R4):
  - `@page` bottom margin from a CSS variable set by the template from `footer.height_mm`;
  - `@bottom-center { content: element(cfdi-footer) }`;
  - the page counter text;
  - the 29 mm QR code;
  - `overflow-wrap: anywhere` for seals;
  - the `position: fixed` cancellation mark.

  Existing rules stay unchanged · `app/rendering/static/print.css`

**⟶ Wait for Wave 3 to finish, then:**

- [x] **T018** [US1] Write the PDF route tests first and confirm they fail, then build the route. Tests and route land in this one task (constitution › Testing).

  Create `tests/api/test_fiscal_documents.py`, following `tests/api/test_print_endpoints.py` (`_auth`, `_deny_privileges`, patched service, context builder and `render_pdf`):
  - 200 `application/pdf`, with `content-disposition: inline; filename="CAG190523ES5-GR004776.pdf"`, the body, and `render_pdf` awaited with the context;
  - 404 `Fiscal document not found`;
  - 401, and 403 without `FISCAL_DOCUMENTS` READ;
  - each 409 in contract order: never issued, version 3.3, 4.0 with no UUID, no XML row, and unparseable XML.

  Then create `app/api/v1/endpoints/fiscal_documents.py`:
  - `_READ = require_privilege(SystemObject.FISCAL_DOCUMENTS, AccessRight.READ)` and `_document_or_404`;
  - `GET /{fiscal_document_id}/pdf` with `response_class=Response` and `responses=rendering.PDF_RESPONSE`;
  - the 409 checks in contract order;
  - the file name `{issuer}-{batch}{serial:06d}.pdf`.

  Register the router in `app/api/v1/router.py` at `/fiscal-documents`, tag `fiscal-documents`. The new tests, T013 and T014 pass · `tests/api/test_fiscal_documents.py`, `app/api/v1/endpoints/fiscal_documents.py`, `app/api/v1/router.py`

**Checkpoint**: a stamped 4.0 invoice prints with every section, the QR code, the footer and the cancellation mark. mbe-ui can print an invoice whose id it already has.

## Phase 4: User Story 2 - Find a fiscal document and download its XML (Priority: P1)

**Goal**: list, detail and XML download.

**Independent Test**: list with a search, open one, and download its XML. The bytes equal the stored text.

Files: `app/api/v1/endpoints/fiscal_documents.py` (adds routes), `tests/api/test_fiscal_documents.py` (adds tests)

### Tests and implementation

- [x] **T019** [US2] Write these tests first in `tests/api/test_fiscal_documents.py` and confirm they fail. Tests and routes land in this one task (constitution › Testing):
  - the list passes every query parameter through to `list_documents`, returns `ListResponse` shaped items, and gives 422 for `status=bogus`;
  - the detail gives 200 with its lines, and 404;
  - the XML gives 200 `application/xml`, `attachment; filename="{issuer}-{batch}{serial:06d}.xml"`, a body equal to the stored text encoded as UTF-8, a 409 for never issued, and a 409 for no XML row;
  - 401 and 403 on each route;
  - `app.openapi()` gives the XML route's 200 as `application/xml` with a binary schema and no JSON entry.

  Then add `GET ''` (list), `GET /{fiscal_document_id}` and `GET /{fiscal_document_id}/xml` to `app/api/v1/endpoints/fiscal_documents.py`, per the contract, declaring the XML route's binary `responses=`. The new tests pass · `tests/api/test_fiscal_documents.py`, `app/api/v1/endpoints/fiscal_documents.py`

**Checkpoint**: mbe-ui can find any fiscal document, show it, download its XML and print it.

## Phase 5: User Story 3 - Print a credit note or an applied advance (Priority: P2)

**Goal**: the related CFDIs section.

**Independent Test**: render `credit_note.xml` and `advance.xml`. Each shows `CFDI Relacionados`, its relation label and the related UUID.

Files: `app/services/print_contexts.py` (adds `related`), `app/rendering/templates/fiscal_document.html` (fills the related slot), `tests/unit/test_print_contexts.py`, `tests/integration/test_print_render.py`

### Tests (write first, confirm failing)

**Wave 1: independent (different files):**

- [x] **T020** [P] [US3] Add to `tests/unit/test_print_contexts.py`: `related` is `[(relation_label(code), [uuid, …])]` for each `CfdiRelacionados` block. It is empty for an invoice, and never set for a payment receipt.
- [x] **T021** [P] [US3] Add to `tests/integration/test_print_render.py`: `credit_note.xml` shows `Nota de Crédito`, `CFDI Relacionados`, `01 : Nota de Crédito de los Documentos Relacionados` and the related UUID. `advance.xml` shows `Aplicación de Anticipos` and `07 : CFDI por Aplicación de Anticipo`.

**⟶ Wait for Wave 1 to finish, then:**

### Implementation

- [x] **T022** [US3] Fill `related` in `fiscal_document_context` for non-payment documents (R7). T020 passes · `app/services/print_contexts.py`
- [x] **T023** [US3] Fill the `{% if related %}` slot in `fiscal_document.html`: `Tipo de Relación` with the label, and one numbered UUID per related document. T021 passes · `app/rendering/templates/fiscal_document.html`

**Checkpoint**: credit notes and applied advances print with their relations.

## Phase 6: User Story 4 - Print a payment receipt (Priority: P3)

**Goal**: the payments section in place of the concepts.

**Independent Test**: render `payment.xml`. Every settled document and payment figure appears, and the QR code's `tt` is `0`.

Files: `app/services/print_contexts.py` (adds `payments`), `app/rendering/templates/fiscal_document.html` (fills the payments slot), `tests/unit/test_print_contexts.py`, `tests/integration/test_print_render.py`

### Tests (write first, confirm failing)

**Wave 1: independent (different files):**

- [x] **T024** [P] [US4] Add to `tests/unit/test_print_contexts.py` (research R7):
  - a payment receipt has no concept rows and no concept totals;
  - `payments` holds each payment's date, form label, `NumOperacion`, `NomBancoOrdExt`, currency and exchange rate (only when not MXN) and amount, and each settled document's UUID, series and folio, installment, currency (with equivalence when it differs), previous balance, amount paid and outstanding balance;
  - `amount_in_words` comes from the payment amount.
- [x] **T025** [P] [US4] Add to `tests/integration/test_print_render.py`: `payment.xml` shows `Recibo Electrónico de Pago`, `Fecha del Pago`, every settled document's UUID and `Saldo Anterior`/`Importe Pagado`/`Saldo Insoluto` values, `Importe Total` and the amount in words of the payment. It has no `CFDI Relacionados`.

**⟶ Wait for Wave 1 to finish, then:**

### Implementation

- [x] **T026** [US4] Fill `payments` in `fiscal_document_context` for `TipoDeComprobante="P"` and skip the concept rows and totals there. T024 passes · `app/services/print_contexts.py`
- [x] **T027** [US4] Fill the `{% if payments %}` slot in `fiscal_document.html` with the settled-documents table and the payment block (contract §PDF content 5), in place of the concepts. T025 passes · `app/rendering/templates/fiscal_document.html`

**Checkpoint**: every 4.0 type prints.

## Phase 7: Polish

**Wave 1: independent (different files):**

- [x] **T028** [P] Update `CHANGELOG.md` `[Unreleased]` → Added: the four `/fiscal-documents` routes and the `segno` dependency. Note the logo operations step (quickstart) · `CHANGELOG.md`
- [x] **T029** [P] Run `uv run pytest` and `uv run ruff check app/ migrations/ tests/`. Both must pass with no failures. Record the counts in quickstart.md › Results · `specs/020-cfdi-invoice-pdf/quickstart.md`

**⟶ Wait for Wave 1 to finish, then:**

- [x] **T030** Run quickstart's manual checks against `mbe_dev` from xolotl (tmux `mbe-api-dev`, after pulling the branch):
  - SC-001: fidelity against legacy's PDFs, for one document of each type (the user supplies legacy's PDFs);
  - SC-002, SC-003, SC-007: the 200-document sample;
  - SC-005: render the same 200 documents again with outbound network blocked (the zero-network fetcher, as in spec 019), and check the bytes are identical;
  - SC-004: timing;
  - SC-006: phone scans of a normal invoice, the `&` RFC and a `Ñ` RFC.

  SC-006 needs the user's phone; record it as pending until it is done. Record every result, including mismatches, under Results · `specs/020-cfdi-invoice-pdf/quickstart.md`

## Dependencies & Execution Order

```text
Setup (T001–T002)
  └─► Foundational (T003–T011)
        └─► US1 invoice PDF (T012–T018) 🎯 MVP
              ├─► US2 list, detail, XML (T019)
              ├─► US3 related CFDIs (T020–T023)
              └─► US4 payment receipt (T024–T027)
                    └─ all ─► Polish (T028–T030)
```

- **Setup**: T001 and T002 in one wave.
- **Foundational**: Wave 1 (T003–T006, tests) blocks Wave 2 (T007–T011). T006 also needs T011's seed, so it goes green last.
- **US1**: Wave 1 (T012–T014, tests), then T015, then Wave 3 (T016, T017), then T018, which writes the route tests before the route.
- **US2**: T019, one task: tests first, then the routes. It adds routes to US1's router file, so it runs after US1.
- **US3** and **US4**: tests (one wave), then context, then template. Both add to US1's context builder, template and test files, so run them one at a time after US1, in either order.
- **Polish**: T028 and T029 together, then T030.
- **Shared files**, extended additively and in phase order, following spec 019's precedent:
  - `fiscal_documents.py` and `test_fiscal_documents.py`: US1 → US2;
  - `print_contexts.py`, `fiscal_document.html`, `test_print_contexts.py` and `test_print_render.py`: US1 → US3 → US4.

  They are shared because one router serves the resource and one template serves every CFDI type (research R7). Splitting either would break the codebase's one-router-per-resource convention for no gain.
