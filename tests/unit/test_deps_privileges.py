"""`has_any_privilege` and `require_any_privilege` — one of several grants opens a route (spec 022).

A lookup route accepts its own Read or Read on the modules whose screens use it (FR-005), and the
customer link exception accepts one of three Update grants (FR-006). Each grant pairs an object
with its own right, so the bit is checked per object, never across them.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import HTTPException

from app.core.deps import (
    CurrentUser,
    can_see_every_facility,
    has_any_privilege,
    require_any_privilege,
    require_privilege,
)
from app.enums import AccessRight, SystemObject

CUSTOMERS_READ = (SystemObject.CUSTOMERS, AccessRight.READ)
SALES_ORDERS_READ = (SystemObject.SALES_ORDERS, AccessRight.READ)


def _db(*rows: tuple[int, int]) -> SimpleNamespace:
    result = MagicMock()
    result.all.return_value = list(rows)
    return SimpleNamespace(execute=AsyncMock(return_value=result))


def _user(*, administrator: bool = False) -> CurrentUser:
    return CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=administrator,
        facility_id=None,
        employee_id=7,
    )


@pytest.mark.asyncio
async def test_no_rows_is_no_access() -> None:
    assert not await has_any_privilege(_db(), 'tester', [CUSTOMERS_READ])


@pytest.mark.asyncio
async def test_a_zero_mask_is_no_access() -> None:
    assert not await has_any_privilege(_db((2, 0)), 'tester', [CUSTOMERS_READ])


@pytest.mark.asyncio
async def test_the_wrong_bit_is_no_access() -> None:
    db = _db((int(SystemObject.CUSTOMERS), int(AccessRight.CREATE)))

    assert not await has_any_privilege(db, 'tester', [CUSTOMERS_READ])


@pytest.mark.asyncio
async def test_the_right_bit_on_the_second_grant_is_enough() -> None:
    db = _db((int(SystemObject.SALES_ORDERS), int(AccessRight.READ)))

    assert await has_any_privilege(db, 'tester', [CUSTOMERS_READ, SALES_ORDERS_READ])


@pytest.mark.asyncio
async def test_the_right_is_checked_per_object() -> None:
    """Read on SALES_ORDERS does not satisfy a grant that asks SALES_ORDERS for Update."""
    grants = [
        (SystemObject.CUSTOMERS, AccessRight.UPDATE),
        (SystemObject.SALES_ORDERS, AccessRight.UPDATE),
    ]
    db = _db((int(SystemObject.SALES_ORDERS), int(AccessRight.READ)))

    assert not await has_any_privilege(db, 'tester', grants)


@pytest.mark.asyncio
async def test_require_any_privilege_returns_the_user_when_allowed() -> None:
    check = require_any_privilege(CUSTOMERS_READ, SALES_ORDERS_READ)
    user = _user()

    assert await check(current_user=user, db=_db((7, 15))) is user


@pytest.mark.asyncio
async def test_require_any_privilege_refuses_with_403() -> None:
    check = require_any_privilege(CUSTOMERS_READ, SALES_ORDERS_READ)

    with pytest.raises(HTTPException) as exc:
        await check(current_user=_user(), db=_db())

    assert exc.value.status_code == 403
    assert exc.value.detail == 'Insufficient privileges'
    assert exc.value.code == 'auth.forbidden'
    assert exc.value.params == {
        'required': [
            {'system_object': int(SystemObject.CUSTOMERS), 'right': int(AccessRight.READ)},
            {'system_object': int(SystemObject.SALES_ORDERS), 'right': int(AccessRight.READ)},
        ]
    }


@pytest.mark.asyncio
async def test_require_privilege_names_its_one_grant() -> None:
    check = require_privilege(SystemObject.LABELS, AccessRight.DELETE)
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    db = SimpleNamespace(execute=AsyncMock(return_value=result))

    with pytest.raises(HTTPException) as exc:
        await check(current_user=_user(), db=db)

    assert exc.value.code == 'auth.forbidden'
    assert exc.value.params == {
        'required': [{'system_object': int(SystemObject.LABELS), 'right': int(AccessRight.DELETE)}]
    }
    assert all(isinstance(v, int) for g in exc.value.params['required'] for v in g.values())


def test_require_admin_refuses_with_its_own_code() -> None:
    from app.core.deps import require_admin

    with pytest.raises(HTTPException) as exc:
        require_admin(_user())

    assert exc.value.status_code == 403
    assert exc.value.code == 'auth.administrator_required'
    assert exc.value.detail == 'Administrator access required'


@pytest.mark.asyncio
async def test_an_administrator_is_not_looked_up() -> None:
    check = require_any_privilege(CUSTOMERS_READ)
    db = _db()
    user = _user(administrator=True)

    assert await check(current_user=user, db=db) is user
    db.execute.assert_not_awaited()


def test_both_factories_expose_their_grants() -> None:
    assert require_any_privilege(CUSTOMERS_READ, SALES_ORDERS_READ).grants == (
        CUSTOMERS_READ,
        SALES_ORDERS_READ,
    )
    assert require_privilege(SystemObject.LABELS, AccessRight.DELETE).grants == (
        (SystemObject.LABELS, AccessRight.DELETE),
    )


# ── can_see_every_facility (spec 034, FR-009, FR-014) ─────────────────────────


@pytest.mark.asyncio
async def test_an_administrator_sees_every_facility_without_a_query() -> None:
    db = _db()

    assert await can_see_every_facility(db, _user(administrator=True), SystemObject.PAYMENTS_EDITOR)
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    'system_object', [SystemObject.PAYMENTS_EDITOR, SystemObject.SEARCH_CREDITS_FROM_ALL_STORES]
)
async def test_read_on_the_named_object_sees_every_facility(system_object: SystemObject) -> None:
    db = _db((int(system_object), int(AccessRight.READ)))

    assert await can_see_every_facility(db, _user(), system_object)


@pytest.mark.asyncio
async def test_another_bit_or_no_row_does_not() -> None:
    other_bit = _db((int(SystemObject.PAYMENTS_EDITOR), int(AccessRight.UPDATE)))

    assert not await can_see_every_facility(other_bit, _user(), SystemObject.PAYMENTS_EDITOR)
    assert not await can_see_every_facility(_db(), _user(), SystemObject.PAYMENTS_EDITOR)
