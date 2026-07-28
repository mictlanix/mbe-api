"""Application boot: the checks run, and a failing one stops the process starting.

`@app.on_event('startup')` is deprecated by FastAPI, so these ran under `lifespan` instead. The
migration is only safe if one property survives it — a check that raises must still abort the
boot. A deployment that starts anyway would file stock against a warehouse that does not exist,
which is the failure the checks exist to prevent.
"""

import inspect
from unittest.mock import AsyncMock

import pytest

import app.main as main


class TestLifespanRunsTheChecks:
    @pytest.mark.asyncio
    async def test_both_checks_run_before_serving(self, monkeypatch) -> None:
        calls: list[str] = []

        async def _employee() -> None:
            calls.append('employee')

        async def _warehouse() -> None:
            calls.append('warehouse')

        monkeypatch.setattr(main, 'ensure_system_employee', _employee)
        monkeypatch.setattr(main, 'verify_in_transit_warehouse', _warehouse)

        async with main.lifespan(main.app):
            assert calls == ['employee', 'warehouse']

    @pytest.mark.asyncio
    async def test_the_employee_is_created_before_the_warehouse_is_checked(
        self, monkeypatch
    ) -> None:
        """Ordered deliberately: the employee is the row a later failure would leave missing."""
        calls: list[str] = []

        monkeypatch.setattr(
            main, 'ensure_system_employee', AsyncMock(side_effect=lambda: calls.append('employee'))
        )
        monkeypatch.setattr(
            main,
            'verify_in_transit_warehouse',
            AsyncMock(side_effect=lambda: calls.append('warehouse')),
        )

        async with main.lifespan(main.app):
            pass

        assert calls.index('employee') < calls.index('warehouse')


class TestAFailingCheckAbortsTheBoot:
    """The property the move to `lifespan` had to preserve."""

    @pytest.mark.asyncio
    async def test_a_raising_check_propagates(self, monkeypatch) -> None:
        monkeypatch.setattr(main, 'ensure_system_employee', AsyncMock())
        monkeypatch.setattr(
            main,
            'verify_in_transit_warehouse',
            AsyncMock(side_effect=RuntimeError('IN_TRANSIT_WAREHOUSE_ID is not set')),
        )

        with pytest.raises(RuntimeError, match='IN_TRANSIT_WAREHOUSE_ID'):
            async with main.lifespan(main.app):
                pytest.fail('the application must not start when a check fails')

    @pytest.mark.asyncio
    async def test_serving_never_begins_when_the_first_check_fails(self, monkeypatch) -> None:
        second = AsyncMock()
        monkeypatch.setattr(
            main, 'ensure_system_employee', AsyncMock(side_effect=RuntimeError('no employee'))
        )
        monkeypatch.setattr(main, 'verify_in_transit_warehouse', second)

        with pytest.raises(RuntimeError):
            async with main.lifespan(main.app):
                pass

        second.assert_not_awaited()


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
