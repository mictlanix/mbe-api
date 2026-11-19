# Data Model: Connect stamping and cancellation to the real PAC

**Spec**: [spec.md](spec.md) | **Research**: [research.md](research.md)

This spec adds no table and no column. It changes stored values once, through migration 035, and it changes when an existing transition happens.

## Configuration (not stored)

**PAC account**: one per instance. It is held in settings and never in the database. See [contracts/configuration.md](contracts/configuration.md).

## Stored values rewritten by migration 035 (mbe_dev)

| Table.column | Rows (2026-11-18) | Change |
|---|---|---|
| `fiscal_document.rfc_pac` | 17 (63907–63929) | `SIM010101SIM` → `SIM010101SI0` |
| `fiscal_document_xml.data` of those documents | 17 | `RfcProvCertif="SIM010101SIM"` → `RfcProvCertif="SIM010101SI0"` |
| `pac_simulation.stamped_xml` | ≤ 19 | the same replacement |

- **Rollback:** the rollback swaps both values back.
- **How a document counts as simulated:** it is simulated when `rfc_pac == SIMULATED_PAC_RFC`, which is unchanged logic with the new value.

## State transitions that change

### A document left `stamping` (spec 039 R8), per provider

| Provider | Refresh before 5 min | Refresh after 5 min | Outcome |
|---|---|---|---|
| simulator (`resolves_by_resubmitting = False`) | lookup, no change if not found | lookup, sealed again if not found | unchanged from spec 039 |
| Multifacturas (`True`) | no PAC call, stays `stamping` | resubmits the stored sealed XML | stamp → issued; rejected → sealed (`pac.rejected`); unavailable or timeout → stays `stamping` |

**New incidence reason:** `CFDI stamp resolved {uuid}`, written when a resubmission stores the stamp.

### An open cancellation request when SAT answers valid with no cancellation

| Request status | Spec 039 | This spec |
|---|---|---|
| `requested` (no PAC answer) | `failed` after 5 min from `requested_at` | unchanged |
| `pending` (PAC answered 201, or the simulator's pending) | `failed` at once | `failed` only after 72 hours from `answered_at`; before that, only `last_checked_at` changes |

Every other SAT answer settles the request as in spec 039:
- **Cancelled:** `accepted`, or `expired` when SAT reports the receiver's deadline expired.
- **Valid and rejected:** `rejected`.
- **Valid and pending, on a `requested` request:** `pending`.
- **Not found:** no change.
