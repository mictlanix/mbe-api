from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi import status as http_status
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
from app.schemas import ListResponse
from app.schemas.core import BankAccountCreate, BankAccountResponse, BankAccountUpdate
from app.services import bank_account_service

router = APIRouter()


@router.get('', response_model=ListResponse[BankAccountResponse])
async def list_bank_accounts(
    search: str | None = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    _: CurrentUser = Depends(require_privilege(SystemObject.BANK_ACCOUNTS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> ListResponse[BankAccountResponse]:
    items, total = await bank_account_service.list_bank_accounts(
        db, search=search, skip=skip, limit=limit
    )
    return ListResponse(items=list(items), total=total)


@router.post('', response_model=BankAccountResponse, status_code=http_status.HTTP_201_CREATED)
async def create_bank_account(
    data: BankAccountCreate,
    _: CurrentUser = Depends(require_privilege(SystemObject.BANK_ACCOUNTS, AccessRight.CREATE)),
    db: AsyncSession = Depends(get_db),
) -> BankAccountResponse:
    account = await bank_account_service.create_bank_account(db, data)
    return BankAccountResponse.model_validate(account)


@router.get('/{bank_account_id}', response_model=BankAccountResponse)
async def get_bank_account(
    bank_account_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.BANK_ACCOUNTS, AccessRight.READ)),
    db: AsyncSession = Depends(get_db),
) -> BankAccountResponse:
    account = await bank_account_service.get_bank_account(db, bank_account_id)
    if account is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Bank account not found'
        )
    return BankAccountResponse.model_validate(account)


@router.put('/{bank_account_id}', response_model=BankAccountResponse)
async def update_bank_account(
    bank_account_id: int,
    data: BankAccountUpdate,
    _: CurrentUser = Depends(require_privilege(SystemObject.BANK_ACCOUNTS, AccessRight.UPDATE)),
    db: AsyncSession = Depends(get_db),
) -> BankAccountResponse:
    account = await bank_account_service.get_bank_account(db, bank_account_id)
    if account is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Bank account not found'
        )
    account = await bank_account_service.update_bank_account(db, account, data)
    return BankAccountResponse.model_validate(account)


@router.delete('/{bank_account_id}', status_code=http_status.HTTP_204_NO_CONTENT)
async def delete_bank_account(
    bank_account_id: int,
    _: CurrentUser = Depends(require_privilege(SystemObject.BANK_ACCOUNTS, AccessRight.DELETE)),
    db: AsyncSession = Depends(get_db),
) -> None:
    account = await bank_account_service.get_bank_account(db, bank_account_id)
    if account is None:
        raise HTTPException(
            status_code=http_status.HTTP_404_NOT_FOUND, detail='Bank account not found'
        )
    await bank_account_service.delete_bank_account(db, account)
