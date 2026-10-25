# Research: CFDI Invoices up to a Sealed Document

Measured on mbe_dev (read only) on 2026-10-25. The current system is `mictlanix/mbe`: `Web/Controllers/Mvc/FiscalDocumentsController.cs`, `Web/Helpers/CFDHelpers40.cs` and `Model/Helpers/ModelHelpers.cs`. cfdlib is `mictlanix/cfdlib`, `src/CFDv40`.

## R1. The sealed state needs no schema change

- **Decision:** a document is **sealed** when:
  - it is neither completed nor cancelled; and
  - it has a `fiscal_document_xml` row.

  `document_status()` returns `draft | sealed | issued | cancelled`. Stamping (#46) will set `completed`, the stamp columns and the stamped XML, which moves the document to issued.
- **Evidence:** every combination on mbe_dev:

  | completed | cancelled | serial | XML | UUID | rows |
  |---|---|---|---|---|---|
  | 0 | 0 | – | – | – | 8 |
  | 0 | 1 | – | – | – | 1,458 |
  | 0 | 1 | yes | – | yes | 1 |
  | 1 | 0 | yes | yes | – | 6,487 (self-sealed CFD 2.x) |
  | 1 | 0 | yes | yes | yes | 53,342 |
  | 1 | 1 | yes | yes | – / yes | 367 / 2,005 |

  No row is uncompleted with an XML, so the rule cannot misread existing data. The current system shows a sealed invoice as a draft, as the spec's Assumptions accept.
- **Alternatives:**
  - **A status column:** rejected. It needs DDL on a shared table, and the existing pair already distinguishes every state.
  - **Sealed as `completed = 1` without a UUID:** rejected. It collides with the 6,487 legacy CFD 2.x rows, and every legacy report would count unstamped invoices as issued.
- **Spec 020's reads:**
  - The list's `status` filter gains `sealed`. `draft` becomes "not completed, not cancelled, no XML", using an `EXISTS` on `fiscal_document_xml`.
  - The XML download and PDF keep requiring `completed`, so a sealed document is refused with 409, as it is today. The sealed XML has no legal value until stamped.

## R2. Serial assignment under concurrency (FR-014, SC-003)

- **Decision:** in the confirm transaction:
  1. lock the batch row:

     ```sql
     SELECT … FROM taxpayer_batch WHERE taxpayer = :issuer AND batch = :batch FOR UPDATE
     ```

     The row is unique: index `taxpayer_batch_taxpayer_batch_idx (taxpayer, batch)`.
  2. read the highest serial with a **locking** read:

     ```sql
     SELECT MAX(serial) FROM fiscal_document WHERE issuer = :issuer AND batch = :batch LOCK IN SHARE MODE
     ```

  3. set `serial = max + 1`, seal, write the XML, and commit once.

  A document that already has a serial (it was reopened) keeps it and skips steps 1 and 2.
- **Why the second read must lock:**
  - mbe_dev runs InnoDB at `REPEATABLE-READ`. A plain `SELECT` reads the snapshot taken at the transaction's first consistent read.
  - The request session has already read the user and the document before confirm runs. A plain `MAX` could therefore miss a serial that the previous lock holder committed while this request waited, and the #45 duplicate would come back.
  - A locking read always sees the latest committed rows.
- **Why the batch lock comes first:**
  - Shared locks on the issuer's index range alone deadlock. Two confirmers each hold S on the other's row and then want X on their own.
  - The batch lock admits one confirmer per series at a time.
  - Draft edits and inserts for the same issuer wait at most for one confirm, which is milliseconds: the seal is the only CPU work.
- **No gaps:**
  - A failed confirm rolls back everything, serial included.
  - A reopened invoice keeps its number, so the number is never free to reuse.
  - The 9 legacy duplicates stay. `MAX` continues past them.
- **Testing:**
  - SQLite ignores `FOR UPDATE`, so unit tests cover the sequence: next after max, a reopened invoice reuses its serial, and a failure consumes nothing.
  - SC-003 runs on MariaDB, through the test deployment on xolotl: 20 concurrent confirms in a throwaway series (quickstart §4).
- **Alternatives:**
  - **A unique index on `(issuer, batch, serial)`:** impossible while the 9 duplicates exist, and it is DDL.
  - **A counter column on `taxpayer_batch`:** DDL. It must also be seeded from `MAX` and kept in step with the existing rows; the batch lock gives the same guarantee without it.
  - **Retry on duplicate:** there is no constraint to detect a duplicate with.

## R3. The original string uses SAT's stylesheet, through lxml

- **Decision:**
  - Vendor cfdlib's XSLT 1.0 set (`src/CFDv40/Resources/cadena_original_4_0.xslt` and its 35 included files, 352 KB) into `app/resources/cfdi40/`.
  - Correct the include `divisas.xslt` to `Divisas.xslt` there, since the file system is case-sensitive.
  - Compute the string with `lxml.etree.XSLT`. Each worker thread compiles the stylesheet once (`threading.local`), because lxml stylesheets must not be shared across threads. The seal runs in `asyncio.to_thread`, as `csd_service.parse_csd` does.
- **Rationale:**
  - It is the exact transform the current system and SAT apply, so SC-002 parity is the same code path on both sides.
  - The complement templates only match complement nodes, which invoices never carry, so they cost nothing.
- **New dependency:** `lxml`. Python's standard library has neither XSLT nor XSD validation. It is declared in the spec's Assumptions.
- **Alternatives:**
  - **A hand-written original string in Python:** rejected. It would re-implement SAT's ordering and normalisation rules, and SC-002 would then compare our code with itself.
  - **SAT's own `cadenaoriginal_4_0.xslt`:** rejected. It is XSLT 2.0 and includes its complements over `http://`, so it cannot run offline in lxml.

## R4. Sealing

- **Decision:**
  - The seal is `base64(RSA-PKCS#1 v1.5(SHA-256(original_string as UTF-8)))`, using `cryptography`.
  - The key is opened with `csd_service._load_key(key_data, key_password)`.
  - `NoCertificado` is `taxpayer_certificate_id`, already 20 digits.
  - `Certificado` is `base64(certificate_data)`.
  - The original string is computed with `Sello=""` absent and `NoCertificado`/`Certificado` present, as cfdlib's `Comprobante.Sign` does: SAT's stylesheet skips `Sello` and `Certificado` anyway.
- **Certificate choice (FR-013 step 4):**
  - The issuer's certificates with `status = ACTIVE` and `valid_from <= issued <= valid_to`, both naive Mexico City local time, as spec 008 stores them.
  - None gives 422 "The issuer has no active certificate valid at the issue date".
  - If several match, the latest `valid_to` wins. The current system demanded exactly one active certificate and crashed otherwise.
- **Reuse:**
  - `_load_key` and `_load_certificate` from `csd_service` are reused. The new module imports them; no copy.
  - Verification in tests uses the certificate's public key, so SC-001's "the seal verifies" is a direct assertion.

## R5. XML values and rounding

All rules come from `InvoiceToCFDv40` (`CFDHelpers40.cs:368`) and `ModelHelpers.cs:34-145`. Rounding is half away from zero (`ROUND_HALF_UP` on Decimal).

### Per line

The inputs are quantity `q`, price `p`, discount rate `d`, tax rate `t` and tax-included flag `ti`. Exempt is `t = -1`, and the rate is treated as 0 in the amounts.

| Value | Formula | Scale |
|---|---|---|
| `ValorUnitario` | `p / (1 + t)` if `ti` and `t > 0`, else `p` | 6 |
| `Importe` (subtotal) | `q·p / (1 + t)` if `ti` and `t > 0`, else `q·p` | 2 |
| `Descuento` | `q·p·d / (1 + t)` if `ti` and `t > 0`, else `q·p·d` | 2, written only when > 0 |
| total | `discount = round2(q·p·d)`. If `ti` or `t ≤ 0`: `round2(q·p − discount)`, else `round2((q·p − discount)·(1 + t))` | 2 |
| tax `Importe` | `total − subtotal + discount` | 2 |
| `Base` | `taxes / t` if `t > 0`, else `subtotal − discount` | unrounded |

The legacy `Total` helper's tax-excluded branch is at `ModelHelpers.cs:84-88`; the implementation reads it there. Every measured line is tax-included.

### Per line, the tax nodes

- `ObjetoImp` is `01` and there is no `Impuestos` when `subtotal == discount` (a 100 % discount). Otherwise it is `02`.
- **`t ≥ 0`:** a `Traslado` with `Impuesto="002"`, `TipoFactor="Tasa"`, `TasaOCuota` = `t` at 6 places, `Base` and `Importe`.
- **`t = -1`:** a `Traslado` with `TipoFactor="Exento"` and `Base` only.

### Document totals

- `SubTotal` = Σ line subtotals.
- `Descuento` = Σ line discounts, written only when > 0.
- `Total` = Σ line totals. There are no retentions (spec Assumptions).
- `Impuestos/Traslados`: one per (`Impuesto`, `TipoFactor`, `TasaOCuota`), with `Base` = round2(Σ base) and `Importe` = round2(Σ importe). Exempt groups have no `TasaOCuota` or `Importe`.
- `TotalImpuestosTrasladados` = Σ group importes.

### Two cases the current system never met, decided by SAT's rules

- **Every taxed line is exempt:** omit `TotalImpuestosTrasladados`.
- **No line carries taxes** (all at 100 % discount): omit `Comprobante/Impuestos`.

### Formatting

- `Cantidad` keeps the column's 4 places (`"4.0000"`).
- `TasaOCuota` uses 6 places (`"0.160000"`).
- 2-place amounts always show 2 places.
- `Base` is written normalised, without exponent or trailing zeros (`"310.375"`, `"1569"`). This matches C#'s decimal division in `tests/fixtures/cfdi/invoice.xml`.

  Python's `Decimal` division can return an exponent form (`1.569E+3`), so the writer formats with `format(value.normalize(), 'f')`.

### Header

| Attribute | Value |
|---|---|
| `Version` | `4.0` |
| `Serie`, `Folio` | batch, serial |
| `Fecha` | `issued` as `YYYY-MM-DDTHH:MM:SS` |
| `FormaPago` | `payment_method` as 2 digits |
| `MetodoPago` | `PUE` if terms are IMMEDIATE, else `PPD` |
| `Moneda` | `MXN`, with no `TipoCambio` |
| `TipoDeComprobante` | `I` |
| `Exportacion` | `01` |
| `LugarExpedicion` | `issued_location` |
| `Emisor` | `Rfc` = issuer, `Nombre` = `issuer_name`, `RegimenFiscal` = `issuer_regime` |
| `Receptor` | `Rfc`, `Nombre` = `recipient_name`, `DomicilioFiscalReceptor` = `taxpayer_postal_code`, `RegimenFiscalReceptor` = `taxpayer_regime`, `UsoCFDI` = `usage`, with `P01` written as `G03` |
| `Concepto` | `ClaveProdServ`, `NoIdentificacion` = code (omitted when blank), `Cantidad`, `ClaveUnidad`, `Unidad` = unit name, `Descripcion` = name |
| `CfdiRelacionados` | one node, `TipoRelacion` = the relations' type as 2 digits, one `CfdiRelacionado UUID` per relation, from the related document's `stamp_uuid` |
| `xsi:schemaLocation` | `http://www.sat.gob.mx/cfd/4 http://www.sat.gob.mx/sitio_internet/cfd/4/cfdv40.xsd` |

### Relation types

`fiscal_document_relation.type` stores cfdlib's enum **number** as text: `'4'` and `'7'`, plus `'0'` on 158 payment-receipt rows. New rows store `'4'` and `'7'` the same way, and the XML writes `04` and `07`.

The current system's relation search (`GetRelations`, `GetReplacementRelations`) defines the targets:
- **04:** type < 100, completed and **cancelled**;
- **07:** type not 100 or 200, completed and not cancelled.

Both require the same issuer and recipient. The measured rows agree:
- `'4'` → 2 cancelled invoices;
- `'7'` → 50 invoices and 3 documents of type 101.

FR-010 is corrected to say this.

- **Rationale:** SC-002 compares the original string of a rebuilt invoice with the stored one. Any departure from these formulas shows up there.
- **Alternatives:** `app/services/totals.py`. It was rejected for the XML, because it rounds once per document while CFDI rounds per line, and its tax-included base differs. It stays the sales documents' helper.

## R6. Remaining quantity (FR-006, clarified 2026-10-25)

- **Decision:** for each sales order line, collect events in one query per kind, for all lines of the order at once:
  - **invoice events:** a `fiscal_document_detail` row for the line, whose document is not cancelled and is either completed or sealed (R1), excluding the current draft. The quantity is taken at the document's `issued` time.
  - **refund events:** a `customer_refund_detail` row whose refund is completed and not cancelled, at `customer_refund.date`.

  Walk them in time order. At equal times a refund goes first, the conservative choice.

  ```
  invoiced = 0; refunded = 0
  for each event:
      invoice: invoiced += qty
      refund:  refunded += min(qty, max(0, q − invoiced − refunded))
  remaining = max(0, q − invoiced − refunded)
  ```

- **Rationale:** this is the clarified rule. A pure function over a list of events is easy to unit test with the spec's two examples and the 141 measured cases.
- **Alternatives:** the current system's `q − max(invoiced, refunded)`. It was rejected in clarification.

## R7. Defaults and copied values on create (FR-003, FR-004)

| Field | Value | Current-system source |
|---|---|---|
| `facility` | the caller's facility (`current.facility_id`); 422 when none | `WebConfig.Store` |
| `issuer` | request, else `facility.taxpayer` | `New`, line 211 |
| `batch` | request, else `facility.default_batch` when it is an Invoice batch of the issuer, else the issuer's first Invoice batch by id. 422 "The issuer has no invoice series" | clarified |
| `issuer_name`, `issuer_regime`, `issuer_regime_name` | issuer and `sat_tax_regime.description` | `New`, line 252 |
| `issued_at`, `issued_location` | `facility.address`, `facility.location` | `New`, line 248 |
| `customer` | request (required) | |
| `recipient` | request (required), one of the customer's `customer_taxpayer` links | |
| `recipient_name`, `taxpayer_regime` | the recipient record | |
| `taxpayer_postal_code` | the recipient's postal code, or `facility.location` for XAXX010101000 | |
| `type` | 0 (Invoice) | |
| `currency`, `exchange_rate` | MXN, 1 | |
| `payment_terms` | NET_D when `customer.credit_days > 0` and `customer.credit_limit > 0`, else IMMEDIATE | `HasCredit` (`Model/Customer.cs:79`) |
| `payment_method` | 99 | |
| `usage` | G01 | |
| `completed`, `cancelled` | false | |
| `version`, `provider` | 0, 0 until confirm. Confirm sets 4.0 and `issuer.provider` | measured: drafts 0/0, issued 4.0/4 |
| `retention_rate`, `local_retention_rate` | 0 | |
| `creator`, `updater`, times | caller's employee, `local_now()` | |

Measured: `issued_location` equals `facility.location` on every 2026 row. `issued_at`'s address postal code differs on some stores; it is copied as the current system does and is not used in the XML.

## R8. Lines

- **From an order (FR-005):**
  - Refused with 404 when the order does not exist. Refused with 422 when it is not completed or is cancelled.
  - One line per order line of an invoiceable product with remaining quantity > 0.
  - Order lines already on the draft are skipped (FR-006).
  - Nothing added gives 422 "No invoiceable lines were found".
  - **Copied from the order line:** `product_code`, `product_name`, `comment`, `price`, `discount_rate` → `discount`, `tax_rate`, `tax_included`.
  - **Copied from the product:** `product_service`, `unit_of_measurement`, and the unit's name. `exchange_rate` and `currency` come from the document.
  - **Invoiceable product:** the current system's `AddItems` filter, `IsInvoiceable`. Implementation checks the product column (`product.invoiceable`).
- **Manual (FR-007):**
  - Requires Create on StandaloneFiscalDocuments (56).
  - Quantity 1, with the customer's price-list `product_price` (422 when the product has none), and the product's tax rate, tax-included flag, SAT keys and unit.
  - The current system converts currency; this one does not, because it is MXN only.
- **Edit (FR-011):**
  - quantity > 0, and ≤ remaining for an order line;
  - price ≥ 0;
  - discount 0–1;
  - tax rate in {0.16, 0, -1};
  - code, name and comment, where a blank name resets to the product's name.
- **Remove:** any line of a draft.

## R9. Relations (FR-010)

- **Add:** `{relation: fiscal_document_id, type: '04' | '07'}`. The API takes the SAT code and stores the number (R5).
  - **Refused (422) when:**
    - the target does not match the type rules in R5;
    - the issuer or recipient differs;
    - the target has no `stamp_uuid`;
    - the type differs from the draft's existing relations.
  - **Refused (409)** when the same target is already related: unique index `document_relation_idx (document, relation)`.
  - **Stored values:** `installment = 1`, `exchange_rate = 1`, `previous_balance = 0`, `amount = 0`. The current system leaves the last two at their defaults.
- **Remove:** by relation row id.

## R10. Confirm and reopen

**Confirm checks** (422 unless noted):
- the state is draft (409 otherwise);
- there is at least one line;
- every line has a SAT product key and unit;
- every quantity is > 0 and ≤ its remaining quantity;
- the recipient has a regime and a postal code;
- `issued` lies within the last 72 hours and not in the future. When it is null it is set to `local_now()` truncated to seconds, as in the current system;
- a valid certificate exists (R4).

Then come R2, R5, R3 and R4. The result is stored in `fiscal_document_xml` and in `version = 4.0` and `provider = issuer.provider`, and the transaction is committed.

**Reopen:**
- **Requirements:** a sealed state (409 otherwise) and Update on FiscalDocuments (23).
- **Effect:** deletes the `fiscal_document_xml` row and keeps `serial`, `issued` and every other field.
- **Next confirm:** reseals with the same serial, after the 72-hour check, so a stale date must first be edited.

**Header edit** (FR-008, FR-009): accepted only on a draft.
- **Recipient:**
  - must be linked to the customer;
  - recopies the name, regime and postal code, applying the XAXX rule.
- **Batch:** must be an Invoice batch of the same issuer. Changing it on a reopened invoice that already has a serial is refused (409), because the serial belongs to its series.
- **Payment terms:** NET_D forces payment form 99.
- **Facility:** additionally requires Update on IssuedLocationId (103), as in the current system. It recopies `issued_at` and `issued_location`, and the XAXX postal code.
- **Issue date:** must be ≤ now and > now − 72 h.
- **Other fields:** usage (a SAT CFDI usage that exists), payment form, reference (≤ 25 characters) and comment (≤ 1000 characters).

## R11. Batches (FR-001, FR-002)

- **Routes:** nested under the issuer, `/taxpayer-issuers/{rfc}/batches`, gated on Taxpayers (24) like the issuer routes.
- **Template:** stored as the current system's JSON text. The API takes and returns the parsed fields:
  - `name`
  - `logo`
  - `header_height`
  - `footer_height`
  - `extra_info`

  It writes them back in the same JSON shape, which `cfdi.batch_settings` already reads. The existing rows use single quotes, which `batch_settings` tolerates; new rows are written as standard JSON.
- **"In use":** any `fiscal_document` with `issuer = rfc` and `batch = series`, in any state.
- **Uniqueness:** `(taxpayer, batch)` is already a unique index. A duplicate gives 409.

## R12. XSD validation in tests (SC-001)

- **Decision:** vendor SAT's `cfdv40.xsd` (50 KB), `tdCFDI.xsd` (7 KB) and `catCFDI.xsd` (6.0 MB, stored gzipped; libxml2 reads `.gz` transparently) into `tests/fixtures/sat/`, with their `schemaLocation` imports rewritten to the local files. Downloaded from sat.gob.mx on 2026-10-25; all three returned 200.
- **Use:** a test helper `assert_valid_cfdi40(xml)` validates with `lxml.etree.XMLSchema`. It is used by the sealing unit tests and the confirm API tests.
- **Rationale:** the real catalogue validates `ClaveProdServ`, `ClaveUnidad`, `UsoCFDI` and regimes, which is where real invoices fail.

## R13. Parity with stamped invoices (SC-002)

- **Decision:** a read-only script on xolotl:
  1. picks stamped CFDI 4.0 invoices that cover the spec's cases;
  2. builds each one's XML from its stored rows with the new builder, using the stored `Fecha`, `NoCertificado` and `Certificado`;
  3. compares the original string of both through the same stylesheet.

  The cases are order-based and standalone invoices, XAXX, 0 %, exempt, a discount, PPD, and a type 07 relation.

  The run is recorded in the spec's verification notes.
- **In the repo:** `tests/fixtures/cfdi/invoice.xml` (anonymised) gets an equivalent seeded document. A unit test asserts that the rebuilt original string equals the fixture's.
