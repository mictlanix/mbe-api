import os

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from fastapi import status as http_status
from fastapi.responses import FileResponse
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.deps import CurrentUser, get_current_user
from app.db.session import get_db
from app.enums import EntityStatus
from app.schemas import ListResponse
from app.schemas.core import VehicleOperatorCreate, VehicleOperatorResponse, VehicleOperatorUpdate
from app.services import image_service, vehicle_operator_service

router = APIRouter()


@router.get('', response_model=ListResponse[VehicleOperatorResponse])
async def list_vehicle_operators(
    search: str | None = Query(None),
    employee: int | None = Query(None),
    status: EntityStatus | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[VehicleOperatorResponse]:
    items, total = await vehicle_operator_service.list_vehicle_operators(
        db, search=search, employee=employee, status=status, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=VehicleOperatorResponse, status_code=http_status.HTTP_201_CREATED)
async def create_vehicle_operator(
    data: VehicleOperatorCreate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VehicleOperatorResponse:
    vo = await vehicle_operator_service.create_vehicle_operator(db, data)
    return VehicleOperatorResponse.model_validate(vo)


@router.get('/{vehicle_operator_id}', response_model=VehicleOperatorResponse)
async def get_vehicle_operator(
    vehicle_operator_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VehicleOperatorResponse:
    vo = await vehicle_operator_service.get_vehicle_operator(db, vehicle_operator_id)
    if vo is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Vehicle operator not found'
        )
    return VehicleOperatorResponse.model_validate(vo)


@router.put('/{vehicle_operator_id}', response_model=VehicleOperatorResponse)
async def update_vehicle_operator(
    vehicle_operator_id: int,
    data: VehicleOperatorUpdate,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VehicleOperatorResponse:
    vo = await vehicle_operator_service.get_vehicle_operator(db, vehicle_operator_id)
    if vo is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Vehicle operator not found'
        )
    vo = await vehicle_operator_service.update_vehicle_operator(db, vo, data)
    return VehicleOperatorResponse.model_validate(vo)


@router.delete('/{vehicle_operator_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_vehicle_operator(
    vehicle_operator_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    vo = await vehicle_operator_service.get_vehicle_operator(db, vehicle_operator_id)
    if vo is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Vehicle operator not found'
        )
    photo = await vehicle_operator_service.delete_vehicle_operator(db, vo)
    if photo is not None:
        await image_service.delete_image(settings.license_photos_dir, photo)


@router.post('/{vehicle_operator_id}/license-photo', response_model=VehicleOperatorResponse)
async def upload_license_photo(
    vehicle_operator_id: int,
    file: UploadFile = File(...),
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> VehicleOperatorResponse:
    vo = await vehicle_operator_service.get_vehicle_operator(db, vehicle_operator_id)
    if vo is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Vehicle operator not found'
        )
    content = await file.read()
    try:
        filename = await image_service.save_proof_image(content, settings.license_photos_dir)
    except ValueError as exc:
        raise HTTPException(status_code=http_status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))
    previous = await vehicle_operator_service.set_license_photo(db, vo, filename)
    if previous is not None:
        await image_service.delete_image(settings.license_photos_dir, previous)
    return VehicleOperatorResponse.model_validate(vo)


@router.get('/{vehicle_operator_id}/license-photo')
async def get_license_photo(
    vehicle_operator_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> FileResponse:
    """Streamed behind authentication, never a static URL.

    A driver license photo is personal data. The `/images` mount is unauthenticated — correct for
    product photos, wrong for this.
    """
    vo = await vehicle_operator_service.get_vehicle_operator(db, vehicle_operator_id)
    if vo is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Vehicle operator not found'
        )
    if vo.license_photo is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='License photo not found'
        )
    path = os.path.join(settings.license_photos_dir, vo.license_photo)
    if not os.path.exists(path):
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND,
            detail='License photo is missing from storage',
        )
    return FileResponse(path, media_type='image/png')


@router.delete('/{vehicle_operator_id}/license-photo', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_license_photo(
    vehicle_operator_id: int,
    _: CurrentUser = Depends(get_current_user),
    db: AsyncSession = Depends(get_db),
) -> None:
    vo = await vehicle_operator_service.get_vehicle_operator(db, vehicle_operator_id)
    if vo is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Vehicle operator not found'
        )
    if vo.license_photo is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='License photo not found'
        )
    previous = await vehicle_operator_service.clear_license_photo(db, vo)
    if previous is not None:
        await image_service.delete_image(settings.license_photos_dir, previous)
