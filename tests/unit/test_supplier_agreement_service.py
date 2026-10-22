"""A supplier's agreements: the period rule, and the parent check (#13).

`supplier_agreement` is a child of a supplier and means nothing without it, so an agreement that
belongs to a different supplier than the path names must read as absent rather than be touched.
"""

from datetime import date
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.schemas.supplier import SupplierAgreementCreate, SupplierAgreementUpdate
from app.services import supplier_agreement_service as service


def _agreement(supplier: int = 1, **overrides) -> SimpleNamespace:
    base = dict(
        supplier_agreement_id=5,
        supplier=supplier,
        start=date(2026, 1, 1),
        end=date(2026, 12, 31),
        comment=None,
    )
    base.update(overrides)
    return SimpleNamespace(**base)


def _db(found: SimpleNamespace | None = None) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(return_value=found)
    db.add = lambda _: None
    return db


class TestCreate:
    @pytest.mark.asyncio
    async def test_an_end_before_the_start_is_refused_with_422(self) -> None:
        # `model_construct` skips the schema's own validator, so this is the service's guard.
        data = SupplierAgreementCreate.model_construct(
            start=date(2026, 2, 1), end=date(2026, 1, 1), comment=None
        )
        db = _db()

        with pytest.raises(HTTPException) as refused:
            await service.create_agreement(db, 1, data)

        assert refused.value.status_code == 422
        assert db.commit.await_count == 0

    @pytest.mark.asyncio
    async def test_a_single_day_agreement_is_accepted(self) -> None:
        data = SupplierAgreementCreate(start=date(2026, 2, 1), end=date(2026, 2, 1))
        db = _db()

        agreement = await service.create_agreement(db, 7, data)

        assert agreement.supplier == 7
        assert db.commit.await_count == 1


class TestUpdate:
    @pytest.mark.asyncio
    async def test_the_merged_values_are_validated(self) -> None:
        """Sending only `end` is checked against the stored `start`."""
        agreement = _agreement(start=date(2026, 6, 1), end=date(2026, 12, 31))
        db = _db()

        with pytest.raises(HTTPException) as refused:
            await service.update_agreement(
                db, agreement, SupplierAgreementUpdate(end=date(2026, 5, 31))
            )

        assert refused.value.status_code == 422
        assert db.commit.await_count == 0
        assert agreement.end == date(2026, 12, 31)

    @pytest.mark.asyncio
    async def test_a_valid_change_is_applied(self) -> None:
        agreement = _agreement()
        db = _db()

        await service.update_agreement(
            db, agreement, SupplierAgreementUpdate(end=date(2027, 1, 31), comment='renewed')
        )

        assert agreement.end == date(2027, 1, 31)
        assert agreement.comment == 'renewed'
        assert db.commit.await_count == 1


class TestParentCheck:
    @pytest.mark.asyncio
    async def test_an_agreement_of_another_supplier_is_absent(self) -> None:
        db = _db(_agreement(supplier=2))

        assert await service.get_agreement(db, 1, 5) is None

    @pytest.mark.asyncio
    async def test_an_agreement_of_the_same_supplier_is_found(self) -> None:
        found = _agreement(supplier=1)
        db = _db(found)

        assert await service.get_agreement(db, 1, 5) is found

    @pytest.mark.asyncio
    async def test_an_unknown_agreement_is_absent(self) -> None:
        assert await service.get_agreement(_db(None), 1, 5) is None


class TestListAndDelete:
    @pytest.mark.asyncio
    async def test_the_list_is_ordered_by_start_then_id(self) -> None:
        rows = SimpleNamespace(scalars=lambda: SimpleNamespace(all=lambda: []))
        db = _db()
        db.execute = AsyncMock(return_value=rows)

        await service.list_agreements(db, 1)

        sql = str(db.execute.await_args.args[0].compile())
        assert 'ORDER BY supplier_agreement.start, supplier_agreement.supplier_agreement_id' in sql

    @pytest.mark.asyncio
    async def test_delete_removes_and_commits(self) -> None:
        db = _db()
        agreement = _agreement()

        await service.delete_agreement(db, agreement)

        db.delete.assert_awaited_once_with(agreement)
        assert db.commit.await_count == 1
