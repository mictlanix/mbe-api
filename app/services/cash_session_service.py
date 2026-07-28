"""Cash sessions — a cashier's shift on a drawer.

A session bounds the cash a cashier handles and is what ties counter payments to a shift. It is
also a hard prerequisite for confirming a refund (FR-063), which is why this lands before returns.

The opening amount has no column on `cash_session`; it is stored as a `cash_count` row of the
starting-cash type, which is how the legacy schema records it.
"""

from collections.abc import Sequence
from datetime import date, datetime
from decimal import Decimal

from fastapi import HTTPException, status
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.deps import CurrentUser
from app.enums import CashCountType
from app.models.core import CashCount, CashDrawer, CashSession
from app.models.sales import CustomerPayment
from app.schemas.cash_session import CashSessionClose, CashSessionOpen, SessionState

# ── Decision rules (pure) ─────────────────────────────────────────────────────


def session_state(session: object | None, *, today: date) -> SessionState:
    """Classify the cashier's session (FR-053).

    A session opened on an earlier day is `STALE` rather than simply open: it must be closed before
    more money is taken against it, and the client needs to tell that apart from having none.
    """
    if session is None:
        return SessionState.NONE

    start = getattr(session, 'start', None)
    if start is not None and start.date() < today:
        return SessionState.STALE
    return SessionState.OPEN


# ── Context ───────────────────────────────────────────────────────────────────



def _drawer(current: CurrentUser, requested: int | None) -> int:
    drawer = requested if requested is not None else current.cash_drawer_id
    if drawer is None:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail='No cash drawer is configured for your user; set one or supply it explicitly',
        )
    return drawer


# ── Queries ───────────────────────────────────────────────────────────────────


async def open_session_for_cashier(db: AsyncSession, cashier: int) -> CashSession | None:
    """The cashier's current open session — the most recent, if legacy data left several.

    `end IS NULL` is not unique in practice: the legacy application left cashiers with several
    sessions open at once. Asserting uniqueness here raised `MultipleResultsFound`, which surfaced
    as a 500 on any payment recorded by such a cashier. `open_session` still refuses to *create* a
    second, so this tolerance only ever applies to pre-existing rows.
    """
    return (
        await db.execute(
            select(CashSession)
            .where(CashSession.cashier == cashier, CashSession.end.is_(None))
            .order_by(CashSession.start.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def open_session_for_drawer(db: AsyncSession, drawer: int) -> CashSession | None:
    """The drawer's current open session — most recent first, for the same reason as above."""
    return (
        await db.execute(
            select(CashSession)
            .where(CashSession.cash_drawer == drawer, CashSession.end.is_(None))
            .order_by(CashSession.start.desc())
            .limit(1)
        )
    ).scalar_one_or_none()


async def get_session(db: AsyncSession, cash_session_id: int) -> CashSession | None:
    return await db.get(CashSession, cash_session_id)


async def opening_amount(db: AsyncSession, cash_session_id: int) -> Decimal:
    total = (
        await db.execute(
            select(func.sum(CashCount.denomination * CashCount.quantity)).where(
                CashCount.session == cash_session_id,
                CashCount.type == int(CashCountType.STARTING_CASH),
            )
        )
    ).scalar_one_or_none()
    return total if total is not None else Decimal(0)


async def payments_by_method(db: AsyncSession, cash_session_id: int) -> list[dict]:
    """What the session took, grouped by payment method (FR-051)."""
    rows = (
        await db.execute(
            select(CustomerPayment.method, func.sum(CustomerPayment.amount))
            .where(CustomerPayment.cash_session == cash_session_id)
            .group_by(CustomerPayment.method)
            .order_by(CustomerPayment.method)
        )
    ).all()
    return [{'method': method, 'total': total or Decimal(0)} for method, total in rows]


async def attach_derived(db: AsyncSession, session: CashSession) -> CashSession:
    session.__dict__['opening_amount'] = await opening_amount(db, session.cash_session_id)
    session.__dict__['payments_by_method'] = await payments_by_method(db, session.cash_session_id)
    return session


async def attach_summary_amounts(db: AsyncSession, sessions: Sequence[CashSession]) -> None:
    """Opening amounts and per-method payment totals for a whole page, in two queries."""
    ids = [s.cash_session_id for s in sessions]
    if not ids:
        return

    opening_rows = (
        await db.execute(
            select(CashCount.session, func.sum(CashCount.denomination * CashCount.quantity))
            .where(
                CashCount.session.in_(ids),
                CashCount.type == int(CashCountType.STARTING_CASH),
            )
            .group_by(CashCount.session)
        )
    ).all()
    opening = {sid: amount or Decimal(0) for sid, amount in opening_rows}

    payment_rows = (
        await db.execute(
            select(
                CustomerPayment.cash_session,
                CustomerPayment.method,
                func.sum(CustomerPayment.amount),
            )
            .where(CustomerPayment.cash_session.in_(ids))
            .group_by(CustomerPayment.cash_session, CustomerPayment.method)
            .order_by(CustomerPayment.method)
        )
    ).all()
    by_session: dict[int, list[dict]] = {sid: [] for sid in ids}
    for sid, method, amount in payment_rows:
        by_session.setdefault(sid, []).append(
            {'method': method, 'total': amount or Decimal(0)}
        )

    for session in sessions:
        session.__dict__['opening_amount'] = opening.get(
            session.cash_session_id, Decimal(0)
        )
        session.__dict__['payments_by_method'] = by_session.get(session.cash_session_id, [])


# ── Transitions ───────────────────────────────────────────────────────────────


async def open_session(
    db: AsyncSession, data: CashSessionOpen, *, current: CurrentUser
) -> CashSession:
    """One open session per drawer and one per cashier (FR-050)."""
    cashier = current.employee_id
    drawer_id = _drawer(current, data.cash_drawer)

    if await db.get(CashDrawer, drawer_id) is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail='Cash drawer not found')

    if await open_session_for_drawer(db, drawer_id) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='That cash drawer already has an open session',
        )
    if await open_session_for_cashier(db, cashier) is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail='You already have an open session; close it before opening another',
        )

    session = CashSession(
        start=datetime.now(), end=None, cashier=cashier, cash_drawer=drawer_id,
        cash_supervisor=None,
    )
    db.add(session)
    await db.flush()

    if data.opening_amount > 0:
        db.add(
            CashCount(
                session=session.cash_session_id,
                denomination=data.opening_amount,
                quantity=1,
                type=int(CashCountType.STARTING_CASH),
            )
        )

    await db.commit()
    await db.refresh(session)
    return await attach_derived(db, session)


async def close_session(
    db: AsyncSession, session: CashSession, data: CashSessionClose, *, current: CurrentUser
) -> CashSession:
    """Record the denomination counts and end the shift (FR-052)."""
    employee = current.employee_id

    if session.end is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT, detail='Session is already closed'
        )

    for count in data.counts:
        db.add(
            CashCount(
                session=session.cash_session_id,
                denomination=count.denomination,
                quantity=count.quantity,
                type=int(CashCountType.COUNTED_CASH),
            )
        )

    session.end = datetime.now()
    session.cash_supervisor = employee
    await db.commit()
    await db.refresh(session)
    return await attach_derived(db, session)


async def current_session(
    db: AsyncSession, *, current: CurrentUser
) -> tuple[SessionState, CashSession | None]:
    cashier = current.employee_id
    session = await open_session_for_cashier(db, cashier)
    state = session_state(session, today=date.today())
    if session is not None:
        await attach_derived(db, session)
    return state, session


async def list_sessions(
    db: AsyncSession,
    *,
    current: CurrentUser,
    cash_drawer: int | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[CashSession], int]:
    base = select(CashSession)
    count_q = select(func.count()).select_from(CashSession)
    if cash_drawer is not None:
        base = base.where(CashSession.cash_drawer == cash_drawer)
        count_q = count_q.where(CashSession.cash_drawer == cash_drawer)

    total: int = (await db.execute(count_q)).scalar_one()
    page = base.order_by(CashSession.cash_session_id.desc()).offset(skip).limit(limit)
    items = (await db.execute(page)).scalars().all()
    await attach_summary_amounts(db, items)
    return items, total
