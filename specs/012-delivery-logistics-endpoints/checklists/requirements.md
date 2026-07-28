# Specification Quality Checklist: Delivery & Logistics Endpoints

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-26
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed

## Requirement Completeness

- [x] No [NEEDS CLARIFICATION] markers remain
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable
- [x] Success criteria are technology-agnostic (no implementation details)
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Scope is clearly bounded
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] Feature meets measurable outcomes defined in Success Criteria
- [x] No implementation details leak into specification

## Notes

**Deliberate deviation on "no implementation details".** The Context, Assumptions and Divergences
sections name existing tables, columns and repository files (`delivery_order.confirmed`,
`app/models/logistics.py`, the legacy `WebConfig` values). This is intentional and matches the
house style set by spec 011: the brief was explicitly *"the current state of this repo takes
precedence over docs"*, and a reconciliation between two conflicting source documents and a live
schema cannot be stated without naming what it reconciles. The User Scenarios, Functional
Requirements and Success Criteria sections stay in domain language — statuses, quantities, folios,
proof of delivery — and are readable without the repository open. FR-001 names the three legacy
booleans it replaces because *which* flags disappear is a business-visible consequence, not an
implementation choice.

**Grounding checks performed against the repository while validating:**

- `CurrentUser.administrator` exists — FR-019's lead-time waiver is real, not invented.
- `point_sale.warehouse` exists — FR-033's dispatch-warehouse default is real.
- `lot_serial_rqmt` exists with source / reference / warehouse / product / quantity — the
  reservation assumption behind FR-055 reuses it rather than adding a table.
- All five logistics `SystemObject` values are already enumerated — FR-066 invents no privileges.
- Migration 005 added `status` and dropped each legacy boolean — the precedent the
  boolean-replacement assumption cites.

**Open items carried into planning (not blockers):**

- The inventory change in FR-055 through FR-057 supersedes two acceptance scenarios delivered by
  spec 011. `/speckit-plan` must budget for reworking `sales_order_service.confirm_order` and
  `cancel_order` plus their existing tests, and the plan's Complexity Tracking table should record
  the cross-feature impact.
- The ledger's transaction-source enumeration has no value for a delivery movement; a new one is
  needed for the in-transit entries.
- ~~The reason-code set named in the Assumptions is stated as a default, not elicited from the
  user.~~ **Resolved** in the 2026-07-26 clarification session and promoted to FR-045a.

**Added by the 2026-07-26 clarification session** (all 16 items still pass):

- Legacy data settles rather than migrates: 26,763 delivery orders become terminal and the
  pending queue starts empty; the reservation model applies only to sales orders confirmed after
  this ships, so the 178,045 already-confirmed ones keep their posted outbound entries. This
  removes what would otherwise have been the largest unbounded risk in the migration.
- Proof-of-delivery images are authenticated (FR-044a) rather than reusing the public static
  mount, satisfying Constitution VII. FR-044b additionally requires that one order's proof cannot
  be pulled out from under another — a real hazard, since the existing image store is
  content-addressed and deduplicates identical files.
- v2's "notify creator on rejection" is deliberately deferred with the rationale recorded; the
  discoverability substitute is specified instead (FR-067, US2 scenario 4).
- FR-070 and FR-071 add sales-order coverage as a derived figure *and* maintain the stored
  `delivered` flag, so the cross-feature contract with spec 011 is explicit rather than implied.
