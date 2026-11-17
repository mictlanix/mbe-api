# Data Model: CFDI stamping and cancellation

All changes are in migration **034** (`migrations/034_cfdi_stamping_cancellation.sql`, with its rollback). They are additive, and the current system ignores them. Applying the migration to mbe_dev needs confirmation.

## fiscal_document (existing, two new columns)

| Column | Type | Notes |
|---|---|---|
| `stamping_since` | `DATETIME NULL` | Set when a stamp request is about to reach the PAC (research R3). Cleared when the stamp is stored, or when the PAC rejects or is unavailable, or when a look-up past the 5-minute grace finds no stamp. A non-null value means **stamping** |
| `cancelling_since` | `DATETIME NULL` | Set with a cancellation request, in the same transaction (research R8). Cleared when that request closes (accepted, expired, rejected, not cancellable, failed). A non-null value on a completed, uncancelled document means **cancellation_pending**. It keeps the in-flight state on the row a lock delivers (R15) |

**Existing columns this feature writes:**
- **On stamp:** `completed`, `stamp_uuid`, `stamped`, `authority_digital_seal` (TFD `SelloSAT`), `authority_certificate_number` (TFD `NoCertificadoSAT`) and `rfc_pac` (TFD `RfcProvCertif`). `fiscal_document_xml.data` becomes the stamped XML.
- **On cancel:** `cancelled`, `cancellation_date`, `cancellation_reason` (`'01'`–`'04'`) and `cancellation_substitution` (a UUID, for 01).

These are the columns the current system fills, so its screens read documents that the API stamped and cancelled.

### States (derived, in this order)

| State | Condition |
|---|---|
| cancelled | `cancelled = 1` |
| cancellation_pending | `completed = 1` and `cancelling_since IS NOT NULL` |
| issued | `completed = 1` |
| stamping | `stamping_since IS NOT NULL` |
| sealed | an XML row exists |
| draft | otherwise |

```text
draft ──confirm──▶ sealed ──stamp──▶ stamping ──stamp stored──▶ issued
  ▲                 │  ▲               │
  └────reopen───────┘  └──rejected / unavailable / not found (past grace)
issued ──cancel──▶ (request) ──accepted──▶ cancelled
                       ├──pending──▶ cancellation_pending ──SAT cancelled──▶ cancelled
                       │                    └──rejected / not received──▶ issued
                       └──not cancellable / rejected / unavailable──▶ issued
```

A timeout leaves the document stamping (on stamp) or cancellation_pending (on cancel). Only a refresh moves it on.

## fiscal_document_cancellation (new)

One row per cancellation request.

| Column | Type | Notes |
|---|---|---|
| `fiscal_document_cancellation_id` | `INT AUTO_INCREMENT PK` | |
| `fiscal_document` | `INT NOT NULL` FK → `fiscal_document` | |
| `reason` | `VARCHAR(2) NOT NULL` FK → `sat_reason_cancellation` | `01`–`04` |
| `substitution` | `VARCHAR(36) NULL` | Required for 01; must be a stamped, live document of the same issuer and recipient. NULL for 02–04, where a substitute sent is ignored |
| `status` | `VARCHAR(20) NOT NULL` | One of the values below |
| `pac_code` | `VARCHAR(20) NULL` | When the PAC rejected the request |
| `pac_message` | `VARCHAR(500) NULL` | Same |
| `requested_by` | `INT NOT NULL` FK → `employee` | The user who requested the cancellation |
| `requested_at` | `DATETIME NOT NULL` | |
| `answered_at` | `DATETIME NULL` | The PAC's first answer: accepted, pending, not cancellable or rejected |
| `resolved_at` | `DATETIME NULL` | When the status became final |
| `last_checked_at` | `DATETIME NULL` | The last status query, whatever it answered |
| `open_document` | `INT AS (IF(status IN ('requested','pending'), fiscal_document, NULL)) VIRTUAL` | **UNIQUE**: at most one open request per document. Same technique as migration 033 |

`status` values:

| Value | Open | Meaning |
|---|---|---|
| `requested` | yes | Sent; the answer is unknown (timeout) |
| `pending` | yes | SAT waits for the receiver |
| `accepted` | no | The document is cancelled |
| `expired` | no | The receiver never answered and SAT cancelled the document |
| `rejected` | no | The receiver or the PAC rejected the request (see `pac_code`) |
| `not_cancellable` | no | SAT reports the document cannot be cancelled |
| `failed` | no | The PAC was unavailable, or SAT never received the request |

Index: `(status, requested_at)`, which the job uses.

## pac_simulation (new, used only by the simulated provider)

| Column | Type | Notes |
|---|---|---|
| `pac_simulation_id` | `INT AUTO_INCREMENT PK` | |
| `issuer` | `VARCHAR(13) NOT NULL` | From the sealed XML |
| `batch` | `VARCHAR(10) NULL` | Same; `''` when the CFDI has no series. NULL for a row created from a UUID it never stamped |
| `serial` | `INT NULL` | Same. **UNIQUE** (`issuer`, `batch`, `serial`); rows with NULLs don't collide |
| `uuid` | `CHAR(36) NULL UNIQUE` | Set once stamped |
| `stamped_at` | `DATETIME NULL` | NULL for a row created from a UUID it never stamped; the default cancellation rule then answers `pending` |
| `stamped_xml` | `MEDIUMTEXT NULL` | |
| `sat_status` | `VARCHAR(10) NOT NULL DEFAULT 'valid'` | `valid` or `cancelled` |
| `cancellation_status` | `VARCHAR(10) NOT NULL DEFAULT 'none'` | `none`, `requested`, `pending`, `accepted`, `rejected`, `expired` |
| `next_stamp` | `VARCHAR(20) NULL` | One-shot outcome (research R6) |
| `next_cancellation` | `VARCHAR(20) NULL` | One-shot outcome |
| `pac_code` | `VARCHAR(20) NULL` | For a chosen `rejected` outcome |
| `pac_message` | `VARCHAR(500) NULL` | Same |
| `updated_at` | `DATETIME NOT NULL` | |

The table exists on every instance, and stays empty unless `pac_provider = simulated`.

## Settings (app/core/config.py)

| Setting | Type | Default | Notes |
|---|---|---|---|
| `app_env` | `development \| staging \| production` | `production` | Fails closed (research R5) |
| `pac_provider` | `none \| simulated` | `none` | `none`: stamp, cancel and refresh answer `pac.not_configured`. Step 3 adds the real value |

## Response fields (app/schemas/fiscal.py)

- **`FiscalDocumentSummary.status`** gains `stamping` and `cancellation_pending`.
- **`FiscalDocumentResponse`** gains:
  - `stamp` (`FiscalDocumentStamp`): `{uuid, stamped, sat_certificate, provider_rfc, simulated}`, or `null` before stamping. `stamped` repeats the top-level field on purpose, so the object is complete on its own;
  - `stamping_since`;
  - `cancellation_substitution`;
  - `cancellations`: a list, newest first, of `{id, reason, substitution, status, pac_code, pac_message, requested_by, requested_at, answered_at, resolved_at, last_checked_at}`.

## Validation rules (from the spec)

- Stamp: sealed only. Re-checks the 72-hour issue date.
- Cancel: issued only, with no open request.
  - The reason is in `sat_reason_cancellation`.
  - 01 needs a valid substitute.
  - An invoice must have no live related receipt or credit note.
- A payment receipt or credit note cannot add or confirm against an invoice with an open request.
- `open_document` is not mapped in the model, as `sales_order_payment.live` (migration 033) is not. The SQLite tests build the schema from the models, so there the at-most-one rule rests on the `cancellation_open` check; MariaDB also enforces it with the unique index.
- Refresh: stamping, or an open request.
