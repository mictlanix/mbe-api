"""The business clock: "now" and "today" come from `BUSINESS_TIMEZONE`, never the host (#228).

Every datetime the API stores is naive wall-clock time in one zone, shared with the legacy monolith.
Reading the host clock made that zone whatever the server happened to be set to, so these tests
pin the helpers to a zone far from any plausible host and check they follow the setting.
"""

from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from pydantic import ValidationError

from app.core import clock
from app.core.config import Settings, settings

_TOKYO = ZoneInfo('Asia/Tokyo')
_MEXICO_CITY = ZoneInfo('America/Mexico_City')


@pytest.fixture
def tokyo(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'business_timezone', _TOKYO)


@pytest.fixture
def mexico_city(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, 'business_timezone', _MEXICO_CITY)


class TestLocalNow:
    def test_it_follows_the_setting_not_the_host(self, tokyo: None) -> None:
        now = clock.local_now()

        expected = datetime.now(_TOKYO).replace(tzinfo=None)
        assert abs(now - expected) < timedelta(seconds=2)

    def test_it_is_naive_like_every_stored_column(self, tokyo: None) -> None:
        assert clock.local_now().tzinfo is None


class TestLocalToday:
    def test_it_is_the_date_in_the_business_timezone(self, tokyo: None) -> None:
        today = clock.local_today()

        assert isinstance(today, date)
        assert today == datetime.now(_TOKYO).date()


class TestToLocal:
    def test_a_naive_value_is_taken_as_already_local(self, mexico_city: None) -> None:
        value = datetime(2026, 9, 27, 12, 0)

        assert clock.to_local(value) == value

    def test_a_utc_value_is_converted_to_business_wall_clock(self, mexico_city: None) -> None:
        converted = clock.to_local(datetime.fromisoformat('2026-09-27T18:00:00+00:00'))

        assert converted == datetime(2026, 9, 27, 12, 0)
        assert converted.tzinfo is None

    def test_any_offset_is_honoured_not_only_utc(self, mexico_city: None) -> None:
        converted = clock.to_local(datetime.fromisoformat('2026-09-27T12:00:00-05:00'))

        assert converted == datetime(2026, 9, 27, 11, 0)
        assert converted.tzinfo is None


class TestTheSettingIsRequired:
    """No default: a deployment that forgets it must not start on the host's clock instead."""

    def test_a_missing_setting_stops_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv('BUSINESS_TIMEZONE', raising=False)

        with pytest.raises(ValidationError, match='business_timezone'):
            Settings(_env_file=None)

    def test_an_unknown_zone_stops_startup(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv('BUSINESS_TIMEZONE', 'Mars/Olympus')

        with pytest.raises(ValidationError, match='invalid timezone') as error:
            Settings(_env_file=None)

        assert 'business_timezone' in str(error.value)
