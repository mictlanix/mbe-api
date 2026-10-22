from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_any_privilege, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.supplier import (
    SupplierAgreementCreate,
    SupplierAgreementResponse,
    SupplierAgreementUpdate,
    SupplierCreate,
    SupplierDetailResponse,
    SupplierResponse,
    SupplierUpdate,
)
from app.services import supplier_agreement_service, supplier_service

router = APIRouter()

_READ = require_privilege(SystemObject.SUPPLIERS, AccessRight.READ)
_CREATE = require_privilege(SystemObject.SUPPLIERS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.SUPPLIERS, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.SUPPLIERS, AccessRight.DELETE)
# Also read from other modules' screens (spec 022 FR-005).
_LOOKUP = require_any_privilege(
    (SystemObject.SUPPLIERS, AccessRight.READ),
    (SystemObject.PRODUCTS, AccessRight.READ),
    (SystemObject.PRICING, AccessRight.READ),
)


@router.get('', response_model=ListResponse[SupplierResponse])
async def list_suppliers(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_LOOKUP),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[SupplierResponse]:
    items, total = await supplier_service.list_suppliers(db, search=search, skip=skip, limit=limit)
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=SupplierDetailResponse, status_code=status.HTTP_201_CREATED)
async def create_supplier(
    data: SupplierCreate,
    _: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> SupplierDetailResponse:
    supplier = await supplier_service.create_supplier(db, data)
    return SupplierDetailResponse.model_validate(supplier)


@router.get('/{supplier_id}', response_model=SupplierDetailResponse)
async def get_supplier(
    supplier_id: int,
    _: CurrentUser = Depends(_LOOKUP),
    db: AsyncSession = Depends(get_db),
) -> SupplierDetailResponse:
    supplier = await supplier_service.get_supplier_detail(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Supplier not found')
    return SupplierDetailResponse.model_validate(supplier)


@router.put('/{supplier_id}', response_model=SupplierDetailResponse)
async def update_supplier(
    supplier_id: int,
    data: SupplierUpdate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SupplierDetailResponse:
    supplier = await supplier_service.get_supplier(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Supplier not found')
    supplier = await supplier_service.update_supplier(db, supplier, data)
    return SupplierDetailResponse.model_validate(supplier)


@router.delete('/{supplier_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_supplier(
    supplier_id: int,
    _: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> None:
    supplier = await supplier_service.get_supplier(db, supplier_id)
    if supplier is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Supplier not found')
    await supplier_service.delete_supplier(db, supplier)


async def _require_supplier(db: AsyncSession, supplier_id: int) -> None:
    if await supplier_service.get_supplier(db, supplier_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Supplier not found')


async def _require_agreement(db: AsyncSession, supplier_id: int, agreement_id: int):
    await _require_supplier(db, supplier_id)
    agreement = await supplier_agreement_service.get_agreement(db, supplier_id, agreement_id)
    if agreement is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Agreement not found')
    return agreement


@router.get('/{supplier_id}/agreements', response_model=list[SupplierAgreementResponse])
async def list_agreements(
    supplier_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> list[SupplierAgreementResponse]:
    await _require_supplier(db, supplier_id)
    agreements = await supplier_agreement_service.list_agreements(db, supplier_id)
    return [SupplierAgreementResponse.model_validate(a) for a in agreements]


@router.post(
    '/{supplier_id}/agreements',
    response_model=SupplierAgreementResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_agreement(
    supplier_id: int,
    data: SupplierAgreementCreate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SupplierAgreementResponse:
    await _require_supplier(db, supplier_id)
    agreement = await supplier_agreement_service.create_agreement(db, supplier_id, data)
    return SupplierAgreementResponse.model_validate(agreement)


@router.put(
    '/{supplier_id}/agreements/{supplier_agreement_id}',
    response_model=SupplierAgreementResponse,
)
async def update_agreement(
    supplier_id: int,
    supplier_agreement_id: int,
    data: SupplierAgreementUpdate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> SupplierAgreementResponse:
    agreement = await _require_agreement(db, supplier_id, supplier_agreement_id)
    agreement = await supplier_agreement_service.update_agreement(db, agreement, data)
    return SupplierAgreementResponse.model_validate(agreement)


@router.delete(
    '/{supplier_id}/agreements/{supplier_agreement_id}',
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_agreement(
    supplier_id: int,
    supplier_agreement_id: int,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> None:
    agreement = await _require_agreement(db, supplier_id, supplier_agreement_id)
    await supplier_agreement_service.delete_agreement(db, agreement)
