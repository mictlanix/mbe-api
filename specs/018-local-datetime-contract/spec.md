# Feature Specification: Local Datetime Contract

**Feature Branch**: `018-local-datetime-contract`
**Created**: 2026-09-22
**Status**: Draft
**Input**: GitHub issue #228, resolved with option B. Datetimes stay local wall-clock time without an offset, pinned to Mexico City time, which is what the legacy system sharing the database already writes.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Clients read timestamps without guessing (Priority: P1)

A developer building a client reads the API schema and sees that every datetime is Mexico City wall-clock time with no offset. They display it as-is. Today the schema promises a standard timestamp with an offset, the wire carries none, and at least one client (mbe-ui, issue mictlanix/mbe-ui#176) shifts every date it shows by six hours.

**Why this priority**: The gap between the written contract and the wire is the root cause of the client bug. Closing it is the one change the issue calls blocking.

**Independent Test**: Fetch the API schema and check that each datetime field states the convention. Fetch a sales order created at a known local time and check the value matches that wall-clock time exactly.

**Acceptance Scenarios**:

1. **Given** the published API schema, **When** a developer reads any datetime field, **Then** its description says the value is local wall-clock time in the business timezone, with no UTC offset.
2. **Given** a sales order created at 19:03 Mexico City time, **When** a client fetches it, **Then** the order date reads 19:03 with no offset.

### User Story 2 - Timestamps do not depend on the server's clock setting (Priority: P1)

An operator deploys the API on a host or container whose timezone is UTC, or anything else. Every timestamp the API records, and every "is it overdue yet" decision it makes, still uses Mexico City time. Records written by the API and by the legacy system agree.

**Why this priority**: Today "local" silently means whatever the host is set to. One misconfigured deploy would write six-hour-off timestamps into the shared database with no error.

**Independent Test**: Run the API with the host timezone set to UTC. Create a record and check its timestamp matches Mexico City wall-clock time.

**Acceptance Scenarios**:

1. **Given** the API runs on a host set to UTC, **When** a sales order is created without a date, **Then** its date is the current Mexico City wall-clock time.
2. **Given** a vehicle operator is created or updated, **When** its modification time is read, **Then** it is Mexico City wall-clock time, like every other record, not UTC.
3. **Given** an order whose due date passed ten minutes ago in Mexico City, **When** the API checks the customer for overdue orders on a UTC host, **Then** the order counts as overdue.

### User Story 3 - A timestamp sent with an offset is stored as the intended moment (Priority: P2)

A client that follows the standard sends `2026-09-27T18:00:00Z`. The API stores 12:00, the same moment in Mexico City time. Today the offset is dropped silently and 18:00 is stored, six hours off, with no error.

**Why this priority**: No client sends real offset-bearing times today (mbe-ui sends only midnights marked UTC), so no data is being corrupted yet. It will be the first time one does.

**Independent Test**: Send a datetime carrying a `Z` or `-05:00` offset, in a request body and in a list filter. Check the business logic receives the equivalent Mexico City wall-clock time, with no offset. The conversion happens before any business logic runs, so what it receives is what gets stored.

**Acceptance Scenarios**:

1. **Given** a request body carrying `2026-09-27T18:00:00Z`, **When** the record is saved and read back, **Then** it reads `2026-09-27T12:00:00`.
2. **Given** a request body carrying `2026-09-27T12:00:00` with no offset, **When** it is saved, **Then** it is stored unchanged.
3. **Given** a list request filtering with `date_from=2026-09-27T06:00:00Z`, **When** results are returned, **Then** the filter applies from 00:00 Mexico City time on that day.

### Edge Cases

- A client sends a midnight marked UTC (`…T00:00:00.000Z`), as mbe-ui does today to satisfy its serializer. Under this feature it becomes 18:00 on the previous day. That is correct behavior, but it changes what mbe-ui's date-only inputs store. The client must drop the fake UTC marker when this ships. See Assumptions.
- A date falls on a Mexico City daylight-saving change. Mexico City has not observed daylight saving since 2022, so conversion is a fixed offset in practice. The timezone database handles historical dates either way.
- A timestamp is written by the legacy system. It is already Mexico City wall-clock time and is read unchanged.
- A deployment's config file has no timezone entry, or a misspelled one. The API does not start, and the error names the missing or invalid setting. Existing deployments must add the entry before upgrading.
- JWT token expiry. It stays a true UTC instant: it is never stored and never shown as a wall-clock time.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The business timezone MUST be a single configurable setting, set explicitly to `America/Mexico_City` in the environment config file (`.env`) and documented with that value in `.env.example`. The setting has no built-in default: the API MUST refuse to start when it is missing or names an unknown timezone, and say which setting is wrong.
- **FR-002**: Every "current time" the API records or compares against MUST be wall-clock time in the business timezone, whatever the host's timezone is.
- **FR-003**: Vehicle operator creation and update MUST record business-timezone wall-clock time, as every other record does, instead of UTC.
- **FR-004**: A datetime received with a UTC offset, in a request body or a query parameter, MUST be converted to business-timezone wall-clock time before use or storage.
- **FR-005**: A datetime received without an offset MUST be accepted and used unchanged.
- **FR-006**: Every datetime field in the published API schema MUST state that it is business-timezone wall-clock time with no UTC offset.
- **FR-007**: Datetimes in responses MUST keep their current wire format: no offset, unchanged values.
- **FR-008**: Token expiry MUST remain an absolute UTC instant, unaffected by the business timezone.

### Key Entities

- **Business timezone**: the one timezone all stored and exchanged datetimes are expressed in. It is shared with the legacy system that writes to the same database.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: 100% of datetime fields in the published schema describe the local-time convention.
- **SC-002**: With the host set to UTC, a record created at a known moment carries the Mexico City wall-clock time for that moment, to the second.
- **SC-003**: A datetime sent with any offset reads back as the same moment in Mexico City wall-clock time, in every endpoint that accepts datetimes.
- **SC-004**: No code path records a "current time" outside the single business-timezone source, token expiry excepted.
- **SC-005**: Existing responses are byte-for-byte unchanged in format for data already stored.

## Assumptions

- **No backfill of vehicle operator rows.** A read-only probe of the development database found 14 vehicle operator rows, of which only 2 have a 2026 modification time, both from one minute on 2026-03-18. They may carry UTC-shifted times. That is too few to justify a migration, and they are corrected on their next update.
- **mbe-ui ships its fix alongside this one.** Once offsets are honored, the client's "midnight marked UTC" workaround stores the previous evening. mictlanix/mbe-ui#176 tracks the client side, and should be coordinated with this release.
- **Date-only concepts stay datetimes.** Fields like promise date remain datetimes on the wire. Turning them into plain dates is a separate change.
- The schema documents the convention in field descriptions. The declared field format is left as-is, because the goal is to change what clients read, not how generators type the field.

## Verbatim Constraints

- Timezone value: `America/Mexico_City`, written in the environment config file (`.env`, with `.env.example` carrying the same entry).

## Out of Scope

- Moving to aware UTC storage end to end (option A in the issue).
- Migrating or reinterpreting existing stored data.
- Changing the serialized wire format of responses.
- The client-side fix in mbe-ui.
