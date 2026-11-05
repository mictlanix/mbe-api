"""Scheduled reports (spec 038, FR-160–FR-165, research R15–R16).

A schedule emails a saved view's export to up to 10 users, daily, weekly on chosen weekdays or
monthly on a day (its month's last day when the month is shorter), at a time in business time.
Only the view's owner schedules it. Every save checks the view's export as the owner and each
recipient against the families the view reads (`analytics.recipient_ineligible`).

The job `app/jobs/send_scheduled_reports.py` claims each due schedule with a conditional `UPDATE`
on the `next_run_time` it read, so an occurrence is sent once however many runs overlap, and a
missed run sends only its latest occurrence. A send runs as the owner is now (R16), records a row
in `analytics_send` before doing anything else, and marks it sent only once the mailer returns.
"""

import calendar
import json
import logging
from collections.abc import Sequence
from datetime import date, datetime, timedelta
from typing import Any

import anyio
from fastapi.exceptions import RequestValidationError
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.clock import local_now
from app.core.config import settings
from app.core.deps import CurrentUser, forbidden
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.enums import EntityStatus
from app.models.analytics import (
    AnalyticsSchedule,
    AnalyticsScheduleRecipient,
    AnalyticsSend,
    AnalyticsView,
)
from app.models.user import User
from app.schemas import ListResponse
from app.schemas.analytics import (
    AnalyticsExportRequest,
    AnalyticsScheduleCreate,
    AnalyticsScheduleResponse,
    AnalyticsScheduleUpdate,
    AnalyticsSendResponse,
    Cadence,
    ExportFormat,
    ExportSource,
    SendOutcome,
    timing_problem,
)
from app.services import mail
from app.services.analytics import detail, engine, exports, views
from app.services.analytics.catalog import MEASURE_FAMILIES, MEASURES, Family

log = logging.getLogger(__name__)

#: The stored `cadence`, `format` and `outcome`.
CADENCES = (Cadence.DAILY, Cadence.WEEKLY, Cadence.MONTHLY)
FORMATS = {code: format_ for format_, code in exports.FORMAT_CODES.items()}
OUTCOMES = (SendOutcome.RUNNING, SendOutcome.SENT, SendOutcome.FAILED, SendOutcome.SKIPPED)
ONE_DAY = timedelta(days=1)
#: Every cadence has an occurrence within a year and a month.
HORIZON = 400


# ── When ──────────────────────────────────────────────────────────────────────


def _runs_on(schedule: AnalyticsSchedule, day: date) -> bool:
    match CADENCES[schedule.cadence]:
        case Cadence.DAILY:
            return True
        case Cadence.WEEKLY:
            return bool(schedule.weekdays & (1 << day.weekday()))
        case _:
            last = calendar.monthrange(day.year, day.month)[1]
            return day.day == min(schedule.month_day or 1, last)


def next_occurrence(schedule: AnalyticsSchedule, after: datetime) -> datetime:
    """The first occurrence strictly after `after`, in business time."""
    day = after.date()
    for _ in range(HORIZON):
        at = datetime.combine(day, schedule.time_of_day)
        if at > after and _runs_on(schedule, day):
            return at
        day += ONE_DAY
    raise ValueError('the schedule has no occurrence')


def _latest_occurrence(schedule: AnalyticsSchedule, now: datetime) -> datetime | None:
    day = now.date()
    for _ in range(HORIZON):
        at = datetime.combine(day, schedule.time_of_day)
        if at <= now and _runs_on(schedule, day):
            return at
        day -= ONE_DAY
    return None


def due(schedule: AnalyticsSchedule, now: datetime) -> tuple[datetime | None, datetime]:
    """What a run at `now` sends and claims: the latest occurrence at or before `now` (so a missed
    run sends once, for the latest), and the next occurrence after `now`."""
    return _latest_occurrence(schedule, now), next_occurrence(schedule, now)


# ── Who may receive it ────────────────────────────────────────────────────────


def reads(checked: exports.Checked) -> list[Family]:
    """The families a view's export shows, with cost when it shows cost (FR-160)."""
    held = checked.families
    match checked.source:
        case ExportSource.QUERY:
            measures = [MEASURES[key] for key in checked.request.measures]
            needed = {m.family for m in measures}
            if any(m.sensitive for m in measures):
                needed.add(Family.COST)
        case ExportSource.DETAIL:
            needed = {detail.KIND_FAMILY.get(checked.request.kind, Family.SALES)}
            needed |= held & {Family.COST}
        case ExportSource.CUSTOMER:
            needed = held & {Family.SALES, Family.PAYMENTS, Family.RECEIVABLES, Family.COST}
        case _:
            needed = held & {*MEASURE_FAMILIES, Family.COST}
    return [family for family in Family if family in needed]


async def ineligible(
    db: AsyncSession, users: Sequence[str], needed: list[Family]
) -> dict[str, str]:
    """Each user who may not receive the report, with the first family they lack. An inactive
    user lacks them all, so it is the first the view reads. An unknown user is not found."""
    found = {
        user.user_id: user
        for user in (await db.execute(select(User).where(User.user_id.in_(users)))).scalars()
    }
    refused: dict[str, str] = {}
    for user_id in users:
        user = found.get(user_id)
        if user is None:
            raise not_found(User, user_id)
        if user.status != EntityStatus.ACTIVE:
            refused[user_id] = needed[0].value
            continue
        held = await engine.families(db, _as_user(user, None))
        missing = next((family for family in needed if family not in held), None)
        if missing is not None:
            refused[user_id] = missing.value
    return refused


def _as_user(user: User, facility: int | None) -> CurrentUser:
    return CurrentUser(
        user_id=user.user_id, session_version=user.session_version,
        administrator=user.administrator, facility_id=facility, employee_id=user.employee_id,
    )  # fmt: skip


async def rebuild_owner(db: AsyncSession, user_id: str) -> CurrentUser:
    """The owner as they are now (R16): their record's privileges and facility. An inactive owner,
    or one with no facility to scope them to, is refused with `auth.forbidden`."""
    user = await db.get(User, user_id, populate_existing=True)
    if user is None or user.status != EntityStatus.ACTIVE:
        raise forbidden([])
    if user.settings is None and not user.administrator:
        raise forbidden([])
    return _as_user(user, user.settings.facility_id if user.settings else None)


def _export_request(view: AnalyticsView, format_: ExportFormat, today: date) -> Any:
    kind = views.kind_of(view)
    request = views.request_of(kind, json.loads(view.definition), views.period_of(view), today)
    return AnalyticsExportRequest(format=format_, **{kind.value: request})


async def _check(
    db: AsyncSession,
    view: AnalyticsView,
    format_: ExportFormat,
    recipients: list[str],
    current: CurrentUser,
) -> None:
    """The view's export checked as the owner, and every recipient against what it reads."""
    request = _export_request(view, format_, views.local_today())
    checked = await exports.check(db, request, current)
    refused = await ineligible(db, recipients, reads(checked))
    if refused:
        user, family = next(iter(refused.items()))
        raise DomainError(
            ErrorCode.ANALYTICS_RECIPIENT_INELIGIBLE,
            f'{user} may not receive this report: they lack {family}',
            {'user': user, 'family': family},
        )


# ── Schedules ─────────────────────────────────────────────────────────────────


def _mask(weekdays: Sequence[int]) -> int:
    return sum({1 << (day - 1) for day in weekdays})


def _weekdays(mask: int) -> list[int]:
    return [day for day in range(1, 8) if mask & (1 << (day - 1))]


async def _recipients(db: AsyncSession, schedules: list[int]) -> dict[int, list[str]]:
    query = (
        select(AnalyticsScheduleRecipient.analytics_schedule, AnalyticsScheduleRecipient.user)
        .where(AnalyticsScheduleRecipient.analytics_schedule.in_(schedules))
        .order_by(AnalyticsScheduleRecipient.user)
    )
    found: dict[int, list[str]] = {schedule: [] for schedule in schedules}
    for schedule, user in (await db.execute(query)).all():
        found[schedule].append(user)
    return found


async def _set_recipients(db: AsyncSession, schedule: int, users: list[str]) -> None:
    table = AnalyticsScheduleRecipient
    await db.execute(delete(table).where(table.analytics_schedule == schedule))
    db.add_all(table(analytics_schedule=schedule, user=user) for user in users)


def response_of(row: AnalyticsSchedule, recipients: list[str]) -> AnalyticsScheduleResponse:
    return AnalyticsScheduleResponse(
        analytics_schedule_id=row.analytics_schedule_id,
        analytics_view=row.analytics_view,
        cadence=CADENCES[row.cadence],
        weekdays=_weekdays(row.weekdays),
        month_day=row.month_day,
        time_of_day=row.time_of_day,
        format=FORMATS[row.format],
        subject=row.subject,
        recipients=recipients,
        enabled=row.enabled,
        next_run_time=row.next_run_time,
    )


async def respond(db: AsyncSession, row: AnalyticsSchedule) -> AnalyticsScheduleResponse:
    found = await _recipients(db, [row.analytics_schedule_id])
    return response_of(row, found[row.analytics_schedule_id])


async def list_schedules(db: AsyncSession, current: CurrentUser) -> list[AnalyticsScheduleResponse]:
    """The caller's schedules; an administrator's are everyone's."""
    query = select(AnalyticsSchedule).order_by(AnalyticsSchedule.analytics_schedule_id)
    if not current.administrator:
        query = query.join(
            AnalyticsView, AnalyticsView.analytics_view_id == AnalyticsSchedule.analytics_view
        ).where(AnalyticsView.user == current.user_id)
    rows = (await db.execute(query)).scalars().all()
    found = await _recipients(db, [row.analytics_schedule_id for row in rows])
    return [response_of(row, found[row.analytics_schedule_id]) for row in rows]


async def get(
    db: AsyncSession, schedule: int, current: CurrentUser
) -> tuple[AnalyticsSchedule, AnalyticsView]:
    """A schedule of the caller's own views, or any for an administrator; else not found."""
    row = await db.get(AnalyticsSchedule, schedule)
    view = await db.get(AnalyticsView, row.analytics_view) if row is not None else None
    if view is None or (view.user != current.user_id and not current.administrator):
        raise not_found(AnalyticsSchedule, schedule)
    return row, view


async def owned(
    db: AsyncSession, schedule: int, current: CurrentUser
) -> tuple[AnalyticsSchedule, AnalyticsView]:
    """A schedule the caller may change: of their own view. An administrator is refused others'."""
    row, view = await get(db, schedule, current)
    if view.user != current.user_id:
        raise forbidden([])
    return row, view


async def create(
    db: AsyncSession, data: AnalyticsScheduleCreate, current: CurrentUser
) -> AnalyticsSchedule:
    view = await views.owned(db, data.analytics_view, current)
    recipients = list(dict.fromkeys(data.recipients))
    await _check(db, view, data.format, recipients, current)
    now = local_now().replace(microsecond=0)
    row = AnalyticsSchedule(
        analytics_view=view.analytics_view_id,
        cadence=CADENCES.index(data.cadence),
        weekdays=_mask(data.weekdays),
        month_day=data.month_day,
        time_of_day=data.time_of_day,
        format=exports.FORMAT_CODES[data.format],
        subject=data.subject,
        enabled=data.enabled,
        creation_time=now,
        modification_time=now,
    )
    row.next_run_time = next_occurrence(row, now)
    db.add(row)
    await db.flush()
    await _set_recipients(db, row.analytics_schedule_id, recipients)
    await db.commit()
    return row


def _timing(row: AnalyticsSchedule) -> tuple:
    return row.cadence, row.weekdays, row.month_day, row.time_of_day, row.enabled


async def update_schedule(
    db: AsyncSession, schedule: int, data: AnalyticsScheduleUpdate, current: CurrentUser
) -> AnalyticsSchedule:
    """Change what is given; every save checks the export and the recipients again. A cadence
    change drops the days that don't apply to the new cadence. The next run moves only when the
    timing changes, so a due occurrence not yet claimed is still sent after any other change."""
    row, view = await owned(db, schedule, current)
    cadence = data.cadence or CADENCES[row.cadence]
    weekdays = data.weekdays
    if weekdays is None:
        weekdays = _weekdays(row.weekdays) if cadence is Cadence.WEEKLY else []
    month_day = data.month_day
    if month_day is None and cadence is Cadence.MONTHLY:
        month_day = row.month_day
    problem = timing_problem(cadence, weekdays, month_day)
    if problem:
        raise RequestValidationError(
            [{'type': 'analytics_schedule', 'loc': ('body', 'cadence'), 'msg': problem}]
        )
    format_ = data.format or FORMATS[row.format]
    recipients = (
        list(dict.fromkeys(data.recipients))
        if data.recipients is not None
        else (await _recipients(db, [schedule]))[schedule]
    )
    await _check(db, view, format_, recipients, current)
    now = local_now().replace(microsecond=0)
    timing = _timing(row)
    row.cadence = CADENCES.index(cadence)
    row.weekdays = _mask(weekdays)
    row.month_day = month_day
    if data.time_of_day is not None:
        row.time_of_day = data.time_of_day
    row.format = exports.FORMAT_CODES[format_]
    if data.subject is not None:
        row.subject = data.subject
    if data.enabled is not None:
        row.enabled = data.enabled
    if _timing(row) != timing:
        row.next_run_time = next_occurrence(row, now)
    row.modification_time = now
    if data.recipients is not None:
        await _set_recipients(db, schedule, recipients)
    await db.commit()
    return row


async def delete_schedule(db: AsyncSession, schedule: int, current: CurrentUser) -> None:
    """Delete the schedule; its recipients and send history go with it."""
    row, _ = await owned(db, schedule, current)
    await db.delete(row)
    await db.commit()


# ── Sends ─────────────────────────────────────────────────────────────────────


def send_response(row: AnalyticsSend, now: datetime) -> AnalyticsSendResponse:
    """One attempt. A row left running over 10 minutes reads as failed: its process died."""
    outcome, code = OUTCOMES[row.outcome], row.code
    if outcome is SendOutcome.RUNNING and now - row.started_at > exports.STALE:
        outcome, code = SendOutcome.FAILED, ErrorCode.SERVER_ERROR.value
    recipients = json.loads(row.recipients)
    return AnalyticsSendResponse(
        analytics_send_id=row.analytics_send_id,
        occurrence=row.occurrence,
        started_at=row.started_at,
        finished_at=row.finished_at,
        outcome=outcome,
        code=code,
        sent_to=recipients['sent_to'],
        dropped=[item['user'] for item in recipients['dropped']],
        size=row.size,
        row_count=row.row_count,
    )


async def list_sends(
    db: AsyncSession, schedule: int, current: CurrentUser, skip: int, limit: int
) -> ListResponse[AnalyticsSendResponse]:
    """A schedule's attempts, newest first."""
    await get(db, schedule, current)
    where = AnalyticsSend.analytics_schedule == schedule
    total = (await db.execute(select(func.count()).where(where))).scalar_one()
    query = (
        select(AnalyticsSend)
        .where(where)
        .order_by(AnalyticsSend.started_at.desc(), AnalyticsSend.analytics_send_id.desc())
        .offset(skip)
        .limit(limit)
    )
    now = local_now()
    rows = (await db.execute(query)).scalars().all()
    return ListResponse(items=[send_response(row, now) for row in rows], total=total)


async def claim(db: AsyncSession, schedule: int, read: datetime, following: datetime) -> bool:
    """Move the schedule from the `next_run_time` it was read with to `following`; True when this
    caller did it (R15). Another run that read the same value changes no row and sends nothing."""
    result = await db.execute(
        update(AnalyticsSchedule)
        .where(
            AnalyticsSchedule.analytics_schedule_id == schedule,
            AnalyticsSchedule.next_run_time == read,
        )
        .values(next_run_time=following)
    )
    await db.commit()
    return result.rowcount == 1


def _recipients_json(sent_to: list[str], dropped: dict[str, str]) -> str:
    return json.dumps(
        {
            'sent_to': sent_to,
            'dropped': [{'user': user, 'family': family} for user, family in dropped.items()],
        }
    )


async def send(
    db: AsyncSession, schedule: AnalyticsSchedule, occurrence: datetime, now: datetime
) -> AnalyticsSend:
    """Send one claimed occurrence and record the attempt: running first, then sent, failed with
    its code, or skipped (FR-162)."""
    row = AnalyticsSend(
        analytics_schedule=schedule.analytics_schedule_id, occurrence=occurrence, started_at=now,
        outcome=OUTCOMES.index(SendOutcome.RUNNING), recipients=_recipients_json([], {}),
        format=schedule.format,
    )  # fmt: skip
    db.add(row)
    await db.commit()
    send_id = row.analytics_send_id
    try:
        outcome, code = await _deliver(db, schedule, row, now)
    except DomainError as exc:
        outcome, code = SendOutcome.FAILED, exc.code
    except Exception:
        log.exception('scheduled report %s failed', schedule.analytics_schedule_id)
        await db.rollback()
        row = await db.get(AnalyticsSend, send_id, populate_existing=True)
        outcome, code = SendOutcome.FAILED, ErrorCode.SERVER_ERROR
    row.outcome = OUTCOMES.index(outcome)
    row.code = code.value if code is not None else None
    row.finished_at = local_now()
    await db.commit()
    return row


async def _deliver(
    db: AsyncSession, schedule: AnalyticsSchedule, row: AnalyticsSend, now: datetime
) -> tuple[SendOutcome, ErrorCode | None]:
    """Build the owner's export and mail it to the recipients who still qualify. A refusal raises
    `DomainError`; a skip returns its code."""
    view = await db.get(AnalyticsView, schedule.analytics_view, populate_existing=True)
    owner = await rebuild_owner(db, view.user)
    format_ = FORMATS[schedule.format]
    checked = await exports.check(db, _export_request(view, format_, now.date()), owner)
    users = (await _recipients(db, [schedule.analytics_schedule_id]))[
        schedule.analytics_schedule_id
    ]
    dropped = await ineligible(db, users, reads(checked))
    sent_to = [user for user in users if user not in dropped]
    row.recipients = _recipients_json(sent_to, dropped)
    count, answer = await exports.build_table(db, checked)
    if exports.export_mode(format_, count) == 'refused':
        raise DomainError(
            ErrorCode.ANALYTICS_PDF_TOO_LARGE,
            'Too many rows for a PDF; export to XLSX or CSV',
            {'rows': count, 'max_rows': exports.PDF_ROWS},
        )
    if format_ is ExportFormat.PDF:
        data = await exports.render_pdf(db, checked, answer)
    else:
        data = await anyio.to_thread.run_sync(exports.file_bytes, answer, format_)
    row.size, row.row_count = len(data), count
    if len(data) > settings.report_attachment_max_bytes:
        return SendOutcome.SKIPPED, ErrorCode.ANALYTICS_ATTACHMENT_TOO_LARGE
    if not sent_to:
        return SendOutcome.SKIPPED, ErrorCode.ANALYTICS_RECIPIENT_INELIGIBLE
    mailer = mail.get_mailer()
    if mailer is None:
        return SendOutcome.SKIPPED, ErrorCode.EMAIL_NOT_CONFIGURED
    query = select(User.email).where(User.user_id.in_(sent_to)).order_by(User.user_id)
    start, end = checked.request.start, checked.request.end
    message = mail.Message(
        to=list((await db.execute(query)).scalars()),
        subject=schedule.subject,
        body=f'{view.name}, del {start} al {end}. Datos al {now:%Y-%m-%d %H:%M}.',
        attachments=(
            mail.Attachment(
                f'analytics-{checked.source.value}-{start}-{end}.{format_.value}',
                exports.MEDIA_TYPES[format_],
                data,
            ),
        ),
    )
    try:
        await mailer.send(message)
    except mail.MailError as exc:
        log.warning('scheduled report %s: %s', schedule.analytics_schedule_id, exc)
        return SendOutcome.FAILED, ErrorCode.EMAIL_SEND_FAILED
    return SendOutcome.SENT, None
