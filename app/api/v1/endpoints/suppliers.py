from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.schemas import ListResponse
from app.schemas.supplier import SupplierCreate, SupplierResponse, SupplierUpdate
from app.services import supplier_service

router = APIRouter()


@router.get('', response_model=ListResponse[SupplierResponse])
async def list_suppliers(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[SupplierResponse]:
    items, total = await supplier_service.list_suppliers(db, search=search, skip=skip, limit=limit)
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=SupplierResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    data: SupplierCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SupplierResponse:
    supplier = await supplier_service.create_supplier(db, data)
    return SupplierResponse.model_validate(supplier)


@router.get('/{supplier_id}', response_model=SupplierResponse)
async def get_supplier(
    supplier_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SupplierResponse:
    supplier = await supplier_service.get_supplier(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Supplier not found')
    return SupplierResponse.model_validate(supplier)


@router.put('/{supplier_id}', response_model=SupplierResponse)
async def update_supplier(
    supplier_id: int,
    data: SupplierUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> SupplierResponse:
    supplier = await supplier_service.get_supplier(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Supplier not found')
    supplier = await supplier_service.update_supplier(db, supplier, data)
    return SupplierResponse.model_validate(supplier)


@router.delete('/{supplier_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(
    supplier_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    supplier = await supplier_service.get_supplier(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Supplier not found')
    await supplier_service.delete_supplier(db, supplier)
