# Specification Quality Checklist: Product Merge Integrity

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-07-25
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

1. *No implementation details* — the first draft named the tables throughout: `sales_order_detail`,
   `fiscal_document_detail`, `UPDATE IGNORE`, `referencing_columns`. Restated as behaviour —
   "every record that refers to the duplicate", "records describing something that happened",
   "records describing how a product is set up". The table names survive in
   [data-model.md](../data-model.md), where naming them is the point, and the
   [contract](../contracts/product-merge.md), which a client reads. The one exception left in the
   spec is the Assumptions' note on fiscal documents, because the snapshot argument cannot be made
   without saying what a stamped document carries.
2. *Success criteria measurable* — a draft criterion read "merges work reliably". Replaced by
   SC-001 and SC-003, which are counts against a real catalog: zero products that cannot be
   merged, against 13,248 of 21,542; zero records left pointing at a deleted product.
3. *Requirements testable* — "a merge should not lose data" was both unfalsifiable and, as
   written, false: FR-007 deliberately discards the duplicate's configuration. Split into FR-007
   (configuration is discarded), FR-008 (the canonical's is intact), FR-009 (the outcome does not
   depend on what the canonical already had) and FR-010 (history is never deleted), each of which
   a test asserts.

**Iteration 2 — all items pass.**

### Notes on scope

No `[NEEDS CLARIFICATION]` markers were needed, but two decisions in this spec would have
warranted them had they not already been settled during implementation, and both are recorded in
Assumptions rather than in requirements, because either answer would have been defensible:

- **Fiscal history follows the merge** instead of blocking it. The alternative protects a
  snapshot that was never at risk, at the cost of 10,434 products.
- **The canonical's configuration wins in full**, losing a label or per-customer discount that
  existed only on the duplicate. The alternative — the `UPDATE IGNORE` behaviour that actually
  shipped in #112 — kept more data but produced an outcome that could only be described by
  naming which individual rows collided.

This spec was written **after** the implementation, recording behaviour delivered across PRs #114
and #115. The requirements were derived from the decisions and the measurements, not retrofitted
to the code. FR-005 (no step may suppress its own failure) and FR-009 (the outcome may not depend
on what the canonical already had) in particular describe properties that were only discovered to
be necessary once the first fix was in place — the same pattern as feature 006, where
production-shaped data taught the requirement.
