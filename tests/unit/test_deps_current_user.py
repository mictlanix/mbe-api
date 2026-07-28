"""`CurrentUser` must carry the caller's employee, point of sale and cash drawer.

Every sales document records a creator, updater and salesperson as an employee, and a sales order
additionally needs a point of sale. Both were already loaded by `get_current_user` and simply not
surfaced — these tests pin the surfacing, including the "not configured" point of sale the
services have to refuse on (FR-004a). The employee has no such case: `user.employee` is NOT NULL
since migration 012 (#127).
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from fastapi import HTTPException

from app.core.deps import CurrentUser, get_current_user
from app.enums import EntityStatus


def _user(*, employee_id: int = 7, settings: object | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        user_id='tester',
        employee_id=employee_id,
        administrator=False,
        status=EntityStatus.ACTIVE,
        session_version=1,
        settings=settings,
    )


def _settings(point_sale_id: int | None = 3, cash_drawer_id: int | None = 5) -> SimpleNamespace:
    return SimpleNamespace(
        facility_id=1, point_sale_id=point_sale_id, cash_drawer_id=cash_drawer_id
    )


async def _resolve(user: SimpleNamespace) -> CurrentUser:
    db = AsyncMock()
    db.get = AsyncMock(return_value=user)
    with patch(
        'app.core.deps.decode_token',
        return_value={'sub': 'tester', 'session_version': 1, 'facility_id': 1},
    ):
        return await get_current_user(token='irrelevant', db=db)


@pytest.mark.asyncio
async def test_carries_employee_point_sale_and_cash_drawer() -> None:
    current = await _resolve(_user(settings=_settings()))

    assert current.employee_id == 7
    assert current.point_sale_id == 3
    assert current.cash_drawer_id == 5


@pytest.mark.asyncio
async def test_point_sale_is_none_when_not_configured() -> None:
    """`user_settings.point_sale` is nullable but `sales_order.point_sale` is not — FR-004a."""
    current = await _resolve(_user(settings=_settings(point_sale_id=None, cash_drawer_id=None)))

    assert current.employee_id == 7
    assert current.point_sale_id is None
    assert current.cash_drawer_id is None


@pytest.mark.asyncio
async def test_settings_fields_are_none_when_user_has_no_settings_row() -> None:
    """The employee comes from the user row, so it survives a missing settings row."""
    current = await _resolve(_user(settings=None))

    assert current.employee_id == 7
    assert current.point_sale_id is None
    assert current.cash_drawer_id is None


@pytest.mark.asyncio
async def test_inactive_user_still_rejected() -> None:
    """The added fields must not weaken the existing status check."""
    user = _user(settings=_settings())
    user.status = EntityStatus.INACTIVE

    with pytest.raises(HTTPException) as exc:
        await _resolve(user)

    assert exc.value.status_code == 401


def test_only_the_settings_fields_default() -> None:
    """The employee is not optional and has no default — a `CurrentUser` always names one."""
    current = CurrentUser(
        user_id='tester', session_version=1, administrator=True, facility_id=None, employee_id=7
    )

    assert current.employee_id == 7
    assert current.point_sale_id is None
    assert current.cash_drawer_id is None
