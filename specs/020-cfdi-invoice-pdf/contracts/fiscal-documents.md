# Contract: `/fiscal-documents`

Mounted at `/api/v1/fiscal-documents`, tag `fiscal-documents`. Every route requires a valid token (401 otherwise) and `SystemObject.FISCAL_DOCUMENTS` READ (403 otherwise). All routes are read-only.

## `GET /fiscal-documents`

| Query | Type | Meaning |
|---|---|---|
| `search` | str | `GR4776` / `GR 4776` → batch + serial; digits → serial; a UUID → `stamp_uuid`; otherwise recipient RFC or name contains |
| `issuer` | str | issuer RFC |
| `type` | int | legacy `FiscalDocumentType` code |
| `status` | `draft` \| `issued` \| `cancelled` | |
| `date_from`, `date_to` | LocalDateTime | on `issued`, inclusive |
| `skip` | int ≥ 0 | default 0 |
| `limit` | 1–100 | default 20 |

200: `ListResponse[FiscalDocumentSummary]`, newest first. 422 for an unknown `status`.

## `GET /fiscal-documents/{id}`

200: `FiscalDocumentResponse`. 404 `Fiscal document not found`.

## `GET /fiscal-documents/{id}/xml`

200: the stored XML, unchanged, as UTF-8.
- `Content-Type: application/xml`
- `Content-Disposition: attachment; filename="{issuer}-{batch}{serial:06d}.xml"`

OpenAPI 200: `{"content": {"application/xml": {"schema": {"type": "string", "format": "binary"}}}}`, with no JSON entry.

| Status | `detail` |
|---|---|
| 404 | `Fiscal document not found` |
| 409 | `Fiscal document has not been issued` |
| 409 | `Stamped XML is missing or unreadable` (no XML row) |

## `GET /fiscal-documents/{id}/pdf`

200: a letter-size PDF.
- `Content-Type: application/pdf`
- `Content-Disposition: inline; filename="{issuer}-{batch}{serial:06d}.pdf"`

OpenAPI 200: `rendering.PDF_RESPONSE`, i.e. `{"application/pdf": {"schema": {"type": "string", "format": "binary"}}}`, with no JSON entry.

Checked in this order:

| Status | `detail` |
|---|---|
| 404 | `Fiscal document not found` |
| 409 | `Fiscal document has not been issued` |
| 409 | `Only CFDI 4.0 documents can be printed` |
| 409 | `Fiscal document is not stamped` |
| 409 | `Stamped XML is missing or unreadable` (no XML row, or it does not parse) |

## PDF content (FR-005 to FR-015, FR-022)

The labels are from research R8, and every value is from the stamped XML unless research R1 says otherwise.

1. **Header**: logo (when found), title (`Factura`, `Nota de Crédito`, `Aplicación de Anticipos`, `Recibo Electrónico de Pago`, …), `{batch} {serial:06d}`, `Certificado` (`NoCertificado`), `Folio Fiscal` (UUID), `Expedición` (`C.P. {LugarExpedicion} / {Fecha yyyy-MM-dd}`).
2. **Receptor**: RFC, name, `Dom. Fiscal (C.P.)`, `Uso CFDI`, `Régimen Fiscal`.
3. **Emisor**: RFC, name, `Régimen Fiscal`, `Dom. Fiscal (C.P.)`. Also the QR code, 29 mm, with the payload `https://verificacfdi.facturaelectronica.sat.gob.mx/default.aspx?id=…&re=…&rr=…&tt=…&fe=…`.
4. **Concepts** (non-payment types): `No.`, `Cantidad`, `UM`, `Código`, `Producto` (description, `(NoIdentificacion)`, comment), `Precio`, `Importe`. Then `Método de Pago`, `Forma de Pago`, `Moneda` (with `Tipo de Cambio` when not MXN), `Subtotal`, `Descuento` (non-zero), `IVA`, `IVA Retenido` (when present), `Total`.
5. **Payments** (payment receipts): per settled document `No.`, `Folio Fiscal`, series and folio, `Núm. Parc.`, currency (with equivalence when it differs), `Saldo Anterior`, `Importe Pagado`, `Saldo Insoluto`. Then `Fecha del Pago`, `Nombre del Banco Ordenante (Extranjero)` (when present), `Forma de Pago`, `Referencia de Pago` (when present), `Moneda`/`Tipo de Cambio`, `Importe Total`.
6. **Amount in words**.
7. **CFDI Relacionados** (non-payment types with relations): `Tipo de Relación` label and one numbered UUID per related document.
8. **Stamp block**: `Certificado Digital SAT`, `Fecha de Certificación`, `RFC del proveedor de certificación`, `Cadena Original`, `Sello Digital del Emisor`, `Sello Digital del SAT`.
9. **Footer, every page**: `Banco: … Cuenta: … CLABE: … {Currency}` per account, `FacturaElectronicaSimple.mx`, `Este documento es una representación impresa de un CFDI.`, `Página N de M`.
10. **Cancelled**: `CANCELADO` and the cancellation date on every page.
