# Phase 0 Research: Facility Catalog Gaps

## R1 — How far should the address expansion go?

**Decision**: Expand on `FacilityResponse` only. `FacilitySummary.address` stays a bare key.

**Rationale**: The reporting issue asked for both. But `FacilitySummary` is the deliberately
flat shape embedded in warehouse, point-of-sale and cash-drawer responses, and its `location`
is flat there too. Expanding it would add an address fetch to every one of those list
endpoints — spending the cost this feature exists to remove. The product owner chose the
narrower scope over the request as filed.

**Alternatives considered**: Expanding both, as asked — rejected on the above. Adding an
`?expand=` parameter — rejected as configurability nobody requested (Principle I).

## R2 — What should gate the taxpayer resource?

**Decision**: `SystemObject.TAXPAYERS` (24) on every route, read included.

**Rationale**: The obvious model was the sibling `/taxpayer-recipients`, which requires only
an authenticated session. But recipients are customer billing details, whereas issuers are the
entities that sign fiscal documents, and the resource gained write endpoints (R4). `TAXPAYERS`
has existed in the permission enum since the legacy system and governed nothing.

**Consequence accepted**: because it governed nothing, no `access_privilege` row grants it, so
no non-administrator can reach these endpoints until it is granted. This is a deployment
prerequisite, recorded in the changelog and in spec Assumptions. It was deliberately not
softened by leaving reads ungated: a half-gated resource is harder to reason about than one
that needs a permission granted once.

## R3 — Why did the point-of-sale rule require an unrelated fix?

**Decision**: Convert `point_sale_service._attach_relations` to write `facility_detail` /
`warehouse_detail` before adding the rule.

**Rationale**: The expansion was overwriting the mapped `facility` and `warehouse` columns
with ORM objects. The update path must compare the *raw* foreign keys — `ps.facility` after a
fetch was a `Facility` instance, not an integer, so the rule could not be written correctly
without fixing this first. This is the same identity-map hazard as issue #95, which had been
fixed for warehouses only.

**Wider consequence**: the same pattern survived in seven other services. Rather than expand
this feature, the audit was filed as issue #104 and fixed separately, keeping the change
surgical (Principle III).

## R4 — Read-only or full CRUD for taxpayer entities?

**Decision**: Full CRUD.

**Rationale**: The issue asked for read only — enough to turn a typed field into a picker.
Read-only would have left the entity creatable by no one, so the catalog a client can now
search could never gain an entry through the API. Half-exposing a resource is the kind of
asymmetry that generates the next issue.

**Recorded as**: a deliberate scope extension in spec Assumptions, not a silent one.

## R5 — What is `provider`, and should it be exposed?

**Decision**: Expose it, typed by a newly ported enum.

**Rationale**: It was the only column the read-only draft omitted. `docs/constants.md` already
documented it as `FiscalCertificationProvider` — the certification provider used for stamping
fiscal documents — carried over from the legacy C# constants but never ported to
`app/enums.py`. Typing it means unknown values are rejected on write rather than persisted.

**Not done**: no `server_default` was added to the model column. The database column is
already `NOT NULL` with no default, and declaring one without a migration would be misleading
metadata.

## R6 — What happens when a taxpayer entity is deleted?

**Decision**: Refuse with a conflict, naming what still references it.

**Rationale**: Four tables reference `taxpayer_issuer` — facilities, certificates, fiscal
batches and fiscal documents. The database would reject the delete anyway, but as a driver
error surfacing to the client as a server fault, since the project had no handler for that at
the time.

**Superseded by**: the hand-written guard added here was replaced by the shared guard in
feature 006 (#104/#107), which found that this one was in the correct shape but that the
equivalent guard on price lists checked only one of its two referencing tables.
