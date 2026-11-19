# Data Model: Connect stamping and cancellation to the real PAC

**Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

Migration 037 replaces the simulator's table and rewrites the old simulator's provider RFC. Two existing transitions change when they happen.

## Configuration (not stored)

**PAC account**: one per instance. It is held in settings and never in the database. See [contracts/configuration.md](contracts/configuration.md).

## New table: `pac_simulator_store` (migration 037, API-only)

The library's simulated PAC keeps its state here (research R10). mbe-api never interprets the values.

| Column | Type | Notes |
|---|---|---|
| `store_key` | `VARCHAR(100)` PK | the library's key: `seal:<sha-256 hex>` per sealed document, `uuid:<UUID>` per stamp |
| `value` | `MEDIUMTEXT` NOT NULL | the library's JSON record. A seal record holds the stamped XML (up to ~68 KB). |
| `updated_at` | `DATETIME` NOT NULL | when the row was last written, Mexico City local time |

- **Rows:** a key appears with the first call that writes it, through the library's `set`. Rows are never deleted.
- **Sessions:** each read or write runs in its own committed session.

## Dropped table: `pac_simulation` (migration 034)

It is dropped without converting its rows (research R7).
- **Measured on 2026-11-18:** 21 rows, none with a queued outcome. No simulated document is `stamping`, and no cancellation request is open.
- **Rollback:** the rollback recreates it, empty.

## Stored values rewritten by migration 037 (mbe_dev)

| Table.column | Rows (2026-11-18) | Change |
|---|---|---|
| `fiscal_document.rfc_pac` | 17 (63907–63929), more if the UI stamps before the deploy | `SIM010101SIM` → `SIM010101SI0` |
| `fiscal_document_xml.data` of those documents | the same | `RfcProvCertif="SIM010101SIM"` → `RfcProvCertif="SIM010101SI0"` |

- **Check after the run:** no row carries the old value.
- **Rollback:** the rollback swaps both values back.
- **How a document counts as simulated:** it is simulated when `rfc_pac` equals the library simulator's RFC, `SIM010101SI0` (FR-021).

## State transitions that change

### A document left `stamping` (spec 039 R8), per provider

| Provider | Refresh before 5 min | Refresh after 5 min | Outcome |
|---|---|---|---|
| simulator (`resolves_by_resubmitting = False`) | look-up, no change if not found | look-up, sealed again if not found | unchanged from spec 039 |
| Multifacturas (`True`) | no PAC call, stays `stamping` | resubmits the stored sealed XML | stamp → issued; rejected → sealed (`pac.rejected`); unavailable or timeout → stays `stamping` |

**New incidence reason:** `CFDI stamp resolved {uuid}`, written when a resubmission stores the stamp.

### An open cancellation request when SAT answers valid with no cancellation

| Request status | Spec 039 | This spec |
|---|---|---|
| `requested` (no PAC answer) | `failed` after 5 min from `requested_at` | unchanged |
| `pending` (Multifacturas' 201, or the simulator's pending) | `failed` at once | `failed` only after 72 hours from `answered_at`. Before that, only `last_checked_at` changes. |

Every other SAT answer settles the request as in spec 039:
- **Cancelled:** `accepted`, or `expired` when SAT reports the receiver's deadline expired.
- **Valid and rejected:** `rejected`.
- **Valid and pending, on a `requested` request:** `pending`.
- **Not found:** no change.
