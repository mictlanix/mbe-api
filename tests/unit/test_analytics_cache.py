"""Spec 037, T024: the response cache (FR-021, R13)."""

from app.services.analytics import cache
from app.services.analytics.cache import ResponseCache


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def test_a_hit_within_three_minutes_and_a_miss_after() -> None:
    clock = Clock()
    store = ResponseCache(clock=clock)
    store.put('k', b'{}')

    clock.now += 179.9
    hit = store.get('k')
    clock.now += 0.2
    miss = store.get('k')

    assert (hit, miss) == (b'{}', None)


def test_the_least_recently_used_entry_goes_past_256() -> None:
    store = ResponseCache(clock=Clock())
    for i in range(256):
        store.put(f'k{i}', b'x')
    store.get('k0')  # used, so k1 is now the oldest

    store.put('k256', b'x')

    assert store.get('k0') == b'x'
    assert store.get('k1') is None
    assert len(store.entries) == 256


def test_scope_and_visibility_never_share_an_entry() -> None:
    definition = '{"measures": ["net_sales"]}'
    base = cache.key('query', definition, 1, ['sales'])

    assert cache.key('query', definition, 2, ['sales']) != base
    assert cache.key('query', definition, None, ['sales']) != base
    assert cache.key('query', definition, 1, ['sales', 'cost']) != base
    assert cache.key('dashboard', definition, 1, ['sales']) != base
    assert cache.key('query', definition, 1, ['sales']) == base


def test_the_etag_is_stable_for_the_same_body() -> None:
    assert cache.etag(b'{"a":1}') == cache.etag(b'{"a":1}')
    assert cache.etag(b'{"a":1}') != cache.etag(b'{"a":2}')
    assert cache.etag(b'{}').startswith('"')
