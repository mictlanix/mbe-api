"""Spec 037, T052: the purge job deletes expired exports, files and rows, and keeps the rest."""

from datetime import datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.models.analytics import AnalyticsExport
from app.services.analytics import exports

pytestmark = pytest.mark.usefixtures('seeded')

NOW = datetime(2026, 5, 4, 12, 0)


@pytest.fixture(autouse=True)
def _exports_under_tmp(tmp_path: Path) -> None:
    original = settings.exports_dir
    settings.exports_dir = str(tmp_path / 'exports')
    yield
    settings.exports_dir = original


async def _export(db: AsyncSession, expiry: datetime) -> tuple[int, Path]:
    row = AnalyticsExport(
        user='tester', source=0, format=2, definition='{}', status=2, row_count=1,
        creation_time=expiry - timedelta(hours=24), completion_time=expiry - timedelta(hours=24),
        expiry_time=expiry,
    )  # fmt: skip
    db.add(row)
    await db.flush()
    row.file_name = f'{row.analytics_export_id}.csv'
    await db.commit()
    path = Path(settings.exports_dir) / row.file_name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text('x')
    return row.analytics_export_id, path


async def test_the_job_removes_the_expired_and_keeps_the_rest(db: AsyncSession) -> None:
    expired, old_file = await _export(db, NOW - timedelta(minutes=1))
    alive, new_file = await _export(db, NOW + timedelta(hours=3))

    removed = await exports.purge(db, NOW)

    left = (await db.execute(select(AnalyticsExport.analytics_export_id))).scalars().all()
    assert removed == 1
    assert left == [alive]
    assert not old_file.exists() and new_file.exists()
    assert expired not in left
