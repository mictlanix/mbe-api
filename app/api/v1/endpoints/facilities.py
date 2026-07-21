from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, EntityStatus, SystemObject
from app.schemas import ListResponse
from app.schemas.core import FacilityCreate, FacilityResponse, FacilityUpdate
from app.services import facility_service, image_service

router = APIRouter()


@router.get('', response_model=ListResponse[FacilityResponse])
async def list_facilities(
    search: str | None = Query(None),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.FACILITIES, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[FacilityResponse]:
    items, total = await facility_service.list_facilities(
        db, search=search, status=status, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=FacilityResponse, status_code=http_status.HTTP_201_CREATED)
async def create_facility(
    data: FacilityCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.FACILITIES, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> FacilityResponse:
    facility = await facility_service.create_facility(db, data)
    return FacilityResponse.model_validate(facility)


@router.post('/{facility_id}/logo', response_model=FacilityResponse)
async def upload_facility_logo(
    facility_id: int,
    file: UploadFile = File(...),
    _: CurrentUser = Depends(require_privilege(SystemObject.FACILITIES, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> FacilityResponse:
    facility = await facility_service.get_facility(db, facility_id)
    if facility is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Facility not found')
    content = await file.read()
    try:
        filename = await image_service.process_and_save_image(content, settings.images_dir)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    facility = await facility_service.update_facility(db, facility, FacilityUpdate(logo=filename))
    return FacilityResponse.model_validate(facility)


@router.get('/{facility_id}', response_model=FacilityResponse)
async def get_facility(
    facility_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.FACILITIES, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> FacilityResponse:
    facility = await facility_service.get_facility(db, facility_id)
    if facility is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Facility not found')
    return FacilityResponse.model_validate(facility)


@router.put('/{facility_id}', response_model=FacilityResponse)
async def update_facility(
    facility_id: int,
    data: FacilityUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.FACILITIES, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> FacilityResponse:
    facility = await facility_service.get_facility(db, facility_id)
    if facility is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Facility not found')
    facility = await facility_service.update_facility(db, facility, data)
    return FacilityResponse.model_validate(facility)


@router.delete('/{facility_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_facility(
    facility_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.FACILITIES, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    facility = await facility_service.get_facility(db, facility_id)
    if facility is None:
        raise HTTPException(status_code=http_status.HTTP_404_NOT_FOUND, detail='Facility not found')
    await facility_service.delete_facility(db, facility)
