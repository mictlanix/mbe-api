# Phase 1 Data Model: Facility Catalog Gaps

**No schema change.** Every table and column already existed. What changes is which of them
the API exposes, and in what shape.

## Taxpayer entity (`taxpayer_issuer`)

Modelled but previously unreachable — no schema, no service, no endpoint.

| Field | Type | Exposed | Notes |
|-------|------|---------|-------|
| `taxpayer_issuer_id` | tax identifier, 12–13 chars | yes (key) | The RFC. Natural primary key |
| `name` | text, optional | yes | Legal name |
| `regime` | reference to tax regime | yes, expanded | Rendered as code + description |
| `postal_code` | reference to postal code, optional | yes, expanded | Rendered as code + description |
| `provider` | certification provider | yes | Newly typed; see below |
| `comment` | text, optional | yes | |

**Certification provider** — a fixed set carried over from the legacy system and previously
documented but not enforced: none, Diverza, FiscoClic, Servisim, ProFact. Values outside the
set are rejected on write.

**Referenced by**: facilities, certificates, fiscal batches, fiscal documents. A deletion is
refused while any of them still points at it.

## Facility — address representation

| Context | Before | After |
|---------|--------|-------|
| Facility as the subject of a response | address key only | full address object |
| Facility embedded in a warehouse / point of sale / cash drawer | address key only | **unchanged** — still the key |

The expanded value is attached under a separate key from the stored one, so the two contexts
can be served from the same underlying record within a request without one corrupting the
other. The same technique already protected the facility's postal location.

## Point of sale — the pairing rule

A point of sale holds two independent references: a facility, and a warehouse that itself
belongs to a facility. The rule is that the two facilities must be the same.

| Amendment | Validated? |
|-----------|-----------|
| Create | Always |
| Amend the warehouse | Yes, against the point of sale's current facility |
| Amend the facility | Yes, against the point of sale's current warehouse |
| Amend both | Yes, against each other |
| Amend neither | No — an unrelated edit never re-validates |

Two distinct failures are distinguished: a warehouse that does not exist at all, versus one
that exists but belongs elsewhere. Collapsing them would leave a client unable to tell a typo
from a policy violation.

## State transitions

None. Lifecycle `status` is unaffected.
