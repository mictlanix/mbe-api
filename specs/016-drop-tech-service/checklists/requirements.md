# Specification Quality Checklist: Retire Technical Service and Vehicle Service Orders

**Purpose**: Validate Companion specification completeness before planning
**Created**: 2026-08-30
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] Written for non-technical stakeholders
- [x] All mandatory sections completed (User Scenarios, Requirements, Success Criteria)

## Requirement Completeness

- [x] Any [NEEDS CLARIFICATION] markers are genuine ambiguities (≤3) deferred to clarify — not unresolved guesses (none remain; FR-007 resolved at the gate)
- [x] Each Functional Requirement is a single, testable MUST/SHOULD statement
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
- [ ] No implementation details leak into the specification

## Notes

**The one open ambiguity was resolved at the review gate**, so no `[NEEDS CLARIFICATION]` marker
remains and the clarify step has nothing to ask. FR-007 now records the decision — correct the
checked-in schema dump — along with the two rejected alternatives and the accepted cost, so the
reasoning survives the decision.

**On the last unchecked item.** The spec names the seven tables and the four identifiers literally,
under Verbatim Constraints, and the Key Entities section describes the schema derivation in terms a
reader outside this repository would not recognise. Both are deliberate: the table and entry names
are values the change must match exactly, which is what that section exists for, and the derivation
is the subject of the open question in FR-007, so it cannot be abstracted away without hiding the
decision. Judged an acceptable and bounded leak rather than a defect to fix.

**Scope note carried into planning.** The originating issue's follow-up list names five tech service
tables; investigation found the same module file also maps `vehicle_service_order` and
`service_order_detail`, both dropped by the same migration. All seven are in scope, which is what
makes FR-001's "removed in full rather than reduced" a meaningful requirement rather than a
formality.
