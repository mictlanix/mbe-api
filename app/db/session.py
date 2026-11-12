from collections.abc import AsyncGenerator
from typing import Any

import pymysql.converters
from pymysql.constants import FIELD_TYPE
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


def _bit_to_bool(value: bytes | None) -> bool | None:
    if value is None:
        return None
    return value != b'\x00'


_conv = pymysql.converters.conversions.copy()
_conv[FIELD_TYPE.BIT] = _bit_to_bool

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    connect_args={'conv': _conv},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)

# Named locks (`GET_LOCK`) a connection holds, kept in its pool record's `info`. They belong to
# the session, not the transaction, so they outlive the COMMIT and are released here, when the
# connection goes back to the pool (#157).
NAMED_LOCKS = 'named_locks'


@event.listens_for(engine.sync_engine.pool, 'checkin')
def _release_named_locks(dbapi_connection: Any, record: Any) -> None:
    names = record.info.pop(NAMED_LOCKS, None)
    if not names or dbapi_connection is None:
        return
    try:
        cursor = dbapi_connection.cursor()
        for name in names:
            cursor.execute('SELECT RELEASE_LOCK(%s)', (name,))
        cursor.close()
    except Exception as error:
        # Closing the connection releases every lock it holds.
        record.invalidate(error)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
