"""Which of a supplier's four sub-panels a caller may read (spec 035, US1, FR-001..FR-003).

Each panel answers to its own read privilege: bank accounts 13, contacts 12, addresses 11 and
agreements 3. Administrators read all four without a query.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from app.core.deps import CurrentUser
from app.enums import AccessRight, SystemObject
from app.services.supplier_service import readable_panels

ALL_PANELS = {
    SystemObject.BANK_ACCOUNTS,
    SystemObject.CONTACTS,
    SystemObject.ADDRESSES,
    SystemObject.SUPPLIERS,
}


def _user(*, administrator: bool = False) -> CurrentUser:
    return CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=None,
        employee_id=1,
    )


def _db(*rows: tuple[SystemObject, int]) -> AsyncMock:
    db = AsyncMock()
    db.execute = AsyncMock(
        return_value=SimpleNamespace(all=lambda: [(int(obj), mask) for obj, mask in rows])
    )
    return db


@pytest.mark.asyncio
async def test_an_administrator_reads_all_four_without_a_query() -> None:
    db = _db()

    assert await readable_panels(db, _user(administrator=True)) == ALL_PANELS
    assert db.execute.await_count == 0


@pytest.mark.asyncio
@pytest.mark.parametrize('panel', sorted(ALL_PANELS))
async def test_each_read_privilege_opens_only_its_own_panel(panel: SystemObject) -> None:
    db = _db((panel, int(AccessRight.READ)))

    assert await readable_panels(db, _user()) == {panel}


@pytest.mark.asyncio
async def test_a_row_without_the_read_bit_opens_nothing() -> None:
    mask = int(AccessRight.CREATE | AccessRight.UPDATE | AccessRight.DELETE)
    db = _db(*[(panel, mask) for panel in ALL_PANELS])

    assert await readable_panels(db, _user()) == set()


@pytest.mark.asyncio
async def test_no_privilege_rows_opens_nothing() -> None:
    assert await readable_panels(_db(), _user()) == set()


@pytest.mark.asyncio
async def test_a_mask_with_read_among_other_rights_opens_the_panel() -> None:
    db = _db((SystemObject.CONTACTS, int(AccessRight.READ | AccessRight.UPDATE)))

    assert await readable_panels(db, _user()) == {SystemObject.CONTACTS}


@pytest.mark.asyncio
async def test_one_query_covers_all_four_panels() -> None:
    db = _db((SystemObject.BANK_ACCOUNTS, 2), (SystemObject.ADDRESSES, 2))

    assert await readable_panels(db, _user()) == {
        SystemObject.BANK_ACCOUNTS,
        SystemObject.ADDRESSES,
    }
    assert db.execute.await_count == 1
