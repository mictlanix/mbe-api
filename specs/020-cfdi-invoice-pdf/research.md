# Research: CFDI Invoice PDF

All facts below were checked against `mbe_dev` (recreated 2026-09-29) and legacy `mbe` at the paths given. Sample checks were read-only.

## R1. Printed values come from the stamped XML

**Decision**: Parse the stored XML (`fiscal_document_xml.data`) and print every amount, code, seal, certificate number, UUID and date from it. Take from the database only what the XML does not carry:

| Printed | Source |
|---|---|
| Issuer regime name | `fiscal_document.issuer_regime_name` (legacy's snapshot) |
| Recipient regime name | `sat_tax_regime.description` for the XML's `RegimenFiscalReceptor` (legacy joins live) |
| CFDI use description | `sat_cfdi_usage.description` for the XML's `UsoCFDI` |
| Payment form name | `formatting.method_name(int(FormaPago))`, the spec 019 table: SAT `c_FormaPago` codes equal `PaymentMethod` values |
| Line comment | `fiscal_document_detail.comment`, paired with the XML's `Concepto` by position |
| Cancellation date | `fiscal_document.cancellation_date` (the XML never records cancellation) |
| Issuer postal code | `taxpayer_issuer.postal_code`, as legacy's `Issuer.PostalCode` (the 4.0 `Emisor` carries none) |
| Reference | `fiscal_document.reference` (not in the XML) |
| Related document's series and folio (payment receipt) | the XML's `DoctoRelacionado` `Serie`/`Folio` |

**Rationale**:
- `fiscal_document` stores no totals and no issuer seal. Legacy recomputes the totals from the detail rows, which can drift from what was stamped. The QR code's `tt` must be the stamped total, or SAT's check fails.
- On a sample of 377 stamped 4.0 documents (250 random invoices plus every other 4.0 document), the XML's `Sello`, `SelloSAT` and UUID equal the stored columns in every case.
- All 17,433 stamped 4.0 documents have their XML.
- 7,228 4.0 lines have a comment. It is not in the XML's `Descripcion` (checked on document 63870), so it can only come from the database.

**Alternatives considered**: Printing from database columns, as legacy does. Rejected: totals are recomputed rather than read, and the printout could then disagree with the CFDI it represents.

**Line pairing**: If the number of XML concepts differs from the number of detail rows, comments are left out rather than attached to the wrong line.

## R2. Original string of the stamp

**Decision**: Build it from the XML's `TimbreFiscalDigital` as `||Version|UUID|FechaTimbrado|RfcProvCertif|[Leyenda|]SelloCFD|NoCertificadoSAT||`. Each value is whitespace-normalized, and `Leyenda` is omitted with its separator when absent. Attribute values are used exactly as stamped.

**Rationale**: This is SAT's `cadenaoriginal_TFD_1_1.xslt`, which legacy runs through cfdlib (`cfdlib/src/CFDv40/TimbreFiscalDigital.Custom.cs:67-81`, stored by `FiscalDocumentsController.cs:1576`). The rebuilt string equals `fiscal_document.original_string` for all 377 sampled documents. No 4.0 document carries a `Leyenda`.

**Alternatives considered**: Printing the stored `original_string`. It is equal today, but it is a copy, and R1 says printed stamp values come from the XML. Running SAT's XSLT would need `lxml`, which is not installed. Seven fields do not justify a dependency.

## R3. SAT verification QR code

**Decision**:
- **Payload**: `https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx?id={UUID}&re={Emisor Rfc}&rr={Receptor Rfc}&tt={Total}&fe={last 8 characters of Sello}`, with every value taken from the XML verbatim.
- **Escaping**: an `&` inside an RFC is written as `%26`. Nothing else is escaped.
- **Rendering**: an SVG drawn by `segno` (error correction M, zero quiet zone as in legacy), inlined as a `data:` URI. It is sized 29 mm, like legacy.

**Rationale**:
- Legacy's format is `Resources.resx:2150`, and its image comes from `BarcodesController.cs:71-86`.
- `tt` is the XML `Total` string, so it is exactly what was stamped. For a payment receipt that is `0`, the stamped `Total` of every type-200 document.
- **`&`**: one 4.0 recipient RFC contains `&`. Legacy inserts it raw, which splits the query string, so that document's code cannot verify today.
- **`Ñ`**: no stamped 4.0 document has `Ñ` in an RFC, counted with a binary comparison on 2026-09-30. An earlier count of 1,674 was wrong: `utf8mb4_general_ci` matches `Ñ` to `N`. Legacy prints `Ñ` raw, and so does this, so no QR code changes. A unit test pins the case with a fixture RFC.
- SC-006 checks the `&` case on a phone, with document 57904.
- `segno` is pure Python with no dependencies and emits SVG, matching how spec 019 inlines barcodes.

**Alternatives considered**:
- `qrcode`: it needs Pillow for images and emits less compact SVG.
- Serving a QR image from a route, as legacy does: its anonymous `QRCode33` endpoint, keyed by a sequential id, leaks RFCs, UUIDs and totals. Inlining avoids any such route.
- Percent-encoding the whole payload: it departs from legacy's QR codes for every character but `&`, with no known benefit.

## R4. Letter geometry, footer, page numbers and cancellation mark

**Decision**:
- **Page**: letter size, with the 6 mm margins of `_print_layout.html`.
- **Footer**: a running element (`position: running(footer)`, placed with `@page { @bottom-center { content: element(footer) } }`). It carries:
  - the bank accounts from the batch settings;
  - `FacturaElectronicaSimple.mx`;
  - the legend `Este documento es una representación impresa de un CFDI.`;
  - `Página N de M` from `counter(page)` and `counter(pages)`.
- **Footer height**: `FooterHeight` from the batch settings sets the page's bottom margin, in mm. The default is 15, the value every batch holds.
- **Cancellation mark**: a `position: fixed` element that WeasyPrint repeats on every page. It reads `CANCELADO` plus the cancellation date.

**Rationale**: WeasyPrint 70 supports running elements, page counters and fixed elements repeated per page. Legacy's jsreport footer is the same idea, using `{#pageNum}`/`{#numPages}`. `HeaderHeight` is 0 in every batch and no 4.0 layout has a header, so it is ignored.

**Alternatives considered**: A footer as the last block of the body. Rejected: it would not repeat on every page (FR-015).

## R5. Batch settings

**Decision**:
- **Lookup**: find the `taxpayer_batch` by the document's issuer and batch, then parse `template` with `ast.literal_eval`.
- **Fields read**:
  - `Logo`: its file name only, looked up under `settings.images_dir`;
  - `FooterHeight`;
  - `ExtraInfo`: a list of `{Bank, Account, CLABE, Currency}`.
- **Fallback**: a missing row, a parse error or a wrong type gives the defaults: no logo, footer height 15 mm, no accounts. `Name` is not used.

**Rationale**:
- Every batch's template is written with single quotes (`{'Name': 'T02Blue', …}`), so `json.loads` rejects it. Legacy parses it with Newtonsoft, which is lenient. `ast.literal_eval` accepts it and cannot execute code.
- Every batch names `T02Blue`, and `Print40T02Blue` is legacy's only 4.0 layout.
- The logo in every batch is `~/Content/images/casamaestra.png`, a file inside the legacy application, and it is not in the API's images directory. Until someone copies it there, invoices print without a logo. That is an operations step, noted in quickstart.md.

**Alternatives considered**: Rewriting the quotes and using `json.loads`. Rejected: fragile, since an apostrophe inside a bank name breaks it. Bundling the logo in the code: rejected, because it is tenant data.

## R6. XML parsing

**Decision**: Use `xml.etree.ElementTree.fromstring` on the stored text encoded as UTF-8, matching elements by local name (`{*}Concepto`). A parse failure, or a missing `Comprobante` or `TimbreFiscalDigital`, raises a single error that the route turns into a 409.

**Rationale**:
- The XML is stored stamping output, not a user upload.
- Python 3.12's expat does not resolve external entities and limits entity amplification, so the XXE and billion-laughs risks that `defusedxml` guards against do not apply.
- Local-name matching keeps the parser independent of namespace prefixes (`cfdi:`, `tfd:`, `pago20:`).

**Alternatives considered**: `defusedxml`, a dependency whose protection this input does not need. `lxml`, a compiled dependency with no use beyond XSLT (see R2).

## R7. Document types and their sections

**Decision**: One template, `fiscal_document.html`, with two branches. Payment receipts (`TipoDeComprobante="P"`) get the payments section; every other type gets the concepts section. Related CFDIs are printed for any non-payment document with a `CfdiRelacionados` block. The relation label comes from the XML's `TipoRelacion`:

| Code | Label (legacy resx) |
|---|---|
| `01` | `01 : Nota de Crédito de los Documentos Relacionados` |
| `04` | `04 : Sustitución de los CFDI Previos` |
| `07` | `07 : CFDI por Aplicación de Anticipo` |
| other | `{code}` alone |

The title comes from `fiscal_document.type`: `Factura` (0), `Nota de Crédito` (100), `Aplicación de Anticipos` (101), `Recibo Electrónico de Pago` (200), and so on for the rest of legacy's `FiscalDocumentType`.

**Rationale**:
- In the data, credit notes (100) carry `01` and applied advances (101) carry `07`. Legacy picks the label from the document type, so reading the code instead gives the same text.
- Every 4.0 payment receipt has exactly one `pago20:Pago`. The template still loops over payments, which costs nothing and is correct for more than one.
- Payment receipt fields:

  | Printed | XML (per payment) |
  |---|---|
  | Payment date | `FechaPago` |
  | Payment form | `FormaDePagoP` |
  | Payment reference | `NumOperacion` |
  | Foreign bank name | `NomBancoOrdExt` |
  | Currency, exchange rate | `MonedaP`, `TipoCambioP` |
  | Amount | `Monto` |

  | Printed | XML (per settled document) |
  |---|---|
  | UUID | `IdDocumento` |
  | Series and folio | `Serie`, `Folio` |
  | Installment | `NumParcialidad` |
  | Currency, equivalence | `MonedaDR`, `EquivalenciaDR` |
  | Previous balance | `ImpSaldoAnt` |
  | Amount paid | `ImpPagado` |
  | Outstanding balance | `ImpSaldoInsoluto` |

- **Retentions**: 21 documents have retentions. An `IVA Retenido` row shows the XML's `TotalImpuestosRetenidos` when present. No 4.0 document uses local taxes (`implocal`).

## R8. Labels and formats

**Decision**: Use legacy's Spanish labels from `Resources.resx`:
- Header: `Folio Fiscal`, `Certificado`, `Expedición`.
- Parties: `Receptor`, `Emisor`, `Régimen Fiscal`, `Uso CFDI`, `Dom. Fiscal (C.P.)`.
- Lines: `No.`, `Cantidad`, `UM`, `Código`, `Producto`, `Precio`, `Importe`.
- Payment: `Método de Pago`, `Forma de Pago`, `Moneda`, `Tipo de Cambio`.
- Totals: `Subtotal`, `Descuento`, `IVA`, `IVA Retenido`, `Total`.
- Related CFDIs: `CFDI Relacionados`, `Tipo de Relación`.
- Stamp block: `Certificado Digital SAT`, `Fecha de Certificación`, `RFC del proveedor de certificación`, `Cadena Original`, `Sello Digital del Emisor`, `Sello Digital del SAT`.
- Payment receipt: `Fecha del Pago`, `Núm. Parc.`, `Saldo Anterior`, `Importe Pagado`, `Saldo Insoluto`, `Importe Total`, `Referencia de Pago`, `Nombre del Banco Ordenante (Extranjero)`.

Value formats, following legacy:

| Value | Format |
|---|---|
| Quantity | `0.####` |
| Unit price and amount | `#,###.00##` |
| Money totals | `$1,234.56` (spec 019 `money`) |
| Folio | `{series} {folio:06d}` |
| Dates | `yyyy-MM-dd` |
| Stamp date | `yyyy-MM-dd HH:mm:ss` |
| Unit | `{ClaveUnidad} / {Unidad}` |
| Regime, use | `{code} {description}` |
| Payment form | `{code:02d} : {name}` |
| Payment method | `PUE : Pago en una sola exhibición` / `PPD : Pago en parcialidades o diferido` |

Seals and the original string wrap anywhere (`overflow-wrap: anywhere`), which is legacy's zero-width-break template done in CSS. The amount in words uses spec 019's corrected `amount_in_words`: the payment amount for type 200, the total otherwise, in the XML's currency.

## R9. Routes, errors and file names

**Decision**:
- **Privilege**: every route requires `SystemObject.FISCAL_DOCUMENTS` READ.
- **Errors**:

  | Case | Response |
  |---|---|
  | Unknown id | 404 `Fiscal document not found` |
  | PDF or XML of a document never issued (`completed = 0`) | 409 `Fiscal document has not been issued` |
  | PDF of a version other than 4.0 | 409 `Only CFDI 4.0 documents can be printed` |
  | PDF of an issued 4.0 document with no stamp UUID | 409 `Fiscal document is not stamped` |
  | Issued document whose XML is missing, or (for the PDF) cannot be parsed | 409 `Stamped XML is missing or unreadable` |

  The checks run in the order of the table. Versions 2.0 and 2.2 were issued as CFDs, which were sealed by the issuer but never stamped, and each has its XML. So the XML download needs only `completed = 1`, while the PDF also needs version 4.0 and a stamp. No issued 4.0 document lacks a stamp today; the check guards the case anyway.

- **File name**: `{issuer}-{batch}{serial:06d}`, as legacy's `Resources.resx:1316`. The PDF is `inline`; the XML is `attachment` with `application/xml`.
- **XML body**: the stored text encoded as UTF-8, unchanged.

**Rationale**: This matches the codebase's convention of plain-string `detail` 409s (about 55 uses), and mbe-ui can tell the cases apart by text. `FISCAL_DOCUMENTS = 23` already exists in `SystemObject`.

**Alternatives considered**: 422 for the unsupported version. Rejected: nothing about the request is invalid. It is the document's state that prevents printing.

## R10. The list

**Decision**:
- **Parameters**: `search`, `issuer`, `type`, `status` (`draft` | `stamped` | `cancelled`), `date_from`/`date_to` (`LocalDateTime`, on `issued`), `skip`, `limit` (default 20, max 100).
- **Order**: newest first by `fiscal_document_id`, as the other lists.
- **Search**:
  - letters followed by digits, as in `GR4776` or `GR 4776`: batch and serial;
  - digits only: serial;
  - a UUID: `stamp_uuid`;
  - anything else: `ilike` on `recipient` and `recipient_name`.
- **Total**: read from the XML of the rows on the page. `null` when a row has no XML.
- **Status**:
  - `cancelled` is `cancelled = 1`;
  - `issued` is `completed = 1` and not cancelled;
  - `draft` is `completed = 0` and not cancelled.

  This is legacy's own rule: `IsCompleted` decides whether the stamped layout is used. Every never-issued document is a version 0.0 draft (1,454 rows, 1,446 of them cancelled), apart from 12 version 2.0 rows.
- **Scope**: there is no default facility scope.

**Rationale**:
- No totals column exists. Parsing at most 100 small XMLs per page is cheap: stored 4.0 XMLs average 5.9 KB, and the largest is 50 KB.
- The attribute's case depends on the version. CFDI 3.3 and 4.0 write `Total`. CFD 2.0/2.2 and CFDI 3.2 write `total`, on all 23,497 of those documents. The reader tries `Total`, then `total`.
- The list follows `sales_orders` for its parameters and the `both()` count helper, with one difference: fiscal documents are per issuer, not per facility, so the list isn't filtered to the caller's facility by default.

**Alternatives considered**:
- Computing the total in SQL from the detail rows: this re-implements legacy's tax arithmetic in a query.
- `REGEXP_SUBSTR` on the XML: MariaDB-only, and the integration tests run on SQLite.
- Adding a totals column: a schema change for a read feature.
