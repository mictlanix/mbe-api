from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas import ListResponse
from app.schemas.sat_catalog import SatCatalogResponse
from app.services import sat_catalog_service
from app.services.sat_catalog_service import SAT_CATALOG_MAP

router = APIRouter()


def _make_list_handler(slug: str):
    config = SAT_CATALOG_MAP[slug]

    async def handler(
        search: str | None = Query(None),
        skip: int = Query(0, ge=0),
        limit: int = Query(20, ge=1, le=100),
        _: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> ListResponse[SatCatalogResponse]:
        rows, total = await sat_catalog_service.list_sat(
            db, config, search=search, skip=skip, limit=limit
        )
        return ListResponse(
            items=[sat_catalog_service.to_response(r, config) for r in rows],
            total=total,
        )

    handler.__name__ = f'list_{slug.replace("-", "_")}'
    return handler


def _make_get_handler(slug: str):
    config = SAT_CATALOG_MAP[slug]

    async def handler(
        id: str,
        _: CurrentUser = Depends(get_current_user),
        db: AsyncSession = Depends(get_db),
    ) -> SatCatalogResponse:
        row = await sat_catalog_service.get_sat(db, config.model, id)
        if row is None:
            raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Not found')
        return sat_catalog_service.to_response(row, config)

    handler.__name__ = f'get_{slug.replace("-", "_")}'
    return handler


for _slug in SAT_CATALOG_MAP:
    router.add_api_route(
        f'/{_slug}',
        _make_list_handler(_slug),
        methods=['GET'],
        response_model=ListResponse[SatCatalogResponse],
        tags=['sat-catalogs'],
    )
    router.add_api_route(
        f'/{_slug}/{{id}}',
        _make_get_handler(_slug),
        methods=['GET'],
        response_model=SatCatalogResponse,
        tags=['sat-catalogs'],
    )
