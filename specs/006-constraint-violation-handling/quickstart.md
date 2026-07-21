# Quickstart: Validating Constraint Violation Handling

Every scenario is read-only against the database except where noted. Run from the repo root.

## Prerequisites

- `uv sync`
- `.env` pointing at a populated database (the counts below come from `mbe_demo`)

## 1. Test suite

```bash
uv run pytest -q
```

Expected: all tests pass. The ones that speak to this feature:

- `tests/unit/test_references.py` — blocker ordering, truncation, exemptions, `table.column`
  labelling, and that an unsaved instance issues no query
- `tests/api/test_constraint_errors.py` — a `409` for each of the three classes, and that the
  driver message never reaches the body
- `tests/unit/test_taxpayer_issuer_service.py` — the issuer delete is wired to the shared guard

## 2. The API contract is unchanged

```bash
uv run python -c "
import json; from app.main import app
json.dump(app.openapi(), open('/tmp/after.json','w'), indent=2, sort_keys=True)"
git stash -q -u
uv run python -c "
import json; from app.main import app
json.dump(app.openapi(), open('/tmp/before.json','w'), indent=2, sort_keys=True)"
git stash pop -q
diff /tmp/before.json /tmp/after.json && echo "identical"
```

Expected: `identical`. Satisfies SC-007 — this feature changes behaviour, never the published
interface.

## 3. The guard finds real blockers

Mocks cannot prove the metadata traversal works, so check it against real rows:

```bash
PYTHONPATH=. uv run python - <<'EOF'
import asyncio
from sqlalchemy import select
from app.db.session import AsyncSessionLocal, engine
from app.models.core import Facility, Warehouse, Employee
from app.models.user import User
from app.services.references import find_blocking_references

async def main():
    async with AsyncSessionLocal() as db:
        for model, exempt in [
            (Facility, frozenset()),
            (Warehouse, frozenset()),
            (Employee, frozenset()),
            (User, frozenset({'user_settings', 'access_privilege'})),
        ]:
            row = (await db.execute(select(model).limit(1))).scalars().first()
            blockers = await find_blocking_references(db, row, exempt=exempt)
            print(f'{model.__name__:<10}', ', '.join(f'{t}({n})' for t, n in blockers[:3]) or 'deletable')
    await engine.dispose()

asyncio.run(main())
EOF
```

Expected shape (counts vary by dataset):

```
Facility   sales_order.facility(47175), customer_payment.facility(35351), fiscal_document.facility(5253)
Warehouse  purchase_order_detail.warehouse(28863), inventory_receipt.warehouse(4038), inventory_transfer.warehouse_to(2769)
Employee   cash_session.cashier(176), incidence.updater(94), cash_session.cash_supervisor(83)
User       deletable
```

Two things to confirm by eye:

- Labels are `table.column`, and a table appearing twice (`inventory_transfer.warehouse_to`
  vs `inventory_transfer.warehouse`) is distinguished by column — FR-004.
- `User` reports `deletable`: its only references are the exempted cascade tables, so a user
  is always deletable — research R6.

## 4. Guard counts agree with the database

```bash
PYTHONPATH=. uv run python - <<'EOF'
import asyncio
from sqlalchemy import select, text
from app.db.session import AsyncSessionLocal, engine
from app.models.core import Facility
from app.services.references import find_blocking_references

async def main():
    async with AsyncSessionLocal() as db:
        f = (await db.execute(select(Facility).limit(1))).scalars().first()
        raw = (await db.execute(
            text('select count(*) from warehouse where facility = :i'),
            {'i': f.facility_id})).scalar_one()
        guard = dict(await find_blocking_references(db, f)).get('warehouse.facility', 0)
        print(f'raw={raw} guard={guard} match={raw == guard}')
    await engine.dispose()

asyncio.run(main())
EOF
```

Expected: `match=True`. The guard is counting what the database counts, not an approximation.

## 5. Lint and types

```bash
uv run ruff check app tests
uv run ruff format --check app tests
uv run mypy app
```

Expected: ruff clean; mypy at its pre-existing baseline with **no** errors in `references.py`
or `main.py`.

## Success criteria mapping

| Criterion | Verified by |
|-----------|-------------|
| SC-001 no constraint violation reads as a server fault | Step 1 (`test_constraint_errors.py`) |
| SC-002 operator can identify what to remove | Step 3 (labels name table and column) |
| SC-003 unreferenced deletes still succeed | Step 1 (existing delete tests unchanged) |
| SC-004 re-saving unchanged never conflicts | Step 1 (update-path exclusion test) |
| SC-005 new references covered automatically | Step 3 (traversal is metadata-driven) |
| SC-006 no internal detail disclosed | Step 1 (leakage test asserts absence) |
| SC-007 contract unchanged | Step 2 (OpenAPI diff empty) |
