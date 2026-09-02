from collections.abc import Sequence

from fastapi import HTTPException, status
from sqlalchemy import delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.constants import COST_PRICE_LIST_ID
from app.models.customer import Customer
from app.models.product import PriceList
from app.schemas.product import PriceListCreate, PriceListUpdate
from app.services.references import (
    assert_not_referenced,
    find_blocking_references,
    referencing_columns,
)

#: Fires on a single assignment (`customer_service`) and on a retirement's replacement.
COST_LIST_NOT_ASSIGNABLE = 'The cost price list cannot be assigned to a customer'

#: Used by `product_price_service`. Does not name the purchases module that will own cost writes —
#: the message has to hold both before that lands and after.
COST_LIST_READ_ONLY = 'The cost price list is read-only'


def assert_not_cost_list(price_list_id: int, *, detail: str) -> None:
    """Refuse an operation on the cost price list (#194).

    `COST_PRICE_LIST_ID` holds average cost, not a sale price, and only the cost snapshot on a
    sales-order line reads it. To every other surface it looked like Mostrador — which is how an
    active customer in mbe_dev ended up priced at cost.

    `detail` varies because the rules do: not assignable, not deletable, read-only. A client can
    only act on the refusal if it knows which one it hit.
    """
    if price_list_id == COST_PRICE_LIST_ID:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=detail)


async def list_price_lists(
    db: AsyncSession,
    *,
    search: str | None = None,
    skip: int = 0,
    limit: int = 20,
) -> tuple[Sequence[PriceList], int]:
    # Excluded here, not by the caller (#194): this fills mbe-ui's customer price-list dropdown,
    # and every list in it is one `assert_not_cost_list` would refuse on assignment.
    not_cost = PriceList.price_list_id != COST_PRICE_LIST_ID
    base = select(PriceList).where(not_cost)
    count_q = select(func.count()).select_from(PriceList).where(not_cost)

    if search:
        term = f'%{search}%'
        base = base.where(PriceList.name.ilike(term))
        count_q = count_q.where(PriceList.name.ilike(term))

    total: int = (await db.execute(count_q)).scalar_one()
    items = (await db.execute(base.offset(skip).limit(limit))).scalars().all()
    return items, total


async def get_price_list(db: AsyncSession, price_list_id: int) -> PriceList | None:
    """The lookup behind every `/price-lists/{id}` route. `None` for the cost list (#194).

    All four routes already branch on `None`, so this one line is the 404 on `GET`, `PUT`,
    `DELETE` and the delete preview, and a fifth route inherits it. 404 rather than an explanatory
    400 because `GET /price-lists` already omits the row.

    Not a general-purpose getter: `product_price_service` and the replacement check below use
    `db.get(PriceList, ...)`, which is why cost is still readable through `/product-prices` and
    why `replacement={cost}` still gets its own 400 rather than "not found".
    """
    if price_list_id == COST_PRICE_LIST_ID:
        return None
    return await db.get(PriceList, price_list_id)


async def create_price_list(db: AsyncSession, data: PriceListCreate) -> PriceList:
    # `model_dump` rather than attribute access: both margin fields are deprecated (#185) and
    # Pydantic warns on the attribute, but the write still has to honour what the client sent.
    sent = data.model_dump()
    pl = PriceList(
        name=data.name,
        high_profit_margin=sent['high_profit_margin'],
        low_profit_margin=sent['low_profit_margin'],
    )
    db.add(pl)
    await db.commit()
    await db.refresh(pl)
    return pl


async def update_price_list(db: AsyncSession, pl: PriceList, data: PriceListUpdate) -> PriceList:
    if data.name is not None:
        pl.name = data.name
    sent = data.model_dump()
    if sent['high_profit_margin'] is not None:
        pl.high_profit_margin = sent['high_profit_margin']
    if sent['low_profit_margin'] is not None:
        pl.low_profit_margin = sent['low_profit_margin']
    await db.commit()
    await db.refresh(pl)
    return pl


# The list's own contents, and the only relation a retirement may delete. A `product_price` row is
# the price of a product *in this list* — unique on `(product, list)`, unreachable and meaningless
# once the list is gone, and it records no event; `delete_product` already treats the other half of
# the same pair this way. Everything else keeps blocking, including a relation added later that
# nobody has classified yet: refusing is recoverable, deleting is not.
#
# Read in exactly two places, both below — as the blocker check's `exempt`, and as the cascade's
# filter — so a relation cannot be exempted without being swept, or swept without being exempted.
# Drift between a hand-kept list and the relations that actually exist is what #112 was.
_DELETE_CASCADE = frozenset({'product_price'})


async def preview_delete(db: AsyncSession, pl: PriceList) -> list[tuple[str, int]]:
    """Count the rows riding on the list, largest first, without touching any of them.

    Deliberately no `exempt`: the report covers *every* relation, which is the union of what the
    retirement deletes and what it moves or refuses on. That is what keeps it from describing a
    different operation than the one it precedes — there is no second list to keep in step.
    """
    return await find_blocking_references(db, pl)


async def delete_price_list(
    db: AsyncSession, pl: PriceList, *, replacement_id: int | None = None
) -> None:
    """Retire the list: move its customers onto `replacement_id`, delete its prices, delete it.

    The customer move runs *before* the blocker check, so the check passes for the ordinary reason
    — nothing references the list any more — rather than through an exemption that would depend on
    a request parameter. With no replacement named, the check runs first and refuses exactly as it
    did before, naming `customer.price_list` and its count.

    One commit, at the end. Every refusal here leaves the data untouched: the validations write
    nothing at all, and the 409 raised after the move is never committed, so closing the session
    rolls it back.

    Two refusals for the cost list (#194): deleting it takes every cost row with it, after which
    `add_line` snapshots `cost = 0` in silence; naming it as `replacement` is
    `UPDATE customer SET price_list = <cost>` over 10,782 customers. Spec 015's rule that
    `replacement=0` is not "none given" still holds — it is rejected for being the cost list, on
    `is not None` as before.
    """
    assert_not_cost_list(pl.price_list_id, detail='The cost price list cannot be deleted')
    if replacement_id is not None:
        assert_not_cost_list(replacement_id, detail=COST_LIST_NOT_ASSIGNABLE)
        if replacement_id == pl.price_list_id:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail='Cannot replace a price list with itself',
            )
        if await db.get(PriceList, replacement_id) is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail='Replacement price list not found',
            )
        await db.execute(
            update(Customer)
            .where(Customer.price_list == pl.price_list_id)
            .values(price_list=replacement_id)
        )

    await assert_not_referenced(db, pl, exempt=_DELETE_CASCADE)
    for table, column in referencing_columns(PriceList):
        if table.name in _DELETE_CASCADE:
            # Core rather than interpolated text, unlike the merge's loop: the column here is
            # named `list`, and whether a given dialect needs that quoted is not worth reasoning
            # about per backend.
            await db.execute(delete(table).where(column == pl.price_list_id))
    await db.delete(pl)
    await db.commit()
