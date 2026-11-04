"""Purchase requests from draft to approved, over their routes (spec 031, US1).

The caller is an administrator of facility 1 whose point of sale stocks from warehouse 1. The
facility rule is checked through the service with a non-administrator. SQLite cannot show the
concurrent submits of SC-002; quickstart step 3 runs them on the test deployment.
"""

import pytest
from fastapi import HTTPException
from sqlalchemy import select

from app.core.config import settings
from app.core.deps import CurrentUser
from app.enums import SourceType
from app.models.incidence import Incidence
from app.models.purchases import PurchaseRequest, PurchaseRequestDetail
from app.schemas.purchases import PurchaseRequestUpdate, RejectRequest
from app.services import purchase_request_service as service
from tests.integration import inventory_seed as seed
from tests.problem import assert_problem

URL = '/api/v1/purchase-requests'


async def _draft(client, warehouse: int | None = None) -> int:
    body = {} if warehouse is None else {'warehouse': warehouse}
    response = await client.post(URL, json=body)
    assert response.status_code == 201, response.text
    return response.json()['purchase_request_id']


async def _add(client, request: int, product: int, **fields):
    return await client.post(f'{URL}/{request}/lines', json={'product': product, **fields})


async def _submitted(client, product: int, warehouse: int | None = None) -> int:
    request = await _draft(client, warehouse)
    assert (await _add(client, request, product, quantity='2')).status_code == 200
    response = await client.post(f'{URL}/{request}/submit')
    assert response.status_code == 200, response.text
    return request


def _clerk(*, facility: int) -> CurrentUser:
    return CurrentUser(
        user_id='clerk', session_version=1, administrator=False, facility_id=facility, employee_id=1
    )


# ── Drafts and lines ──────────────────────────────────────────────────────────


async def test_a_draft_defaults_to_the_point_of_sale_warehouse(client, db, seeded):
    response = await client.post(URL, json={'comment': 'Para la semana'})
    assert response.status_code == 201, response.text
    body = response.json()
    assert (body['warehouse'], body['facility'], body['status'], body['serial']) == (
        1,
        1,
        'draft',
        None,
    )
    assert (body['line_count'], body['lines'], body['approver']) == (0, [], None)


async def test_a_new_line_takes_its_defaults_and_writes_the_dead_columns(client, db, seeded):
    setup = await seed.setup_inventory(db)
    boxed = await seed.product(db, code='BOX', min_order_qty=6)
    loose = await seed.product(db, code='LOOSE', min_order_qty=0)
    request = await _draft(client)

    response = await _add(client, request, boxed)
    assert response.status_code == 200, response.text
    line = response.json()['lines'][0]
    assert (line['quantity'], line['warehouse'], line['product_name']) == (
        '6.00',
        1,
        'Producto BOX',
    )
    assert (line['to_purchase'], line['min_order_qty'], line['purchase_order']) == (True, 6, None)
    assert response.json()['warnings'] == []

    response = await _add(client, request, loose, warehouse=setup.b.warehouse, product_name='Otro')
    line = response.json()['lines'][1]
    assert (line['quantity'], line['warehouse'], line['product_name']) == (
        '1.00',
        setup.b.warehouse,
        'Otro',
    )

    rows = (
        (
            await db.execute(
                select(PurchaseRequestDetail).where(
                    PurchaseRequestDetail.purchase_request == request
                )
            )
        )
        .scalars()
        .all()
    )
    for row in rows:
        assert (row.to_purchase, row.accepted, row.customer) == (
            True,
            False,
            settings.default_customer_id,
        )


async def test_below_the_minimum_is_saved_with_a_warning(client, db, seeded):
    boxed = await seed.product(db, code='BOX', min_order_qty=6)
    request = await _draft(client)
    response = await _add(client, request, boxed, quantity='2')
    assert response.status_code == 200
    assert response.json()['warnings'] == [
        'BOX: quantity 2 is below the minimum order quantity of 6'
    ]
    line_id = response.json()['lines'][0]['purchase_request_detail_id']
    response = await client.put(f'{URL}/{request}/lines/{line_id}', json={'quantity': '3'})
    assert response.json()['warnings'] == [
        'BOX: quantity 3 is below the minimum order quantity of 6'
    ]
    response = await client.put(f'{URL}/{request}/lines/{line_id}', json={'quantity': '6'})
    assert response.json()['warnings'] == []


async def test_line_refusals(client, db, seeded):
    setup = await seed.setup_inventory(db)
    unbuyable = await seed.product(db, code='NOBUY', purchasable=False)
    request = await _draft(client)

    body = assert_problem(
        await _add(client, request, unbuyable), 422, 'product.not_purchasable'
    )
    assert body['params'] == {'product_id': unbuyable, 'product_code': 'NOBUY'}
    assert (await _add(client, request, 999)).status_code == 404
    assert (await _add(client, request, 1, warehouse=setup.a.transit)).status_code == 422
    assert (await _add(client, request, 1, warehouse=999)).status_code == 404
    response = await client.put(f'{URL}/{request}/lines/999', json={'quantity': '1'})
    assert response.status_code == 404
    assert response.json()['code'] == 'resource.not_found'


async def test_a_line_can_be_changed_and_removed_while_a_draft(client, db, seeded):
    setup = await seed.setup_inventory(db)
    request = await _draft(client)
    line_id = (await _add(client, request, 1)).json()['lines'][0]['purchase_request_detail_id']

    response = await client.put(
        f'{URL}/{request}/lines/{line_id}',
        json={'quantity': '4.5', 'product_name': 'Renombrado', 'warehouse': setup.b.warehouse},
    )
    assert response.status_code == 200, response.text
    line = response.json()['lines'][0]
    assert (line['quantity'], line['product_name'], line['warehouse']) == (
        '4.50',
        'Renombrado',
        setup.b.warehouse,
    )

    response = await client.delete(f'{URL}/{request}/lines/{line_id}')
    assert (response.status_code, response.json()['line_count']) == (200, 0)


# ── Submit and serial (FR-004) ────────────────────────────────────────────────


async def test_submit_numbers_from_the_facility_maximum_across_its_warehouses(client, db, seeded):
    setup = await seed.setup_inventory(db)
    second = await seed.warehouse(db, facility=1, code='W1B')
    await seed.purchase_request(db, warehouse=1, lines=[(1, '1', 1)], state='approved', serial=41)
    # Another facility's higher serial does not count.
    await seed.purchase_request(
        db, warehouse=setup.b.warehouse, lines=[(1, '1', 1)], state='approved', serial=90
    )

    first = await _submitted(client, 1)
    other = await _submitted(client, 1, warehouse=second)
    body_first = (await client.get(f'{URL}/{first}')).json()
    body_other = (await client.get(f'{URL}/{other}')).json()
    assert (body_first['serial'], body_first['status']) == (42, 'submitted')
    assert (body_other['serial'], body_other['facility']) == (43, 1)

    elsewhere = await _submitted(client, 1, warehouse=setup.b.warehouse)
    assert (await client.get(f'{URL}/{elsewhere}')).json()['serial'] == 91


async def test_a_refused_submit_takes_no_number(client, db, seeded):
    empty = await _draft(client)
    response = await client.post(f'{URL}/{empty}/submit')
    assert (response.status_code, response.json()['detail']) == (409, 'The request has no lines')
    assert_problem(response, 409, 'purchase_request.no_lines')
    assert (await db.get(PurchaseRequest, empty)).serial is None

    numbered = await _submitted(client, 1)
    assert (await client.get(f'{URL}/{numbered}')).json()['serial'] == 1


async def test_a_line_without_quantity_is_refused_on_submit(client, db, seeded):
    request, _ = await seed.purchase_request(db, warehouse=1, lines=[(1, '0', 1)])
    response = await client.post(f'{URL}/{request}/submit')
    body = assert_problem(response, 422, 'purchase_request.line_without_quantity')
    line = (
        await db.execute(select(PurchaseRequestDetail.purchase_request_detail_id))
    ).scalar_one()
    assert body['params'] == {'line_id': line}


async def test_a_submitted_request_is_read_only(client, db, seeded):
    request = await _submitted(client, 1)
    line_id = (await client.get(f'{URL}/{request}')).json()['lines'][0][
        'purchase_request_detail_id'
    ]
    response = await client.put(f'{URL}/{request}', json={'comment': 'x'})
    body = assert_problem(response, 409, 'purchase_request.not_draft')
    assert body['params'] == {'status': 'submitted'}
    assert_problem(await _add(client, request, 1), 409, 'purchase_request.not_draft')
    response = await client.put(f'{URL}/{request}/lines/{line_id}', json={'quantity': '1'})
    assert_problem(response, 409, 'purchase_request.not_draft')
    response = await client.delete(f'{URL}/{request}/lines/{line_id}')
    assert_problem(response, 409, 'purchase_request.not_draft')
    body = assert_problem(
        await client.post(f'{URL}/{request}/submit'), 409, 'purchase_request.not_draft'
    )
    assert body['params'] == {'status': 'submitted'}


# ── Approve and reject (FR-005, FR-006) ───────────────────────────────────────


async def test_approve_records_the_approver_and_time(client, db, seeded):
    request = await _submitted(client, 1)
    response = await client.post(f'{URL}/{request}/approve')
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['status'], body['approver']) == ('approved', 1)
    assert body['approval_time'] is not None

    body = assert_problem(
        await client.post(f'{URL}/{request}/approve'), 409, 'purchase_request.not_submitted'
    )
    assert body['params'] == {'status': 'approved'}
    response = await client.post(f'{URL}/{request}/reject', json={'reason': 'Tarde'})
    assert_problem(response, 409, 'purchase_request.not_submitted')


async def test_a_draft_cannot_be_approved_or_rejected(client, db, seeded):
    request = await _draft(client)
    body = assert_problem(
        await client.post(f'{URL}/{request}/approve'), 409, 'purchase_request.not_submitted'
    )
    assert body['params'] == {'status': 'draft'}
    response = await client.post(f'{URL}/{request}/reject', json={'reason': 'No'})
    assert_problem(response, 409, 'purchase_request.not_submitted')


async def test_reject_records_the_reason_and_returns_to_draft_keeping_the_serial(
    client, db, seeded
):
    request = await _submitted(client, 1)
    response = await client.post(f'{URL}/{request}/reject', json={'reason': '  Sin presupuesto '})
    assert response.status_code == 200, response.text
    body = response.json()
    assert (body['status'], body['serial'], body['approver'], body['approval_time']) == (
        'draft',
        1,
        None,
        None,
    )

    incidence = (
        await db.execute(
            select(Incidence).where(
                Incidence.source == int(SourceType.PURCHASE_REQUEST),
                Incidence.instance_id == request,
            )
        )
    ).scalar_one()
    assert (incidence.comment, incidence.updater) == ('Sin presupuesto', 1)

    # Editable again, and resubmitting keeps the serial.
    assert (await _add(client, request, 1, quantity='1')).status_code == 200
    response = await client.post(f'{URL}/{request}/submit')
    assert (response.status_code, response.json()['serial']) == (200, 1)


async def test_bulk_approve_reports_each_id(client, db, seeded):
    first = await _submitted(client, 1)
    draft = await _draft(client)
    response = await client.post(f'{URL}/approve', json={'ids': [first, draft, 999]})
    assert response.status_code == 200, response.text
    assert response.json()['results'] == [
        {
            'purchase_request_id': first,
            'approved': True,
            'reason': None,
            'code': None,
            'params': None,
        },
        {
            'purchase_request_id': draft,
            'approved': False,
            'reason': 'Only a submitted request can be approved or rejected',
            'code': 'purchase_request.not_submitted',
            'params': {'status': 'draft'},
        },
        {
            'purchase_request_id': 999,
            'approved': False,
            'reason': 'Purchase request not found',
            'code': 'resource.not_found',
            'params': {'resource': 'purchase_request', 'id': 999},
        },
    ]
    assert (await client.get(f'{URL}/{first}')).json()['status'] == 'approved'


# ── Facility (FR-002, FR-011) ─────────────────────────────────────────────────


async def test_a_numbered_request_keeps_its_facility(client, db, seeded):
    setup = await seed.setup_inventory(db)
    second = await seed.warehouse(db, facility=1, code='W1B')
    request = await _submitted(client, 1)
    await client.post(f'{URL}/{request}/reject', json={'reason': 'Corregir'})

    response = await client.put(f'{URL}/{request}', json={'warehouse': setup.b.warehouse})
    assert_problem(response, 409, 'purchase_request.facility_locked')
    response = await client.put(f'{URL}/{request}', json={'warehouse': second})
    assert (response.status_code, response.json()['warehouse']) == (200, second)

    # Before a serial, a draft may move anywhere.
    fresh = await _draft(client)
    response = await client.put(f'{URL}/{fresh}', json={'warehouse': setup.b.warehouse})
    assert (response.status_code, response.json()['facility']) == (200, setup.b.facility)


async def test_another_facility_cannot_edit_submit_or_cancel(client, db, seeded):
    setup = await seed.setup_inventory(db)
    request, _ = await seed.purchase_request(db, warehouse=1, lines=[(1, '1', 1)])
    outsider = _clerk(facility=setup.b.facility)

    for call in (
        service.update(db, request, PurchaseRequestUpdate(comment='x'), current=outsider),
        service.submit(db, request, current=outsider),
        service.cancel(db, request, current=outsider),
    ):
        with pytest.raises(HTTPException) as caught:
            await call
        assert caught.value.status_code == 403

    # Approving and rejecting are not limited by facility.
    submitted, lines = await seed.purchase_request(
        db, warehouse=1, lines=[(1, '1', 1)], state='submitted', serial=1
    )
    approved = await service.approve(db, submitted, current=outsider)
    assert approved.approved
    other, _ = await seed.purchase_request(
        db, warehouse=1, lines=[(1, '1', 1)], state='submitted', serial=2
    )
    rejected = await service.reject(db, other, RejectRequest(reason='No'), current=outsider)
    assert not rejected.completed
    marked = await service.mark(db, submitted, lines[0], to_purchase=False, current=outsider)
    assert marked is not None


# ── Marking (FR-008, FR-010) and cancel (FR-009) ──────────────────────────────


async def test_marking_needs_a_submitted_or_approved_request(client, db, seeded):
    draft, draft_lines = await seed.purchase_request(db, warehouse=1, lines=[(1, '1', 1)])
    response = await client.put(
        f'{URL}/{draft}/lines/{draft_lines[0]}/to-purchase', json={'to_purchase': False}
    )
    body = assert_problem(response, 409, 'purchase_request.lines_not_markable')
    assert body['params'] == {'status': 'draft'}

    approved, lines = await seed.purchase_request(
        db, warehouse=1, lines=[(1, '1', 1)], state='approved', serial=1
    )
    response = await client.put(
        f'{URL}/{approved}/lines/{lines[0]}/to-purchase', json={'to_purchase': False}
    )
    assert response.status_code == 200, response.text
    assert response.json()['lines'][0]['to_purchase'] is False

    # A line of another request is not found under this one.
    response = await client.put(
        f'{URL}/{approved}/lines/{draft_lines[0]}/to-purchase', json={'to_purchase': True}
    )
    assert response.status_code == 404
    assert response.json()['code'] == 'resource.not_found'


async def test_a_line_on_a_live_order_shows_it_and_cannot_be_unmarked(client, db, seeded):
    request, lines = await seed.purchase_request(
        db, warehouse=1, lines=[(1, '2', 1)], state='approved', serial=1
    )
    order, details = await seed.purchase_order(
        db, lines=[(1, '2', 1)], completed=False, request_lines=[lines[0]]
    )

    line = (await client.get(f'{URL}/{request}')).json()['lines'][0]
    shown = (line['purchase_order_detail'], line['purchase_order'], line['purchase_order_status'])
    assert shown == (details[0], order, 'draft')
    response = await client.put(
        f'{URL}/{request}/lines/{lines[0]}/to-purchase', json={'to_purchase': False}
    )
    body = assert_problem(response, 409, 'purchase_request.line_on_live_order')
    assert body['params'] == {'line_id': lines[0]}


async def test_cancel_is_refused_while_a_line_is_on_a_live_order(client, db, seeded):
    request, lines = await seed.purchase_request(
        db, warehouse=1, lines=[(1, '2', 1)], state='approved', serial=1
    )
    await seed.purchase_order(db, lines=[(1, '2', 1)], completed=True, request_lines=[lines[0]])
    body = assert_problem(
        await client.post(f'{URL}/{request}/cancel'), 409, 'purchase_request.lines_on_live_order'
    )
    assert body['params'] == {'count': 1}


async def test_cancel_is_allowed_when_the_order_was_cancelled(client, db, seeded):
    request, lines = await seed.purchase_request(
        db, warehouse=1, lines=[(1, '2', 1)], state='approved', serial=1
    )
    await seed.purchase_order(
        db, lines=[(1, '2', 1)], completed=False, cancelled=True, request_lines=[lines[0]]
    )
    response = await client.post(f'{URL}/{request}/cancel')
    assert (response.status_code, response.json()['status']) == (200, 'cancelled')
    assert_problem(
        await client.post(f'{URL}/{request}/cancel'), 409, 'purchase_request.already_cancelled'
    )
    body = assert_problem(await _add(client, request, 1), 409, 'purchase_request.not_draft')
    assert body['params'] == {'status': 'cancelled'}


# ── Lists (FR-007, FR-012) ────────────────────────────────────────────────────


async def test_lists_default_to_the_callers_live_requests(client, db, seeded):
    setup = await seed.setup_inventory(db)
    mine = await _draft(client)
    cancelled = await _draft(client)
    await client.post(f'{URL}/{cancelled}/cancel')
    other_employee = await seed.purchase_request(db, warehouse=1, lines=[], creator=1)
    # Created by someone else: the baseline has one employee, so rewrite the row's creator.
    row = await db.get(PurchaseRequest, other_employee[0])
    row.creator = row.updater = await _employee(db)
    await db.commit()

    ids = [item['purchase_request_id'] for item in (await client.get(URL)).json()['items']]
    assert ids == [mine]

    body = (await client.get(URL, params={'all': 'true'})).json()
    assert [item['purchase_request_id'] for item in body['items']] == [other_employee[0], mine]

    by_status = (await client.get(URL, params={'status': 'cancelled'})).json()
    assert [item['purchase_request_id'] for item in by_status['items']] == [cancelled]

    elsewhere = await _draft(client, setup.b.warehouse)
    by_facility = (
        await client.get(URL, params={'facility': setup.b.facility, 'all': 'true'})
    ).json()
    assert [item['purchase_request_id'] for item in by_facility['items']] == [elsewhere]
    by_warehouse = (await client.get(URL, params={'warehouse': setup.b.warehouse})).json()
    assert by_warehouse['total'] == 1
    assert (await client.get(URL, params={'id': mine})).json()['total'] == 1
    assert (await client.get(URL, params={'status': 'paid'})).status_code == 422


async def test_lists_filter_by_serial_creator_and_date(client, db, seeded):
    numbered = await _submitted(client, 1)
    await _draft(client)
    assert [
        item['purchase_request_id']
        for item in (await client.get(URL, params={'serial': 1})).json()['items']
    ] == [numbered]
    assert (await client.get(URL, params={'creator': 1})).json()['total'] == 2
    assert (await client.get(URL, params={'from': '2000-01-01', 'to': '2000-12-31'})).json()[
        'total'
    ] == 0


async def test_the_approval_queue_is_oldest_first_across_facilities(client, db, seeded):
    setup = await seed.setup_inventory(db)
    first, _ = await seed.purchase_request(
        db, warehouse=1, lines=[(1, '1', 1), (1, '2', 1)], state='submitted', serial=1
    )
    second, _ = await seed.purchase_request(
        db, warehouse=setup.b.warehouse, lines=[(1, '1', 1)], state='submitted', serial=1
    )
    await seed.purchase_request(db, warehouse=1, lines=[(1, '1', 1)], state='approved', serial=2)

    body = (await client.get(f'{URL}/approval-queue')).json()
    assert [(i['purchase_request_id'], i['line_count']) for i in body['items']] == [
        (first, 2),
        (second, 1),
    ]
    body = (await client.get(f'{URL}/approval-queue', params={'facility': setup.b.facility})).json()
    assert [i['purchase_request_id'] for i in body['items']] == [second]


async def _employee(db) -> int:
    from datetime import date

    from app.enums import EntityStatus
    from app.models.core import Employee

    row = Employee(
        first_name='Luis',
        last_name='Mora',
        nickname='luis',
        gender=1,
        birthday=date(1990, 1, 1),
        sales_person=False,
        status=EntityStatus.ACTIVE,
        start_job_date=date(2020, 1, 1),
    )
    db.add(row)
    await db.commit()
    return row.employee_id


# ── Coded errors without a route of their own ─────────────────────────────────


async def test_a_request_needs_a_warehouse_when_the_caller_has_no_point_of_sale(
    client, db, seeded, current_user
):
    from app.schemas.purchases import PurchaseRequestCreate

    nowhere = CurrentUser(**{**current_user.__dict__, 'point_sale_id': None})
    with pytest.raises(HTTPException) as caught:
        await service.create(db, PurchaseRequestCreate(), current=nowhere)
    assert caught.value.code == 'warehouse.required'
    assert caught.value.params == {'resource': 'purchase_request'}


async def test_a_facility_without_an_in_transit_location_cannot_submit(
    client, db, seeded, monkeypatch
):
    request, _ = await seed.purchase_request(db, warehouse=1, lines=[(1, '1', 1)])

    async def no_transit(_db):  # noqa: ANN001, ANN202
        return {}

    monkeypatch.setattr(service.inv, '_transit_map', no_transit)
    with pytest.raises(HTTPException) as caught:
        await service.submit(db, request, current=_clerk(facility=1))
    assert caught.value.code == 'facility.no_in_transit_location'
    assert caught.value.params == {'facility_id': 1}


async def test_a_warehouse_that_changed_facility_is_a_retry(client, db, seeded, monkeypatch):
    request, _ = await seed.purchase_request(db, warehouse=1, lines=[(1, '1', 1)])
    monkeypatch.setattr(service, '_facility_map', _no_facilities)
    with pytest.raises(HTTPException) as caught:
        await service.submit(db, request, current=_clerk(facility=1))
    assert caught.value.code == 'purchase_request.warehouse_facility_changed'
    assert caught.value.params == {}


async def _no_facilities(_db):  # noqa: ANN001, ANN202
    return {}
