# Data Model: CFDI Invoice PDF

**Terms**: the spec's *series* is the `batch` column and its *folio* is `serial`. Both are printed as `{batch} {serial:06d}`.

There is no schema change and no migration. Every table already exists and is read only. The new pieces are the response schemas, one parsed-XML structure and the render context.

## Existing tables read

| Table | Model (`app/models/`) | Used for |
|---|---|---|
| `fiscal_document` | `fiscal.FiscalDocument` | list, detail, status, the PDF's database-only values (R1) |
| `fiscal_document_detail` | `fiscal.FiscalDocumentDetail` | detail lines, PDF line comments |
| `fiscal_document_xml` | `fiscal.FiscalDocumentXml` | the XML download and every printed value |
| `taxpayer_batch` | `fiscal.TaxpayerBatch` | logo, footer height, bank accounts (R5) |
| `sat_tax_regime` | `sat_catalog.SatTaxRegime` | the recipient regime's description |
| `sat_cfdi_usage` | `sat_catalog.SatCfdiUsage` | the CFDI use description |

`fiscal_document_xml.fiscal_document_xml_id` is also the document's id: one XML per document, with the same key.

## Status (derived, never stored)

| `status` | Rule |
|---|---|
| `cancelled` | `cancelled = 1` |
| `issued` | `completed = 1` and not cancelled |
| `draft` | `completed = 0` and not cancelled |

## Response schemas (`app/schemas/fiscal.py`)

**`FiscalDocumentSummary`**: one row in the list.

| Field | Type | Source |
|---|---|---|
| `fiscal_document_id` | int | |
| `type` | int | legacy `FiscalDocumentType` code (0, 100, 101, 200, …) |
| `version` | Decimal | |
| `batch`, `serial` | str \| None, int \| None | |
| `issuer`, `issuer_name` | str, str \| None | |
| `recipient`, `recipient_name` | str \| None | |
| `issued` | LocalDateTime \| None | |
| `stamp_uuid` | str \| None | |
| `status` | `draft` \| `issued` \| `cancelled` | derived |
| `total` | Decimal \| None | the XML root's `Total` (CFDI 3.3/4.0), or `total` (CFD 2.0/2.2, CFDI 3.2); `None` without XML |

**`FiscalDocumentLine`**: `fiscal_document_detail_id`, `product_service`, `product_code`, `product_name`, `unit_of_measurement`, `unit_of_measurement_name`, `quantity`, `price`, `discount`, `tax_rate`, `tax_included`, `comment`.

**`FiscalDocumentResponse`**: the summary's fields plus `issuer_regime`, `issuer_regime_name`, `taxpayer_regime`, `taxpayer_postal_code`, `usage`, `payment_method`, `payment_terms`, `currency`, `exchange_rate`, `issued_location`, `reference`, `comment`, `stamped` (LocalDateTime), `cancellation_date` (LocalDateTime), `cancellation_reason`, and `lines: list[FiscalDocumentLine]`.

Every datetime is `LocalDateTime`, which `tests/unit/test_datetime_contract.py` enforces.

## Parsed XML (`app/services/cfdi.py`)

`parse(data: str) -> Cfdi` returns plain dicts and lists of attribute strings, keeping values exactly as stamped:

- `comprobante`: the root's attributes: `Serie`, `Folio`, `Fecha`, `Sello`, `FormaPago`, `NoCertificado`, `SubTotal`, `Descuento`, `Moneda`, `TipoCambio`, `Total`, `TipoDeComprobante`, `MetodoPago`, `LugarExpedicion`.
- `emisor`, `receptor`: their attributes.
- `conceptos`: one dict per `Concepto`.
- `traslados_total`, `retenciones_total`: `Impuestos/@TotalImpuestosTrasladados` and `@TotalImpuestosRetenidos` at the root level, or `None`.
- `relacionados`: a list of `(TipoRelacion, [UUID, …])`.
- `pagos`: a list of `{attrs, documentos: [attrs, …]}` from `pago20:Pago`.
- `timbre`: the `TimbreFiscalDigital` attributes.

Derived helpers:
- `tfd_original_string(timbre) -> str` (R2).
- `sat_qr_payload(cfdi) -> str` (R3).

**Validation**: a parse failure, or a missing `Comprobante` or `TimbreFiscalDigital`, raises `CfdiError`. Nothing else is validated: the XML was already validated by the PAC when it was stamped.

## Batch settings (`app/services/cfdi.py`)

`batch_settings(template: str | None) -> BatchSettings`, a small dataclass:

| Field | Default | From |
|---|---|---|
| `logo_name` | `None` | the file-name part of `Logo` |
| `footer_height_mm` | 15 | `FooterHeight`, when a non-negative number |
| `accounts` | `[]` | `ExtraInfo`: items with `Bank`, `Account`, `CLABE`, `Currency`, each read as a string |

`ast.literal_eval` failures and wrong types fall back to the defaults (R5).

## Render context (`app/services/print_contexts.py`)

`fiscal_document_context(db, document, xml) -> (template, context)`:
- `header`: `logo` (a `file://` URL or `None`), `title` from the type, and `folio`.
- `issuer`, `recipient`: the XML values, plus the regime and use descriptions (R1).
- `lines`: the XML concepts, formatted (R8), each with its `comment` when the counts match (R1).
- `totals`: subtotal, discount, IVA, IVA retained and total, from the XML.
- `payment`: method, form, currency and exchange rate.
- `related`: `[(label, [uuid, …])]` (R7).
- `payments`: for payment receipts (R7).
- `amount_in_words`: in the XML's `Moneda` (`MonedaP` for a payment receipt), mapped `MXN`/`USD`/`EUR` → `CurrencyCode`. Any other code falls back to MXN, as spec 019's `amount_in_words` does.
- `stamp`: date, SAT certificate, PAC RFC, original string, and both seals.
- `qr_data_uri`.
- `footer`: `accounts`, `height_mm`.
- `cancelled`: `None`, or `{'date': 'yyyy-MM-dd'}`, where the date is `None` when not recorded.
