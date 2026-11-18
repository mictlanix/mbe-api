"""Static rules of the batch and invoice request bodies (spec 027, contract)."""

from decimal import Decimal

import pytest
from pydantic import ValidationError

from app.schemas.fiscal import (
    CreditNoteCreate,
    FiscalDocumentRelationResponse,
    FiscalEmailRequest,
    InvoiceLineCopy,
    InvoiceLineUpdate,
    InvoiceRelationAdd,
    InvoiceUpdate,
    PacSimulationUpdate,
    PaidInvoiceAdd,
    PaidInvoiceUpdate,
    PaymentReceiptCreate,
    ReceiverAnswer,
    TaxpayerBatchCreate,
    relation_code,
)

_TEMPLATE = {'name': 'T02Blue'}


@pytest.mark.parametrize(
    'body',
    [
        {'batch': '', 'type': 0, 'template': _TEMPLATE},
        {'batch': '   ', 'type': 0, 'template': _TEMPLATE},
        {'batch': 'ABCDEFGHIJK', 'type': 0, 'template': _TEMPLATE},
        {'batch': 'A', 'type': 4, 'template': _TEMPLATE},
        {'batch': 'A', 'type': 0, 'template': {'name': ''}},
        {'batch': 'A', 'type': 0, 'template': {'name': 'T', 'footer_height': -1}},
    ],
)
def test_batch_rejects(body: dict) -> None:
    with pytest.raises(ValidationError):
        TaxpayerBatchCreate.model_validate(body)


def test_batch_series_is_trimmed() -> None:
    assert (
        TaxpayerBatchCreate.model_validate(
            {'batch': ' RH ', 'type': 200, 'template': _TEMPLATE}
        ).batch
        == 'RH'
    )


@pytest.mark.parametrize(
    'body',
    [
        {'quantity': '0'},
        {'quantity': '-1'},
        {'price': '-0.01'},
        {'discount': '1.01'},
        {'discount': '-0.1'},
        {'tax_rate': '0.08'},
        {'product_name': 'x' * 1001},
        {'product_code': 'x' * 36},
    ],
)
def test_line_update_rejects(body: dict) -> None:
    with pytest.raises(ValidationError):
        InvoiceLineUpdate.model_validate(body)


@pytest.mark.parametrize('rate', ['0.16', '0', '-1', '0.160000'])
def test_line_update_accepts_the_three_rates(rate: str) -> None:
    assert InvoiceLineUpdate.model_validate({'tax_rate': rate}).tax_rate == Decimal(rate)


def test_line_update_keeps_only_the_fields_sent() -> None:
    assert InvoiceLineUpdate.model_validate({'price': '10'}).model_fields_set == {'price'}


@pytest.mark.parametrize(
    'body',
    [
        {'payment_method': 0},
        {'payment_method': 1001},
        {'payment_method': 7},
        {'usage': 'G0'},
        {'reference': 'x' * 26},
        {'comment': 'x' * 1001},
        {'payment_terms': 5},
    ],
)
def test_header_update_rejects(body: dict) -> None:
    with pytest.raises(ValidationError):
        InvoiceUpdate.model_validate(body)


def test_relation_type_is_a_sat_code() -> None:
    assert InvoiceRelationAdd.model_validate({'relation': 1, 'type': '07'}).type == '07'
    with pytest.raises(ValidationError):
        InvoiceRelationAdd.model_validate({'relation': 1, 'type': '7'})


@pytest.mark.parametrize(
    ('stored', 'code'), [('4', '04'), ('7', '07'), ('0', None), (None, None), ('', None)]
)
def test_stored_relation_types_read_as_sat_codes(stored: str | None, code: str | None) -> None:
    assert relation_code(stored) == code
    response = FiscalDocumentRelationResponse.model_validate(
        {
            'fiscal_document_relation_id': 1,
            'relation': 2,
            'type': stored,
            'stamp_uuid': None,
            'batch': None,
            'serial': None,
        }
    )
    assert response.type == code


# ── Payment receipts, credit notes and email (spec 028) ───────────────────────


@pytest.mark.parametrize(
    'body',
    [
        {'payment_amount': '0'},
        {'payment_amount': '-5'},
        {'payment_amount': '1.001'},
        {'payment_reference': 'x' * 51},
        {'ordering_bank': 'x' * 26},
        {'payment_date': '2999-01-01'},
    ],
)
def test_receipt_header_rejects(body: dict) -> None:
    with pytest.raises(ValidationError):
        InvoiceUpdate.model_validate(body)


def test_receipt_header_keeps_only_the_fields_sent() -> None:
    update = InvoiceUpdate.model_validate({'payment_date': '2026-10-01', 'payment_amount': '10.5'})
    assert update.model_fields_set == {'payment_date', 'payment_amount'}


@pytest.mark.parametrize(
    ('schema', 'body'),
    [
        (PaidInvoiceAdd, {'invoice': 1, 'amount': '0'}),
        (PaidInvoiceAdd, {'amount': '5'}),
        (PaidInvoiceUpdate, {'amount': '-1'}),
        (PaidInvoiceUpdate, {}),
        (InvoiceLineCopy, {'line': 1, 'quantity': '0'}),
        (CreditNoteCreate, {}),
        (PaymentReceiptCreate, {'customer': 1}),
        (FiscalEmailRequest, {'to': []}),
        (FiscalEmailRequest, {'to': ['not-an-address']}),
        (FiscalEmailRequest, {'to': [f'a{i}@x.mx' for i in range(11)]}),
    ],
)
def test_receipt_credit_note_and_email_bodies_reject(schema: type, body: dict) -> None:
    with pytest.raises(ValidationError):
        schema.model_validate(body)


def test_optional_amounts_and_quantities_default_to_none() -> None:
    assert PaidInvoiceAdd.model_validate({'invoice': 7}).amount is None
    assert InvoiceLineCopy.model_validate({'line': 3}).quantity is None
    assert FiscalEmailRequest.model_validate({'to': [' A@x.mx', 'a@X.mx']}).to == ['A@x.mx']


# ── Simulated PAC controls (spec 039, contracts/endpoints.md) ─────────────────


@pytest.mark.parametrize(
    'body',
    [
        {'stamp': 'accepted'},
        {'cancellation': 'stamped'},
        {'pac_code': 'C' * 21},
        {'pac_message': 'm' * 501},
    ],
)
def test_simulation_update_rejects(body: dict) -> None:
    with pytest.raises(ValidationError):
        PacSimulationUpdate.model_validate(body)


def test_simulation_update_takes_each_outcome() -> None:
    for stamp in ('stamped', 'rejected', 'unavailable', 'timeout_stamped', 'timeout_not_stamped'):
        assert PacSimulationUpdate.model_validate({'stamp': stamp}).stamp == stamp
    for cancellation in (
        'accepted',
        'pending',
        'not_cancellable',
        'rejected',
        'unavailable',
        'timeout',
    ):
        body = PacSimulationUpdate.model_validate({'cancellation': cancellation})
        assert body.cancellation == cancellation
    body = PacSimulationUpdate.model_validate({'pac_code': 'C' * 20, 'pac_message': 'm' * 500})
    assert (body.stamp, body.cancellation) == (None, None)


@pytest.mark.parametrize('answer', ['accept', 'reject', 'expire'])
def test_receiver_answer_accepts(answer: str) -> None:
    assert ReceiverAnswer.model_validate({'answer': answer}).answer == answer


@pytest.mark.parametrize('body', [{'answer': 'accepted'}, {'answer': ''}, {}])
def test_receiver_answer_rejects(body: dict) -> None:
    with pytest.raises(ValidationError):
        ReceiverAnswer.model_validate(body)
