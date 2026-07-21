from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, AddressType, EntityStatus, SystemObject
from app.schemas import ListResponse
from app.schemas.core import AddressCreate, AddressResponse, AddressUpdate
from app.services import address_service

router = APIRouter()


@router.get("", response_model=ListResponse[AddressResponse])
async def list_addresses(
    search: str | None = Query(None),
    type: AddressType | None = Query(None),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.ADDRESSES, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[AddressResponse]:
    items, total = await address_service.list_addresses(
        db, search=search, type=type, status=status, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post("", response_model=AddressResponse, status_code=http_status.HTTP_201_CREATED)
async def create_address(
    data: AddressCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.ADDRESSES, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> AddressResponse:
    address = await address_service.create_address(db, data)
    return AddressResponse.model_validate(address)


@router.get("/{address_id}", response_model=AddressResponse)
async def get_address(
    address_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.ADDRESSES, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> AddressResponse:
    address = await address_service.get_address(db, address_id)
    if address is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Address not found")
    return AddressResponse.model_validate(address)


@router.put("/{address_id}", response_model=AddressResponse)
async def update_address(
    address_id: int,
    data: AddressUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.ADDRESSES, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> AddressResponse:
    address = await address_service.get_address(db, address_id)
    if address is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Address not found")
    address = await address_service.update_address(db, address, data)
    return AddressResponse.model_validate(address)


@router.delete("/{address_id}", status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_address(
    address_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.ADDRESSES, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    address = await address_service.get_address(db, address_id)
    if address is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail="Address not found")
    await address_service.delete_address(db, address)
