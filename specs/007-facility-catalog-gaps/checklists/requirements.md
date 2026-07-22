# Specification Quality Checklist: Facility Catalog Gaps

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

**Iteration 1 — 2 failures, corrected:**

1. *No implementation details* — the draft named endpoint paths, `422`/`404` codes, and the
   field names `FacilityResponse.address` / `FacilitySummary`. Restated in domain terms:
   "wherever the facility itself is the subject of the response" and "the compact
   representation embedded in another record". Paths and codes now live in `contracts/`,
   which is where they belong.
2. *Requirements testable* — "the address should be available" did not say where. Split into
   FR-008 (expanded on the facility's own responses) and FR-009 (unchanged where embedded),
   which are separately assertable and encode the product owner's scope decision.

**Iteration 2 — all items pass.**

### Notes on scope

No `[NEEDS CLARIFICATION]` markers were needed. The two decisions that would have required
them were put to the product owner before implementation:

- whether a warehouse may be shared across facilities (no — enforce it)
- how far the address expansion should go (the facility's own responses only)

Both are recorded in Assumptions along with their consequences. The second reversed the
reporting issue's stated request, which is why it is recorded rather than silently followed.

This spec was written after the implementation to record work already shipped in PR #103.
Two Assumptions capture decisions that are otherwise invisible in the code: the deliberate
extension from read-only to full CRUD, and the deployment prerequisite created by gating on a
permission nobody currently holds.
