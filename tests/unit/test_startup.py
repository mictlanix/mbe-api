"""Application boot: the check runs, and a failing one stops the process starting.

`@app.on_event('startup')` is deprecated by FastAPI, so this runs under `lifespan` instead. The
migration is only safe if one property survives it — a check that raises must still abort the
boot.

Spec 013 removed the second check. `verify_in_transit_warehouse` existed only to catch an unset
`IN_TRANSIT_WAREHOUSE_ID`; there is one in-transit location per facility now, found by its flag,
so there is no setting left to misconfigure and nothing for a startup check to verify. What these
tests still assert is the property that mattered — a raising check aborts the boot — plus the
absence of the retired check, so it cannot quietly come back.
"""

import inspect
from unittest.mock import AsyncMock

import pytest

import app.main as main


class TestLifespanRunsTheCheck:
    @pytest.mark.asyncio
    async def test_the_system_employee_is_created_before_serving(self, monkeypatch) -> None:
        calls: list[str] = []

        async def _employee() -> None:
            calls.append('employee')

        monkeypatch.setattr(main, 'ensure_system_employee', _employee)

        async with main.lifespan(main.app):
            assert calls == ['employee']

    def test_the_in_transit_check_is_gone(self) -> None:
        """FR-006. It guarded a setting that no longer exists.

        Asserted rather than merely deleted: a startup check that refuses to boot the whole API
        because one facility lacks a location was explicitly rejected (research R7), so its return
        would be a regression, not an improvement.
        """
        assert not hasattr(main, 'verify_in_transit_warehouse')
        assert 'in_transit' not in inspect.getsource(main.lifespan.__wrapped__)


class TestAFailingCheckAbortsTheBoot:
    """The property the move to `lifespan` had to preserve."""

    @pytest.mark.asyncio
    async def test_a_raising_check_propagates(self, monkeypatch) -> None:
        monkeypatch.setattr(
            main, 'ensure_system_employee', AsyncMock(side_effect=RuntimeError('no employee'))
        )

        with pytest.raises(RuntimeError, match='no employee'):
            async with main.lifespan(main.app):
                pytest.fail('the application must not start when a check fails')

    @pytest.mark.asyncio
    async def test_serving_never_begins_when_the_check_fails(self, monkeypatch) -> None:
        served = False
        monkeypatch.setattr(
            main, 'ensure_system_employee', AsyncMock(side_effect=RuntimeError('no employee'))
        )

        with pytest.raises(RuntimeError):
            async with main.lifespan(main.app):
                served = True

        assert served is False


class TestTheDeprecatedHookIsGone:
    def test_the_decorator_is_not_used(self) -> None:
        """Structural, not textual: the docstring quotes the decorator to explain why it went."""
        decorators = [
            line.strip()
            for line in inspect.getsource(main).splitlines()
            if line.strip().startswith('@')
        ]

        assert not any(d.startswith('@app.on_event') for d in decorators), decorators

    def test_the_app_is_wired_to_the_lifespan(self) -> None:
        assert main.app.router.lifespan_context is not None
