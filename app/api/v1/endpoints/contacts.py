from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.core import ContactCreate, ContactResponse, ContactUpdate
from app.services import contact_service

router = APIRouter()


@router.get('', response_model=ListResponse[ContactResponse])
async def list_contacts(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.CONTACTS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[ContactResponse]:
    items, total = await contact_service.list_contacts(db, search=search, skip=skip, limit=limit)
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=ContactResponse, status_code=http_status.HTTP_201_CREATED)
async def create_contact(
    data: ContactCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.CONTACTS, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> ContactResponse:
    contact = await contact_service.create_contact(db, data)
    return ContactResponse.model_validate(contact)


@router.get('/{contact_id}', response_model=ContactResponse)
async def get_contact(
    contact_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.CONTACTS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ContactResponse:
    contact = await contact_service.get_contact(db, contact_id)
    if contact is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Contact not found')
    return ContactResponse.model_validate(contact)


@router.put('/{contact_id}', response_model=ContactResponse)
async def update_contact(
    contact_id: int,
    data: ContactUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.CONTACTS, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> ContactResponse:
    contact = await contact_service.get_contact(db, contact_id)
    if contact is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Contact not found')
    contact = await contact_service.update_contact(db, contact, data)
    return ContactResponse.model_validate(contact)


@router.delete('/{contact_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_contact(
    contact_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.CONTACTS, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    contact = await contact_service.get_contact(db, contact_id)
    if contact is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Contact not found')
    await contact_service.delete_contact(db, contact)
