# Specification Quality Checklist: Digital Seal Certificates

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

**Iteration 1 — 3 failures, corrected:**

1. *No implementation details* — the draft was written in the vocabulary of the
   implementation: DER, PKCS#8, X.509 serial, RSA public numbers, `load_only`, and
   `America/Mexico_City`. Restated as behaviour a stakeholder can evaluate: "the password
   unlocks the key", "the key belongs to the certificate", "recorded in the same convention as
   existing records". The mechanics moved to `plan.md`, `research.md` and `contracts/`.
2. *Success criteria measurable* — "dates should be correct" cannot be tested, since the
   question is *correct against what*. Split into SC-005 (agrees with the previous system's
   records) and SC-006 (never overstates validity), both checkable against real data.
3. *Requirements unambiguous* — an early FR said the response "should not expose secrets",
   which a response-filtering implementation would satisfy while still reading them from
   storage. Split into FR-004 (not returned) and FR-005 (not retrieved), because the second is
   the one that survives a careless future edit.

**Iteration 2 — all items pass.**

### Notes on scope

No `[NEEDS CLARIFICATION]` markers were needed, but one decision deliberately gated
implementation rather than being guessed: how the key password is stored. The read endpoints
shipped first, and the upload was only designed once the product owner confirmed the password
stays unencrypted. That sequencing is recorded in `tasks.md` (T002) rather than smoothed over.

This spec was written after the implementation, covering work from two pull requests. User
Story 3 is unusual and deliberately so: it is a P1 story discovered *after* its feature
shipped, when the parser was checked against real certificates and the validity window proved
wrong on every one. It is recorded as a story rather than a footnote because the requirement
it encodes — that dates agree with existing records — was genuinely missing from the original
specification, not merely mis-implemented.
