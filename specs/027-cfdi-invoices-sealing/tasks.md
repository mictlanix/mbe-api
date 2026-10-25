---
description: "Task list for CFDI invoices up to a sealed document"
---

# Tasks: CFDI Invoices up to a Sealed Document

**Input**: design documents in `specs/027-cfdi-invoices-sealing/`: plan.md, spec.md, research.md (R1–R13), data-model.md, contracts/fiscal-invoices.md and quickstart.md.

**Tests**: required. Constitution v1.2.0 makes tests mandatory and test-first for every change, overriding the template's "optional" line. Every test task is written before its implementation task and must fail first.

**Organization**: tasks are grouped by user story. The run happens in the worktree `/Users/eddy/Projects/mictlanix/mbe-api-027`; check `git branch --show-current` before each commit. `uv run ruff check app/ migrations/ tests/` must pass before every commit, and no commit carries a Co-Authored-By line.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: can run in parallel (different files, no dependency on an incomplete task)
- **[Story]**: US1–US4 from spec.md

---

## Phase 1: Setup

**Purpose:** the dependency and the vendored SAT files every later phase needs.

- [ ] T001 Add `lxml` to `[project].dependencies` in `pyproject.toml` and run `uv lock`, which updates `uv.lock`. Verify that `uv run python -c "import lxml.etree"` works.
- [ ] T002 [P] Vendor the XSLT set into `app/resources/cfdi40/`.
  - Copy `cadena_original_4_0.xslt` and every file it includes from `/Users/eddy/Projects/mictlanix/cfdlib/src/CFDv40/Resources/`: all `*.xslt` except `cadenaoriginal_4_0.xslt`, the XSLT 2.0 copy, and `cadenaoriginal_TFD_1_1.xslt`.
  - Change the include `divisas.xslt` to `Divisas.xslt` in the vendored main file.
  - Add `app/resources/cfdi40/README.md` stating the source, the commit (`git -C …/cfdlib rev-parse HEAD`) and that one edit (research R3).
- [ ] T003 [P] Vendor SAT's schemas into `tests/fixtures/sat/`.
  - Download `cfdv40.xsd`, `tdCFDI.xsd` and `catCFDI.xsd` from the URLs in research R12, and gzip the catalogue as `catCFDI.xsd.gz`.
  - Rewrite the `xs:import schemaLocation` attributes to the local file names.
  - Add `tests/fixtures/sat/README.md` with the URLs and the download date.
- [ ] T004 Add `tests/cfdi_schema.py` with `assert_valid_cfdi40(xml: str) -> None`. It loads `tests/fixtures/sat/cfdv40.xsd` once with `lxml.etree.XMLSchema` and raises `AssertionError` listing `schema.error_log`. Add a smoke test in `tests/unit/test_cfdi_schema.py`: `tests/fixtures/cfdi/invoice.xml` with its TFD complement stripped validates, and the same XML with `Moneda` removed fails. This depends on T001 and T003.

---

## Phase 2: Foundational (blocks every story)

**Purpose:** the sealed state, the shared schemas, and the pure CFDI builder and sealer. Every confirm in US1, US2 and US4 needs them.

### Sealed state (research R1, FR-016)

- [ ] T005 [P] Extend `tests/unit/test_fiscal_document_service.py` with the `document_status` cases, then extend `tests/integration/test_fiscal_document_queries.py` with the list filter cases. These tests must fail first.
  - **`document_status(document, has_xml)` returns:**
    - draft: not completed, not cancelled, no XML;
    - sealed: not completed, not cancelled, with XML;
    - issued: completed;
    - cancelled: cancelled, whatever the rest.
  - **The list filter:**
    - `status=sealed` returns only sealed documents;
    - `status=draft` excludes them.
- [ ] T006 Implement 'sealed' in `app/services/fiscal_document_service.py`.
  - Add `'sealed'` to `DocumentStatus`, and give `document_status` the XML flag.
  - In `get_document`, pass `xml is not None`.
  - In `list_documents`, pass `document_id in xmls`. Filter `draft` and `sealed` with `exists(select(FiscalDocumentXml.fiscal_document_xml_id).where(... == FiscalDocument.fiscal_document_id))`.
  - Update `status: Literal[...]` in `app/schemas/fiscal.py`.
  - Make T005 pass.

### Schemas (contracts/fiscal-invoices.md)

- [ ] T007 Add the request and response models to `app/schemas/fiscal.py`, following the contract:
  - `BatchTemplate`, `TaxpayerBatchCreate`, `TaxpayerBatchUpdate` and `TaxpayerBatchResponse`, including `in_use`;
  - `InvoiceCreate`, `InvoiceUpdate` (all optional, `model_fields_set` decides what changes), `InvoiceLineUpdate`, `InvoiceSalesOrderAdd`, `InvoiceLineAdd` and `InvoiceRelationAdd` (`type: Literal['04', '07']`);
  - `FiscalDocumentRelationResponse`;
  - `customer`, `facility` and `relations` on `FiscalDocumentResponse`, defaulting to an empty list so spec 020's callers keep working;
  - `product` and `order_detail` on `FiscalDocumentLine`.

  Validation lives in the models where it is static: lengths, `0 ≤ discount ≤ 1`, `tax_rate ∈ {0.16, 0, -1}`, `quantity > 0`, `price ≥ 0`. Extend `tests/unit/test_user_schemas.py`-style coverage in a new `tests/unit/test_fiscal_schemas.py` with one rejected value per rule, written first.

### CFDI builder, original string and seal (research R3, R4, R5)

- [ ] T008 Write `tests/unit/test_cfdi_invoice.py` first; it fails because the module does not exist yet. Use plain dataclass inputs and no database. Build the test CSD with `_make_csd` imported from `tests/unit/test_csd_service.py`.
  - **Line formulas (R5):**
    - the fixture's first line (4 × 90.00, tax included, 16 %) gives `ValorUnitario` 77.586207, `Importe` 310.34, tax 49.66 and `Base` "310.375";
    - a line with a discount;
    - a tax-excluded line;
    - a 0 % line, whose `Base` = subtotal − discount;
    - an exempt line: `TipoFactor="Exento"`, no `TasaOCuota` or `Importe`;
    - a 100 % discount line: `ObjetoImp="01"` and no `Impuestos`.
  - **Formatting:** `Cantidad` keeps 4 decimal places, `TasaOCuota` "0.160000", `Base` "1569" (never `1.569E+3`), and rounding is half away from zero.
  - **Document totals:**
    - transfers are grouped by (tax, factor, rate);
    - `TotalImpuestosTrasladados` is omitted when everything is exempt;
    - `Impuestos` is omitted when no line carries taxes.
  - **Header:**
    - `MetodoPago` is PUE for IMMEDIATE and PPD for NET_D;
    - `FormaPago` uses 2 digits;
    - P01 is written as G03;
    - there is no `TipoCambio`;
    - `CfdiRelacionados` writes `TipoRelacion` "07" with one UUID per relation.
  - **Parity:** rebuilding the data of `tests/fixtures/cfdi/invoice.xml` gives the same original string as the fixture without its `Complemento` (SC-002, in the repo).
  - **Seal:** the sealed XML passes `assert_valid_cfdi40`. Its `Sello` verifies with the certificate's public key over `original_string(xml)` (SC-001). `NoCertificado` and `Certificado` are set.
- [ ] T009 Implement `app/services/cfdi_invoice.py` and make T008 pass. It provides:
  - `InvoiceData`/`InvoiceLineData`/`RelationData` dataclasses holding only what the XML needs;
  - `build(data) -> lxml Element`;
  - `original_string(xml_bytes) -> str`, using the vendored XSLT compiled once per thread through `threading.local` (R3);
  - `seal(data, certificate_data, key_data, key_password) -> str`, which builds the XML and sets `NoCertificado` and `Certificado`, computes the original string, signs with SHA256withRSA PKCS#1 v1.5 using `csd_service._load_key`, sets `Sello` as base64, and returns the serialized UTF-8 XML with its declaration;
  - `async seal_async(...)`, wrapping `seal` in `asyncio.to_thread`.

  Write a module docstring citing `CFDHelpers40.cs:368` and `ModelHelpers.cs`.

**Checkpoint:** status reports sealed, schemas exist, and the builder produces XSD-valid, verifiable sealed XML from plain data.

---

## Phase 3: User Story 1 — Invoice a customer's sales orders (P1) 🎯 MVP

**Goal:** create an invoice, add completed orders at their remaining quantity, edit and remove lines, and confirm it to the sealed state with a safe serial.

**Independent Test:** create an invoice, add two completed orders, change a quantity, and confirm. The result has the next serial, `status = sealed`, and an XML that is XSD-valid and whose seal verifies.

### Tests for User Story 1 (write first, must fail)

- [ ] T010 [P] [US1] Add `remaining_quantity(quantity, events)` cases to `tests/unit/test_invoice_service.py` (R6):
  - 10 with 3 refunded, then 7 invoiced, gives 0;
  - 10 with 7 invoiced, then 3 refunded, gives 0;
  - 10 with 3 refunded first gives 7;
  - with 4 invoiced, 3 refunded, then 2 more invoiced: the refund only reaches the uninvoiced 6, giving 1 = 10 − 6 − 3;
  - at equal times the refund goes first;
  - the result is never negative;
  - with no events, the quantity is returned.

  Also add pure tests of `default_batch(facility_default, issuer_batches)` (R7):
  - the store's default when it is an Invoice batch of the issuer;
  - otherwise the first Invoice batch by id;
  - no Invoice batch gives `None`.
- [ ] T011 [P] [US1] Add `tests/integration/test_invoice_flow.py` on the SQLite schema, using `seed_baseline`, `seed_sales_order` and `seed_fiscal_document`, plus a seeded issuer, Invoice batch, recipient link and an active certificate from `_make_csd`.
  - **Create (R7):**
    - the defaults apply;
    - XAXX010101000 takes the facility's `location` as postal code;
    - a recipient not linked to the customer gives 422;
    - no Invoice batch gives 422.
  - **Add an order:**
    - only invoiceable products are added, at their remaining quantity, with the order line's price, discount, tax and code, and the product's SAT keys;
    - adding the same order twice does not duplicate lines;
    - an order that is not completed, or is cancelled, gives 422;
    - when everything is already invoiced, 422 "No invoiceable lines were found";
    - lines on a sealed invoice count as invoiced for another draft.
  - **Edit a line:** quantity above remaining gives 422.
  - **Remove a line:** works.
  - **Confirm:**
    - an empty invoice gives 422;
    - a missing SAT key gives 422;
    - a date older than 72 hours gives 422;
    - no valid certificate gives 422;
    - success sets the serial to MAX + 1 of the issuer and series, with legacy rows seeded at serial 41 giving 42;
    - success sets `issued` when it is null, `version` 4.0 and `provider`, writes an XML row that passes `assert_valid_cfdi40`, and reports `status` sealed;
    - a second confirm of a sealed invoice gives 409;
    - a confirm that fails after the serial step leaves no serial and no XML.
  - Edits to a sealed document give 409.
- [ ] T012 [P] [US1] Add `tests/api/test_invoices.py`, with the services patched as in `tests/api/test_fiscal_documents.py`. Cover these US1 routes from the contract: `POST /fiscal-documents`, `POST /{id}/sales-orders`, `PUT` and `DELETE /{id}/lines/{line_id}`, and `POST /{id}/confirm`.
  - Each route: the happy path with status code and body shape; 401 without a token; 403 without privilege 23 at the right level (Create for POST, Update for the rest); 404 for a missing document.
  - Service errors map to 409 and 422 with the contract's detail texts.

### Implementation for User Story 1

- [ ] T013 [US1] Implement `app/services/invoice_service.py`, US1 part. Make T010 and T011 pass.
  - **Pure helpers:** `remaining_quantity(quantity, events)` and `default_batch(...)`.
  - **`create_invoice(db, current, data)`** applies R7:
    - `current.facility_id` is required;
    - the issuer defaults to `facility.taxpayer`;
    - the regime name comes from the SAT tax regime;
    - the customer and recipient link go through the `customer_taxpayer` table;
    - payment terms come from `customer.credit_limit > 0`.
  - **`order_line_remaining(db, detail_ids, exclude_document)`** runs two queries, invoice events and refund events (R6). Invoice events are completed or sealed, not cancelled, and not the current document; sealed means an `EXISTS` on the XML row.
  - **`add_sales_order(db, current, document, sales_order_id)`** applies R8.
  - **`update_line(...)`** and **`remove_line(...)`** apply FR-011, with the remaining check for order lines.
  - **`confirm(db, current, document)`** applies R10's checks, then R2:
    - `select(TaxpayerBatch).where(...).with_for_update()`;
    - `select(func.max(FiscalDocument.serial)).where(...).with_for_update(read=True)`;
    - keep an existing serial;
    - pick the certificate (R4);
    - call `cfdi_invoice.seal_async` with data mapped from the rows (relations' `stamp_uuid` and the type as 2 digits);
    - insert `FiscalDocumentXml`, set `version`, `provider`, `updater` and `modification_time`;
    - commit once, or roll back on any error.
  - **`_require_draft(document)`** raises the 409 "Fiscal document is not a draft".
  - **Errors:** raise `ValueError`/`LookupError` subclasses, following the module conventions of `sales_order_service.py`, that the routes map to 422 and 409.
- [ ] T014 [US1] Add the US1 routes to `app/api/v1/endpoints/fiscal_documents.py`:
  - `POST ''` (201), `POST /{id}/sales-orders`, `PUT` and `DELETE /{id}/lines/{line_id}`, and `POST /{id}/confirm`, as in the contract;
  - privileges with `require_privilege(SystemObject.FISCAL_DOCUMENTS, AccessRight.CREATE/UPDATE)`;
  - each returns `FiscalDocumentResponse` built from `fiscal_document_service.get_document`, which now attaches relations and the new fields (extend `get_document` to load relations with the related document's `stamp_uuid`, batch and serial).

  Make T012 pass.

**Checkpoint:** US1 is complete and independently testable. This is the MVP.

---

## Phase 4: User Story 2 — Standalone invoice (P1)

**Goal:** add products by hand, gated on StandaloneFiscalDocuments (56), then edit and confirm as in US1.

**Independent Test:** a draft with two manual lines, edited and confirmed, is sealed. The XML validates, and its totals match the lines.

- [ ] T015 [P] [US2] Extend `tests/integration/test_invoice_flow.py`:
  - a manual line takes quantity 1, the customer's price-list price, and the product's tax rate, tax-included flag, SAT keys, unit and unit name;
  - a product with no price in that list gives 422;
  - a blank name on update resets to the product's name;
  - a draft with only manual lines confirms, and `Total` equals the sum of the line totals.
- [ ] T016 [P] [US2] Extend `tests/api/test_invoices.py` for `POST /fiscal-documents/{id}/lines`:
  - 201 or 200 with the document;
  - 401;
  - 403 for a user with Update on 23 but no Create on 56;
  - 404;
  - 409 on a sealed document.
- [ ] T017 [US2] Implement `add_manual_line(db, current, document, product_id)` in `app/services/invoice_service.py` (R8, manual), and the blank-name reset in `update_line`.
- [ ] T018 [US2] Add `POST /{id}/lines` to `app/api/v1/endpoints/fiscal_documents.py`, gated with `require_privilege(SystemObject.STANDALONE_FISCAL_DOCUMENTS, AccessRight.CREATE)`. Make T015 and T016 pass.

**Checkpoint:** US1 and US2 both work. The invoice types in use, order-based and standalone, can be sealed.

---

## Phase 5: User Story 3 — Issuer batches (P2)

**Goal:** list, create, update and delete an issuer's series, protecting series in use.

**Independent Test:** create a series, use it on an invoice, then try to rename it (409), change its template (200) and delete it (409).

- [ ] T019 [P] [US3] Add `tests/api/test_taxpayer_batches.py`. It runs against the integration client and SQLite schema, as `tests/integration` does; if the API test style requires patching, split it into service tests in `tests/integration/test_taxpayer_batches.py`.
  - **List:** batches ordered by series, with `in_use`.
  - **Create:**
    - 201;
    - a duplicate series for the issuer gives 409;
    - the same series under another issuer is allowed;
    - a series longer than 10 characters, or an unknown type, gives 422.
  - **Update:**
    - a template change is allowed while in use;
    - a series or type change while in use gives 409;
    - any change is allowed when not in use.
  - **Delete:** in use gives 409; otherwise 204.
  - **Errors:** an unknown issuer or batch gives 404; no token gives 401; missing privilege 24 at the right level gives 403.
  - **Template round trip:** a legacy single-quoted template reads back parsed, and a saved template is valid JSON that `cfdi.batch_settings` reads.
- [ ] T020 [US3] Implement `app/services/taxpayer_batch_service.py`: `list_batches`, `get_batch`, `create_batch`, `update_batch`, `delete_batch` and `in_use(db, issuer, batch)`. The template is serialized to and from the legacy JSON keys `Name`, `Logo`, `HeaderHeight`, `FooterHeight` and `ExtraInfo`. Parsing reuses the tolerant parsing in `cfdi.batch_settings`.
- [ ] T021 [US3] Add the four `/{rfc}/batches` routes to `app/api/v1/endpoints/taxpayer_issuers.py`, gated on `SystemObject.TAXPAYERS` with READ, CREATE, UPDATE and DELETE. A duplicate series maps to 409 via the existing integrity-error handling or an explicit check. Make T019 pass.

**Checkpoint:** batches can be maintained without the current system.

---

## Phase 6: User Story 4 — Header, relations, reopen (P2)

**Goal:** edit the header fields, relate stamped documents (04 and 07), and reopen a sealed invoice keeping its serial.

**Independent Test:** edit each header field, add a 07 relation, confirm and check the XML, reopen, change the date, and confirm again with the same serial.

- [ ] T022 [P] [US4] Extend `tests/integration/test_invoice_flow.py`.
  - **Header:**
    - a recipient change recopies the name, regime and postal code (XAXX rule);
    - a recipient not linked to the customer gives 422;
    - the batch must be an Invoice batch of the same issuer, else 422;
    - a batch change after a serial exists gives 409;
    - NET_D forces payment form 99;
    - an issue date in the future, or more than 72 hours back, gives 422;
    - a facility change recopies `issued_at`, `issued_location` and the XAXX postal code;
    - an unknown usage gives 422.
  - **Relations:**
    - 07 to a stamped, non-cancelled invoice of the same issuer and recipient succeeds, and to a stamped type 101 too;
    - 04 to a cancelled stamped invoice succeeds;
    - 04 to a non-cancelled invoice gives 422;
    - another recipient or issuer gives 422;
    - a target without a UUID gives 422;
    - mixing 04 and 07 gives 422;
    - a duplicate target gives 409;
    - the type is stored as `'7'` and returned as `"07"`;
    - the sealed XML lists the UUIDs under `TipoRelacion="07"`;
    - a relation can be removed.
  - **Reopen:**
    - a sealed invoice returns to draft, keeping its serial, with the XML row deleted;
    - a draft gives 409;
    - after reopening, changing the date and confirming again keeps the same serial;
    - a stamped invoice (completed) gives 409 on reopen, confirm and edits.
- [ ] T023 [P] [US4] Extend `tests/api/test_invoices.py` for `PUT /fiscal-documents/{id}`, `POST`/`DELETE /{id}/relations…` and `POST /{id}/reopen`:
  - happy paths, 401, 403 and 404;
  - a `facility` change without Update on IssuedLocationId (103) gives 403, while other fields with only 23 give 200;
  - 409 and 422 mapping.
- [ ] T024 [US4] Implement in `app/services/invoice_service.py`:
  - `update_header(db, current, document, data)` (R10), where only fields in `model_fields_set` change;
  - `add_relation` and `remove_relation` (R9);
  - `reopen` (R10).
- [ ] T025 [US4] Add `PUT /{id}`, `POST /{id}/relations`, `DELETE /{id}/relations/{relation_id}` and `POST /{id}/reopen` to `app/api/v1/endpoints/fiscal_documents.py`. For a body with `facility`, also check `has_privilege` or `require_privilege(SystemObject.ISSUED_LOCATION_ID, AccessRight.UPDATE)` inside the handler. Make T022 and T023 pass.

**Checkpoint:** all four stories work.

---

## Phase 7: Polish & Cross-Cutting Concerns

- [ ] T026 [P] Update `docs/specs/10-fiscal-documents.md` (FR-017) with:
  - batch maintenance;
  - the invoice editor and its routes;
  - remaining quantity (the clarified rule);
  - sealing and the original string;
  - the serial lock;
  - the sealed state and reopen;
  - relation targets;
  - stamping marked as pending (#46).
- [ ] T027 [P] Update `CHANGELOG.md` `[Unreleased]`:
  - **Added:** taxpayer batches, the invoice editor, sealing and lxml.
  - **Changed:** fiscal document `status` gains `sealed`.
- [ ] T028 Run `uv run ruff check app/ migrations/ tests/` and `uv run pytest -q`. Everything must be green. Fix anything this feature broke, including `tests/integration/test_no_endpoint_returns_500.py` for the new routes.
- [ ] T029 Write the SC-002 parity script in the scratchpad, following quickstart §3, and run it read-only on xolotl. Selecting the samples and the stored values needs the branch deployed (T030) or the code copied to `/tmp`. Record the samples, the result and any differences in the spec's verification notes, in `specs/027-cfdi-invoices-sealing/spec.md` under a "Verification" section.
- [ ] T030 **Ask the user first.** Deploy the branch to the xolotl test instance (quickstart §2): as `mictlanix@`, check out `027-cfdi-invoices-sealing`, run `uv sync`, then, as root, `systemctl restart mbe-api-test`.
- [ ] T031 **Ask the user first; it writes to mbe_dev.** Run SC-003 (quickstart §4): 20 concurrent confirms in a throwaway series `ZZ027`, plus the failure run. Then do the cleanup, listing each step and getting approval before it runs. Record the outcome in spec.md "Verification".
- [ ] T032 **Ask the user first.** Run SC-004 (quickstart §5) on the test instance, with the before and after snapshots. Record the outcome in spec.md "Verification", and remove `/tmp/mbeq.py` and any scratch scripts from xolotl.

---

## Dependencies & Execution Order

- **Setup (T001–T004)** runs first. T002 and T003 run in parallel. T004 needs T001 and T003.
- **Foundational (T005–T009)** blocks every story. T005 and T008 run in parallel. T006 follows T005. T007 is independent of T005 and T006. T009 follows T008 and T004.
- **US1 (T010–T014)** needs Phase 2. It is the MVP.
- **US2 (T015–T018)** needs US1's `create_invoice`, `update_line` and `confirm` (T013, T014).
- **US3 (T019–T021)** needs only Phase 2's schemas (T007). It can run in parallel with US1.
- **US4 (T022–T025)** needs US1 (T013, T014) for create and confirm.
- **Polish:** T026–T028 come after all stories. T029–T032 follow T028, in order, each gated on user confirmation where marked.

### Parallel opportunities

- T002 ∥ T003
- T005 ∥ T007 ∥ T008
- T010 ∥ T011 ∥ T012 (three test files)
- US3 (T019–T021) ∥ US1
- T015 ∥ T016
- T022 ∥ T023
- T026 ∥ T027

### Parallel example: User Story 1

```text
Task: "remaining_quantity and default_batch unit tests in tests/unit/test_invoice_service.py" (T010)
Task: "invoice flow integration tests in tests/integration/test_invoice_flow.py" (T011)
Task: "US1 route tests in tests/api/test_invoices.py" (T012)
```

---

## Implementation Strategy

1. **MVP:** Setup, Foundational and US1. The result: order-based invoices (85 % of the volume) can be created and sealed. Validate the US1 independent test.
2. **Then:**
   - US2, the standalone 15 %;
   - US3, batches, possibly in parallel with US1;
   - US4, header, relations and reopen.
3. **Polish:**
   - docs and CHANGELOG;
   - the full suite;
   - on xolotl, after confirmation: SC-002 parity, the deploy, SC-003 concurrency and the SC-004 flow.
4. **PR:** it must close mictlanix/mbe-roadmap#44 and #47.
