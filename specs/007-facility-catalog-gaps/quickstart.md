# Quickstart: Validating Facility Catalog Gaps

Run from the repo root. Steps 3–5 are read-only against a populated database.

## Prerequisites

- `uv sync`
- `.env` pointing at a populated database (counts below come from `mbe_demo`)

## 1. Test suite

```bash
uv run pytest -q
```

Relevant files: `tests/api/test_taxpayer_issuers.py` (the new resource, including the
short-identifier and unknown-provider rejections), `tests/unit/test_point_sale_service.py`
(the pairing rule, including facility-only amendment), `tests/api/test_facilities.py` (address
now expanded), `tests/unit/test_fk_expansion_isolation.py` (expansion leaves the raw key
intact).

## 2. The new resource appears in the published contract

```bash
uv run python -c "
from app.main import app
spec = app.openapi()
for p in sorted(x for x in spec['paths'] if 'taxpayer-issuers' in x):
    print(p, sorted(m.upper() for m in spec['paths'][p]))
print('FacilityResponse.address ->', spec['components']['schemas']['FacilityResponse']['properties']['address'])
"
```

Expected:

```
/api/v1/taxpayer-issuers ['GET', 'POST']
/api/v1/taxpayer-issuers/{rfc} ['DELETE', 'GET', 'PUT']
FacilityResponse.address -> {'$ref': '#/components/schemas/AddressResponse'}
```

The `$ref` is FR-008: the client generator will now produce an address object, not an integer.

## 3. Real records serialize

```bash
PYTHONPATH=. uv run python - <<'PY'
import asyncio
from app.db.session import AsyncSessionLocal, engine
from app.schemas.core import FacilityResponse, PointSaleResponse, WarehouseResponse
from app.schemas.fiscal import TaxpayerIssuerResponse
from app.services import facility_service, point_sale_service, warehouse_service, taxpayer_issuer_service

async def main():
    async with AsyncSessionLocal() as db:
        for label, call, schema in [
            ('facilities', facility_service.list_facilities, FacilityResponse),
            ('warehouses', warehouse_service.list_warehouses, WarehouseResponse),
            ('points of sale', point_sale_service.list_point_sales, PointSaleResponse),
            ('taxpayer issuers', taxpayer_issuer_service.list_taxpayer_issuers, TaxpayerIssuerResponse),
        ]:
            items, _ = await call(db, limit=20)
            for i in items:
                schema.model_validate(i)
            print(f'  ok {label}: {len(items)} rows')
        f = (await facility_service.list_facilities(db, limit=1))[0][0]
        print('  raw FK intact on the shared record:', isinstance(f.address, int), isinstance(f.location, str))
    await engine.dispose()

asyncio.run(main())
PY
```

Expected: all four validate, and the last line prints `True True` — the facility's stored keys
survive expansion, which is what keeps the embedded compact form correct (FR-009).

## 4. The pairing rule holds end to end

Safe: the guard rejects before anything is written, and the whole run is rolled back.

```bash
PYTHONPATH=. uv run python - <<'PY'
import asyncio
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from fastapi import HTTPException
from app.db.session import engine
from app.models.core import PointSale, Warehouse
from app.schemas.core import PointSaleUpdate
from app.services import point_sale_service

async def main():
    async with engine.connect() as conn:
        tx = await conn.begin()
        db = AsyncSession(bind=conn, expire_on_commit=False)
        ps = (await db.execute(select(PointSale).limit(1))).scalars().first()
        other = (await db.execute(
            select(Warehouse).where(Warehouse.facility != ps.facility).limit(1))).scalars().first()
        try:
            await point_sale_service.update_point_sale(db, ps, PointSaleUpdate(warehouse=other.warehouse_id))
            print('  FAIL: mismatched pairing accepted')
        except HTTPException as e:
            print(f'  ok refused with {e.status_code}: {e.detail}')
        await db.close(); await tx.rollback()
    await engine.dispose()

asyncio.run(main())
PY
```

Expected: `refused with 422: Warehouse N does not belong to facility M` (FR-010).

## 5. Lint and types

```bash
uv run ruff check app tests && uv run ruff format --check app tests && uv run mypy app
```

Expected: ruff clean; mypy at its pre-existing baseline.

## Success criteria mapping

| Criterion | Verified by |
|-----------|-------------|
| SC-001 taxpayer selectable from a searchable list | Steps 1, 2 (list + `search`) |
| SC-002 taxpayer displayed as readable text | Step 3 (issuers serialize with expanded regime) |
| SC-003 addresses render with no extra requests | Steps 2, 3 (`$ref`, real rows validate) |
| SC-004 no mismatched pairing storable | Steps 1, 4 |
| SC-005 client workarounds deletable | Steps 2, 4 together |
| SC-006 in-use taxpayer entity not removable | Step 1 (delete guard tests) |
