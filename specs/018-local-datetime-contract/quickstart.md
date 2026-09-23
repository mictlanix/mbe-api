# Quickstart: Validate the Local Datetime Contract

## Prerequisites

- `.env` contains `BUSINESS_TIMEZONE=America/Mexico_City`.
- `mbe_dev` reachable as configured in `DATABASE_URL`.

## 1. Automated checks

```bash
uv run pytest tests/unit/test_clock.py tests/unit/test_datetime_contract.py
uv run pytest
uv run ruff check app/ migrations/ tests/
```

Expected: everything passes. See [research.md R5](./research.md#r5-how-the-change-is-tested) for what each test proves.

## 2. The setting is required

```bash
BUSINESS_TIMEZONE= uv run python -c "import app.core.config"              # empty value
BUSINESS_TIMEZONE=Mars/Olympus uv run python -c "import app.core.config"  # unknown zone
```

Expected: both fail with `invalid timezone`, naming `business_timezone`. An environment variable, even an empty one, overrides `.env`. The truly missing case (`Field required`) cannot be shown while `.env` carries the entry. T002 covers it with `_env_file=None`.

## 3. Host timezone no longer matters

```bash
TZ=UTC uv run uvicorn app.main:app --port 8001
```

Create a sales order without a `date`. Expected: its `date` matches Mexico City wall-clock time, not UTC.

## 4. Offsets are honored

Create a sales order with `"promise_date": "2026-09-27T18:00:00Z"`. Expected: it reads back as `"2026-09-27T12:00:00"`. More cases: [contracts/datetime-convention.md](./contracts/datetime-convention.md).

## 5. The schema says so

Open `/docs` and check any datetime field, for example `SalesOrderResponse.date` or the `date_from` parameter on the customer-payments list. Expected: the local-time description is present.
