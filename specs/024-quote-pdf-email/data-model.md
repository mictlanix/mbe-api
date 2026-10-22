# Data Model: Quotation PDF and Email

No migration and no new table.

## Read

| Table | Columns | Used for |
|---|---|---|
| `sales_quote` + `sales_quote_detail` | header, lines (through `attach_derived`) | the document |
| `facility`, `address` | name, logo, address | the document header and the email subject |
| `customer` | name, `credit_days` | the document's customer and terms |
| `contact` | name, mobile, phone, `phone_ext` | the document's contact |
| `address` (ship-to) | address fields, comment | the document |
| `employee` | `first_name`, `last_name` | "Elaborada por" and the email signature |

## Written

`incidence`: one row per successful email (research R5).

| Column | Value |
|---|---|
| `source` | `11`: the new `SourceType.SALES_QUOTE`, which is API-only like `FACILITY = 10` |
| `instance_id` | `sales_quote_id` |
| `updater` | the sender's `employee_id` |
| `modification_time` | local now |
| `comment` | `Quote emailed` |
| `content` | `To: …`, truncated to 1,000 characters |

## Not stored

- **Mail message:** recipients, subject, body and attachments. It is built per request and never persisted.
- **Mail settings:** configuration only (research R4).

## Quote state and the routes

| `completed` | `cancelled` | Document | Email |
|---|---|---|---|
| 0 | 0 (draft) | 200 | 409 |
| 1 | 0 (confirmed) | 200 | 200 |
| any | 1 (cancelled) | 200 | 409 |
