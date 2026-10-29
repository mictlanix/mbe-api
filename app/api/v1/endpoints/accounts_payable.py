"""Accounts payable (spec 033, US3): what the company owes its suppliers, aged.

Read on AccountsPayable (19), company-wide whatever the caller's facility: a PO has no facility, and
what the company owes a supplier isn't one facility's debt (FR-019). Balances count from the go-live
cutoff (spec 032), so the view is empty until it's set.
"""

from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.deps import CurrentUser, require_privilege
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.db.session import get_db
from app.enums import AccessRight, CurrencyCode, SystemObject
from app.schemas.accounts import (
    AccountPayment,
    AccountScope,
    AccountsSummaryResponse,
    PartyDetail,
    StatementEmailRequest,
    StatementEmailResponse,
)
from app.services import (
    account_email,
    accounts_service,
    mail,
    print_contexts,
    supplier_payment_service,
    totals,
)

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


@router.get('/{supplier_id}/statement')
async def print_statement(
    supplier_id: int,
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The supplier's account statement as a PDF, covering every facility (FR-023, FR-025)."""
    template, context = await print_contexts.account_statement_context(
        db, accounts_service.SUPPLIERS, supplier_id, facility=current.facility_id
    )
    pdf = await rendering.render_pdf(template, context)
    return rendering.pdf_response(pdf, f'estado-de-cuenta-{supplier_id}.pdf')


@router.post('/{supplier_id}/statement/email', response_model=StatementEmailResponse)
async def email_statement(
    supplier_id: int,
    data: StatementEmailRequest,
    current: CurrentUser = Depends(_READ),
    mailer: mail.Mailer | None = Depends(mail.get_mailer),
    db: AsyncSession = Depends(get_db),
) -> StatementEmailResponse:
    """Email the statement to the supplier's contacts and typed addresses, and record the send on
    the supplier (FR-024)."""
    if mailer is None:
        raise DomainError(ErrorCode.EMAIL_NOT_CONFIGURED, 'Email is not configured')
    sent_at, to = await account_email.email_statement(
        db,
        accounts_service.SUPPLIERS,
        supplier_id,
        contact_ids=data.contacts,
        to=data.to,
        mailer=mailer,
        current=current,
    )
    return StatementEmailResponse(sent_at=sent_at, to=to)
