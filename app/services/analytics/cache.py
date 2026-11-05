"""A short per-process cache of analytics responses (spec 037, FR-021, research R13).

Identical requests within 60 seconds are answered from memory. The key holds everything that
changes the answer: the normalized definition, the resolved facility scope and the families the
caller holds, so one caller's figures never reach a caller who may see less. Freshness stays within
FR-020's five minutes because nothing is kept longer than a minute.
"""

import hashlib
import json
import time
from collections import OrderedDict
from collections.abc import Callable, Iterable

TTL_SECONDS = 60.0
MAX_ENTRIES = 256


def key(kind: str, definition_json: str, facility: int | None, families: Iterable[str]) -> str:
    material = json.dumps(
        {
            'kind': kind,
            'definition': json.loads(definition_json),
            'facility': facility,
            'families': sorted(families),
        },
        sort_keys=True,
    )
    return hashlib.sha256(material.encode()).hexdigest()


def etag(body: bytes) -> str:
    return '"' + hashlib.sha256(body).hexdigest()[:32] + '"'


class ResponseCache:
    """TTL and LRU over response bodies."""

    def __init__(
        self,
        ttl: float = TTL_SECONDS,
        size: int = MAX_ENTRIES,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.ttl = ttl
        self.size = size
        self.clock = clock
        self.entries: OrderedDict[str, tuple[float, bytes]] = OrderedDict()

    def get(self, name: str) -> bytes | None:
        entry = self.entries.get(name)
        if entry is None:
            return None
        stored, body = entry
        if self.clock() - stored >= self.ttl:
            del self.entries[name]
            return None
        self.entries.move_to_end(name)
        return body

    def put(self, name: str, body: bytes) -> None:
        self.entries[name] = (self.clock(), body)
        self.entries.move_to_end(name)
        while len(self.entries) > self.size:
            self.entries.popitem(last=False)

    def clear(self) -> None:
        self.entries.clear()


#: The process's cache. Each uvicorn worker has its own (plan, Technical Context).
responses = ResponseCache()
