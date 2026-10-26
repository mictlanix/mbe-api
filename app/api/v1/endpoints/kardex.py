from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas.inventory import Kardex
from app.services import kardex_service

router = APIRouter()

_READ = require_privilege(SystemObject.KARDEX, AccessRight.READ)


@router.get('', response_model=Kardex)
async def read_kardex(
    product: int = Query(...),
    warehouse: int = Query(...),
    from_: date = Query(..., alias='from'),
    to: date = Query(..., description='Inclusive of its whole day.'),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> Kardex:
    """One product's movements in one warehouse, with opening, running and closing balances."""
    return await kardex_service.kardex(db, product=product, warehouse=warehouse, from_=from_, to=to)
