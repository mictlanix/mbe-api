"""The job that resolves stamping documents and open cancellations (spec 039, R12, T035).

The session factory is a fake and `fiscal_stamping.refresh` is patched: what is pinned here is the
job's own behaviour, its order, its exit code, and that it starts only with a safe provider.
"""

from contextlib import asynccontextmanager
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest

from app.core.error_codes import ErrorCode
from app.core.errors import DomainError
from app.jobs import resolve_fiscal_documents as job
from tests.fakes.pac import ScriptedPac
from tests.unit.test_inventory_locks import FakeResult

_SYSTEM = SimpleNamespace(employee_id=1)


class Session:
    """Answers the job's two reads: stamping documents, then open requests."""

    def __init__(self, stamping: list[tuple], requested: list[tuple]) -> None:
        self.answers = [stamping, requested]
        self.commits = 0

    async def execute(self, _statement) -> FakeResult:  # noqa: ANN001
        return FakeResult(self.answers.pop(0) if self.answers else [])

    async def commit(self) -> None:
        self.commits += 1


def _factory(stamping: list[tuple], requested: list[tuple]):  # noqa: ANN202
    sessions: list[object] = []

    @asynccontextmanager
    async def factory():  # noqa: ANN202
        session = Session(stamping, requested) if not sessions else SimpleNamespace(n=len(sessions))
        sessions.append(session)
        yield session

    factory.sessions = sessions  # type: ignore[attr-defined]
    return factory


async def _run(refresh: AsyncMock, stamping=(), requested=()) -> tuple[int, object]:  # noqa: ANN001
    factory = _factory(list(stamping), list(requested))
    with (
        patch.object(job.fiscal_stamping, 'refresh', refresh),
        patch.object(
            job.employee_service, 'ensure_system_employee', AsyncMock(return_value=_SYSTEM)
        ),
    ):
        result = await job.run(ScriptedPac(), session_factory=factory)
    return result, factory


async def test_run_refreshes_each_document_oldest_first_in_its_own_session() -> None:
    refresh = AsyncMock()
    stamping = [(3, datetime(2026, 11, 17, 9, 30))]
    requested = [(5, datetime(2026, 11, 17, 9, 0)), (4, datetime(2026, 11, 17, 10, 0))]

    result, factory = await _run(refresh, stamping, requested)

    assert result == 0
    assert [c.args[1] for c in refresh.await_args_list] == [5, 3, 4]
    sessions = [c.args[0] for c in refresh.await_args_list]
    assert len({id(s) for s in sessions}) == 3
    assert factory.sessions[0] not in sessions
    assert all(c.args[3] == _SYSTEM.employee_id for c in refresh.await_args_list)


async def test_run_continues_past_a_failure_and_returns_1(
    capsys: pytest.CaptureFixture,
) -> None:
    failure = DomainError(
        ErrorCode.PAC_REJECTED,
        'The PAC rejected the query',
        {'pac_code': 'X1', 'pac_message': 'secret payload'},
    )
    refresh = AsyncMock(side_effect=[failure, RuntimeError('raw payload'), None])
    stamping = [(1, datetime(2026, 11, 17, 9)), (2, datetime(2026, 11, 17, 10))]

    result, _ = await _run(refresh, stamping, [(3, datetime(2026, 11, 17, 11))])

    assert result == 1
    assert refresh.await_count == 3
    out = capsys.readouterr().out
    assert 'fiscal document 1' in out and 'pac.rejected' in out
    assert 'fiscal document 2' in out and 'RuntimeError' in out
    assert 'secret payload' not in out and 'raw payload' not in out


async def test_a_document_another_run_resolved_is_not_a_failure() -> None:
    refresh = AsyncMock(
        side_effect=DomainError(ErrorCode.FISCAL_DOCUMENT_NOTHING_TO_REFRESH, 'Nothing')
    )

    result, _ = await _run(refresh, [(1, datetime(2026, 11, 17, 9))])

    assert result == 0


async def test_run_with_nothing_in_flight_returns_0() -> None:
    refresh = AsyncMock()

    result, _ = await _run(refresh)

    assert result == 0
    refresh.assert_not_awaited()


def test_main_without_a_provider_exits_0(capsys: pytest.CaptureFixture) -> None:
    with (
        patch.object(job, 'settings', SimpleNamespace(pac_provider='none', app_env='production')),
        patch.object(job, 'get_pac_provider', return_value=None),
        patch.object(job, 'run', AsyncMock()) as run,
    ):
        assert job.main() == 0

    run.assert_not_awaited()
    assert 'no PAC provider' in capsys.readouterr().out


def test_main_refuses_the_simulator_in_production() -> None:
    with (
        patch.object(
            job, 'settings', SimpleNamespace(pac_provider='simulated', app_env='production')
        ),
        patch.object(job, 'get_pac_provider') as provider,
        patch.object(job, 'run', AsyncMock()) as run,
        pytest.raises(RuntimeError, match='pac_provider=simulated'),
    ):
        job.main()

    provider.assert_not_called()
    run.assert_not_awaited()


@pytest.mark.parametrize('result', [0, 1])
def test_main_exits_with_runs_result(result: int) -> None:
    pac = ScriptedPac()
    with (
        patch.object(
            job, 'settings', SimpleNamespace(pac_provider='simulated', app_env='development')
        ),
        patch.object(job, 'get_pac_provider', return_value=pac),
        patch.object(job, 'run', AsyncMock(return_value=result)) as run,
        patch.object(job, 'engine', SimpleNamespace(dispose=AsyncMock())),
    ):
        assert job.main() == result

    assert run.await_args.args == (pac,)
