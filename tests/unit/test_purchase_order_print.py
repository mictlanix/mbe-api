"""The purchase order's printed totals and its email (spec 031, US5)."""

from decimal import Decimal
from types import SimpleNamespace

from app.services.purchase_order_service import order_email_message, order_totals


def _line(quantity: str, price: str, tax_rate: str = '0.16') -> SimpleNamespace:
    return SimpleNamespace(
        quantity=Decimal(quantity),
        price=Decimal(price),
        discount=Decimal(0),
        tax_rate=Decimal(tax_rate),
        # Ignored: prices are before tax whatever a legacy line says (clarification Q1).
        tax_included=True,
    )


def test_tax_is_added_at_each_lines_rate():
    assert order_totals([_line('2', '100'), _line('1', '50', '0')]) == (
        Decimal('250.00'),
        Decimal('32.00'),
        Decimal('282.00'),
    )


def test_an_empty_order_totals_zero():
    assert order_totals([]) == (Decimal('0.00'), Decimal('0.00'), Decimal('0.00'))


def test_the_email_carries_the_pdf_and_the_optional_message():
    message = order_email_message(
        order_id=42,
        company='Matriz',
        sender='Ana Ruiz',
        to=['compras@proveedor.mx'],
        note='Favor de confirmar la fecha de entrega.',
        pdf=b'%PDF',
    )
    assert message.to == ['compras@proveedor.mx']
    assert message.subject == 'Matriz Orden de compra 42'
    assert message.body == (
        'Le enviamos la orden de compra 42.\n'
        'Favor de confirmar la fecha de entrega.\n'
        'Atte: Ana Ruiz.'
    )
    (attachment,) = message.attachments
    assert (attachment.filename, attachment.content_type, attachment.data) == (
        'orden-compra-00000042.pdf',
        'application/pdf',
        b'%PDF',
    )


def test_without_a_message_the_body_is_the_standard_one():
    message = order_email_message(
        order_id=7, company='Matriz', sender='Ana Ruiz', to=['a@b.mx'], note=None, pdf=b''
    )
    assert message.body == 'Le enviamos la orden de compra 7.\nAtte: Ana Ruiz.'
