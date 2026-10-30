"""The purchase order's PDF and its email (spec 031, US5), rendered and sent for real against a
recording mailer."""

import io

import pytest
from fastapi import HTTPException
from pypdf import PdfReader
from sqlalchemy import select

from app.enums import SourceType
from app.main import app as fastapi_app
from app.models.incidence import Incidence
from app.services import mail
from app.services import purchase_order_service as service
from app.services.mail import MailError
from tests.integration import inventory_seed as seed
from tests.problem import assert_problem

URL = '/api/v1/purchase-orders'


class RecordingMailer:
    """Keeps messages instead of sending them, or raises the error it was given."""

    def __init__(self, error: Exception | None = None) -> None:
        self.sent: list = []
        self.error = error

    async def send(self, message) -> None:  # noqa: ANN001
        if self.error is not None:
            raise self.error
        self.sent.append(message)


async def _confirmed(client, db) -> int:  # noqa: ANN001
    """A confirmed PO with a line to facility 2's warehouse first, then facility 1's."""
    setup = await seed.setup_inventory(db)
    vendor = await seed.supplier(db, code='PRV', name='Proveedora del Centro')
    order, _ = await seed.purchase_order(
        db,
        lines=[(setup.non_stockable, '2', setup.b.warehouse), (1, '3', 1)],
        completed=False,
        supplier=vendor,
        price='100',
    )
    response = await client.put(f'{URL}/{order}', json={'comment': 'Entregar por la mañana'})
    assert response.status_code == 200, response.text
    response = await client.post(f'{URL}/{order}/confirm')
    assert response.status_code == 200, response.text
    return order


def _text(content: bytes) -> str:
    pages = PdfReader(io.BytesIO(content)).pages
    for page in pages:
        assert float(page.mediabox.width) == pytest.approx(612, abs=1)  # Letter
    return ' '.join(' '.join(page.extract_text() for page in pages).split())


async def _incidences(db, order: int) -> list[Incidence]:
    query = select(Incidence).where(
        Incidence.source == int(SourceType.PURCHASE_ORDER), Incidence.instance_id == order
    )
    return list((await db.execute(query)).scalars())


@pytest.mark.usefixtures('seeded')
async def test_the_pdf_shows_supplier_lines_warehouses_and_totals(client, db) -> None:
    order = await _confirmed(client, db)
    response = await client.get(f'{URL}/{order}/document')
    assert response.status_code == 200, response.text
    assert response.headers['content-type'] == 'application/pdf'
    text = _text(response.content)

    # The header is the facility of the lowest line's warehouse: facility 2, "Sucursal".
    assert 'SUCURSAL' in text.upper()
    assert f'{order:08d}' in text
    for expected in (
        'Proveedora del Centro',
        'PRV',
        'SRV',
        'Producto SRV',
        'W2',
        'P1',
        'W1',
        'Entregar por la mañana',
        'Ana Ruiz',
    ):
        assert expected in text, expected
    # 2 × 100 + 3 × 100 = 500, IVA 80, total 580.
    for figure in ('500.00', '80.00', '580.00'):
        assert figure in text, figure


@pytest.mark.usefixtures('seeded')
async def test_a_draft_or_cancelled_po_is_not_printed_or_emailed(client, db) -> None:
    vendor = await seed.supplier(db)
    draft, _ = await seed.purchase_order(db, lines=[(1, '1', 1)], completed=False, supplier=vendor)
    gone, _ = await seed.purchase_order(
        db, lines=[(1, '1', 1)], completed=False, cancelled=True, supplier=vendor
    )
    for order in (draft, gone):
        response = await client.get(f'{URL}/{order}/document')
        body = assert_problem(response, 409, 'purchase_order.not_confirmed')
        assert body['params'] == {'purchase_order_id': order}
    mailer = RecordingMailer()
    fastapi_app.dependency_overrides[mail.get_mailer] = lambda: mailer
    for order in (draft, gone):
        response = await client.post(f'{URL}/{order}/email', json={'to': ['a@b.mx']})
        assert_problem(response, 409, 'purchase_order.not_confirmed')
    assert mailer.sent == []


@pytest.mark.usefixtures('seeded')
async def test_email_sends_the_pdf_and_records_the_send(client, db) -> None:
    order = await _confirmed(client, db)
    mailer = RecordingMailer()
    fastapi_app.dependency_overrides[mail.get_mailer] = lambda: mailer

    response = await client.post(
        f'{URL}/{order}/email',
        json={'to': ['compras@proveedor.mx', 'COMPRAS@proveedor.mx'], 'message': 'Urgente'},
    )
    assert response.status_code == 200, response.text
    assert response.json()['recipients'] == ['compras@proveedor.mx']
    (message,) = mailer.sent
    assert message.subject == f'Sucursal Orden de compra {order}'
    assert 'Urgente' in message.body
    (attachment,) = message.attachments
    assert attachment.filename == f'orden-compra-{order:08d}.pdf'
    assert attachment.data.startswith(b'%PDF')

    (incidence,) = await _incidences(db, order)
    assert (incidence.comment, incidence.content, incidence.updater) == (
        'Purchase order emailed',
        'To: compras@proveedor.mx',
        1,
    )


@pytest.mark.usefixtures('seeded')
async def test_a_refused_send_is_502_and_not_recorded(client, db, current_user) -> None:
    order = await _confirmed(client, db)
    mailer = RecordingMailer(error=MailError('550 mailbox unavailable'))
    with pytest.raises(HTTPException) as caught:
        await service.email_order(
            db,
            await service.get_order(db, order),
            ['a@b.mx'],
            None,
            mailer=mailer,
            current=current_user,
        )
    assert caught.value.status_code == 502
    assert caught.value.code == 'email.send_failed'
    assert await _incidences(db, order) == []


@pytest.mark.usefixtures('seeded')
async def test_mail_not_configured_is_503(client, db) -> None:
    order = await _confirmed(client, db)
    fastapi_app.dependency_overrides[mail.get_mailer] = lambda: None
    response = await client.post(f'{URL}/{order}/email', json={'to': ['a@b.mx']})
    assert_problem(response, 503, 'email.not_configured')
    assert await _incidences(db, order) == []
