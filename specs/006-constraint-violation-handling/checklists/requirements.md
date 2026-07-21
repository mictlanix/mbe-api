# Specification Quality Checklist: Constraint Violation Handling

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-21
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

- Items marked incomplete require spec updates before `/speckit-clarify` or `/speckit-plan`

### Validation history

**Iteration 1 — 3 failures, all corrected:**

1. *No implementation details* — the first draft named `409`, `500`, `IntegrityError`,
   SQLAlchemy metadata, and the four columns (`warehouse.code`, `point_sale.code`,
   `cash_drawer.code`, `vehicle.license_plate`) throughout the requirements. All were
   restated as behaviour: "refused as a conflict", "reported as a server fault", "covered
   automatically as the data model grows". The status codes survive only in the **Input**
   line, which quotes the original request verbatim, and the four records are named in
   User Story 2 as the affected catalogs rather than as columns.
2. *Success criteria technology-agnostic* — a draft criterion read "the OpenAPI spec is
   byte-identical", which is both a tool name and an implementation artifact. Restated as
   SC-007: "the published interface contract is identical before and after".
3. *Requirements testable* — "the error should be helpful" was unfalsifiable. Split into
   FR-003 (names relationships and counts), FR-004 (distinguishes roles) and FR-005
   (ordering and truncation), each of which a test can assert.

**Iteration 2 — all items pass.**

### Notes on scope

No `[NEEDS CLARIFICATION]` markers were needed: the two decisions that would otherwise have
required them — whether soft delete was acceptable, and whether the owned-child cascades
were exempt — were both settled by the product owner before the spec was written, and are
recorded in Assumptions.

This spec was written **after** the implementation (PR #108) to record a policy decided
during that work. The requirements were derived from the decision and the behaviour, not
retrofitted to the code: FR-004 and FR-005 in particular describe behaviour that was only
discovered to be necessary when the implementation was run against production-shaped data.
