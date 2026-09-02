"""What a price list retirement does, observed statement by statement (#181).

A retirement is three decisions — what it deletes, what it moves, what it refuses on — and all
three are invisible from the endpoint, which sees only a 204. So these tests read the SQL the
service issues rather than trusting a mock's call list, the way `tests/unit/test_product_service.py`
reads the merge's statements: the coverage below is what the retirement does, not what it was
mocked into doing.
"""

import inspect
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi import HTTPException

from app.core.constants import COST_PRICE_LIST_ID
from app.models.product import PriceList
from app.services.price_list_service import (
    _DELETE_CASCADE,
    COST_LIST_NOT_ASSIGNABLE,
    assert_not_cost_list,
    delete_price_list,
    get_price_list,
    list_price_lists,
    preview_delete,
)
from app.services.product_service import get_missing_price_facets
from app.services.references import referencing_columns

RETIRED, REPLACEMENT = 7, 3


def _db(replacement: PriceList | None = None) -> AsyncMock:
    db = AsyncMock()
    db.get = AsyncMock(return_value=replacement)
    return db


async def _steps(
    *, replacement_id: int | None = None, db: AsyncMock | None = None
) -> tuple[list[str], AsyncMock]:
    """Every step a retirement of list 7 takes, in order, and the guard it called.

    `db.execute` records its SQL; the blocker guard records itself as `'CHECK'`, so a test can
    assert what runs *before* what rather than merely that both ran. The guard is patched because
    it would otherwise try to read counts off an `AsyncMock`; its arguments are what matters here
    and they are returned for inspection.
    """
    db = db if db is not None else _db(PriceList(price_list_id=REPLACEMENT))
    steps: list[str] = []

    async def _execute(statement: object, *args: object, **kwargs: object) -> MagicMock:
        steps.append(str(statement).replace('\n', ' '))
        return MagicMock()

    db.execute = AsyncMock(side_effect=_execute)
    guard = AsyncMock(side_effect=lambda *a, **k: steps.append('CHECK'))

    with patch('app.services.price_list_service.assert_not_referenced', new=guard):
        await delete_price_list(db, PriceList(price_list_id=RETIRED), replacement_id=replacement_id)
    return steps, guard


def _deleted_tables(steps: list[str]) -> set[str]:
    return {s.split()[2] for s in steps if s.startswith('DELETE FROM')}


# ── The list's own prices (US1) ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_retirement_deletes_the_lists_own_prices() -> None:
    """The blocker #181 reports. A `product_price` row is the price of a product *in this list*,
    so it goes with the list rather than being reported as something the client must clear."""
    steps, _ = await _steps()

    assert _deleted_tables(steps) == {'product_price'}
    assert [s for s in steps if s.startswith('DELETE FROM')] == [
        'DELETE FROM product_price WHERE product_price.list = :list_1'
    ]


@pytest.mark.asyncio
async def test_retirement_deletes_nothing_but_the_cascade_set() -> None:
    """`customer` is the relation this must never sweep: the assignment survives the list, and
    which tier it lands on is a decision the API does not get to make silently."""
    steps, _ = await _steps(replacement_id=REPLACEMENT)

    assert 'customer' not in _deleted_tables(steps)
    assert _deleted_tables(steps) <= _DELETE_CASCADE


@pytest.mark.asyncio
async def test_retirement_filters_on_the_mapped_column_not_the_attribute_name() -> None:
    """`ProductPrice.price_list` is stored in a column called `list` — aliased away from the
    Python builtin. Naming the attribute here would delete nothing and take the list with it."""
    steps, _ = await _steps()
    (cascade,) = [s for s in steps if s.startswith('DELETE FROM')]

    assert 'product_price.list = ' in cascade
    assert 'product_price.price_list' not in ' '.join(steps)


@pytest.mark.asyncio
async def test_retirement_deletes_the_list_itself_last() -> None:
    db = _db()
    steps, _ = await _steps(db=db)

    assert steps[-1].startswith('DELETE FROM product_price')  # nothing after the cascade
    db.delete.assert_awaited_once()
    db.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_exempt_and_cascade_are_the_same_set() -> None:
    """The invariant, and the reason the cascade is a loop rather than a second call: a relation
    cannot be exempted from the blocker check without being swept, or swept without being
    exempted. Identity, not equality — two equal literals would drift the way #112's hand-written
    list drifted from the relations that actually existed."""
    steps, guard = await _steps()

    assert guard.await_args.kwargs['exempt'] is _DELETE_CASCADE
    every_relation = {t.name for t, _ in referencing_columns(PriceList)}
    assert _deleted_tables(steps) == every_relation & _DELETE_CASCADE


# ── The customers (US2) ───────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_customers_move_before_the_blocker_check_runs() -> None:
    """Order is the whole design (research R2): after the move nothing references the list, so
    the generic check passes for the ordinary reason instead of through an exemption that would
    depend on a request parameter. Reversing these two lines is how this silently breaks."""
    steps, _ = await _steps(replacement_id=REPLACEMENT)

    assert steps[0] == (
        'UPDATE customer SET price_list=:price_list WHERE customer.price_list = :price_list_1'
    )
    assert steps[1] == 'CHECK'


@pytest.mark.asyncio
async def test_no_customer_is_touched_when_no_replacement_is_named() -> None:
    """Omitting the replacement preserves today's behaviour exactly: the check runs first and
    refuses, naming the blocker."""
    steps, _ = await _steps()

    assert steps[0] == 'CHECK'
    assert not [s for s in steps if s.startswith('UPDATE')]


@pytest.mark.asyncio
async def test_a_list_cannot_replace_itself() -> None:
    db = _db()
    with pytest.raises(HTTPException) as exc:
        await delete_price_list(db, PriceList(price_list_id=RETIRED), replacement_id=RETIRED)

    assert (exc.value.status_code, exc.value.detail) == (
        400,
        'Cannot replace a price list with itself',
    )
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_replacement_that_does_not_exist_is_refused() -> None:
    db = _db(replacement=None)
    with pytest.raises(HTTPException) as exc:
        await delete_price_list(db, PriceList(price_list_id=RETIRED), replacement_id=999)

    assert (exc.value.status_code, exc.value.detail) == (404, 'Replacement price list not found')
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_refused_replacement_writes_nothing_at_all() -> None:
    """Both refusals above are raised before any statement, so the contract's all-or-nothing
    claim holds for them without relying on the transaction being rolled back."""
    for replacement_id, db in ((RETIRED, _db()), (999, _db(replacement=None))):
        with pytest.raises(HTTPException):
            await delete_price_list(
                db, PriceList(price_list_id=RETIRED), replacement_id=replacement_id
            )
        db.execute.assert_not_awaited()
        db.delete.assert_not_awaited()
        db.commit.assert_not_awaited()


# ── The report (US3) ──────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_report_counts_every_relation_without_exempting_the_cascade() -> None:
    """Passing `_DELETE_CASCADE` here is the plausible mistake: it would hide the prices, which
    are the largest thing a retirement does and the number an operator most needs to see."""
    db = AsyncMock()
    counts = [('product_price.list', 4312), ('customer.price_list', 12)]
    with patch(
        'app.services.price_list_service.find_blocking_references',
        new=AsyncMock(return_value=counts),
    ) as refs:
        assert await preview_delete(db, PriceList(price_list_id=RETIRED)) == counts

    assert 'exempt' not in refs.await_args.kwargs


@pytest.mark.asyncio
async def test_report_changes_nothing() -> None:
    db = AsyncMock()
    with patch(
        'app.services.price_list_service.find_blocking_references', new=AsyncMock(return_value=[])
    ):
        await preview_delete(db, PriceList(price_list_id=RETIRED))

    db.commit.assert_not_awaited()
    db.delete.assert_not_awaited()
    db.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_report_counts_exactly_what_a_retirement_touches() -> None:
    """The invariant of SC-005. The report's categories are every relation to `price_list`, which
    is the union of what the retirement deletes and what it moves or refuses on — so the two
    cannot describe different operations. If this can fail, the report is a lie."""
    steps, guard = await _steps(replacement_id=REPLACEMENT)

    reported = {table.name for table, _ in referencing_columns(PriceList)}
    swept = _deleted_tables(steps)
    moved = {'customer'}
    blocked = reported - swept - moved

    assert reported == swept | moved | blocked
    assert swept == _DELETE_CASCADE & reported
    assert blocked == set()  # nothing unclassified today; a new relation lands here and blocks


# ── The cost price list (#194) ────────────────────────────────────────────────


class TestTheCostListIsAConstant:
    """Not a setting: the monolith writes cost to this id, so a wrong value is not a preference.

    These pin the shape, not the value: not configurable, and every site names the constant. The
    integration tests cannot check that — the constant *is* 0, so a literal behaves identically.
    """

    def test_it_is_not_a_configurable_setting(self) -> None:
        from app.core.config import Settings

        assert 'cost_price_list_id' not in Settings.model_fields

    def test_zero_is_a_real_row_so_nothing_may_test_it_for_truthiness(self) -> None:
        """0 is "Costo", not an absent value, so `if price_list_id:` would wave it through."""
        assert COST_PRICE_LIST_ID == 0
        assert 'replacement_id is not None' in inspect.getsource(delete_price_list)

    @pytest.mark.parametrize(
        'function',
        [assert_not_cost_list, get_price_list, list_price_lists, get_missing_price_facets],
        ids=['guard', 'resource-lookup', 'listing', 'missing-price-facets'],
    )
    def test_every_site_names_the_constant(self, function: object) -> None:
        """The four places the rule is expressed; only the refusals share the guard."""
        assert 'COST_PRICE_LIST_ID' in inspect.getsource(function)


@pytest.mark.asyncio
async def test_the_resource_lookup_hides_the_cost_list_without_reading_the_row() -> None:
    """The `None` all four routes branch on. It must not read the row first, or the rejection
    would depend on the row still existing."""
    db = AsyncMock()

    assert await get_price_list(db, COST_PRICE_LIST_ID) is None
    db.get.assert_not_called()

    # An ordinary list still resolves.
    await get_price_list(db, REPLACEMENT)
    db.get.assert_awaited_once_with(PriceList, REPLACEMENT)


@pytest.mark.asyncio
async def test_deleting_the_cost_list_is_refused_before_anything_is_written() -> None:
    """Refused ahead of the customer move and the cascade: nothing is issued at all."""
    db = AsyncMock()

    with pytest.raises(HTTPException) as refused:
        await delete_price_list(db, PriceList(price_list_id=COST_PRICE_LIST_ID))

    assert refused.value.status_code == 400
    assert refused.value.detail == 'The cost price list cannot be deleted'
    db.execute.assert_not_called()
    db.commit.assert_not_called()


@pytest.mark.asyncio
async def test_retiring_onto_the_cost_list_is_refused_before_the_customer_move() -> None:
    """`replacement` never passes through `update_customer`, so this is the only place the rule
    can fire for it — and it must fire before the `UPDATE`, the first statement issued."""
    db = AsyncMock()

    with pytest.raises(HTTPException) as refused:
        await delete_price_list(
            db, PriceList(price_list_id=RETIRED), replacement_id=COST_PRICE_LIST_ID
        )

    assert refused.value.status_code == 400
    assert refused.value.detail == COST_LIST_NOT_ASSIGNABLE
    db.execute.assert_not_called()
