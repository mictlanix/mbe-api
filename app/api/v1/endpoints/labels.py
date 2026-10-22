from fastapi import APIRouter, Depends, HTTPException, Query, status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_any_privilege, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.core import LabelCreate, LabelResponse, LabelUpdate
from app.services import label_service

router = APIRouter()

_CREATE = require_privilege(SystemObject.LABELS, AccessRight.CREATE)
_UPDATE = require_privilege(SystemObject.LABELS, AccessRight.UPDATE)
_DELETE = require_privilege(SystemObject.LABELS, AccessRight.DELETE)
# Also read from other modules' screens (spec 022 FR-005).
_LOOKUP = require_any_privilege(
    (SystemObject.LABELS, AccessRight.READ),
    (SystemObject.PRODUCTS, AccessRight.READ),
    (SystemObject.PRICING, AccessRight.READ),
)


@router.get('', response_model=ListResponse[LabelResponse])
async def list_labels(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(_LOOKUP),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[LabelResponse]:
    items, total = await label_service.list_labels(db, search=search, skip=skip, limit=limit)
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=LabelResponse, status_code=status.HTTP_201_CREATED)
async def create_label(
    data: LabelCreate,
    _: CurrentUser = Depends(_CREATE),
    db: AsyncSession = Depends(get_db),
) -> LabelResponse:
    label = await label_service.create_label(db, data)
    return LabelResponse.model_validate(label)


@router.get('/{label_id}', response_model=LabelResponse)
async def get_label(
    label_id: int,
    _: CurrentUser = Depends(_LOOKUP),
    db: AsyncSession = Depends(get_db),
) -> LabelResponse:
    label = await label_service.get_label(db, label_id)
    if label is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Label not found')
    return LabelResponse.model_validate(label)


@router.put('/{label_id}', response_model=LabelResponse)
async def update_label(
    label_id: int,
    data: LabelUpdate,
    _: CurrentUser = Depends(_UPDATE),
    db: AsyncSession = Depends(get_db),
) -> LabelResponse:
    label = await label_service.get_label(db, label_id)
    if label is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Label not found')
    label = await label_service.update_label(db, label, data)
    return LabelResponse.model_validate(label)


@router.delete('/{label_id}', status_code=status.HTTP_204_NO_CONTENT)
async def delete_label(
    label_id: int,
    _: CurrentUser = Depends(_DELETE),
    db: AsyncSession = Depends(get_db),
) -> None:
    label = await label_service.get_label(db, label_id)
    if label is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Label not found')
    await label_service.delete_label(db, label)
