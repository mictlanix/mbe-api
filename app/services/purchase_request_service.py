"""Purchase requests and their approval (spec 031, US1).

**States (research R1)** come from the legacy flags, NULL counting as false: draft, submitted
(`completed`), approved (`completed` and `approved`), cancelled.
"""

from fastapi import HTTPException, status
from sqlalchemy import ColumnElement, and_, false, func, true

from app.models.purchases import PurchaseRequest
from app.schemas.purchases import RequestStatus

# ── States (R1) ───────────────────────────────────────────────────────────────


def request_status(
    completed: bool | None, approved: bool | None, cancelled: bool | None
) -> RequestStatus:
    if cancelled:
        return RequestStatus.CANCELLED
    if not completed:
        return RequestStatus.DRAFT
    if approved:
        return RequestStatus.APPROVED
    return RequestStatus.SUBMITTED


def _flag(column) -> ColumnElement[bool]:  # noqa: ANN001
    return func.coalesce(column, false())


def request_status_clause(value: str) -> ColumnElement[bool]:
    header = PurchaseRequest
    live = _flag(header.cancelled) == false()
    if value == RequestStatus.DRAFT:
        return and_(live, _flag(header.completed) == false())
    if value == RequestStatus.SUBMITTED:
        return and_(live, _flag(header.completed) == true(), _flag(header.approved) == false())
    if value == RequestStatus.APPROVED:
        return and_(live, _flag(header.completed) == true(), _flag(header.approved) == true())
    if value == RequestStatus.CANCELLED:
        return _flag(header.cancelled) == true()
    raise HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail='status must be one of draft, submitted, approved, cancelled',
    )
