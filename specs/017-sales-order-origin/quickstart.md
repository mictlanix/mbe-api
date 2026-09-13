# Quickstart: verifying the origin field

One runnable check per success criterion in [spec.md](./spec.md). Every command below was run
against the finished branch; the recorded output is what it produced.

## Prerequisites

```bash
uv sync
uv run ruff check app/ migrations/ tests/
uv run pytest -q
```

→ `All checks passed!` and `2499 passed` (2,475 before this feature; the 24 new ones are listed
against the criteria below).

The integration suite builds its own SQLite schema from `Base.metadata`, so nothing here needs
MariaDB. Applying migration 020 to `mbe_dev` is a separate, deliberate step — see the last section.

## SC-001 — the workflow is readable from the list row

```bash
uv run pytest tests/integration/test_sales_and_delivery_flow.py -k "list_row_carries_the_workflow" -q
uv run pytest tests/unit/test_list_query_counts.py::TestSalesOrders -q
```

→ `1 passed` and `11 passed`. The first reads `origin` and `sales_quote` off a list row with no
per-row request; the second is the one that would catch an implementation that added a helper and a
query per page where two mapped columns sufficed.

## SC-002 — every converted order records the back office

```bash
uv run pytest tests/integration/ tests/unit/test_sales_quote_service.py -k "convert" -q
```

→ `11 passed`. `POST /sales-quotes/{id}/convert` produces an order reporting `origin: 1` with
nothing supplied by the caller, and a source guard pins the stamp so a later refactor cannot drop
it silently.

## SC-003 — declared is recorded, undeclared is not

```bash
uv run pytest tests/unit/test_sales_order_service.py -k "Origin" -q
```

→ `6 passed`. `SalesOrderCreate()` leaves `origin` at `None`; an out-of-vocabulary value is refused;
the creation path reads `data.origin` and infers nothing.

## SC-004 — selection excludes register sales and pre-change rows

```bash
uv run pytest tests/integration/ -k "selecting_a_workflow" -q
```

→ `1 passed`. `?origin=1` returns the back-office order and omits both the register sale and the
order that recorded nothing.

## SC-005 — nothing existing changes

```bash
grep -n 'UPDATE\|SET ' migrations/020_sales_order_origin.sql || echo 'no row writes'
uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py -q
```

→ `no row writes` — the migration is an `ADD COLUMN` and touches no row of the 335,816 — and
`680 passed`: the mapped column is backed by a migration and described in the data dictionary.

## SC-006 — exclusion keeps the unrecorded history

```bash
uv run pytest tests/integration/ -k "excluding_a_workflow or against_it" -q
```

→ `2 passed`. `?exclude_origin=1` returns the order recording nothing **and** the register sale
while omitting the back-office order; asking for a workflow and against it at once returns an empty
page rather than an error. The first assertion is the one that fails if the filter is written as a
bare `!=`.

## SC-007 — the value cannot be changed after creation

```bash
uv run pytest tests/integration/ -k "cannot_be_changed" -q
```

→ `1 passed`. A `PUT` carrying `origin` returns 200 and leaves the stored value exactly as created,
and an order that recorded nothing cannot be given an origin after the fact.

## Applying the migration to `mbe_dev`

Deliberate and separate from the test run, and worth doing only once the branch is reviewed. It has
**not** been applied — `sales_order.origin` does not exist in `mbe_dev` as of 2026-09-13.

```bash
# read-only first: confirm the column is absent
mysql ... -e "SELECT COUNT(*) FROM information_schema.columns
              WHERE table_schema='mbe_dev' AND table_name='sales_order' AND column_name='origin';"

uv run python -m app.db.migrate      # applies migrations/*.sql in numeric order
```

→ After applying: the column exists, is `NULL` on all 335,816 rows, and
`SELECT COUNT(*) FROM sales_order WHERE origin IS NOT NULL` returns `0`.

To reverse it, `migrations/020_sales_order_origin_rollback.sql` is run by hand — it is never applied
automatically — and every origin recorded up to that point is lost with the column.
