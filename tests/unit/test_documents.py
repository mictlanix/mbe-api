"""Folio assignment and the editability guard, shared by every document numbered per facility.

Folio assignment matters more than it looks. The guarantee comes from taking a `FOR UPDATE` lock on
the owning facility row before reading `MAX(serial)`; customer payments have no unique
`(facility, serial)` index behind it at all, and on the other tables a lost race surfaces only as a
generic 409. These tests pin that the lock is actually requested. Where the lock sits relative to
the step's other reads is pinned per step in `test_numbering_locks.py` (spec 035).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.models.sales import CustomerPayment, CustomerRefund, SalesOrder, SalesQuote
from app.services.documents import assert_editable, assign_folio, lock_facility


def _db(max_serial: int | None) -> AsyncMock:
    db = AsyncMock()
    results = [
        SimpleNamespace(scalar_one_or_none=lambda: 1),  # the FOR UPDATE facility lock
        SimpleNamespace(scalar_one_or_none=lambda: max_serial),  # MAX(serial)
    ]
    db.execute = AsyncMock(side_effect=results)
    return db


def _sql(db: AsyncMock, call: int) -> str:
    return str(db.execute.await_args_list[call].args[0]).lower()


class TestLockFacility:
    """The numbering lock on its own (spec 035, research R2).

    A step that numbers takes it before any ordinary read, so the `MAX(serial)` that follows reads
    past every earlier holder's commit.
    """

    @pytest.mark.asyncio
    async def test_locks_the_facility_row_for_update(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: 5))

        await lock_facility(db, 5)

        assert db.execute.await_count == 1
        assert 'for update' in _sql(db, 0)
        assert 'facility' in _sql(db, 0)

    @pytest.mark.asyncio
    async def test_unknown_facility_is_refused(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(return_value=SimpleNamespace(scalar_one_or_none=lambda: None))

        with pytest.raises(HTTPException) as exc:
            await lock_facility(db, 999)

        assert exc.value.status_code == 404


class TestAssignFolio:
    @pytest.mark.asyncio
    async def test_first_document_for_a_facility_gets_one(self) -> None:
        db = _db(max_serial=None)

        assert await assign_folio(db, SalesOrder, facility=1) == 1

    @pytest.mark.asyncio
    async def test_subsequent_document_increments(self) -> None:
        db = _db(max_serial=41)

        assert await assign_folio(db, SalesOrder, facility=1) == 42

    @pytest.mark.asyncio
    async def test_locks_the_facility_row_before_reading_max(self) -> None:
        """The lock is the whole mechanism; no database constraint backs it up."""
        db = _db(max_serial=7)

        await assign_folio(db, SalesOrder, facility=3)

        assert 'for update' in _sql(db, 0)
        assert 'facility' in _sql(db, 0)
        assert 'max' in _sql(db, 1)

    @pytest.mark.asyncio
    async def test_scopes_the_max_to_the_requested_facility(self) -> None:
        """Folios run per facility, so another facility's numbering must not leak in."""
        db = _db(max_serial=7)

        await assign_folio(db, SalesOrder, facility=3)

        assert 'facility' in _sql(db, 1)

    @pytest.mark.asyncio
    @pytest.mark.parametrize('model', [SalesOrder, SalesQuote, CustomerRefund, CustomerPayment])
    async def test_works_for_every_document_type(self, model: type) -> None:
        db = _db(max_serial=10)

        assert await assign_folio(db, model, facility=1) == 11

    @pytest.mark.asyncio
    async def test_unknown_facility_is_refused(self) -> None:
        db = AsyncMock()
        db.execute = AsyncMock(
            return_value=SimpleNamespace(scalar_one_or_none=lambda: None)
        )

        with pytest.raises(HTTPException) as exc:
            await assign_folio(db, SalesOrder, facility=999)

        assert exc.value.status_code == 404


class TestAssertEditable:
    def test_draft_is_editable(self) -> None:
        assert_editable(SimpleNamespace(completed=False, cancelled=False))

    def test_completed_document_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_editable(SimpleNamespace(completed=True, cancelled=False))

        assert exc.value.status_code == 409

    def test_cancelled_document_is_refused(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_editable(SimpleNamespace(completed=False, cancelled=True))

        assert exc.value.status_code == 409

    def test_refusal_says_which_state_blocked_it(self) -> None:
        with pytest.raises(HTTPException) as exc:
            assert_editable(SimpleNamespace(completed=False, cancelled=True))

        assert 'cancelled' in exc.value.detail.lower()
