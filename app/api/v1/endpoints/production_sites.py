from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas import ListResponse
from app.schemas.core import ProductionSiteCreate, ProductionSiteResponse, ProductionSiteUpdate
from app.services import production_site_service

router = APIRouter()


@router.get("", response_model=ListResponse[ProductionSiteResponse])
async def list_production_sites(
    store: int | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[ProductionSiteResponse]:
    items, total = await production_site_service.list_production_sites(
        db, store=store, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post("", response_model=ProductionSiteResponse, status_code=status.HTTP_201_CREATED)
async def create_production_site(
    data: ProductionSiteCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductionSiteResponse:
    ps = await production_site_service.create_production_site(db, data)
    return ProductionSiteResponse.model_validate(ps)


@router.get("/{production_site_id}", response_model=ProductionSiteResponse)
async def get_production_site(
    production_site_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductionSiteResponse:
    ps = await production_site_service.get_production_site(db, production_site_id)
    if ps is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Production site not found"
        )
    return ProductionSiteResponse.model_validate(ps)


@router.put("/{production_site_id}", response_model=ProductionSiteResponse)
async def update_production_site(
    production_site_id: int,
    data: ProductionSiteUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ProductionSiteResponse:
    ps = await production_site_service.get_production_site(db, production_site_id)
    if ps is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Production site not found"
        )
    ps = await production_site_service.update_production_site(db, ps, data)
    return ProductionSiteResponse.model_validate(ps)


@router.delete("/{production_site_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_production_site(
    production_site_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    ps = await production_site_service.get_production_site(db, production_site_id)
    if ps is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="Production site not found"
        )
    await production_site_service.delete_production_site(db, ps)
