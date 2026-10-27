"""Purchase orders: drafts, lines from requests, confirm into receipts, PDF and email (spec 031,
US2, US3 and US5).

**States (research R1)**: draft, `completed` (the spec's "confirmed") and cancelled, the same values
receipts use.
"""

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_

from app.models.purchases import PurchaseOrder
from app.schemas.sales_order import DocumentStatus, derive_status

# ── States (R1) ───────────────────────────────────────────────────────────────


def order_status(completed: bool | None, cancelled: bool | None) -> DocumentStatus:
    return derive_status(completed=bool(completed), cancelled=bool(cancelled))


def order_status_clause(value: str) -> ColumnElement[bool]:
    header = PurchaseOrder
    if value == DocumentStatus.DRAFT:
        return and_(header.completed.is_(False), header.cancelled.is_(False))
    if value == DocumentStatus.COMPLETED:
        return and_(header.completed.is_(True), header.cancelled.is_(False))
    if value == DocumentStatus.CANCELLED:
        return header.cancelled.is_(True)
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='status must be one of draft, completed, cancelled',
    )
