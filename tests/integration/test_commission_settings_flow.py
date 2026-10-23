"""Spec 025: changing a product's commission label, against real tables."""

from decimal import Decimal

import pytest
from httpx import AsyncClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.enums import SourceType
from app.models.commission import Commission, CommissionProduct
from app.models.incidence import Incidence
from tests.integration.commission_seed import setup_commissions


@pytest.mark.asyncio
async def test_a_product_label_is_changed_not_added(
    client: AsyncClient, db: AsyncSession, seeded: None
) -> None:
    await setup_commissions(db)
    db.add(
        Commission(commission_id=2, name='BLOQUES', commission_rate=Decimal('0.02'), comment='0')
    )
    await db.commit()

    response = await client.put('/api/v1/commissions/product-labels/1', json={'commission': 2})

    assert response.status_code == 200, response.text
    assert response.json()['product'] == 1 and response.json()['commission'] == 2
    db.expire_all()
    rows = (
        (await db.execute(select(CommissionProduct).where(CommissionProduct.product == 1)))
        .scalars()
        .all()
    )
    assert [r.commission for r in rows] == [2]
    entries = (
        (await db.execute(select(Incidence).where(Incidence.source == int(SourceType.COMMISSION))))
        .scalars()
        .all()
    )
    assert len(entries) == 1
    assert entries[0].source == 12 and entries[0].instance_id == 1
