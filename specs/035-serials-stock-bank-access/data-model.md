# Data Model: Supplier Sub-Panels by Privilege, Race-Safe Serials and Stock

No table or column is added. Migration 030 adds three indexes and fills 20 payment serials (research R7).

## Supplier detail (response only)

`SupplierDetailResponse` keeps its shape. What changes is who receives each panel's rows (FR-001):

| Panel | Rows returned when the caller holds | Otherwise |
|---|---|---|
| `bank_accounts` | Bank Accounts (13) read | `[]` |
| `contacts` | Contacts (12) read | `[]` |
| `addresses` | Addresses (11) read | `[]` |
| `agreements` | Suppliers (3) read | `[]` |

- Administrators receive all four.
- On `POST` and `PUT`, `addresses`, `contacts` and `bank_accounts` for a panel the caller cannot read are treated as omitted. An omitted list leaves the links unchanged, as it does today (FR-003).

## Document serial (`serial` on each numbered table)

- **Numbered per facility by `documents.assign_folio`:** `sales_order`, `sales_quote`, `delivery_order`, `customer_refund`, `supplier_payment`, and now `customer_payment`.
- **Value:** `MAX(serial) + 1` over the table's rows of that facility, read after the facility row lock with no ordinary read before it (research R2).
- **Written last,** after every check. A refusal rolls back, so it consumes nothing.
- **Unique `(facility, serial)` index:** on every table above except `customer_payment`. Those indexes stay as they are.

## Customer payment serial

| | Before | After |
|---|---|---|
| A payment the API writes (`create_payment`, a refund's credit-note or cash payout) | `0` | the facility's next payment serial |
| The 20 API payments with `0` on mbe_dev (facility 51, ids 300815–300834) | `0` | 18,714–18,733 in id order, or the next free serials if more were written meanwhile |
| The current system's rows | unchanged | unchanged, duplicates included |
| Index | `facility` alone | new `customer_payment_facility_serial_idx (facility, serial)`, non-unique |

- **Shared sequence:** the sequence is per facility, across every payment type, and continues the current system's (Key Entities).
- **Search:** a numeric `search` on the payment list matches `customer_payment_id` or `serial`.

## Ledger (`lot_serial_tracking`)

New index `lot_serial_tracking_source_reference_idx (source, reference)`. It serves an inventory document's ledger lookups: the cancel reversal and `_ledger_warehouses` (FR-022).

## Reservations (`lot_serial_rqmt`)

New index `lot_serial_rqmt_source_reference_idx (source, reference)`. It serves `release_reservations` and the reservation lookups of departure and return (research R7).

## Lock order

The rows each numbering step locks, and their order, are in research R3. In short: cash session → own documents → lines `FOR SHARE` → facility rows (ascending) → warehouses (ascending, one statement) → ordinary reads.
