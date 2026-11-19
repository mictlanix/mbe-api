"""Fiscal document status, search and list totals (#230, spec 020, research R10)."""

from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.db.base import Base
from app.models.fiscal import FiscalDocumentCancellation
from app.schemas.fiscal import FiscalDocumentSummary
from app.services import fiscal_document_service as svc

_INVOICE = (Path(__file__).parent.parent / 'fixtures' / 'cfdi' / 'invoice.xml').read_text(
    encoding='utf-8'
)


@pytest.mark.parametrize(
    ('completed', 'cancelled', 'has_xml', 'expected'),
    [
        (False, False, False, 'draft'),
        (False, False, True, 'sealed'),
        (True, False, True, 'issued'),
        (True, True, True, 'cancelled'),
        (False, True, False, 'cancelled'),
        (False, True, True, 'cancelled'),
    ],
)
def test_document_status(completed: bool, cancelled: bool, has_xml: bool, expected: str) -> None:
    """Spec 027 research R1: an uncompleted document with its XML stored is sealed."""
    document = SimpleNamespace(
        completed=completed, cancelled=cancelled, stamping_since=None, cancelling_since=None
    )
    assert svc.document_status(document, has_xml=has_xml) == expected


_NOW = datetime(2026, 11, 17, 10, 0, 0)


@pytest.mark.parametrize(
    ('completed', 'cancelled', 'stamping_since', 'cancelling_since', 'has_xml', 'expected'),
    [
        # Spec 039, data-model: cancelled, cancellation_pending, issued, stamping, sealed, draft.
        (False, False, _NOW, None, True, 'stamping'),
        (True, False, None, _NOW, True, 'cancellation_pending'),
        (True, False, None, None, True, 'issued'),
        (True, True, None, _NOW, True, 'cancelled'),
        (False, False, None, None, True, 'sealed'),
        (False, False, None, None, False, 'draft'),
    ],
)
def test_document_status_with_stamping_and_cancellation(
    completed: bool,
    cancelled: bool,
    stamping_since: datetime | None,
    cancelling_since: datetime | None,
    has_xml: bool,
    expected: str,
) -> None:
    document = SimpleNamespace(
        completed=completed,
        cancelled=cancelled,
        stamping_since=stamping_since,
        cancelling_since=cancelling_since,
    )
    assert svc.document_status(document, has_xml=has_xml) == expected


def test_the_new_tables_are_mapped() -> None:
    assert 'fiscal_document_cancellation' in Base.metadata.tables
    assert 'pac_simulator_store' in Base.metadata.tables
    assert 'pac_simulation' not in Base.metadata.tables


class _Rows:
    def __init__(self, rows: list | None = None) -> None:
        self.rows = rows or []

    def scalar_one(self) -> int:
        return 0

    def scalars(self) -> list:
        return self.rows

    def all(self) -> list:
        return self.rows

    def __iter__(self):  # noqa: ANN204
        return iter(self.rows)


class _RecordingSession:
    """Records the statements `list_documents` runs; every one answers an empty result."""

    def __init__(self) -> None:
        self.statements: list = []

    async def execute(self, statement) -> _Rows:  # noqa: ANN001
        self.statements.append(statement)
        return _Rows()


async def _list_conditions(status: str) -> str:
    db = _RecordingSession()
    await svc.list_documents(db, status=status)  # type: ignore[arg-type]
    return str(db.statements[0].compile(compile_kwargs={'literal_binds': True}))


@pytest.mark.asyncio
async def test_the_list_filters_stamping_by_its_marker() -> None:
    sql = await _list_conditions('stamping')
    assert 'stamping_since IS NOT NULL' in sql


@pytest.mark.asyncio
async def test_the_list_filters_cancellation_pending_by_its_marker() -> None:
    sql = await _list_conditions('cancellation_pending')
    assert 'cancelling_since IS NOT NULL' in sql
    assert 'completed' in sql


@pytest.mark.asyncio
async def test_issued_requires_no_open_cancellation() -> None:
    sql = await _list_conditions('issued')
    assert 'cancelling_since IS NULL' in sql


@pytest.mark.asyncio
async def test_sealed_requires_no_stamp_in_process() -> None:
    sql = await _list_conditions('sealed')
    assert 'stamping_since IS NULL' in sql


@pytest.mark.parametrize('status', ['stamping', 'cancellation_pending'])
def test_the_summary_accepts_the_new_statuses(status: str) -> None:
    fields = {name: None for name in FiscalDocumentSummary.model_fields}
    fields.update(fiscal_document_id=1, type=1, version=Decimal('4.0'), issuer='AAA', status=status)

    assert FiscalDocumentSummary.model_validate(fields).status == status


class _DetailSession:
    """`get_document` over one stored document, with no lines, relations or XML."""

    def __init__(self, document: SimpleNamespace) -> None:
        self.document = document

    async def get(self, _model, _id) -> SimpleNamespace:  # noqa: ANN001
        return self.document

    async def execute(self, _statement) -> '_DetailRows':  # noqa: ANN001
        return _DetailRows()


class _DetailRows(_Rows):
    def scalar_one_or_none(self) -> None:
        return None


def _stored(**overrides: object) -> SimpleNamespace:
    values = {
        'fiscal_document_id': 1,
        'type': 1,
        'completed': False,
        'cancelled': False,
        'stamping_since': None,
        'cancelling_since': None,
        'stamp_uuid': None,
        'stamped': None,
        'authority_certificate_number': None,
        'rfc_pac': None,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


@pytest.mark.asyncio
async def test_the_detail_has_no_stamp_without_a_uuid() -> None:
    document = await svc.get_document(_DetailSession(_stored()), 1)  # type: ignore[arg-type]

    assert document.stamp is None


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ('rfc_pac', 'simulated'), [('SIM010101SI0', True), ('AAA010101AAA', False), (None, False)]
)
async def test_the_detail_stamp_is_filled_and_flags_the_simulator(
    rfc_pac: str | None, simulated: bool
) -> None:
    stored = _stored(
        completed=True,
        stamp_uuid='0A1B2C3D-0000-0000-0000-000000000000',
        stamped=_NOW,
        authority_certificate_number='00001000000500000001',
        rfc_pac=rfc_pac,
    )

    document = await svc.get_document(_DetailSession(stored), 1)  # type: ignore[arg-type]

    assert document.stamp == {
        'uuid': '0A1B2C3D-0000-0000-0000-000000000000',
        'stamped': _NOW,
        'sat_certificate': '00001000000500000001',
        'provider_rfc': rfc_pac,
        'simulated': simulated,
    }


@pytest.mark.parametrize(
    ('search', 'expected'),
    [
        ('GR4776', ('batch_serial', ('GR', 4776))),
        ('gr 4776', ('batch_serial', ('GR', 4776))),
        ('4776', ('serial', 4776)),
        ('bdcad3dc-5428-5e2d-89ae-588872e59495', ('uuid', 'BDCAD3DC-5428-5E2D-89AE-588872E59495')),
        ('TOMAS', ('text', 'TOMAS')),
        ('  CLIENTE DEMO ', ('text', 'CLIENTE DEMO')),
        ('A 5241', ('batch_serial', ('A', 5241))),
        ('A5241', ('batch_serial', ('A', 5241))),
        # An RFC is text, even one whose homoclave is all digits (#337).
        ('XAXX010101000', ('text', 'XAXX010101000')),
        (' xaxx010101000 ', ('text', 'xaxx010101000')),
        ('CAG190523ES5', ('text', 'CAG190523ES5')),
        ('XAÑ010101AB1', ('text', 'XAÑ010101AB1')),
    ],
)
def test_classify_search(search: str, expected: tuple) -> None:
    assert svc.classify_search(search) == expected


def test_xml_total_reads_cfdi_33_and_40() -> None:
    assert svc.xml_total(_INVOICE) == Decimal('3160.00')


def test_xml_total_reads_the_lowercase_total_of_older_versions() -> None:
    cfd = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<cfdi:Comprobante xmlns:cfdi="http://www.sat.gob.mx/cfd/3" version="3.2"'
        ' subTotal="100.00" total="116.00"/>'
    )
    assert svc.xml_total(cfd) == Decimal('116.00')


@pytest.mark.parametrize('data', [None, '', 'not xml', '<Comprobante/>'])
def test_xml_total_is_none_without_a_readable_total(data: str | None) -> None:
    assert svc.xml_total(data) is None


class _CancellationSession(_DetailSession):
    """`get_document` with cancellation rows; it keeps the statement that read them."""

    def __init__(self, document: SimpleNamespace, cancellations: list) -> None:
        super().__init__(document)
        self.cancellations = cancellations
        self.statement = None

    async def execute(self, statement) -> _DetailRows:  # noqa: ANN001
        if statement.column_descriptions[0]['entity'] is FiscalDocumentCancellation:
            self.statement = statement
            return _DetailRows(self.cancellations)
        return _DetailRows()


@pytest.mark.asyncio
async def test_the_detail_lists_cancellations_newest_first_from_the_current_rows() -> None:
    newest, oldest = (
        SimpleNamespace(fiscal_document_cancellation_id=2),
        SimpleNamespace(fiscal_document_cancellation_id=1),
    )
    session = _CancellationSession(_stored(completed=True), [newest, oldest])

    document = await svc.get_document(session, 1)  # type: ignore[arg-type]

    assert document.cancellations == [newest, oldest]
    assert session.statement.get_execution_options()['populate_existing'] is True
    order = [str(clause) for clause in session.statement._order_by_clauses]
    assert order == ['fiscal_document_cancellation.fiscal_document_cancellation_id DESC']
