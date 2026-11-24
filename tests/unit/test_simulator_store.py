"""The simulated PAC's state on the database (spec 040, research R10)."""

from collections.abc import AsyncIterator
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

import pytest
from mictlanix.cfdi import pac as lib
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from app.db.base import Base
from app.models.fiscal import PacSimulatorStore
from app.services.pac.adapter import CfdiPacProvider
from app.services.pac.provider import PacUnavailable
from app.services.pac.simulator_store import DatabaseSimulatorStore
from tests.unit.test_pac_adapter import _sealed


@pytest.fixture
async def sessions(tmp_path: Path) -> AsyncIterator[async_sessionmaker]:
    engine = create_async_engine(
        f'sqlite+aiosqlite:///{tmp_path / "store.sqlite"}',
        poolclass=StaticPool,
        connect_args={'check_same_thread': False},
    )
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all, tables=[PacSimulatorStore.__table__])
    yield async_sessionmaker(engine, expire_on_commit=False)
    await engine.dispose()


async def test_a_missing_key_reads_none(sessions: async_sessionmaker) -> None:
    assert await DatabaseSimulatorStore(sessions).get('seal:missing') is None


async def test_a_value_set_is_read_back_from_a_fresh_session(sessions: async_sessionmaker) -> None:
    store = DatabaseSimulatorStore(sessions)

    await store.set('uuid:A', '{"state": "valid"}')

    assert await DatabaseSimulatorStore(sessions).get('uuid:A') == '{"state": "valid"}'
    async with sessions() as db:
        [row] = (await db.execute(select(PacSimulatorStore))).scalars()
    assert (row.store_key, row.value) == ('uuid:A', '{"state": "valid"}')


async def test_a_second_set_overwrites_the_value_and_its_time(sessions: async_sessionmaker) -> None:
    store = DatabaseSimulatorStore(sessions)
    first, second = datetime(2026, 11, 18, 10, 0), datetime(2026, 11, 18, 10, 5)

    with patch('app.services.pac.simulator_store.local_now', return_value=first):
        await store.set('uuid:A', 'one')
    with patch('app.services.pac.simulator_store.local_now', return_value=second):
        await store.set('uuid:A', 'two')

    async with sessions() as db:
        [row] = (await db.execute(select(PacSimulatorStore))).scalars()
    assert (row.value, row.updated_at) == ('two', second)


async def test_the_simulator_keeps_its_stamps_in_the_store(sessions: async_sessionmaker) -> None:
    pac = lib.SimulatedPac(DatabaseSimulatorStore(sessions))
    sealed = _sealed().encode('utf-8')

    stamp = await pac.stamp(sealed)

    async with sessions() as db:
        keys = sorted(
            row.store_key[:5] for row in (await db.execute(select(PacSimulatorStore))).scalars()
        )
    assert keys == ['seal:', 'uuid:']
    # Another instance on the same store finds it.
    other = lib.SimulatedPac(DatabaseSimulatorStore(sessions))
    assert (await other.find_stamp(sealed)).timbre.UUID == stamp.timbre.UUID


async def test_a_store_that_fails_is_the_pac_unavailable() -> None:
    def broken() -> object:
        raise ConnectionError('the database is down')

    pac = lib.SimulatedPac(DatabaseSimulatorStore(broken))  # type: ignore[arg-type]
    provider = CfdiPacProvider(pac, None, name='simulator', resolves_by_resubmitting=False)  # type: ignore[arg-type]

    with pytest.raises(PacUnavailable):
        await provider.stamp(_sealed())
