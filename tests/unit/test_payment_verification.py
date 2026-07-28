"""The unverified-payment queue and the verify/reject actions (FR-071, FR-072).

A control function: a supervisor checks payments that arrived off the counter — bank transfers,
typically — against the statement. Verifying stamps the supervisor's employee; rejecting leaves an
incidence entry with a reason rather than silently flagging the row.
"""

from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.deps import CurrentUser
from app.enums import SourceType
from app.services.customer_payment_service import (
    list_payments,
    reject_payment,
    verify_payment,
)


def _current(employee_id: int = 7) -> CurrentUser:
    return CurrentUser(
        user_id='super',
        session_version=1,
        administrator=True,
        facility_id=1,
        employee_id=employee_id,
    )


def _payment(**overrides) -> SimpleNamespace:
    base = dict(
        customer_payment_id=1,
        amount=Decimal('5000.00'),
        verifier=None,
        updater=7,
        modification_time=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _db(rows: list | None = None) -> AsyncMock:
    db = AsyncMock()
    results = [
        SimpleNamespace(scalar_one=lambda: len(rows or [])),
        SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: rows or [])),
    ]
    db.execute = AsyncMock(side_effect=results)
    db.added = []
    db.add = lambda obj: db.added.append(obj)
    return db


class TestUnverifiedQueue:
    @pytest.mark.asyncio
    async def test_filters_to_payments_with_no_verifier(self) -> None:
        db = _db()

        await list_payments(db, current=_current(), unverified_only=True)

        assert 'verifier' in str(db.execute.await_args_list[0].args[0]).lower()

    @pytest.mark.asyncio
    async def test_combines_with_method_and_amount_range(self) -> None:
        """FR-071 — the queue is filterable, not a flat dump."""
        db = _db()

        await list_payments(
            db,
            current=_current(),
            unverified_only=True,
            method=3,
            amount_min=Decimal('1000'),
            amount_max=Decimal('9000'),
        )

        sql = str(db.execute.await_args_list[0].args[0]).lower()
        assert 'method' in sql
        assert 'amount' in sql

    @pytest.mark.asyncio
    async def test_verified_true_selects_the_opposite_set(self) -> None:
        db = _db()

        await list_payments(db, current=_current(), verified=True)

        assert 'verifier' in str(db.execute.await_args_list[0].args[0]).lower()


class TestVerify:
    @pytest.mark.asyncio
    async def test_records_the_supervisors_employee(self) -> None:
        db = AsyncMock()
        payment = _payment()

        with patch(
            'app.services.customer_payment_service.attach_unapplied',
            AsyncMock(side_effect=lambda _db, p: p),
        ):
            result = await verify_payment(db, payment, current=_current(employee_id=42))

        assert result.verifier == 42
        db.commit.assert_awaited()

    @pytest.mark.asyncio
    async def test_already_verified_payment_is_refused(self) -> None:
        db = AsyncMock()

        with pytest.raises(HTTPException) as exc:
            await verify_payment(db, _payment(verifier=9), current=_current())

        assert exc.value.status_code == 409


class TestReject:
    @pytest.mark.asyncio
    async def test_writes_an_incidence_carrying_the_reason(self) -> None:
        """FR-072 — a rejection has to say why, or it is not actionable."""
        db = _db()

        with patch(
            'app.services.customer_payment_service.attach_unapplied',
            AsyncMock(side_effect=lambda _db, p: p),
        ):
            await reject_payment(
                db, _payment(), reason='Not on the bank statement', current=_current()
            )

        assert len(db.added) == 1
        entry = db.added[0]
        assert entry.source == int(SourceType.CUSTOMER_PAYMENT)
        assert entry.instance_id == 1
        assert 'Not on the bank statement' in (entry.comment or '')

    @pytest.mark.asyncio
    async def test_a_blank_reason_is_refused_and_nothing_is_written(self) -> None:
        db = _db()

        with pytest.raises(HTTPException) as exc:
            await reject_payment(db, _payment(), reason='   ', current=_current())

        assert exc.value.status_code == 422
        assert db.added == []
        db.commit.assert_not_awaited()
