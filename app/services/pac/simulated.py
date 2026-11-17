"""The simulated PAC (spec 039, research R6). Its operations arrive with US1 and US2."""

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.db.session import AsyncSessionLocal


class SimulatedPac:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal
    ) -> None:
        self.session_factory = session_factory
