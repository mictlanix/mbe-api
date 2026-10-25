# Data Model: CFDI Invoices up to a Sealed Document

No migration. Every table already exists and is mapped in `app/models/fiscal.py`. This feature writes to `taxpayer_batch`, `fiscal_document`, `fiscal_document_detail`, `fiscal_document_relation` and `fiscal_document_xml`, and reads sales orders, refunds, products, prices, customers, recipients, facilities and certificates.

## TaxpayerBatch (`taxpayer_batch`)

| Field | Type | Rules |
|---|---|---|
| `taxpayer_batch_id` | int PK | |
| `taxpayer` | str(13) FK issuer | from the path |
| `batch` | str(10) | 1–10 characters, unique per issuer (existing unique index `taxpayer, batch`) |
| `type` | int | a `FiscalDocumentType`: 0, 1, 2, 3, 100, 101 or 200 |
| `template` | text | JSON: `Name`, `Logo`, `HeaderHeight`, `FooterHeight`, `ExtraInfo` (read by `cfdi.batch_settings`) |

**In use:** a `fiscal_document` exists with `issuer = taxpayer` and `batch = batch`. While a batch is in use, changing `batch` or `type`, or deleting it, gives 409.

## FiscalDocument (`fiscal_document`), as an invoice

The fields this feature sets, and the rules it applies (research R7, R10):

| Group | Fields | Set by |
|---|---|---|
| **Identity** | `type` = 0, `issuer`, `batch`, `serial` | create; `serial` at the first confirm |
| **Issuer copy** | `issuer_name`, `issuer_regime`, `issuer_regime_name` | create |
| **Recipient** | `customer`, `recipient`, `recipient_name`, `taxpayer_regime`, `taxpayer_postal_code` | create, header edit |
| **Store** | `facility`, `issued_at`, `issued_location` | create, header edit (needs privilege 103) |
| **Terms** | `payment_terms`, `payment_method`, `usage`, `currency` = MXN, `exchange_rate` = 1 | create, header edit |
| **Date** | `issued` | header edit, or confirm when null |
| **Text** | `reference` (≤ 25), `comment` (≤ 1000) | header edit |
| **Seal** | `version` = 4.0, `provider` = `issuer.provider` | confirm |
| **State** | `completed`, `cancelled` | not changed here (always false) |
| **Audit** | `creator`, `updater`, `creation_time`, `modification_time` | every write |

`retention_rate` and `local_retention_rate` are 0. `local_retention_name`, the stamp columns, the cancellation columns and `rfc_pac` stay null.

### States (research R1)

```
            create                 confirm                 stamp (#46)
  (none) ─────────▶ draft ───────────────▶ sealed ─────────────▶ issued
                     ▲                        │
                     └──────── reopen ────────┘
```

| State | completed | cancelled | XML row |
|---|---|---|---|
| draft | 0 | 0 | none |
| sealed | 0 | 0 | present |
| issued | 1 | 0 | present |
| cancelled | – | 1 | – |

- Only a draft accepts header, line and relation changes (FR-012).
- Confirm moves a draft to sealed, and reopen moves sealed back to draft.
- Stamping and cancellation are out of scope.

## FiscalDocumentDetail (`fiscal_document_detail`)

| Field | From an order line | Manual |
|---|---|---|
| `product` | the order line's | request |
| `order_detail` | the order line id | null |
| `product_service`, `unit_of_measurement`, `unit_of_measurement_name` | product | product |
| `product_code`, `product_name`, `comment` | order line | product's code and name |
| `quantity` | remaining quantity (R6) | 1 |
| `price` | order line | customer's price-list price |
| `discount` | order line `discount_rate` | 0 |
| `tax_rate`, `tax_included` | order line | product |
| `exchange_rate`, `currency` | document (1, MXN) | document |

**Rules (FR-011):**
- `quantity > 0`, and ≤ remaining for an order line;
- `price ≥ 0`;
- `0 ≤ discount ≤ 1`;
- `tax_rate ∈ {0.16, 0, -1}` (-1 means exempt).

An order line appears at most once per document (FR-006).

## FiscalDocumentRelation (`fiscal_document_relation`)

| Field | Value |
|---|---|
| `document` | the draft |
| `relation` | target `fiscal_document_id` |
| `type` | `'4'` or `'7'`, the stored form. The API speaks `04` and `07` |
| `installment`, `exchange_rate`, `previous_balance`, `amount` | 1, 1, 0, 0 |

**Targets (R5):**
- **04:** type < 100, completed and cancelled.
- **07:** type not 100 or 200, completed and not cancelled.

Both require the same issuer and recipient, and a `stamp_uuid`. All relations of a document share one type. `(document, relation)` is unique (existing index).

## FiscalDocumentXml (`fiscal_document_xml`)

`fiscal_document_xml_id` is the document id. `data` is the sealed CFDI 4.0 XML. The row is written at confirm and deleted at reopen; stamping (#46) will replace it with the stamped XML.

## Derived values, not stored

- **Remaining quantity** of an order line (R6): computed from invoice and refund events.
- **Status:** `draft | sealed | issued | cancelled` (R1).
- **Totals:** spec 020 reads them from the XML. A sealed document's XML carries them; a draft has none, so it shows `total = null`, as spec 020 does today.
