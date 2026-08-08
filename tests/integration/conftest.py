"""A real session, real services, real SQL — the layer the mocked tests cannot reach.

Every test under `tests/api/` patches the service out, and every test under `tests/unit/` mocks
`db.execute`. That is fast and it pins contracts, but it means no test ever hands a query to a
database. Two 500s shipped through that gap in one week: #149, where the service raised
`AttributeError` before touching a row, and #154, where a junction was mapped with a column name
the table does not have. In both cases the endpoint had tests, and they passed.

**SQLite, and what that does and does not buy.** The schema here is built from the model
metadata, so this cannot catch a model that disagrees with the real database —
`tests/unit/test_model_schema.py` checks that statically against `docs/mbe_schema.sql` and the
migrations. The two compose: that test proves the models match the deployed schema, these tests
prove the code works against the models.
Neither alone would have caught both bugs; together they cover each other's blind spot.

Foreign keys are **enforced** (`PRAGMA foreign_keys=ON`), which SQLite otherwise leaves off. Without
it a junction insert against a nonexistent id would pass here and 409 in production.

The dialect is SQLite, not MariaDB, so a MySQL-specific construct is still only exercised by the
compile checks in the service tests and by deployment. What these do assert is that the code path
runs at all, against real tables, and answers something other than 500.
"""

import asyncio
import importlib
import pkgutil
import shutil
import tempfile
from collections.abc import AsyncIterator
from pathlib import Path

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

import app.models
from app.core.deps import CurrentUser, get_current_user
from app.db.base import Base
from app.db.session import get_db
from app.main import app as fastapi_app

# Importing every model module is what puts all 100 tables on the metadata; `create_all` only
# creates what it has been told about.
for _, _name, _ in pkgutil.iter_modules(app.models.__path__):
    importlib.import_module(f'app.models.{_name}')

_TEMPLATE_DIR = Path(tempfile.mkdtemp(prefix='mbe-test-schema-'))
_TEMPLATE = _TEMPLATE_DIR / 'template.sqlite'


def _build_template() -> None:
    """Create the schema and the baseline rows once, into a file every test then copies.

    Building them per test cost 0.2s each — 100 `CREATE TABLE`s and ~25 inserts, 213 times over,
    which was 40 of the suite's 50 seconds. A file copy is microseconds, and each test still gets a
    private database it can write to and destroy.

    `asyncio.run` because the seed is written against `AsyncSession`, and it runs at import so no
    event loop outlives it — the artefact is the file, not a connection. A session-scoped async
    fixture instead would mean pinning pytest-asyncio's loop scope for the whole suite.
    """
    from tests.integration.seed import seed_baseline

    async def build() -> None:
        engine = create_async_engine(f'sqlite+aiosqlite:///{_TEMPLATE}')
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        async with async_sessionmaker(engine, expire_on_commit=False)() as db:
            await seed_baseline(db)
        await engine.dispose()

    asyncio.run(build())


_build_template()


@pytest.fixture
async def engine(tmp_path: Path) -> AsyncIterator:
    """A private copy of the template per test, on one connection so writes stay visible."""
    database = tmp_path / 'test.sqlite'
    shutil.copyfile(_TEMPLATE, database)
    engine = create_async_engine(
        f'sqlite+aiosqlite:///{database}',
        poolclass=StaticPool,
        connect_args={'check_same_thread': False},
    )

    @event.listens_for(engine.sync_engine, 'connect')
    def _enforce_foreign_keys(dbapi_connection, _record) -> None:  # noqa: ANN001
        cursor = dbapi_connection.cursor()
        cursor.execute('PRAGMA foreign_keys=ON')
        cursor.close()

    yield engine
    await engine.dispose()


@pytest.fixture
def sessions(engine) -> async_sessionmaker:  # noqa: ANN001
    return async_sessionmaker(engine, expire_on_commit=False)


@pytest.fixture
async def db(sessions: async_sessionmaker) -> AsyncIterator[AsyncSession]:
    """A session for the test itself — seeding, and reading back what an endpoint wrote."""
    async with sessions() as session:
        yield session


@pytest.fixture
def current_user() -> CurrentUser:
    """An administrator, so a failure is never an ambiguous 403.

    Authorisation is covered per-endpoint under `tests/api/`; what is under test here is whether the
    code behind the privilege check runs.
    """
    return CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=True,
        facility_id=1,
        employee_id=1,
        point_sale_id=1,
        cash_drawer_id=1,
    )


@pytest.fixture
async def client(
    sessions: async_sessionmaker, current_user: CurrentUser
) -> AsyncIterator[AsyncClient]:
    """The application with its database and its caller replaced, and nothing else."""

    async def _db() -> AsyncIterator[AsyncSession]:
        async with sessions() as session:
            yield session

    fastapi_app.dependency_overrides[get_db] = _db
    fastapi_app.dependency_overrides[get_current_user] = lambda: current_user
    try:
        async with AsyncClient(
            transport=ASGITransport(app=fastapi_app), base_url='http://test'
        ) as http:
            yield http
    finally:
        fastapi_app.dependency_overrides.clear()


@pytest.fixture
async def seeded(db: AsyncSession) -> None:
    """The baseline rows — one per table at id 1, so a route called with `1` reaches real data.

    They arrive with the template rather than being inserted here, so this asserts their presence
    instead of creating it: a test that depends on `seeded` should fail loudly if the template ever
    stops carrying them, not silently run against an empty database.
    """
    from app.models.customer import Customer

    query = select(Customer.customer_id).where(Customer.customer_id == 1)
    found = (await db.execute(query)).first()
    assert found is not None, 'the template database is missing its baseline rows'
