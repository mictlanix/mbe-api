"""The CFDI library's simulated PAC keeps its state here (spec 040, research R10).

`DatabaseSimulatorStore` implements the library's `SimulatorStore`: string values under the
library's own keys, in `pac_simulator_store`, shared by every API process and the resolve job.
Each read and write runs in its own session and commits at once, so a stamp the simulator issued
survives even when the API never stores it (a timeout). Any failure reaches the library, which
answers it as "PAC unavailable". The values are never parsed here.
"""

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.clock import local_now
from app.db.session import AsyncSessionLocal
from app.models.fiscal import PacSimulatorStore


class DatabaseSimulatorStore:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession] = AsyncSessionLocal
    ) -> None:
        self.session_factory = session_factory

    async def get(self, key: str) -> str | None:
        async with self.session_factory() as db:
            query = select(PacSimulatorStore.value).where(PacSimulatorStore.store_key == key)
            return (await db.execute(query)).scalar_one_or_none()

    async def set(self, key: str, value: str) -> None:
        async with self.session_factory() as db:
            await db.merge(
                PacSimulatorStore(
                    store_key=key, value=value, updated_at=local_now().replace(microsecond=0)
                )
            )
            await db.commit()
