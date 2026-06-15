from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas import ListResponse
from app.schemas.core import StoreCreate, StoreResponse, StoreUpdate
from app.services import store_service

router = APIRouter()


@router.get("", response_model=ListResponse[StoreResponse])
async def list_stores(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[StoreResponse]:
    items, total = await store_service.list_stores(db, skip=skip, limit=limit)
    return ListResponse(items=list(items), total=total)


@router.post("", response_model=StoreResponse, status_code=status.HTTP_201_CREATED)
async def create_store(
    data: StoreCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StoreResponse:
    store = await store_service.create_store(db, data)
    return StoreResponse.model_validate(store)


@router.get("/{store_id}", response_model=StoreResponse)
async def get_store(
    store_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StoreResponse:
    store = await store_service.get_store(db, store_id)
    if store is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    return StoreResponse.model_validate(store)


@router.put("/{store_id}", response_model=StoreResponse)
async def update_store(
    store_id: int,
    data: StoreUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> StoreResponse:
    store = await store_service.get_store(db, store_id)
    if store is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    store = await store_service.update_store(db, store, data)
    return StoreResponse.model_validate(store)


@router.delete("/{store_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_store(
    store_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    store = await store_service.get_store(db, store_id)
    if store is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Store not found")
    await store_service.delete_store(db, store)
