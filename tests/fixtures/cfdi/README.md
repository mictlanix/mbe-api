# CFDI 4.0 fixtures

Stamped CFDI 4.0 documents copied from `mbe_dev` on 2026-09-30 and anonymized, one per case the printed representation handles (#230, spec 020):

| File | Case |
|---|---|
| `invoice.xml` | Invoice (type 0) with a discount and several concepts |
| `invoice_retention.xml` | Invoice with `Retenciones` |
| `credit_note.xml` | Credit note (type 100), `TipoRelacion` 01 |
| `advance.xml` | Applied advance (type 101), `TipoRelacion` 07 |
| `payment.xml` | Payment receipt (type 200), Pagos 2.0 with `NumOperacion` |

## Anonymization

Replaced, the same way in every file:

- Emisor: `Rfc` → `EKU9003173C9`, `Nombre` → `EMPRESA DEMO`.
- Receptor: `Nombre` → `CLIENTE DEMO`, `DomicilioFiscalReceptor` → `06000`.
- Receptor `Rfc`:
  - `invoice.xml`: `XAÑ010101AB1`, a `Ñ`, which the QR code keeps raw;
  - `payment.xml`: `A&A010101AB1`, an `&`, which the QR code writes as `%26`;
  - the rest: `CAC010101AB2`.
- `LugarExpedicion` → `06000`.
- `Certificado` → `ANONIMIZADO`.
- `NumOperacion` → `000123`.
- Every UUID (the stamp, related CFDIs, settled documents) → a UUIDv5 of the original. The same UUID maps to the same value.

Kept as stamped: seals, certificate numbers, codes, descriptions, quantities and amounts. The seals no longer match the data, but nothing verifies them: the printout only reproduces them.
