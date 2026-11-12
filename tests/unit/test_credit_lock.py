"""#157: the named credit lock is released when its connection returns to the pool."""

from typing import Any

from app.db.session import NAMED_LOCKS, _release_named_locks


class _Cursor:
    def __init__(self, calls: list[tuple[str, tuple[Any, ...]]], fail: bool) -> None:
        self.calls = calls
        self.fail = fail

    def execute(self, sql: str, args: tuple[Any, ...]) -> None:
        if self.fail:
            raise OSError('gone')
        self.calls.append((sql, args))

    def close(self) -> None:
        pass


class _Connection:
    def __init__(self, fail: bool = False) -> None:
        self.calls: list[tuple[str, tuple[Any, ...]]] = []
        self.fail = fail

    def cursor(self) -> _Cursor:
        return _Cursor(self.calls, self.fail)


class _Record:
    def __init__(self, names: list[str] | None) -> None:
        self.info: dict[str, Any] = {NAMED_LOCKS: names} if names is not None else {}
        self.invalidated: BaseException | None = None

    def invalidate(self, error: BaseException) -> None:
        self.invalidated = error


def test_checkin_releases_each_named_lock_once() -> None:
    connection, record = _Connection(), _Record(['mbe_dev.credit.7', 'mbe_dev.credit.9'])

    _release_named_locks(connection, record)
    _release_named_locks(connection, record)

    assert connection.calls == [
        ('SELECT RELEASE_LOCK(%s)', ('mbe_dev.credit.7',)),
        ('SELECT RELEASE_LOCK(%s)', ('mbe_dev.credit.9',)),
    ]
    assert NAMED_LOCKS not in record.info and record.invalidated is None


def test_checkin_without_locks_sends_nothing() -> None:
    connection, record = _Connection(), _Record(None)

    _release_named_locks(connection, record)

    assert connection.calls == []


def test_a_failed_release_invalidates_the_connection() -> None:
    """Closing the connection is what frees its locks when the release itself fails."""
    connection, record = _Connection(fail=True), _Record(['mbe_dev.credit.7'])

    _release_named_locks(connection, record)

    assert isinstance(record.invalidated, OSError)
