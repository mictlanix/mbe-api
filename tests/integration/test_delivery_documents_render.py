"""The delivery documents, rendered for real (spec 026).

Each test builds a delivery order (or itinerary) with `delivery_seed`, runs the context and the
renderer on the test's own session, and reads the PDF text back. Using the test's session is what
lets the read-only assertion mean something: any entity a context touched would show up in
`db.dirty`.
"""

import io
from datetime import datetime

from pypdf import PdfReader
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app import rendering
from app.enums import DeliveryOrderStatus, FulfillmentType, PaymentMethod, PaymentTerms
from app.models.logistics import DeliveryOrder, DeliveryOrderDetail
from app.models.sales import CustomerPayment
from app.rendering import formatting
from app.services import delivery_order_service, print_contexts
from tests.integration import delivery_seed as seed

PAST = datetime(2026, 1, 1)
LINK = 'https://maps.example.com/obra-norte'


def _flat(text: str) -> str:
    return ' '.join(text.split())


async def _counts(db: AsyncSession) -> tuple[int, int, int]:
    counts = []
    for model in (DeliveryOrder, DeliveryOrderDetail, CustomerPayment):
        counts.append((await db.execute(select(func.count()).select_from(model))).scalar_one())
    return tuple(counts)


def _read_only(db: AsyncSession) -> None:
    assert not (db.dirty or db.new or db.deleted)


async def _ticket(db: AsyncSession, order_id: int, *, warehouse: int | None = None):  # noqa: ANN202
    """(pages, flat text, the template used), asserting that rendering wrote nothing."""
    before = await _counts(db)
    order = await delivery_order_service.get_order(db, order_id)
    template, context = await print_contexts.delivery_ticket_context(db, order, warehouse=warehouse)
    pdf = await rendering.render_pdf(template, context)
    _read_only(db)
    assert await _counts(db) == before
    pages = PdfReader(io.BytesIO(pdf)).pages
    return pages, _flat(' '.join(page.extract_text() for page in pages)), template


def _paths(page) -> int:  # noqa: ANN001
    """Path segments drawn on the page. WeasyPrint draws an SVG data URI (the QR) as vector paths
    in the content stream, not as an image XObject, so the QR shows up as hundreds of them, where
    the same page without one has a handful (table rules)."""
    return page.get_contents().get_data().count(b' m\n')


class _Delivery:
    """The delivery scenario of T005: a cash order and a credit order past due."""

    cash_order: int
    credit_order: int
    order: int
    contact: int
    ship_to: int


async def _delivery(db: AsyncSession, **kwargs: object) -> _Delivery:
    scenario = _Delivery()
    cement = await seed.product(db, code='P2', name='Cemento Gris')
    sand = await seed.product(db, code='P3', name='Arena Cero')
    scenario.cash_order, cash_line = await seed.sales_order_for(
        db,
        terms=PaymentTerms.IMMEDIATE,
        paid=False,
        due_date=seed.WHEN,
        price='2000',
        quantity='1',
        comment='Descargar atras',
        product=cement,
        product_code='P2',
        product_name='Cemento Gris',
    )
    scenario.credit_order, credit_line = await seed.sales_order_for(
        db, terms=PaymentTerms.NET_D, paid=False, due_date=PAST
    )
    await seed.payment_on_delivery(db, scenario.cash_order, '1500.00', PaymentMethod.CASH)
    scenario.contact = await seed.contact(db, mobile='5512345678')
    scenario.ship_to = await seed.address(db, url_address=LINK, nickname='Obra Norte')
    options = dict(
        fulfillment_type=FulfillmentType.DELIVERY,
        status=DeliveryOrderStatus.APPROVED,
        ship_to=scenario.ship_to,
        contact=scenario.contact,
        comment='Llamar al llegar',
        lines=[
            (cement, '5', 1, cash_line),
            (1, '3', 1, credit_line),
            (sand, '0', 1, credit_line),
        ],
    )
    options.update(kwargs)
    scenario.order = await seed.delivery_order(db, **options)
    return scenario


async def test_the_delivery_ticket_carries_every_element(db: AsyncSession, seeded: None) -> None:
    s = await _delivery(db)

    pages, text, template = await _ticket(db, s.order)

    assert template == 'delivery_ticket.html'
    assert len(pages) == 1
    assert 'Mictlanix SA de CV' in text and 'BBB020202BB1' in text
    assert 'Orden de Entrega' in text
    assert formatting.pad8(s.order) in text
    assert 'Cliente Uno' in text
    assert '2026-08-01' in text
    assert f'Pedido {s.cash_order}: Contado, No Pagado' in text
    assert f'Pedido {s.credit_order}: Crédito, Vencido' in text
    assert 'Pedro Residente' in text and '5512345678' in text
    for line in ('Calle Obra 45', 'Colonia Obra, Benito Juárez', 'CDMX, MX, 06000'):
        assert line in text
    assert 'Llamar al llegar' in text
    assert 'Cemento Gris' in text and 'Producto Uno' in text
    assert 'Descargar atras' in text
    assert 'Pieza' in text
    assert 'Pagos Pendientes' in text
    assert f'{s.cash_order} $1,500.00 Efectivo' in text
    assert 'Importe por cobrar' in text and '$820.00' in text
    assert 'Obra Norte' in text and LINK in text
    assert _paths(pages[0]) > 100  # the QR
    assert 'Persona que recibe' in text and 'Nombre y firma' in text
    assert 'Arena Cero' not in text  # the line at quantity 0


async def test_a_pickup_prints_the_counter_ticket(db: AsyncSession, seeded: None) -> None:
    pablo = await seed.employee(db, first_name='Pablo', last_name='Mostrador')
    so, line = await seed.sales_order_for(
        db,
        terms=PaymentTerms.IMMEDIATE,
        paid=False,
        due_date=seed.WHEN,
        customer_name='Obras Garza',
    )
    await seed.payment_on_delivery(db, so, '300.00', PaymentMethod.CASH)
    sand = await seed.product(db, code='P3', name='Arena Cero')
    order = await seed.delivery_order(
        db,
        fulfillment_type=FulfillmentType.PICKUP,
        status=DeliveryOrderStatus.PICKED_UP,
        lines=[(1, '3', 1, line), (sand, '0', 1, line)],
        serial=7,
    )
    await seed.pickup_event(db, order, pablo)

    pages, text, template = await _ticket(db, order)

    assert template == 'pickup_ticket.html'
    assert 'Entrega en mostrador' in text
    assert 'Obras Garza' in text
    assert 'Entregó' in text and 'Pablo Mostrador' in text and 'Ana Ruiz' not in text
    assert 'Efectivo' in text and '$300.00' in text
    assert formatting.pad8(order) in text
    assert formatting.pad8(7) in text  # the folio
    assert 'Arena Cero' in text  # the pickup ticket keeps lines at quantity 0
    assert f'{so}' in text
    assert pages[0].get_contents().get_data().count(b' re') > 10  # the barcode bars


async def test_a_pickup_not_yet_collected_prints_the_updater(
    db: AsyncSession, seeded: None
) -> None:
    so, line = await seed.sales_order_for(
        db, terms=PaymentTerms.IMMEDIATE, paid=True, due_date=seed.WHEN
    )
    order = await seed.delivery_order(
        db,
        fulfillment_type=FulfillmentType.PICKUP,
        status=DeliveryOrderStatus.READY_FOR_PICKUP,
        lines=[(1, '3', 1, line)],
    )

    _, text, _ = await _ticket(db, order)

    assert 'Entregó' in text and 'Ana Ruiz' in text


async def test_without_a_ship_to_or_a_contact_neither_block_prints(
    db: AsyncSession, seeded: None
) -> None:
    s = await _delivery(db, ship_to=None, contact=None)

    pages, text, _ = await _ticket(db, s.order)

    assert 'Contacto' not in text and 'Pedro Residente' not in text
    assert 'Calle Obra' not in text and LINK not in text
    assert _paths(pages[0]) < 50  # no QR


async def test_the_warehouse_filter_keeps_only_that_warehouses_lines(
    db: AsyncSession, seeded: None
) -> None:
    other = await seed.warehouse(db)
    grava = await seed.product(db, code='P4', name='Grava Fina')
    so, line = await seed.sales_order_for(
        db, terms=PaymentTerms.IMMEDIATE, paid=False, due_date=seed.WHEN, price='2000', quantity='1'
    )
    await seed.payment_on_delivery(db, so, '1500.00', PaymentMethod.CASH)
    order = await seed.delivery_order(
        db,
        fulfillment_type=FulfillmentType.DELIVERY,
        status=DeliveryOrderStatus.APPROVED,
        lines=[(1, '3', 1, line), (grava, '2', other, line)],
    )

    _, everything, _ = await _ticket(db, order)
    _, filtered, _ = await _ticket(db, order, warehouse=other)

    assert 'Producto Uno' in everything and 'Grava Fina' in everything
    assert 'Grava Fina' in filtered and 'Producto Uno' not in filtered
    assert 'Pagos Pendientes' in filtered and '$1,500.00' in filtered
    assert 'Importe por cobrar' in filtered


async def test_a_cancelled_order_is_stamped(db: AsyncSession, seeded: None) -> None:
    for fulfillment in (FulfillmentType.DELIVERY, FulfillmentType.PICKUP):
        _, line = await seed.sales_order_for(
            db, terms=PaymentTerms.IMMEDIATE, paid=True, due_date=seed.WHEN
        )
        order = await seed.delivery_order(
            db,
            fulfillment_type=fulfillment,
            status=DeliveryOrderStatus.CANCELLED,
            lines=[(1, '3', 1, line)],
        )

        _, text, _ = await _ticket(db, order)

        assert 'CANCELADA' in text


async def test_a_long_ticket_is_still_one_page(db: AsyncSession, seeded: None) -> None:
    """SC-005, ticket half: the cut at #ticket-end holds for 34 lines."""
    _, detail = await seed.sales_order_for(
        db, terms=PaymentTerms.IMMEDIATE, paid=True, due_date=seed.WHEN
    )
    products = [
        await seed.product(db, code=f'L{n:02d}', name=f'Material{n:02d}') for n in range(34)
    ]
    order = await seed.delivery_order(
        db,
        fulfillment_type=FulfillmentType.DELIVERY,
        status=DeliveryOrderStatus.APPROVED,
        lines=[(product, '1', 1, detail) for product in products],
    )

    pages, text, _ = await _ticket(db, order)

    assert len(pages) == 1
    assert 'Material00' in text and 'Material33' in text
    assert 'Nombre y firma' in text


async def test_the_route_serves_the_ticket(client, db: AsyncSession, seeded: None) -> None:  # noqa: ANN001
    s = await _delivery(db)

    response = await client.get(f'/api/v1/delivery-orders/{s.order}/ticket')

    assert response.status_code == 200, response.text
    assert response.headers['content-disposition'] == (
        f'inline; filename="entrega-{s.order:08d}.pdf"'
    )
    assert response.content.startswith(b'%PDF')
