"""Accounts receivable (spec 033, US2): what customers owe, aged, against their credit limits.

Read on AccountsReceivable (18). The view is scoped to the caller's facility unless they're an
administrator or hold SearchCreditsFromAllStores (101), which the current system defines but never
checks: its AR screen shows every facility to everyone.
"""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response, status
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.core.deps import CurrentUser, can_see_every_facility, require_privilege
from app.db.session import get_db
from app.enums import AccessRight, SystemObject
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
    customer_payment_service,
    mail,
    print_contexts,
    totals,
)

router = APIRouter()

_READ = require_privilege(SystemObject.ACCOUNTS_RECEIVABLE, AccessRight.READ)


async def _sees_every_facility(db: AsyncSession, current: CurrentUser) -> bool:
    return await can_see_every_facility(db, current, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES)


async def _scope(db: AsyncSession, current: CurrentUser, facility: int | None) -> int | None:
    """The facility the view covers, None for all (FR-018). A scoped caller may name only their
    own facility."""
    if await _sees_every_facility(db, current):
        return facility
    if facility is not None and facility != current.facility_id:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="Seeing another facility's credits needs SearchCreditsFromAllStores",
        )
    return current.facility_id


@router.get('', response_model=AccountsSummaryResponse)
async def list_accounts_receivable(
    party: int | None = Query(None, description='One customer'),
    overdue: bool = Query(False, description='Only customers with something overdue'),
    facility: int | None = Query(None, description='Needs SearchCreditsFromAllStores (101)'),
    sales_agent: int | None = Query(None),
    sort: Literal['owed', 'overdue', 'oldest'] = Query('owed'),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> AccountsSummaryResponse:
    scope = await _scope(db, current, facility)
    result = await accounts_service.summary(
        db,
        accounts_service.CUSTOMERS,
        facility=scope,
        party=party,
        overdue=overdue,
        sales_agent=sales_agent,
        sort=sort,
        skip=skip,
        limit=limit,
    )
    return AccountsSummaryResponse(
        **result, scope=AccountScope(facility=scope, all_facilities=scope is None)
    )


@router.get('/{customer_id}', response_model=PartyDetail)
async def get_account_receivable(
    customer_id: int,
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> PartyDetail:
    """The customer's open orders in scope, and the payments that can be applied to them.

    Another facility's payments are PaymentsEditor (100) territory, as on `GET /customer-payments`
    (#271), whether or not the caller holds SearchCreditsFromAllStores.
    """
    scope = await _scope(db, current, None)
    result = await accounts_service.detail(
        db, accounts_service.CUSTOMERS, customer_id, facility=scope
    )
    every_payment = await can_see_every_facility(db, current, SystemObject.PAYMENTS_EDITOR)
    payments = await customer_payment_service.payments_with_credit(
        db, customers=[customer_id], facility=None if every_payment else current.facility_id
    )
    return PartyDetail(
        **result,
        payments=[
            AccountPayment(
                id=payment.customer_payment_id,
                serial=payment.serial,
                date=payment.date,
                method=payment.method,
                currency=payment.currency,
                amount=payment.amount.quantize(totals.CENTS),
                unapplied=payment.unapplied.quantize(totals.CENTS),
            )
            for payment in payments
        ],
    )


@router.get('/{customer_id}/statement')
async def print_statement(
    customer_id: int,
    current: CurrentUser = Depends(_READ),
    db: AsyncSession = Depends(get_db),
) -> Response:
    """The customer's account statement as a PDF, covering every facility (FR-023, FR-025)."""
    template, context = await print_contexts.account_statement_context(
        db, accounts_service.CUSTOMERS, customer_id, facility=current.facility_id
    )
    pdf = await rendering.render_pdf(template, context)
    return rendering.pdf_response(pdf, f'estado-de-cuenta-{customer_id}.pdf')


@router.post('/{customer_id}/statement/email', response_model=StatementEmailResponse)
async def email_statement(
    customer_id: int,
    data: StatementEmailRequest,
    current: CurrentUser = Depends(_READ),
    mailer: mail.Mailer | None = Depends(mail.get_mailer),
    db: AsyncSession = Depends(get_db),
) -> StatementEmailResponse:
    """Email the statement to the customer's contacts and typed addresses, and record the send on
    the customer (FR-024)."""
    if mailer is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail='Email is not configured'
        )
    sent_at, to = await account_email.email_statement(
        db,
        accounts_service.CUSTOMERS,
        customer_id,
        contact_ids=data.contacts,
        to=data.to,
        mailer=mailer,
        current=current,
    )
    return StatementEmailResponse(sent_at=sent_at, to=to)
