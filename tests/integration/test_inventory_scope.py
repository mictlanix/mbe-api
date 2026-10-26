"""Who sees which receipts, issues and transfers, and who may act on them (spec 029, US5, T024).

The caller is a non-administrator ('tester', employee 1) at point of sale 1, whose warehouse is 1
in facility 1. Employee 2 wrote everything else. Documents are inserted directly so each test
controls creator, warehouses and state; the routes are called over HTTP so the privilege checks run.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

import pytest

from app.core.deps import CurrentUser
from app.enums import AccessRight, EntityStatus
from app.models.core import Employee
from app.models.user import AccessPrivilege
from app.services import inventory_documents as inv
from tests.integration import inventory_seed as seed

WHEN = datetime(2026, 10, 1, 10, 0)
READ = int(AccessRight.READ)
UPDATE = int(AccessRight.UPDATE)
DELETE = int(AccessRight.DELETE)

KINDS = [
    pytest.param(inv.RECEIPT, '/api/v1/inventory-receipts', 'confirm', id='receipt'),
    pytest.param(inv.ISSUE, '/api/v1/inventory-issues', 'confirm', id='issue'),
    pytest.param(inv.TRANSFER, '/api/v1/inventory-transfers', 'dispatch', id='transfer'),
]


@pytest.fixture
def current_user() -> CurrentUser:
    return CurrentUser(
        user_id='tester',
        session_version=1,
        administrator=False,
        facility_id=1,
        employee_id=1,
        point_sale_id=1,
        cash_drawer_id=1,
    )


@dataclass
class World:
    a: int  # facility 1's warehouse, and the point of sale's
    b: int  # facility 2's warehouse
    c: int  # a second warehouse of facility 1


@pytest.fixture
async def world(db, seeded) -> World:
    setup = await seed.setup_inventory(db)
    db.add(
        Employee(
            employee_id=2,
            first_name='Bea',
            last_name='Soto',
            nickname='bea',
            gender=2,
            birthday=datetime(1991, 1, 1).date(),
            sales_person=False,
            status=EntityStatus.ACTIVE,
            start_job_date=datetime(2021, 1, 1).date(),
        )
    )
    await db.commit()
    return World(a=1, b=setup.b.warehouse, c=await seed.warehouse(db, facility=1, code='W3'))


async def grant(db, kind: inv.Kind, mask: int) -> None:
    db.add(
        AccessPrivilege(user_id='tester', system_object=int(kind.system_object), privileges=mask)
    )
    await db.commit()


async def make(
    db,
    kind: inv.Kind,
    *,
    creator: int,
    warehouse: int,
    to: int | None = None,
    state: str = 'draft',
    facility: int = 1,
) -> int:
    """One document; `state` is draft, completed, cancelled, in_transit or received."""
    values: dict[str, Any] = {}
    if kind.transit:
        values['warehouse_to'] = to
        values['in_transit'] = state == 'in_transit'
    document = kind.header(
        facility=facility,
        warehouse=warehouse,
        creator=creator,
        updater=creator,
        creation_time=WHEN,
        modification_time=WHEN,
        completed=state in ('completed', 'in_transit', 'received'),
        cancelled=state == 'cancelled',
        **values,
    )
    db.add(document)
    await db.commit()
    return getattr(document, kind.id_attr)


async def listed(client, kind: inv.Kind, url: str, **params) -> list[int]:
    response = await client.get(url, params={'limit': 100, **params})
    assert response.status_code == 200, response.text
    return [item[kind.id_attr] for item in response.json()['items']]


async def mixed(db, kind: inv.Kind, w: World) -> dict[str, int]:
    """Documents of every relation to the caller: the point of sale's, their own, someone else's."""
    destination = {'to': w.c} if kind.transit else {}
    docs = {
        'pos': await make(
            db, kind, creator=2, warehouse=w.a, **({'to': w.c} if kind.transit else {})
        ),
        'own': await make(
            db, kind, creator=1, warehouse=w.c, **({'to': w.b} if kind.transit else {})
        ),
        'other': await make(db, kind, creator=2, warehouse=w.b, **destination),
    }
    if kind.transit:
        # The point of sale's warehouse is the destination: it still counts.
        docs['pos_destination'] = await make(db, kind, creator=2, warehouse=w.b, to=w.a)
    return docs


# ── Default scope ─────────────────────────────────────────────────────────────


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_default_scope_is_the_pos_warehouse_plus_own(client, db, world, kind, url, step):
    await grant(db, kind, READ)
    docs = await mixed(db, kind, world)

    ids = set(await listed(client, kind, url))

    expected = {docs['pos'], docs['own']} | ({docs['pos_destination']} if kind.transit else set())
    assert ids == expected
    assert docs['other'] not in ids


async def test_transfer_at_the_pos_warehouse_as_destination_is_listed(client, db, world):
    kind, url = inv.TRANSFER, '/api/v1/inventory-transfers'
    await grant(db, kind, READ)
    arriving = await make(db, kind, creator=2, warehouse=world.b, to=world.a)
    leaving = await make(db, kind, creator=2, warehouse=world.a, to=world.b)
    elsewhere = await make(db, kind, creator=2, warehouse=world.b, to=world.c)

    ids = await listed(client, kind, url)

    assert sorted(ids) == [arriving, leaving]
    assert elsewhere not in ids


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_without_a_point_of_sale_only_own_documents(
    client, db, world, current_user, kind, url, step
):
    await grant(db, kind, READ)
    docs = await mixed(db, kind, world)
    current_user.point_sale_id = None

    assert await listed(client, kind, url) == [docs['own']]


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_warehouse_filter_narrows_the_default_scope_and_never_widens_it(
    client, db, world, kind, url, step
):
    await grant(db, kind, READ)
    docs = await mixed(db, kind, world)

    # world.b holds only documents of others (and, for a transfer, the one arriving at the pos
    # and the own one sent there); the filter cannot reach the out-of-scope one.
    in_b = set(await listed(client, kind, url, warehouse=world.b))
    assert docs['other'] not in in_b
    assert in_b == ({docs['own'], docs['pos_destination']} if kind.transit else set())

    assert await listed(client, kind, url, warehouse=world.a) == sorted(
        [docs['pos']] + ([docs['pos_destination']] if kind.transit else []), reverse=True
    )
    assert await listed(client, kind, url, warehouse=world.c) == sorted(
        [docs['own']] + ([docs['pos']] if kind.transit else []), reverse=True
    )


# ── Wider view ────────────────────────────────────────────────────────────────


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_delete_right_on_the_object_sees_everything(client, db, world, kind, url, step):
    await grant(db, kind, READ | DELETE)
    docs = await mixed(db, kind, world)

    assert set(await listed(client, kind, url)) == set(docs.values())


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_delete_right_on_another_object_does_not_widen(client, db, world, kind, url, step):
    await grant(db, kind, READ)
    other = next(k for k in (inv.RECEIPT, inv.ISSUE, inv.TRANSFER) if k is not kind)
    await grant(db, other, DELETE)
    docs = await mixed(db, kind, world)

    assert docs['other'] not in await listed(client, kind, url)


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_administrator_sees_everything_and_filters_by_warehouse(
    client, db, world, current_user, kind, url, step
):
    current_user.administrator = True
    current_user.point_sale_id = None
    docs = await mixed(db, kind, world)

    assert set(await listed(client, kind, url)) == set(docs.values())

    expected_b = {docs['other']} | (
        {docs['own'], docs['pos_destination']} if kind.transit else set()
    )
    assert set(await listed(client, kind, url, warehouse=world.b)) == expected_b


async def test_transfer_warehouse_filter_matches_origin_or_destination(
    client, db, world, current_user
):
    kind, url = inv.TRANSFER, '/api/v1/inventory-transfers'
    current_user.administrator = True
    leaving = await make(db, kind, creator=2, warehouse=world.b, to=world.c)

    assert await listed(client, kind, url, warehouse=world.b) == [leaving]
    assert await listed(client, kind, url, warehouse=world.c) == [leaving]
    assert await listed(client, kind, url, warehouse=world.a) == []


# ── Filters and order ─────────────────────────────────────────────────────────


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_newest_first_and_total_counts_the_scope(client, db, world, kind, url, step):
    await grant(db, kind, READ)
    to = {'to': world.c} if kind.transit else {}
    first = await make(db, kind, creator=1, warehouse=world.a, **to)
    second = await make(db, kind, creator=2, warehouse=world.a, **to)
    third = await make(db, kind, creator=1, warehouse=world.a, **to)
    await make(db, kind, creator=2, warehouse=world.b, **({'to': world.c} if kind.transit else {}))

    response = await client.get(url, params={'limit': 2})
    body = response.json()
    assert [i[kind.id_attr] for i in body['items']] == [third, second]
    assert body['total'] == 3

    response = await client.get(url, params={'skip': 2})
    assert [i[kind.id_attr] for i in response.json()['items']] == [first]


@pytest.mark.parametrize(
    ('kind', 'url', 'values'),
    [
        pytest.param(
            inv.RECEIPT,
            '/api/v1/inventory-receipts',
            ['draft', 'completed', 'cancelled'],
            id='receipt',
        ),
        pytest.param(
            inv.ISSUE, '/api/v1/inventory-issues', ['draft', 'completed', 'cancelled'], id='issue'
        ),
        pytest.param(
            inv.TRANSFER,
            '/api/v1/inventory-transfers',
            ['draft', 'in_transit', 'received', 'cancelled'],
            id='transfer',
        ),
    ],
)
async def test_status_filter_returns_only_that_state(client, db, world, kind, url, values):
    await grant(db, kind, READ)
    to = {'to': world.c} if kind.transit else {}
    by_state = {
        value: await make(db, kind, creator=1, warehouse=world.a, state=value, **to)
        for value in values
    }

    for value, document in by_state.items():
        assert await listed(client, kind, url, status=value) == [document]


@pytest.mark.parametrize(
    ('kind', 'url', 'refused'),
    [
        pytest.param(
            inv.RECEIPT, '/api/v1/inventory-receipts', ['paid', 'received', 'x'], id='receipt'
        ),
        pytest.param(
            inv.ISSUE, '/api/v1/inventory-issues', ['paid', 'in_transit', 'x'], id='issue'
        ),
        pytest.param(
            inv.TRANSFER, '/api/v1/inventory-transfers', ['paid', 'completed', 'x'], id='transfer'
        ),
    ],
)
async def test_status_values_of_another_document_are_422(client, db, world, kind, url, refused):
    await grant(db, kind, READ)

    for value in refused:
        response = await client.get(url, params={'status': value})
        assert response.status_code == 422, (value, response.text)


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_status_filter_stays_inside_the_scope(client, db, world, kind, url, step):
    await grant(db, kind, READ)
    to = {'to': world.c} if kind.transit else {}
    mine = await make(db, kind, creator=1, warehouse=world.a, **to)
    await make(db, kind, creator=2, warehouse=world.b, **to)

    assert await listed(client, kind, url, status='draft') == [mine]


# ── Facility rule (FR-011, R6) ────────────────────────────────────────────────

MESSAGE = 'Document belongs to another facility'


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_confirm_by_a_non_administrator_of_another_facility_is_403(
    client, db, world, current_user, kind, url, step
):
    await grant(db, kind, READ | UPDATE | DELETE)
    current_user.facility_id = 2
    to = {'to': world.b} if kind.transit else {}
    document = await make(db, kind, creator=2, warehouse=world.a, **to)

    response = await client.post(f'{url}/{document}/{step}')

    assert response.status_code == 403
    assert response.json()['detail'] == MESSAGE


@pytest.mark.parametrize('state', ['draft', 'completed'])
@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_cancel_by_a_non_administrator_of_another_facility_is_403(
    client, db, world, current_user, kind, url, step, state
):
    await grant(db, kind, READ | UPDATE | DELETE)
    current_user.facility_id = 2
    to = {'to': world.b} if kind.transit else {}
    document = await make(db, kind, creator=2, warehouse=world.a, state=state, **to)

    response = await client.post(f'{url}/{document}/cancel')

    assert response.status_code == 403
    assert response.json()['detail'] == MESSAGE


@pytest.mark.parametrize(('kind', 'url', 'step'), KINDS)
async def test_the_facility_rule_does_not_bind_the_own_facility_or_an_administrator(
    client, db, world, current_user, kind, url, step
):
    await grant(db, kind, READ | UPDATE | DELETE)
    to = {'to': world.b} if kind.transit else {}
    mine = await make(db, kind, creator=2, warehouse=world.a, **to)
    response = await client.post(f'{url}/{mine}/cancel')
    assert response.status_code == 200, response.text

    current_user.administrator = True
    current_user.facility_id = 2
    theirs = await make(db, kind, creator=2, warehouse=world.a, **to)
    response = await client.post(f'{url}/{theirs}/cancel')
    assert response.status_code == 200, response.text


async def test_transfer_receive_follows_the_destination_and_close_either_end(
    client, db, world, current_user
):
    kind, url = inv.TRANSFER, '/api/v1/inventory-transfers'
    await grant(db, kind, READ | UPDATE | DELETE)

    def in_transit() -> Any:
        return make(
            db, kind, creator=2, warehouse=world.a, to=world.b, state='in_transit', facility=1
        )

    # Origin facility: may close, may not receive (the destination is facility 2).
    current_user.facility_id = 1
    transfer = await in_transit()
    response = await client.post(f'{url}/{transfer}/receive')
    assert (response.status_code, response.json()['detail']) == (403, MESSAGE)

    # A third facility: neither.
    current_user.facility_id = 99
    for step in ('receive', 'close'):
        response = await client.post(f'{url}/{transfer}/{step}')
        assert (response.status_code, response.json()['detail']) == (403, MESSAGE), step

    # The destination facility may receive; the origin may close.
    current_user.facility_id = 2
    response = await client.post(f'{url}/{transfer}/receive')
    assert response.status_code == 200, response.text
    current_user.facility_id = 1
    other = await in_transit()
    response = await client.post(f'{url}/{other}/close')
    assert response.status_code == 200, response.text
