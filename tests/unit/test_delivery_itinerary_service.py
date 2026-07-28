"""Itinerary service: bucketing, the double-assignment guard, departure and closure.

The guard is the assertion that matters most. SC-004 says the same open quantity is never
committed to two itineraries, and the whole of it rests on `SELECT ... FOR UPDATE` against the
delivery-order line — so these tests check the lock is taken, that the arithmetic under it is
right, and that departure does not quietly hand the goods back.
"""

import asyncio
import inspect
from datetime import date
from decimal import Decimal
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.services import delivery_itinerary_service as service


class TestBucketing:
    """The sliding window: one day back, today, two ahead, plus overflow either side."""

    TODAY = date(2026, 7, 27)

    @pytest.mark.parametrize(
        ('scheduled', 'expected'),
        [
            (date(2026, 7, 20), 'earlier'),
            (date(2026, 7, 26), 'yesterday'),
            (date(2026, 7, 27), 'today'),
            (date(2026, 7, 28), 'tomorrow'),
            (date(2026, 7, 29), 'day_after'),
            (date(2026, 7, 30), 'later'),
            (date(2027, 1, 1), 'later'),
        ],
    )
    def test_lands_in_the_right_bucket(self, scheduled: date, expected: str) -> None:
        assert service.bucket_for(scheduled, self.TODAY) == expected

    def test_an_unscheduled_line_falls_to_later(self) -> None:
        assert service.bucket_for(None, self.TODAY) == 'later'


class TestAssertOpen:
    def test_an_open_itinerary_accepts_changes(self) -> None:
        service.assert_open(SimpleNamespace(status=0))

    @pytest.mark.parametrize('status', [1, 2, 3])
    def test_anything_else_is_refused(self, status: int) -> None:
        with pytest.raises(HTTPException) as exc:
            service.assert_open(SimpleNamespace(status=status))

        assert exc.value.status_code == 409


class TestTheGuard:
    """FR-027, FR-028, SC-004."""

    def test_commitment_takes_a_row_lock(self) -> None:
        """The lock is the guard. Without it the re-read below proves nothing."""
        source = inspect.getsource(service._lock_line)

        assert 'with_for_update()' in source

    def test_commit_line_reads_open_quantity_under_that_lock(self) -> None:
        source = inspect.getsource(service.commit_line)

        assert '_lock_line(' in source
        assert 'open_quantity(' in source

    @pytest.mark.asyncio
    async def test_committing_within_the_open_quantity_succeeds(self) -> None:
        line = _order_line(ordered='10')
        db = _db(line)

        entry = await service.commit_line(
            db, _itinerary(), _stop(), delivery_order_detail=11, quantity=Decimal('4')
        )

        assert entry.committed_quantity == Decimal('4')
        assert line.committed_quantity == Decimal('4')
        assert service.delivery_order_service.open_quantity(line) == Decimal('6')

    @pytest.mark.asyncio
    async def test_omitting_the_quantity_fills_to_the_open_quantity(self) -> None:
        line = _order_line(ordered='10', committed='4')
        db = _db(line)

        entry = await service.commit_line(db, _itinerary(), _stop(), delivery_order_detail=11)

        assert entry.committed_quantity == Decimal('6')

    @pytest.mark.asyncio
    async def test_committing_beyond_the_open_quantity_states_what_is_available(self) -> None:
        db = _db(_order_line(ordered='10', committed='4'))

        with pytest.raises(HTTPException) as exc:
            await service.commit_line(
                db, _itinerary(), _stop(), delivery_order_detail=11, quantity=Decimal('7')
            )

        assert exc.value.status_code == 422
        assert '6' in exc.value.detail

    @pytest.mark.asyncio
    async def test_a_fully_committed_line_has_nothing_left(self) -> None:
        db = _db(_order_line(ordered='10', committed='10'))

        with pytest.raises(HTTPException) as exc:
            await service.commit_line(db, _itinerary(), _stop(), delivery_order_detail=11)

        assert exc.value.status_code == 422

    @pytest.mark.asyncio
    async def test_two_concurrent_commitments_cannot_both_take_the_same_quantity(self) -> None:
        """SC-004 rendered as arithmetic: the second caller sees the first one's effect.

        A real race is serialised by the row lock in the database. What is asserted here is the
        half the application owns — that the check re-reads the line and that a second request for
        the same remaining quantity is refused rather than silently doubling it.
        """
        line = _order_line(ordered='10')
        db = _db(line)

        first, second = await asyncio.gather(
            service.commit_line(
                db, _itinerary(), _stop(), delivery_order_detail=11, quantity=Decimal('10')
            ),
            service.commit_line(
                db, _itinerary(), _stop(), delivery_order_detail=11, quantity=Decimal('10')
            ),
            return_exceptions=True,
        )

        outcomes = [first, second]
        succeeded = [o for o in outcomes if not isinstance(o, Exception)]
        refused = [o for o in outcomes if isinstance(o, HTTPException)]

        assert len(succeeded) == 1
        assert len(refused) == 1
        assert line.committed_quantity == Decimal('10')


class TestDeparture:
    def test_committed_is_not_released_at_departure(self) -> None:
        """FR-029a — releasing it here would return goods on the truck to the open pool."""
        source = inspect.getsource(service.depart)

        assert 'entry.sent_quantity = entry.committed_quantity' in source
        assert 'order_line.committed_quantity -=' not in source
        assert 'order_line.committed_quantity =' not in source

    def test_departure_posts_both_halves_of_the_move(self) -> None:
        source = inspect.getsource(service.depart)

        assert source.count('post_movement(') == 2
        # Spec 013: the inbound half goes to the dispatching facility's own in-transit location,
        # not to the single configured warehouse spec 012 had (FR-002).
        assert 'transit[order_line.warehouse]' in source
        assert 'in_transit_warehouse_id' not in source
        assert 'order_line.warehouse' in source

    def test_the_transit_map_is_resolved_before_any_entry_is_written(self) -> None:
        """FR-009 with all-or-nothing semantics.

        If the map were resolved lazily inside the posting loop, a trip whose second facility is
        missing its location would depart with the first facility's goods already posted — half a
        trip in the ledger and no way to tell from the itinerary.
        """
        source = inspect.getsource(service.depart)

        assert source.index('_transit_map(') < source.index('post_movement(')

    def test_departure_does_not_repair_a_missing_location(self) -> None:
        """FR-009a — no self-healing. The refusal is the whole behaviour."""
        source = inspect.getsource(service._transit_map)

        assert 'has no in-transit location' in source
        assert 'Warehouse(' not in source, 'a missing location is refused, never created'
        assert 'db.add(' not in source

    def test_departure_releases_only_what_departed(self) -> None:
        """The ledger now records the movement, so that part of the claim is redundant (FR-057).

        It must be the **singular** release, scoped to product, warehouse and sent quantity. The
        whole-order form would give back the lines that stayed behind, and those would become
        sellable a second time — the oversell FR-055a exists to prevent, by the back door.
        """
        source = inspect.getsource(service.depart)

        assert 'release_reservation(' in source
        assert 'release_reservations(' not in source
        assert 'quantity=entry.sent_quantity' in source

    def test_returned_goods_reclaim_their_reservation(self) -> None:
        """Departure released the claim; a refusal brings the goods back and must bring it back.

        Without this the sales order still owes the customer but holds nothing, so the stock can
        be sold out from under the retry or the partial delivery's child order.
        """
        closure = inspect.getsource(service.close_stop)
        reclaim = inspect.getsource(service._reclaim_reservation)

        assert '_reclaim_reservation(' in closure
        assert 'stock_ledger.reserve(' in reclaim


class TestClosure:
    def test_a_child_order_lands_in_preparation_not_approved(self) -> None:
        """APPROVED is where a counter pickup rests; a delivery child there would have no exit."""
        source = inspect.getsource(service._split_child_order)

        assert 'S.IN_PREPARATION' in source
        assert 'parent_delivery_order=parent.delivery_order_id' in source

    def test_a_child_is_only_created_for_a_real_remainder(self) -> None:
        source = inspect.getsource(service._split_child_order)

        assert 'if not remainder:' in source

    def test_closure_releases_the_commitment(self) -> None:
        source = inspect.getsource(service.close_stop)

        assert 'order_line.committed_quantity -= entry.committed_quantity' in source

    def test_closure_requires_every_line_to_be_accounted_for(self) -> None:
        source = inspect.getsource(service.close_stop)

        assert 'Every line on the stop must be accounted for' in source

    def test_closure_refuses_delivering_more_than_was_sent(self) -> None:
        source = inspect.getsource(service.close_stop)

        assert 'was accepted' in source

    def test_a_shortfall_without_a_reason_is_refused(self) -> None:
        source = inspect.getsource(service.close_stop)

        assert 'needs a reason code' in source

    def test_the_itinerary_closes_only_when_no_stop_is_pending(self) -> None:
        """One failed stop must not block the rest of the trip from closing (FR-050)."""
        source = inspect.getsource(service.close_stop)

        assert 'if not remaining:' in source
        assert 'ItineraryStatus.CLOSED' in source


# ── Fakes ─────────────────────────────────────────────────────────────────────


def _order_line(ordered: str, committed: str = '0') -> SimpleNamespace:
    return SimpleNamespace(
        delivery_order_detail_id=11,
        delivery_order=1,
        quantity=Decimal(ordered),
        committed_quantity=Decimal(committed),
        delivered_quantity=Decimal(0),
        returned_quantity=Decimal(0),
    )


def _itinerary() -> SimpleNamespace:
    return SimpleNamespace(deliveries_itinerary_id=1, status=0)


def _stop() -> SimpleNamespace:
    return SimpleNamespace(deliveries_itinerary_stop_id=10, deliveries_itinerary=1)


def _db(line: SimpleNamespace):
    """A session that hands back `line` from the locking read and records what is staged."""

    class _Result:
        def __init__(self, value):
            self._value = value

        def scalar_one_or_none(self):
            return self._value

    class _Db:
        def __init__(self):
            self.added: list[object] = []

        def add(self, obj):
            self.added.append(obj)

        async def execute(self, _statement):
            return _Result(line)

        async def commit(self):
            return None

        async def refresh(self, _obj):
            return None

    return _Db()


class TestPerFacilityTransit:
    """Spec 013 — goods in transit stay on the books of the facility they left.

    Exercised against `_transit_map` directly: it is where the facility decision is made, and the
    posting loops around it are unchanged from spec 012 apart from which id they read.
    """

    @staticmethod
    def _entries(*warehouse_ids: int):
        return [(SimpleNamespace(), SimpleNamespace(warehouse=w)) for w in warehouse_ids]

    @staticmethod
    def _db(mapping: dict[int, int], facilities: list[int] | None = None):
        class _Scalars:
            def all(self_inner):
                return facilities or []

        class _Result:
            def scalars(self_inner):
                return _Scalars()

        class _Db:
            async def execute(self_inner, _statement):
                return _Result()

        return _Db(), mapping

    def test_each_facility_receives_only_its_own_goods(self, monkeypatch) -> None:
        """FR-002, SC-002 — the defect this feature exists to fix."""
        db, mapping = self._db({12: 21, 17: 22})
        monkeypatch.setattr(
            service.warehouse_service,
            'transit_warehouses_for',
            _async_return(mapping),
        )

        result = asyncio.run(service._transit_map(db, self._entries(12, 17)))

        assert result == {12: 21, 17: 22}
        assert result[12] != result[17], 'two facilities must not share an in-transit location'

    def test_a_trip_spanning_two_facilities_is_not_refused(self, monkeypatch) -> None:
        """FR-005 — one truck may carry goods from more than one facility."""
        db, mapping = self._db({12: 21, 17: 22})
        monkeypatch.setattr(
            service.warehouse_service, 'transit_warehouses_for', _async_return(mapping)
        )

        assert asyncio.run(service._transit_map(db, self._entries(12, 17, 12))) == mapping

    def test_a_facility_without_a_location_refuses_the_whole_departure(self, monkeypatch) -> None:
        """FR-009 — named, and 422 rather than a silent misfiling."""
        db, _ = self._db({12: 21}, facilities=[53])
        monkeypatch.setattr(
            service.warehouse_service, 'transit_warehouses_for', _async_return({12: 21})
        )

        with pytest.raises(HTTPException) as exc:
            asyncio.run(service._transit_map(db, self._entries(12, 17)))

        assert exc.value.status_code == 422
        assert 'no in-transit location' in exc.value.detail
        assert '53' in exc.value.detail, 'the offending facility must be named'

    def test_no_entries_needs_no_lookup(self, monkeypatch) -> None:
        db, _ = self._db({})
        monkeypatch.setattr(service.warehouse_service, 'transit_warehouses_for', _async_return({}))

        assert asyncio.run(service._transit_map(db, [])) == {}


def _async_return(value):
    async def _inner(*_args, **_kwargs):
        return value

    return _inner
