"""Accounts payable (spec 033, US3): what the company owes its suppliers, aged.

Read on AccountsPayable (19), company-wide whatever the caller's facility: a PO has no facility, and
what the company owes a supplier isn't one facility's debt (FR-019). Balances count from the go-live
cutoff (spec 032), so the view is empty until it's set.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, CurrencyCode, SystemObject
from app.schemas.accounts import (
    AccountPayment,
    AccountScope,
    AccountsSummaryResponse,
    PartyDetail,
)
from app.services import accounts_service, supplier_payment_service, totals

router = APIRouter()

_READ = require_privilege(SystemObject.ACCOUNTS_PAYABLE, AccessRight.READ)
_COMPANY_WIDE = AccountScope(facility=None, all_facilities=True)


@router.get('', response_model=AccountsSummaryResponse)
async def list_accounts_payable(
    party: int | None = Query(None, description='One supplier'),
    overdue: bool = Query(False, description='Only suppliers with something overdue'),
    sort: Literal['owed', 'overdue', 'oldest'] = Query('owed'),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> AccountsSummaryResponse:
    result = await accounts_service.summary(
        db,
        accounts_service.SUPPLIERS,
        facility=None,
        party=party,
        overdue=overdue,
        sort=sort,
        skip=skip,
        limit=limit,
    )
    return AccountsSummaryResponse(
        **result, scope=_COMPANY_WIDE, cutoff=await supplier_payment_service.get_cutoff(db)
    )


@router.get('/{supplier_id}', response_model=PartyDetail)
async def get_account_payable(
    supplier_id: int,
    _: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> PartyDetail:
    """The supplier's open POs, and the payments with credit that can be applied (spec 032)."""
    result = await accounts_service.detail(
        db, accounts_service.SUPPLIERS, supplier_id, facility=None
    )
    payments = await supplier_payment_service.payments_with_credit(db, suppliers=[supplier_id])
    return PartyDetail(
        **result,
        payments=[
            AccountPayment(
                id=payment.supplier_payment_id,
                serial=payment.serial,
                date=payment.date,
                method=payment.method,
                currency=CurrencyCode.MXN,
                amount=payment.amount.quantize(totals.CENTS),
                unapplied=credit.quantize(totals.CENTS),
            )
            for payment, credit in payments
        ],
    )
