# Feature Specification: Unified Entity Status

**Feature Branch**: `005-unified-entity-status`

**Created**: 2026-07-19

**Status**: Implemented

**Input**: User description: "Unified entity status enum (fixes GitHub issues #80 and #81). Replace every boolean lifecycle flag across all entities — `disabled` (User, Customer, Address, Facility, Warehouse, PointSale, CashDrawer, Employee), `active` (Employee, Vehicle, VehicleOperator, TaxpayerCertificate), `deactivated` (Product), `enabled` (PaymentMethodOption) — with a single non-nullable `status` field backed by one shared IntEnum `EntityStatus` with values ACTIVE=0, INACTIVE=1, ARCHIVED=2, stored as an integer column and serialized as an integer in JSON. This is a breaking API change with no deprecation period; mbe-ui is the known client and will be updated separately."

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Uniform status field on every entity (Priority: P1)

As an API client author (e.g. mbe-ui), I read and write the lifecycle state of any catalog
entity (customer, product, employee, facility, warehouse, point of sale, cash drawer, payment
method option, vehicle, vehicle operator, user) through one identically named `status` field
with identical semantics, instead of guessing per-entity whether the flag is `active`,
`disabled`, `deactivated`, or `enabled` and which polarity it has.

**Why this priority**: This is the core of issues #80 and #81 — the per-entity naming and
polarity inconsistency is what causes client bugs. Everything else in the feature exists to
support this.

**Independent Test**: Create and fetch each entity type via its API endpoints and verify the
response carries `status` with a documented value, and none of the legacy flag names appear in
any request or response schema.

**Acceptance Scenarios**:

1. **Given** any status-bearing entity, **When** a client fetches it (list or detail), **Then** the response contains a `status` field whose value is one of ACTIVE (0), INACTIVE (1), ARCHIVED (2), and contains no `active`/`disabled`/`deactivated`/`enabled` field.
2. **Given** a create request that omits `status`, **When** the entity is created, **Then** its status is ACTIVE.
3. **Given** an update request setting `status` to INACTIVE or ARCHIVED, **When** it is applied, **Then** the new status is persisted and returned on subsequent reads.
4. **Given** a request supplying a value outside the enum (e.g. 5), **When** it is submitted, **Then** the API rejects it as a validation error.

---

### User Story 2 - Employee's duplicate flags collapse into one (Priority: P2)

As a client author, I manage a single `status` field for Employee. The undocumented
`active`-vs-`disabled` duplication (issue #80) no longer exists.

**Why this priority**: Directly resolves issue #80; depends on the P1 field existing.

**Independent Test**: Fetch an employee and verify exactly one lifecycle field (`status`) is
present; employees that had either legacy flag in the "off" state read as INACTIVE.

**Acceptance Scenarios**:

1. **Given** an existing employee with `active=true` and `disabled` unset or false, **When** data is migrated, **Then** the employee's status is ACTIVE.
2. **Given** an existing employee with `active=false` OR `disabled=true`, **When** data is migrated, **Then** the employee's status is INACTIVE.

---

### User Story 3 - Filter any entity list by status (Priority: P3)

As a client author, I filter every status-bearing entity's list endpoint with the same
`?status=` query parameter (e.g. to build the "Active" filter chip mentioned in issue #81)
instead of the current situation where only three endpoints support filtering, each under a
different parameter name.

**Why this priority**: Valuable for clients but additive — the unified field (P1/P2) is useful
without it.

**Independent Test**: For each list endpoint, request `?status=0` and `?status=1` and verify
only entities with the matching status are returned; omit the parameter and verify all are
returned.

**Acceptance Scenarios**:

1. **Given** entities in mixed states, **When** a list endpoint is called with `?status=0`, **Then** only ACTIVE entities are returned (and pagination totals reflect the filter).
2. **Given** entities in mixed states, **When** a list endpoint is called without `status`, **Then** entities of all statuses are returned.
3. **Given** the products, customers, or employees list endpoints, **When** a client sends the legacy `?deactivated`, `?disabled`, or `?active` parameter, **Then** it has no filtering effect (the parameter no longer exists).

---

### User Story 4 - Non-active users cannot sign in (Priority: P2)

As a system administrator, setting a user's status to INACTIVE or ARCHIVED prevents that user
from logging in, preserving today's behavior where `disabled` users are rejected.

**Why this priority**: Security-relevant behavior that must not regress when the `disabled`
flag disappears.

**Independent Test**: Attempt login with users in each of the three statuses; only ACTIVE
succeeds.

**Acceptance Scenarios**:

1. **Given** a user with status ACTIVE, **When** they log in with valid credentials, **Then** login succeeds.
2. **Given** a user with status INACTIVE or ARCHIVED, **When** they log in with valid credentials, **Then** login is rejected exactly as a disabled user is rejected today.

### Edge Cases

- Legacy rows where the nullable `disabled` column is NULL are treated as ACTIVE during migration (NULL meant "not disabled").
- An employee row with contradictory legacy flags (`active=true` AND `disabled=true`) migrates to INACTIVE (the restrictive flag wins).
- ARCHIVED is a client-settable state via normal updates; nothing in the system sets it automatically, and DELETE endpoints continue to hard-delete rows.
- Status values outside 0/1/2 are rejected at the validation layer on create, update, and as filter values.
- Entities without API exposure (Address, TaxpayerCertificate) still carry the unified status at the persistence layer so future exposure stays consistent.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The system MUST represent entity lifecycle state as a single field named `status` with exactly three values — ACTIVE (0), INACTIVE (1), ARCHIVED (2) — shared by all status-bearing entities: User, Customer, Product, Employee, Facility, Warehouse, PointSale, CashDrawer, PaymentMethodOption, Vehicle, VehicleOperator, Address, TaxpayerCertificate.
- **FR-002**: The `status` field MUST be non-nullable everywhere; entities created without an explicit status MUST default to ACTIVE.
- **FR-003**: All request and response schemas MUST drop the legacy fields `active`, `disabled`, `deactivated`, `enabled` (no deprecation aliases). API docs (OpenAPI) MUST show `status` with its allowed values.
- **FR-004**: Every list endpoint for a status-bearing, API-exposed entity (products, customers, employees, users, facilities, warehouses, points of sale, cash drawers, payment method options, vehicles, vehicle operators) MUST accept an optional `status` query parameter filtering results to that exact status; the legacy `?deactivated`, `?disabled`, `?active` filter parameters MUST be removed.
- **FR-005**: Authentication MUST reject login for any user whose status is not ACTIVE, preserving the current disabled-user rejection behavior.
- **FR-006**: Existing data MUST be migrated in one step: `disabled`-polarity flags map NULL/false→ACTIVE and true→INACTIVE; `active`/`enabled`-polarity flags map true→ACTIVE and false→INACTIVE; Employee maps to ACTIVE only when `active=true` and `disabled` is not true; legacy columns are then removed.
- **FR-007**: The migration MUST be reversible: downgrading restores the legacy columns and maps ACTIVE back to the "on" state and INACTIVE/ARCHIVED back to the "off" state per entity.
- **FR-008**: Values outside the defined enum MUST be rejected with a validation error on create, update, and filter usage.
- **FR-009**: DELETE endpoints MUST retain their current hard-delete behavior; ARCHIVED is only ever set explicitly by clients.

### Key Entities

- **EntityStatus**: The shared lifecycle vocabulary — ACTIVE (0) "in normal use", INACTIVE (1) "temporarily out of use / disabled", ARCHIVED (2) "retained for history, not for use".
- **Status-bearing entities**: User, Customer, Product, Employee, Facility, Warehouse, PointSale, CashDrawer, PaymentMethodOption, Vehicle, VehicleOperator (API-exposed); Address, TaxpayerCertificate (persistence-only). Each replaces its legacy flag(s) with one `status` attribute.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of status-bearing entity endpoints expose the same `status` field; zero endpoints expose `active`, `disabled`, `deactivated`, or `enabled`.
- **SC-002**: A client author can determine any entity's lifecycle state from the API contract alone, with zero per-entity polarity rules to learn (resolves #80 and #81).
- **SC-003**: All 11 API-exposed status-bearing list endpoints support the identical `?status` filter; a filtered request returns only matching entities.
- **SC-004**: After migration, every pre-existing row maps to the correct status per FR-006 with no data loss; login rejection behavior for non-active users is preserved.
- **SC-005**: The full test suite passes, including new tests covering the status field and filters for every affected endpoint.

## Assumptions

- The API exclusively owns the MariaDB database; no other application reads or writes the legacy flag columns, so they can be dropped.
- A hard breaking change is acceptable: mbe-ui is the only known client and will be updated separately; no deprecation window or dual-field period is required.
- Integer serialization of `status` in JSON (0/1/2) follows the established convention of the API's other enumerations (e.g. currency codes); human-readable names live in the API documentation.
- ARCHIVED has no behavioral difference from INACTIVE today beyond its meaning to clients; both block user login. No automatic archival/soft-delete workflows are introduced.
- Current DELETE endpoints' hard-delete semantics are intentionally unchanged.
- GitHub issues #80 and #81 are fully resolved by this feature and can be closed when it ships.
