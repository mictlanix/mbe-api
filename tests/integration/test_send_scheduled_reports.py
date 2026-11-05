"""Spec 038, T060: the scheduled-report job, with a fake mailer and a frozen clock (R15, R16).

`ana` (facility 1, the sales reports) owns a daily 08:00 schedule of her "net sales by product,
last month" view, sent as XLSX to herself and `carla`, who also has the sales reports. A run is
`send_scheduled_reports.run(db, now)`: nothing unless `scheduled_reports_enabled`, then each due
schedule is claimed (R15) and sent as its owner is now (R16). No real mail is ever sent.
"""

import json
from collections.abc import Iterator
from datetime import datetime, time, timedelta

import pytest
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.enums import AccessRight, EntityStatus, SystemObject
from app.jobs import send_scheduled_reports as job
from app.models.analytics import (
    AnalyticsSchedule,
    AnalyticsScheduleRecipient,
    AnalyticsSend,
    AnalyticsView,
)
from app.models.user import AccessPrivilege, User, UserSettings
from app.services import mail
from app.services.analytics import schedules
from tests.integration.analytics_seed import L, sale, seed_catalog

pytestmark = pytest.mark.usefixtures('seeded')

DUE = datetime(2026, 6, 2, 8)
NOW = DUE + timedelta(minutes=3)
RUNNING, SENT, FAILED, SKIPPED = 0, 1, 2, 3


class FakeMailer:
    """Keeps messages instead of sending them, or raises the error it was given. `check` runs
    before a message is kept, to look at the database while the mailer has it."""

    def __init__(self, error: Exception | None = None, check=None) -> None:  # noqa: ANN001
        self.sent: list[mail.Message] = []
        self.error = error
        self.check = check

    async def send(self, message: mail.Message) -> None:
        if self.check is not None:
            await self.check()
        if self.error is not None:
            raise self.error
        self.sent.append(message)


@pytest.fixture
def mailer(monkeypatch: pytest.MonkeyPatch) -> FakeMailer:
    fake = FakeMailer()
    monkeypatch.setattr(mail, 'get_mailer', lambda: fake)
    return fake


@pytest.fixture(autouse=True)
def _enabled(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    monkeypatch.setattr(settings, 'scheduled_reports_enabled', True)
    monkeypatch.setattr(schedules, 'local_now', lambda: NOW)
    yield


@pytest.fixture
async def schedule(db: AsyncSession) -> int:
    await seed_catalog(db)
    await sale(db, datetime(2026, 5, 12, 10), [L('2', '116', cost='50')])
    for user_id in ('ana', 'carla'):
        db.add(
            User(
                user_id=user_id, password='x', email=f'{user_id}@example.com', employee_id=1,
                administrator=False, status=EntityStatus.ACTIVE, session_version=1,
            )
        )  # fmt: skip
    await db.flush()
    db.add(UserSettings(user_id='ana', facility_id=1))
    for user_id in ('ana', 'carla'):
        db.add(
            AccessPrivilege(
                user_id=user_id,
                system_object=int(SystemObject.SALES_BY_PRODUCT),
                privileges=AccessRight.READ,
            )
        )
    view = AnalyticsView(
        user='ana', name='Ventas', kind=0,
        definition=json.dumps({'measures': ['net_sales'], 'dimensions': ['product']}),
        period=json.dumps({'key': 'last_month'}), creation_time=DUE, modification_time=DUE,
    )  # fmt: skip
    db.add(view)
    await db.flush()
    row = AnalyticsSchedule(
        analytics_view=view.analytics_view_id, cadence=0, weekdays=0, time_of_day=time(8),
        format=0, subject='Ventas de mayo', enabled=True, next_run_time=DUE,
        creation_time=DUE, modification_time=DUE,
    )  # fmt: skip
    db.add(row)
    await db.flush()
    for user_id in ('ana', 'carla'):
        db.add(
            AnalyticsScheduleRecipient(analytics_schedule=row.analytics_schedule_id, user=user_id)
        )
    await db.commit()
    return row.analytics_schedule_id


async def _sends(db: AsyncSession) -> list[AnalyticsSend]:
    db.expire_all()
    query = select(AnalyticsSend).order_by(AnalyticsSend.analytics_send_id)
    return list((await db.execute(query)).scalars())


async def _only_send(db: AsyncSession) -> AnalyticsSend:
    sends = await _sends(db)
    assert len(sends) == 1, sends
    return sends[0]


# ── Running ───────────────────────────────────────────────────────────────────


async def test_nothing_runs_while_disabled(
    db: AsyncSession, schedule: int, mailer: FakeMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, 'scheduled_reports_enabled', False)

    assert await job.run(db, NOW) == 0

    assert await _sends(db) == []
    assert mailer.sent == []
    assert (await db.get(AnalyticsSchedule, schedule)).next_run_time == DUE


async def test_a_due_schedule_sends_once_with_the_attachment(
    db: AsyncSession, schedule: int, mailer: FakeMailer
) -> None:
    assert await job.run(db, NOW) == 1
    assert await job.run(db, NOW) == 0

    assert len(mailer.sent) == 1
    message = mailer.sent[0]
    assert sorted(message.to) == ['ana@example.com', 'carla@example.com']
    assert message.subject == 'Ventas de mayo'
    assert 'Ventas' in message.body and '2026-05-01' in message.body
    [attachment] = message.attachments
    assert attachment.filename.endswith('.xlsx')
    assert attachment.data[:2] == b'PK'
    send = await _only_send(db)
    assert send.outcome == SENT
    assert send.code is None
    assert send.occurrence == DUE
    assert send.finished_at is not None
    assert send.size == len(attachment.data)
    assert send.row_count == 1
    assert json.loads(send.recipients) == {'sent_to': ['ana', 'carla'], 'dropped': []}
    assert (await db.get(AnalyticsSchedule, schedule)).next_run_time == DUE + timedelta(days=1)


async def test_a_disabled_schedule_is_not_sent(
    db: AsyncSession, schedule: int, mailer: FakeMailer
) -> None:
    (await db.get(AnalyticsSchedule, schedule)).enabled = False
    await db.commit()

    assert await job.run(db, NOW) == 0
    assert mailer.sent == []


async def test_two_claims_of_the_same_read_value_send_once(db: AsyncSession, schedule: int) -> None:
    read = (await db.get(AnalyticsSchedule, schedule)).next_run_time
    following = DUE + timedelta(days=1)

    assert await schedules.claim(db, schedule, read, following) is True
    assert await schedules.claim(db, schedule, read, following) is False


async def test_thirty_days_run_twice_each_send_each_occurrence_once(
    db: AsyncSession, schedule: int, mailer: FakeMailer
) -> None:
    for day in range(30):
        at = DUE + timedelta(days=day, minutes=2)
        await job.run(db, at)
        await job.run(db, at)

    sends = await _sends(db)
    assert [s.occurrence for s in sends] == [DUE + timedelta(days=d) for d in range(30)]
    assert {s.outcome for s in sends} == {SENT}
    assert len(mailer.sent) == 30


async def test_a_missed_run_sends_once_for_the_latest_occurrence(
    db: AsyncSession, schedule: int, mailer: FakeMailer
) -> None:
    later = DUE + timedelta(days=3, hours=1)

    assert await job.run(db, later) == 1

    assert (await _only_send(db)).occurrence == DUE + timedelta(days=3)
    assert (await db.get(AnalyticsSchedule, schedule)).next_run_time == DUE + timedelta(days=4)


# ── The owner and the view at send time ───────────────────────────────────────


async def test_an_owner_who_lost_the_family_fails_forbidden(
    db: AsyncSession, schedule: int, mailer: FakeMailer
) -> None:
    await db.execute(delete(AccessPrivilege).where(AccessPrivilege.user_id == 'ana'))
    await db.commit()

    await job.run(db, NOW)

    send = await _only_send(db)
    assert (send.outcome, send.code) == (FAILED, 'auth.forbidden')
    assert mailer.sent == []


async def test_an_inactive_owner_fails_forbidden(
    db: AsyncSession, schedule: int, mailer: FakeMailer
) -> None:
    (await db.get(User, 'ana')).status = EntityStatus.INACTIVE
    await db.commit()

    await job.run(db, NOW)

    send = await _only_send(db)
    assert (send.outcome, send.code) == (FAILED, 'auth.forbidden')
    assert mailer.sent == []


async def test_a_view_that_no_longer_validates_fails_and_sends_nothing(
    db: AsyncSession, schedule: int, mailer: FakeMailer
) -> None:
    row = await db.get(AnalyticsSchedule, schedule)
    view = await db.get(AnalyticsView, row.analytics_view)
    view.definition = json.dumps({'measures': ['retired_measure']})
    await db.commit()

    await job.run(db, NOW)

    send = await _only_send(db)
    assert (send.outcome, send.code) == (FAILED, 'analytics.view_invalid')
    assert mailer.sent == []


# ── Recipients, size and mail ─────────────────────────────────────────────────


async def test_a_recipient_who_lost_the_family_is_dropped_and_recorded(
    db: AsyncSession, schedule: int, mailer: FakeMailer
) -> None:
    await db.execute(delete(AccessPrivilege).where(AccessPrivilege.user_id == 'carla'))
    await db.commit()

    await job.run(db, NOW)

    send = await _only_send(db)
    assert send.outcome == SENT
    assert json.loads(send.recipients) == {
        'sent_to': ['ana'],
        'dropped': [{'user': 'carla', 'family': 'sales'}],
    }
    assert [m.to for m in mailer.sent] == [['ana@example.com']]
    assert schedules.send_response(send, NOW).dropped == ['carla']


async def test_an_attachment_over_the_cap_is_skipped(
    db: AsyncSession, schedule: int, mailer: FakeMailer, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(settings, 'report_attachment_max_bytes', 100)

    await job.run(db, NOW)

    send = await _only_send(db)
    assert (send.outcome, send.code) == (SKIPPED, 'analytics.attachment_too_large')
    assert send.size > 100
    assert mailer.sent == []


async def test_without_mail_configured_the_send_is_skipped(
    db: AsyncSession, schedule: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(mail, 'get_mailer', lambda: None)

    await job.run(db, NOW)

    send = await _only_send(db)
    assert (send.outcome, send.code) == (SKIPPED, 'email.not_configured')


async def test_a_refused_mail_fails_and_sent_is_recorded_only_after_the_mailer_returns(
    db: AsyncSession,
    sessions,
    schedule: int,
    monkeypatch: pytest.MonkeyPatch,  # noqa: ANN001
) -> None:
    seen: list[int] = []

    async def check() -> None:
        async with sessions() as other:
            found = (await other.execute(select(AnalyticsSend.outcome))).scalars().all()
            seen.extend(found)

    refusing = FakeMailer(error=mail.MailError('550 rejected'), check=check)
    monkeypatch.setattr(mail, 'get_mailer', lambda: refusing)

    await job.run(db, NOW)

    assert seen == [RUNNING]
    send = await _only_send(db)
    assert (send.outcome, send.code) == (FAILED, 'email.send_failed')
    assert refusing.sent == []


async def test_a_row_left_running_over_ten_minutes_reads_as_failed(
    db: AsyncSession, schedule: int
) -> None:
    started = NOW - timedelta(minutes=11)
    row = AnalyticsSend(
        analytics_schedule=schedule, occurrence=started, started_at=started, outcome=RUNNING,
        recipients=json.dumps({'sent_to': [], 'dropped': []}), format=0,
    )  # fmt: skip
    db.add(row)
    await db.commit()

    stale = schedules.send_response(row, NOW)
    fresh = schedules.send_response(row, started + timedelta(minutes=9))

    assert (stale.outcome, stale.code) == ('failed', 'server.error')
    assert (fresh.outcome, fresh.code) == ('running', None)
