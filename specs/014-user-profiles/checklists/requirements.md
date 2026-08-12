# Specification Quality Checklist: User Profiles as Permission Templates

**Purpose**: Validate specification completeness and quality before proceeding to planning
**Created**: 2026-08-11
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

**Status: all 16 items pass.** 29 functional requirements, 8 success criteria, 3 prioritized user
stories with 25 acceptance scenarios, 11 edge cases, 13 assumptions. Ready for `/speckit-plan`.

### Clarifications resolved (session 2026-08-12)

- **Provisioning at creation → both paths** (FR-011). `POST /users` accepts an optional profile and
  applies it atomically; the standalone apply remains for re-provisioning. Creation naming a missing
  or inactive profile is refused and leaves no user behind.
- **Profile representation → sparse both ways** (FR-003). A profile stores and returns entries only
  for the objects it grants; absence means denied. Users keep their existing full matrix, so the two
  sides of the copy have deliberately different shapes.
- **Write-semantics asymmetry → kept and documented** (FR-026). Apply replaces in full; the existing
  per-user edit stays a partial upsert. Aligning them would change behaviour existing callers depend
  on, which the brief's "no logic change" rules out.
- **User list → carries the origin profile** (FR-020). Every row shows it, empty where absent, so
  the list is legible as well as filterable by it.
- **Name uniqueness → case-insensitive** (FR-004). "cashier" conflicts with "Cashier"; stored as
  typed, compared without case. Pinned because the collation default would otherwise decide it
  silently and the acceptance test asserts a specific outcome.

### Also corrected this session

- **Administrators bypass per-object permission checks.** The spec's admin edge case implied a
  profile constrains an administrator. It does not — the permissions are recorded but inert until
  the `administrator` flag is cleared. Rewritten to say so, and to explain why the apply is still
  permitted rather than refused.
- **Profiles are global**, not facility-scoped. Added to Assumptions; noticed during the scan, not
  worth a question since facility scoping was never implied.

### Clarifications resolved (session 2026-08-11)

- **Apply semantics → full replace** (FR-013). The applied profile becomes the account's complete
  permission set; system objects the profile omits are denied. Makes SC-003 (two users on one
  profile are identical) provable and makes re-apply a genuine correction rather than a partial one.
- **Origin tracking → recorded on the user** (FR-019 – FR-022). The account carries a reference to
  the last profile applied, readable and filterable, so correcting a profile does not depend on an
  external list. Provenance only — never consulted for authorization.

### Consequences the answers pulled in, resolved without a further question

- **Profile deletion refused while referenced** (FR-008). Follows from the origin being a real
  reference: the codebase already refuses referenced deletes uniformly, and clearing the reference
  instead would let a delete silently rewrite user records. Retirement path is the inactive status
  (FR-009). Recorded in Assumptions.
- **Drift detection out of scope.** Full replace plus a recorded origin makes "has this account
  drifted off its profile?" answerable, but defining a match against a since-edited profile is a
  separate feature. Recorded in Assumptions and as an edge case.

### Deliberately resolved as assumptions rather than clarifications

Session invalidation on apply (the constitution mandates it for privilege mutations), the
`administrator` flag being outside a profile (the description said permissions only),
administrator-only access control (matches the existing user endpoints), single-user apply
(the description said "the user", singular), and no migration of existing accounts.
