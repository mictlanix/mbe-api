from collections.abc import AsyncGenerator

import pymysql.converters
from pymysql.constants import FIELD_TYPE
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.config import settings


def _bit_to_bool(value: bytes | None) -> bool | None:
    if value is None:
        return None
    return value != b"\x00"


_conv = pymysql.converters.conversions.copy()
_conv[FIELD_TYPE.BIT] = _bit_to_bool

engine = create_async_engine(
    settings.database_url,
    echo=settings.debug,
    pool_pre_ping=True,
    connect_args={"conv": _conv},
)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session
