# Research: Local Datetime Contract

All decisions below were checked against the installed stack (pydantic 2.13.4, pydantic-settings 2.14.1, FastAPI 0.136.3) with a scratch experiment, not assumed.

## R1. How the timezone setting is declared and made required

- **Decision**: `business_timezone: ZoneInfo` on `Settings` in `app/core/config.py`, with no default. `.env` and `.env.example` carry `BUSINESS_TIMEZONE=America/Mexico_City`.
- **Rationale**: pydantic validates `ZoneInfo` natively. A missing entry fails at `Settings()` with `business_timezone  Field required`; a bad name fails with `invalid timezone: Mars/Olympus`. Both errors name the setting, which is what FR-001 asks for. `settings = Settings()` runs at import, so the process never starts serving with a wrong value. `tzdata` is already a dependency, so the lookup does not depend on the host's tz database.
- **Alternatives considered**: a `str` setting converted on use (defers the failure to the first request); a code default (rejected by the user).

## R2. One source for "now" and "today"

- **Decision**: a new module `app/core/clock.py` with three functions:
  - `local_now()`: `datetime.now(settings.business_timezone).replace(tzinfo=None)`.
  - `local_today()`: `local_now().date()`.
  - `to_local(value)`: returns a naive value unchanged; converts an aware one to the business timezone and drops the offset.
- **Call sites replaced**: every `datetime.now()` in `app/services/` (53 calls, 11 files), the two `datetime.now(tz=UTC).replace(tzinfo=None)` in `app/services/vehicle_operator_service.py`, `date.today()` in `app/services/cash_session_service.py` (2) and `dt.date.today()` in `app/schemas/core.py` (1). The spec did not list `date.today()`, but it reads the host clock the same way, so leaving it would break FR-002.
- **Left alone**: `app/core/security.py` (JWT expiry is an aware UTC instant, FR-008) and `app/services/csd_service.py` (`_SAT_TZ` is the tax authority's timezone, a legal fact about SAT certificates, not the business setting).
- **Rationale**: the setting is read at call time, so tests can override it. Three small functions, no class.
- **Alternatives considered**: calling `datetime.now(settings.business_timezone)` inline at each site. That spreads the "then drop the offset" step across 58 lines, which is exactly how `vehicle_operator_service` drifted.

## R3. Normalizing inbound datetimes

- **Decision**: one annotated type, `LocalDateTime = Annotated[datetime, AfterValidator(to_local), Field(description=…)]`, defined in `app/schemas/__init__.py`. It replaces `datetime` on every schema field (request and response) and on the `date_from`/`date_to` query parameters in `customer_payments.py` (3 endpoints), `sales_orders.py`, `delivery_orders.py` and `cash_sessions.py`: 6 endpoints in 4 files. The two `date`-typed filters (`exchange_rates.py`, `delivery_itineraries.py`) are dates, not datetimes, and stay as they are.
- **Verified behavior**:
  - body `2026-09-27T18:00:00Z` becomes `2026-09-27T12:00:00`
  - body `2026-09-27T12:00:00-05:00` becomes `2026-09-27T11:00:00`
  - body `2026-09-27T12:00:00` stays unchanged
  - query `date_from=2026-09-27T06:00:00Z` becomes `2026-09-27T00:00:00`
- **Rationale**: the conversion happens at the boundary, before any service or the database driver sees the value, so the driver never gets an aware datetime to strip. On response schemas the validator is a no-op (stored values are naive), and using one type everywhere means the schema description cannot miss a field.
- **Alternatives considered**: a shared model base class with a validator (no such base exists; query parameters would still need separate handling); converting in each service (58 places to forget one).

## R4. Documenting the convention in the schema

- **Decision**: the `Field(description=…)` inside `LocalDateTime` carries the text, built from the configured zone at import: *"Local wall-clock time in {zone}, with no UTC offset. A value sent with an offset is converted to {zone}; a value without one is taken as already local."*
- **Verified behavior**: required fields get the description on the property. Optional fields (`LocalDateTime | None`) get it on the `format: date-time` branch of the `anyOf`, which is the node that declares the format. A field with its own `Field(description=…)` keeps that description at property level and still carries the convention on the date-time node.
- **Rationale**: this is the one case `app/schemas/__init__.py` says earns a description: a client can be wrong about it with nothing failing, which is mictlanix/mbe-ui#176.
- **`format: date-time` is kept.** Dropping it would turn the fields into plain strings in generated clients and break them. The description is what corrects the reader.

## R5. How the change is tested

- **Clock**: unit tests set the business timezone to a zone far from the host's (e.g. `Asia/Tokyo`) and check `local_now()`/`local_today()` follow it. `to_local` covers naive, `Z` and a non-UTC offset.
- **Required setting**: unit tests build `Settings` with the entry missing and misspelled, and check the error names `business_timezone`.
- **Boundary**: an API test posts a sales order with a `Z` date and reads it back as local; one list endpoint filtered with a `Z` `date_from`.
- **Contract**: a unit test walks `app.openapi()` and asserts every `format: date-time` node, in components and in parameters, carries the convention text (SC-001).
- **No stray clocks**: a unit test scans `app/` for `datetime.now(` and `date.today(` outside `app/core/clock.py` and `app/core/security.py` (SC-004), so a new call site cannot creep back in.
- **Environment**: tests load `.env` from the repository root, which carries the entry. There is no CI workflow in the repository, so there is nothing else to update.
