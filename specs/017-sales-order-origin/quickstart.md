# Quickstart: verifying the origin field

One runnable check per success criterion in [spec.md](./spec.md). Run them from the repository root
on branch `017-sales-order-origin` after implementation; each names the outcome that counts as a
pass.

## Prerequisites

```bash
uv sync
uv run ruff check app/ migrations/ tests/    # → All checks passed!
uv run pytest -q                             # → the whole suite green
```

The integration suite builds its own SQLite schema from `Base.metadata`, so nothing below needs
MariaDB. Applying migration 020 to `mbe_dev` is a separate, deliberate step — see the last section.

## SC-001 — the workflow is readable from the list row

```bash
uv run pytest tests/integration/test_sales_and_delivery_flow.py -k origin -q
uv run pytest tests/unit/test_list_query_counts.py::TestSalesOrders -q
```

→ The list rows report `origin` (and `sales_quote`) without a per-row request, and the page still
costs a fixed number of queries regardless of page size. The second command is the one that would
catch an implementation that added a helper and an extra query where two mapped columns sufficed.

## SC-002 — every converted order records the back office

```bash
uv run pytest tests/integration/ -k convert -q
```

→ An order produced by `POST /sales-quotes/{id}/convert` reports `origin: 1`, with nothing supplied
by the caller.

## SC-003 — declared is recorded, undeclared is not

```bash
uv run pytest tests/unit/test_sales_order_service.py -k origin -q
```

→ `SalesOrderCreate()` leaves `origin` at `None`; a declared value survives to the constructed
order; nothing is inferred from `point_sale`, `customer` or `ship_to`.

## SC-004 — selection excludes register sales and pre-change rows

```bash
uv run pytest tests/integration/ -k "origin and filter" -q
```

→ `?origin=1` returns back-office orders only: no register sales, and no order that recorded
nothing.

## SC-005 — nothing existing changes

```bash
grep -n 'UPDATE\|SET ' migrations/020_sales_order_origin.sql || echo 'no row writes'
uv run pytest tests/unit/test_model_schema.py tests/unit/test_data_dictionary.py -q
```

→ `no row writes` — the migration is an `ADD COLUMN` and touches no row of the 335,816. The two
schema checks confirm the mapped column is backed by a migration and described in the dictionary.

## SC-006 — exclusion keeps the unrecorded history

```bash
uv run pytest tests/integration/ -k "exclude_origin" -q
```

→ `?exclude_origin=1` returns orders recording nothing **and** orders recording point of sale, and
omits back-office orders. This is the check that fails if the filter was written as a bare `!=`.

## SC-007 — the value cannot be changed after creation

```bash
uv run pytest tests/integration/ -k "origin and immutable" -q
```

→ A `PUT` carrying `origin` returns 200 and leaves the stored value exactly as created.

## Applying the migration to `mbe_dev`

Deliberate and separate from the test run, and worth doing only once the branch is reviewed:

```bash
# read-only first: confirm the column is absent and see what the sweep will touch
mysql ... -e "SELECT COUNT(*) FROM information_schema.columns
              WHERE table_schema='mbe_dev' AND table_name='sales_order' AND column_name='origin';"

uv run python -m app.db.migrate      # applies migrations/*.sql in numeric order
```

→ After applying: the column exists, is `NULL` on all 335,816 rows, and
`SELECT COUNT(*) FROM sales_order WHERE origin IS NOT NULL` returns `0`.

To reverse it, `migrations/020_sales_order_origin_rollback.sql` is run by hand — it is never applied
automatically — and every origin recorded up to that point is lost with the column.
