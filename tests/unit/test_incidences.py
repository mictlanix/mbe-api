"""Audit entries — what makes a payment reversal evidenced rather than silent (FR-045a).

`sales_order_payment` has no canceller or cancellation-time column, so the evidence lives in the
`incidence` table instead of a migration. There is no `SourceType` value for an application, so an
entry keys to the *payment* and names the application in its content — a known limitation accepted
in clarification.

The required reason is the point: SC-009 says no reversal is anonymous or unexplained.
"""

from unittest.mock import AsyncMock

import pytest
from fastapi import HTTPException

from app.enums import SourceType
from app.models.incidence import Incidence
from app.services.incidences import record


def _db() -> AsyncMock:
    db = AsyncMock()
    db.add = lambda obj: db.added.append(obj)
    db.added = []
    return db


def test_records_source_instance_updater_and_time() -> None:
    db = _db()

    record(
        db,
        source=SourceType.CUSTOMER_PAYMENT,
        instance_id=42,
        updater=7,
        reason='Applied to the wrong order',
    )

    entry = db.added[0]
    assert isinstance(entry, Incidence)
    assert entry.source == int(SourceType.CUSTOMER_PAYMENT)
    assert entry.instance_id == 42
    assert entry.updater == 7
    assert entry.modification_time is not None


def test_reason_is_stored() -> None:
    db = _db()

    record(
        db,
        source=SourceType.CUSTOMER_PAYMENT,
        instance_id=1,
        updater=1,
        reason='Duplicate capture',
    )

    assert 'Duplicate capture' in (db.added[0].comment or '')


def test_context_names_what_the_entry_is_about() -> None:
    """An application has no SourceType of its own, so it is named in the content."""
    db = _db()

    record(
        db,
        source=SourceType.CUSTOMER_PAYMENT,
        instance_id=1,
        updater=1,
        reason='Wrong order',
        context='Reversed application 9 against sales order 5',
    )

    assert 'application 9' in (db.added[0].content or '')


@pytest.mark.parametrize('reason', ['', '   ', None])
def test_missing_reason_is_refused(reason: str | None) -> None:
    """A reversal without a stated reason must not be recordable at all."""
    db = _db()

    with pytest.raises(HTTPException) as exc:
        record(
            db,
            source=SourceType.CUSTOMER_PAYMENT,
            instance_id=1,
            updater=1,
            reason=reason,  # type: ignore[arg-type]
        )

    assert exc.value.status_code == 422
    assert db.added == []


def test_works_for_other_source_types() -> None:
    db = _db()

    record(db, source=SourceType.SALES_ORDER, instance_id=3, updater=1, reason='Investigate')

    assert db.added[0].source == int(SourceType.SALES_ORDER)
