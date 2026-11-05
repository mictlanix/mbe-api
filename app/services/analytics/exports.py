"""Analytics exports (spec 037, FR-050–FR-055, research R12, contracts/api.md "Exports").

An export runs its source exactly as the screen does (the engine for a query, the dashboard, the
detail) and writes that answer, so every value matches the JSON for the same request. Up to 10,000
rows it is returned at once; above, an XLSX or CSV is written by a background task into
`settings.exports_dir` and downloaded by its owner for 24 hours. A PDF is always written at once
and holds at most 2,000 table rows.

Writing a file is blocking work, so every writer and every file delete runs in a worker thread
(constitution VI).
"""

import asyncio
import csv
import io
import json
import logging
import os
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import anyio
import xlsxwriter
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app import rendering
from app.core.clock import local_now
from app.core.config import settings
from app.core.deps import CurrentUser
from app.core.error_codes import ErrorCode
from app.core.errors import DomainError, not_found
from app.models.analytics import AnalyticsExport, AnalyticsView
from app.models.core import Employee
from app.schemas.analytics import (
    AnalyticsCustomerRequest,
    AnalyticsCustomerResponse,
    AnalyticsDashboardRequest,
    AnalyticsDashboardResponse,
    AnalyticsDetailRequest,
    AnalyticsDetailResponse,
    AnalyticsExportRequest,
    AnalyticsExportResponse,
    AnalyticsKardexRequest,
    AnalyticsKardexResponse,
    AnalyticsQueryRequest,
    AnalyticsQueryResponse,
    DetailKind,
    DimensionKey,
    ExportFormat,
    ExportSource,
    ExportStatus,
    MeasureKey,
)
from app.services import print_contexts
from app.services.analytics import customer, dashboard, detail, engine, kardex
from app.services.analytics.catalog import DIMENSIONS, MEASURE_FAMILIES, MEASURES, Family

log = logging.getLogger(__name__)

INLINE_ROWS = 10_000
PDF_ROWS = 2_000
WIDEST = 60
LANDSCAPE_COLUMNS = 8
EXPIRY = timedelta(hours=24)
STALE = timedelta(minutes=10)

SOURCE_CODES = {
    ExportSource.QUERY: 0,
    ExportSource.DASHBOARD: 1,
    ExportSource.DETAIL: 2,
    ExportSource.KARDEX: 3,
    ExportSource.CUSTOMER: 4,
}
FORMAT_CODES = {ExportFormat.XLSX: 0, ExportFormat.PDF: 1, ExportFormat.CSV: 2}
STATUS_CODES = {
    ExportStatus.PENDING: 0,
    ExportStatus.RUNNING: 1,
    ExportStatus.READY: 2,
    ExportStatus.FAILED: 3,
}
MEDIA_TYPES = {
    ExportFormat.XLSX: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    ExportFormat.CSV: 'text/csv; charset=utf-8',
    ExportFormat.PDF: 'application/pdf',
}
#: Every file response is binary, as the PDF routes declare (`rendering.PDF_RESPONSE`).
FILE_SCHEMA = {'schema': {'type': 'string', 'format': 'binary'}}
#: XLSX number formats per unit (contracts/api.md "File layout").
CELL_FORMATS = {
    'money': '#,##0.00',
    'quantity': '#,##0.####',
    'count': '#,##0',
    'ratio': '0.0%',
    'date': 'yyyy-mm-dd',
    'datetime': 'yyyy-mm-dd hh:mm',
}


# ── The table every format writes ─────────────────────────────────────────────


@dataclass
class Column:
    key: str
    label: str
    unit: str  # text | int | date | datetime | money | quantity | count | ratio


@dataclass
class Table:
    title: str
    columns: list[Column]
    rows: list[list[Any]]
    totals: list[Any] | None
    cover: list[tuple[str, str]] = field(default_factory=list)


def export_mode(format_: ExportFormat, rows: int) -> str:
    """`inline`, `background` or `refused` (FR-054): PDFs are always inline and capped."""
    if format_ is ExportFormat.PDF:
        return 'refused' if rows > PDF_ROWS else 'inline'
    return 'background' if rows > INLINE_ROWS else 'inline'


def is_bulk_detail(request: AnalyticsDetailRequest) -> bool:
    """A legacy-style bulk download (FR-055): lines or payments over more than one day, with no
    other filter. It needs Download CSV files (113)."""
    return (
        request.kind in (DetailKind.LINES, DetailKind.PAYMENTS)
        and request.end > request.start
        and not request.filters
        and request.flag is None
    )


def formatted(value: Any, unit: str) -> str:
    """A value as text: what sizes a column and what the CSV holds (ISO dates, dot decimals)."""
    if value is None:
        return ''
    if isinstance(value, datetime):
        return value.isoformat(sep=' ', timespec='minutes')
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, 'f')
    return str(value)


def column_widths(table: Table) -> list[int]:
    """The longest formatted value (or header) per column, capped at 60."""
    widths = [len(column.label) for column in table.columns]
    for row in [*table.rows, *([table.totals] if table.totals else [])]:
        for index, value in enumerate(row):
            widths[index] = max(widths[index], len(formatted(value, table.columns[index].unit)))
    return [min(width + 2, WIDEST) for width in widths]


def write_csv(table: Table, path: Path | io.BytesIO) -> None:
    """UTF-8 with a BOM, comma separated, ISO dates, dot decimals, raw ratios, a TOTAL row."""
    text = io.StringIO()
    writer = csv.writer(text, lineterminator='\r\n')
    writer.writerow([column.key for column in table.columns])
    for row in table.rows:
        writer.writerow([formatted(v, c.unit) for v, c in zip(row, table.columns, strict=True)])
    if table.totals is not None:
        writer.writerow(
            [formatted(v, c.unit) for v, c in zip(table.totals, table.columns, strict=True)]
        )
    data = '﻿'.encode() + text.getvalue().encode('utf-8')
    if isinstance(path, io.BytesIO):
        path.write(data)
    else:
        path.write_bytes(data)


def write_xlsx(table: Table, path: Path | io.BytesIO) -> None:
    """A "Resumen" cover sheet and a "Datos" sheet of typed cells, written as a stream."""
    workbook = xlsxwriter.Workbook(path, {'constant_memory': True, 'in_memory': False})
    bold = workbook.add_format({'bold': True})
    formats = {unit: workbook.add_format({'num_format': f}) for unit, f in CELL_FORMATS.items()}
    totals = {
        unit: workbook.add_format({'num_format': f, 'bold': True})
        for unit, f in CELL_FORMATS.items()
    }

    cover = workbook.add_worksheet('Resumen')
    cover.set_column(0, 0, 22)
    cover.set_column(1, 1, 60)
    cover.write(0, 0, table.title, bold)
    for index, (name, value) in enumerate(table.cover, start=2):
        cover.write_string(index, 0, name, bold)
        cover.write_string(index, 1, value)

    sheet = workbook.add_worksheet('Datos')
    for index, width in enumerate(column_widths(table)):
        sheet.set_column(index, index, width)
    for index, column in enumerate(table.columns):
        sheet.write_string(0, index, column.label, bold)
    sheet.freeze_panes(1, 0)
    last = len(table.rows) + (1 if table.totals is not None else 0)
    sheet.autofilter(0, 0, max(len(table.rows), 1), len(table.columns) - 1)
    for number, row in enumerate(table.rows, start=1):
        _write_row(sheet, number, row, table.columns, formats)
    if table.totals is not None:
        _write_row(sheet, last, table.totals, table.columns, totals, bold)
    workbook.close()


def _write_row(
    sheet: Any,
    number: int,
    row: Sequence[Any],
    columns: Sequence[Column],
    formats: dict[str, Any],
    text_format: Any = None,
) -> None:
    for index, (value, column) in enumerate(zip(row, columns, strict=True)):
        if value is None:
            sheet.write_blank(number, index, None, text_format)
        elif column.unit in ('money', 'quantity', 'count', 'ratio', 'int') and not isinstance(
            value, str
        ):
            sheet.write_number(number, index, float(value), formats.get(column.unit))
        elif column.unit in ('date', 'datetime') and isinstance(value, (date, datetime)):
            moment = (
                value
                if isinstance(value, datetime)
                else datetime.combine(value, datetime.min.time())
            )
            sheet.write_datetime(number, index, moment, formats[column.unit])
        else:
            sheet.write_string(number, index, str(value), text_format)


# ── Tables from each source ───────────────────────────────────────────────────


def _unit(measure: str) -> str:
    return MEASURES[MeasureKey(measure)].unit


def query_table(result: AnalyticsQueryResponse, cover: list[tuple[str, str]]) -> Table:
    """The dimension columns then the measure columns; "others", then the totals, last."""
    definition = result.definition
    day_dates = definition.grain.value == 'day'
    columns = [
        Column(
            d.value, DIMENSIONS[d].label, 'date' if d is DimensionKey.DATE and day_dates else 'text'
        )
        for d in definition.dimensions
    ] or [Column('total', '', 'text')]
    columns += [Column(m.value, MEASURES[m].label, MEASURES[m].unit) for m in definition.measures]
    measures = [m.value for m in definition.measures]
    blank = [None] * (max(len(definition.dimensions), 1) - 1)

    def keys_of(row: Any) -> list[Any]:
        return [
            date.fromisoformat(key.value) if column.unit == 'date' and key.value else key.label
            for key, column in zip(row.keys, columns, strict=False)
        ]

    rows = [keys_of(row) + [row.values.get(m) for m in measures] for row in result.rows]
    if result.others is not None:
        rows.append(['(others)', *blank, *(result.others.values.get(m) for m in measures)])
    totals = ['TOTAL', *blank, *(result.totals.values.get(m) for m in measures)]
    return Table('Consulta', columns, rows, totals, cover)


def detail_columns(cost: bool) -> list[Column]:
    columns = [
        Column('document_id', 'Documento', 'int'),
        Column('folio', 'Folio', 'text'),
        Column('date', 'Fecha', 'datetime'),
        Column('facility', 'Sucursal', 'int'),
        Column('customer', 'Cliente', 'int'),
        Column('customer_name', 'Nombre del cliente', 'text'),
        Column('salesperson', 'Vendedor', 'int'),
        Column('product_code', 'Código', 'text'),
        Column('product_name', 'Producto', 'text'),
        Column('quantity', 'Cantidad', 'quantity'),
        Column('amount', 'Importe', 'money'),
    ]
    if cost:
        columns += [Column('cost', 'Costo', 'money'), Column('margin', 'Margen', 'money')]
    return columns + [Column('payment_method', 'Forma de pago', 'int')]


def detail_table(
    result: AnalyticsDetailResponse, cost: bool, cover: list[tuple[str, str]]
) -> Table:
    columns = detail_columns(cost)
    rows = [[getattr(item, c.key) for c in columns] for item in result.items]
    sums = result.sums
    totals = [
        'TOTAL'
        if c.key == 'document_id'
        else sums.get(c.key)
        if c.key in ('quantity', 'amount', 'cost', 'margin')
        else None
        for c in columns
    ]
    columns[0] = Column('document_id', 'Documento', 'text')
    return Table('Detalle', columns, rows, totals, cover)


def kardex_table(
    result: AnalyticsKardexResponse, cost: bool, cover: list[tuple[str, str]]
) -> Table:
    """Every entry of the range with its running balance; the value column with cost only."""
    columns = [
        Column('date', 'Fecha', 'datetime'),
        Column('movement_type', 'Tipo', 'int'),
        Column('source', 'Movimiento', 'text'),
        Column('reference', 'Referencia', 'int'),
        Column('document_id', 'Documento', 'int'),
        Column('quantity', 'Cantidad', 'quantity'),
        Column('balance', 'Saldo', 'quantity'),
    ]
    if cost:
        columns.append(Column('value', 'Valor', 'money'))
    rows = [[getattr(row, c.key) for c in columns] for row in result.rows]
    totals = [
        'TOTAL' if c.key == 'date' else
        result.closing - result.opening if c.key == 'quantity' else
        result.closing if c.key == 'balance' else None
        for c in columns
    ]  # fmt: skip
    columns[0] = Column('date', 'Fecha', 'text')
    lines = [
        ('Producto', str(result.product)),
        ('Almacén', str(result.warehouse)),
        ('Saldo inicial', format(result.opening, 'f')),
        *cover,
    ]
    if cost:
        unit = '' if result.unit_cost is None else format(result.unit_cost, 'f')
        lines.append(('Costo unitario', unit or 'Sin costo'))
    return Table('Kardex', columns, rows, totals, lines)


def customer_table(result: AnalyticsCustomerResponse, cover: list[tuple[str, str]]) -> Table:
    """One line per section row and measure, with its comparison, then the customer's figures. The
    row is the keys' labels, "(others)" or TOTAL; a section the caller lacks has no lines."""
    columns = [
        Column('section', 'Sección', 'text'),
        Column('row', 'Fila', 'text'),
        Column('measure', 'Medida', 'text'),
        Column('value', 'Valor', 'quantity'),
        Column('comparison', 'Comparación', 'quantity'),
    ]
    rows: list[list[Any]] = []
    for name in ('sales', 'receivable', 'top_products'):
        section: AnalyticsQueryResponse | None = getattr(result, name)
        if section is None:
            continue
        labelled = [(' / '.join(k.label for k in row.keys), row) for row in section.rows]
        if section.others is not None:
            labelled.append(('(others)', section.others))
        labelled.append(('TOTAL', section.totals))
        for label, row in labelled:
            for measure, value in row.values.items():
                rows.append([name, label, measure, value, (row.comparison or {}).get(measure)])
    figures: list[tuple[str, Any]] = []
    if result.sales is not None:
        figures += [
            ('last_purchase', formatted(result.last_purchase, 'datetime') or None),
            ('orders_per_month', result.orders_per_month),
        ]
    if result.receivable is not None:
        figures += [
            ('credit_limit', result.credit_limit),
            ('credit_utilization', result.credit_utilization),
        ]
    rows += [['customer', '', key, value, None] for key, value in figures]
    lines = [('Cliente', f'{result.customer} {result.customer_name}'), *cover]
    return Table(f'Cliente {result.customer_name}', columns, rows, None, lines)


# ── Running an export ─────────────────────────────────────────────────────────


def source_of(request: AnalyticsExportRequest) -> ExportSource:
    given = [
        source
        for source, value in (
            (ExportSource.QUERY, request.query),
            (ExportSource.DASHBOARD, request.dashboard),
            (ExportSource.DETAIL, request.detail),
            (ExportSource.KARDEX, request.kardex),
            (ExportSource.CUSTOMER, request.customer),
        )
        if value is not None
    ]
    if len(given) != 1 or (
        given[0] is ExportSource.DASHBOARD and request.format is not ExportFormat.PDF
    ):
        raise DomainError(
            ErrorCode.ANALYTICS_EXPORT_SOURCE_INVALID,
            'Name exactly one source; a dashboard exports only to PDF',
        )
    return given[0]


@dataclass
class Checked:
    """An export request checked as its source's route checks it, ready to run anywhere."""

    source: ExportSource
    format: ExportFormat
    request: (
        AnalyticsQueryRequest
        | AnalyticsDashboardRequest
        | AnalyticsDetailRequest
        | AnalyticsKardexRequest
        | AnalyticsCustomerRequest
    )
    facility: int | None
    families: set[Family]
    user: str
    user_name: str

    def to_json(self) -> str:
        return json.dumps(
            {
                'request': json.loads(self.request.model_dump_json()),
                'facility': self.facility,
                'families': sorted(f.value for f in self.families),
                'user_name': self.user_name,
            }
        )


async def check(db: AsyncSession, request: AnalyticsExportRequest, current: CurrentUser) -> Checked:
    source = source_of(request)
    held = await engine.families(db, current)
    match source:
        case ExportSource.QUERY:
            prepared = await engine.prepare(db, request.query, current)
            inner: Any = prepared.definition
            facility = prepared.facility
        case ExportSource.DASHBOARD:
            if not held & set(MEASURE_FAMILIES):
                raise engine.family_forbidden(Family.SALES)
            engine.validate_period(request.dashboard.start, request.dashboard.end)
            facility = await engine.resolve_scope(db, current, request.dashboard.facility)
            inner = request.dashboard.model_copy(update={'facility': facility})
        case ExportSource.KARDEX:
            facility, _ = await kardex.check(db, request.kardex, current)
            inner = request.kardex
        case ExportSource.CUSTOMER:
            _, facility = await customer.check(db, request.customer, current, held)
            inner = request.customer.model_copy(update={'facility': facility})
        case _:
            if is_bulk_detail(request.detail) and Family.BULK not in held:
                raise engine.family_forbidden(Family.BULK)
            facility, _ = await detail.check(db, request.detail, current)
            inner = request.detail
    employee = await db.get(Employee, current.employee_id) if current.employee_id else None
    name = f'{employee.first_name} {employee.last_name}' if employee else current.user_id
    return Checked(source, request.format, inner, facility, held, current.user_id, name)


async def build_table(
    db: AsyncSession, checked: Checked, *, count_only: bool = False
) -> tuple[int, Any]:
    """`(rows, answer)`: the source run as its route runs it. `count_only` skips a detail's rows."""
    cover = _cover(checked)
    cost = Family.COST in checked.families
    match checked.source:
        case ExportSource.QUERY:
            definition = checked.request
            prepared = engine.Prepared(
                definition,
                engine.parse_filters(definition.filters),
                checked.facility,
                checked.families,
            )
            result = await engine.execute(db, prepared)
            table = query_table(result, cover)
            return len(table.rows), table
        case ExportSource.DETAIL:
            result = await detail.execute(
                db, checked.request, checked.facility, everything=not count_only
            )
            if count_only:
                return result.total, None
            return result.total, detail_table(result, cost, cover)
        case ExportSource.KARDEX:
            found = await kardex.read(db, checked.request, cost=cost, everything=not count_only)
            if count_only:
                return found.total, None
            return found.total, kardex_table(found, cost, cover)
        case ExportSource.CUSTOMER:
            answer = await customer.build(
                db, checked.request, _checked_user(checked), checked.families
            )
            table = customer_table(answer, cover)
            return len(table.rows), table
        case _:
            built = await dashboard.build(
                db, checked.request, _checked_user(checked), checked.families
            )
            return sum(len(s.query.rows) for s in built.series), built


def _checked_user(checked: Checked) -> CurrentUser:
    """The caller of a dashboard or customer 360 export, built with the families checked at the
    request so it shows what the caller saw. The facility was resolved then too, which is what
    makes the build's scope check a no-op."""
    return CurrentUser(
        user_id=checked.user, session_version=0, administrator=True,
        facility_id=checked.facility or 0, employee_id=0,
    )  # fmt: skip


def _cover(checked: Checked) -> list[tuple[str, str]]:
    request = checked.request
    lines = [
        ('Periodo', f'{request.start.isoformat()} a {request.end.isoformat()}'),
        ('Sucursal', 'Todas' if checked.facility is None else str(checked.facility)),
    ]
    comparison = getattr(request, 'comparison', None)
    lines.append(('Comparación', comparison.value if comparison else 'Ninguna'))
    filters = getattr(request, 'filters', [])
    lines.append(
        (
            'Filtros',
            '; '.join(f'{f.dimension.value} = {", ".join(f.values)}' for f in filters) or 'Ninguno',
        )
    )
    vat = getattr(request, 'vat', None)
    lines.append(('IVA', 'Incluido' if vat is not None and vat.value == 'included' else 'Sin IVA'))
    lines.append(('Generado', local_now().isoformat(sep=' ', timespec='minutes')))
    lines.append(('Generado por', f'{checked.user_name} ({checked.user})'))
    return lines


def file_bytes(table: Table, format_: ExportFormat) -> bytes:
    buffer = io.BytesIO()
    (write_xlsx if format_ is ExportFormat.XLSX else write_csv)(table, buffer)
    return buffer.getvalue()


SERIES_TITLES = {
    'sales_over_time': 'Ventas netas en el tiempo',
    'sales_by_facility': 'Ventas netas por sucursal',
    'sales_by_label': 'Ventas netas por etiqueta',
    'sales_by_brand': 'Ventas netas por marca',
    'top_products': 'Productos principales',
    'top_customers': 'Clientes principales',
    'payment_mix': 'Cobranza por forma de pago',
    'heatmap': 'Pedidos por día y hora',
    'stock_value_by_facility': 'Valor de inventario por sucursal',
}
WEEKDAYS = [(1, 'Lun'), (2, 'Mar'), (3, 'Mié'), (4, 'Jue'), (5, 'Vie'), (6, 'Sáb'), (7, 'Dom')]


def _float(value: Decimal | None) -> float:
    return float(value or 0)


def chart_data(answer: AnalyticsDashboardResponse) -> tuple[list[dict], list[dict]]:
    """The dashboard's KPIs and series as the PDF template draws them, from the same rows."""
    kpis = [
        {
            'label': kpi.key,
            'unit': MEASURES[kpi.drill.measures[0]].unit,
            'value': kpi.value,
            'comparison': kpi.comparison,
            'change_pct': kpi.change_pct,
        }
        for kpi in answer.kpis
    ]
    charts = []
    for series in answer.series:
        query = series.query
        measure = query.definition.measures[0].value
        chart: dict[str, Any] = {
            'title': SERIES_TITLES.get(series.key, series.key),
            'chart': series.chart,
        }
        if series.chart == 'heatmap':
            chart['cells'] = {
                (int(row.keys[0].value), int(row.keys[1].value)): _float(row.values[measure])
                for row in query.rows
                if row.keys[0].value is not None and row.keys[1].value is not None
            }
            chart['days'] = WEEKDAYS
            chart['hours'] = list(range(24))
        else:
            chart['points'] = [
                (row.keys[0].label, _float(row.values[measure])) for row in query.rows
            ]
            if query.others is not None:
                chart['points'].append(('(otros)', _float(query.others.values[measure])))
            chart['comparison'] = [
                (row.keys[0].label, _float((row.comparison or {}).get(measure)))
                for row in query.rows
                if row.comparison is not None
            ]
        charts.append(chart)
    return kpis, charts


async def render_pdf(db: AsyncSession, checked: Checked, answer: Any) -> bytes:
    """The PDF of a query, a detail or the dashboard (FR-052), on the spec 019 printing."""
    title = answer.title if isinstance(answer, Table) else 'Tablero'
    header = await print_contexts.header_context(db, checked.facility or 1, title, letter=True)
    table = answer if isinstance(answer, Table) else None
    kpis, charts = ([], []) if table is not None else chart_data(answer)
    context = {
        'header': header,
        'title': title,
        'cover': _cover(checked),
        'table': table,
        'landscape': table is None or len(table.columns) > LANDSCAPE_COLUMNS,
        'kpis': kpis,
        'charts': charts,
    }
    return await rendering.render_pdf('analytics_report.html', context)


# ── Background jobs ───────────────────────────────────────────────────────────

_tasks: set[asyncio.Task] = set()


async def drain() -> None:
    """Wait for every background export this process started (tests use it)."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _path(row: AnalyticsExport) -> Path:
    return Path(settings.exports_dir) / (row.file_name or '')


async def start_background(db: AsyncSession, checked: Checked) -> AnalyticsExport:
    row = AnalyticsExport(
        user=checked.user,
        source=SOURCE_CODES[checked.source],
        format=FORMAT_CODES[checked.format],
        definition=checked.to_json(),
        status=STATUS_CODES[ExportStatus.PENDING],
        creation_time=local_now(),
    )
    db.add(row)
    await db.commit()
    sessions = async_sessionmaker(db.bind, expire_on_commit=False)
    task = asyncio.create_task(_run(sessions, row.analytics_export_id, checked))
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)
    return row


async def _run(sessions: async_sessionmaker, export_id: int, checked: Checked) -> None:
    async with sessions() as db:
        row = await db.get(AnalyticsExport, export_id)
        row.status = STATUS_CODES[ExportStatus.RUNNING]
        await db.commit()
        try:
            count, table = await build_table(db, checked)
            row.file_name = f'{export_id}.{checked.format.value}'
            path = _path(row)
            data = await anyio.to_thread.run_sync(file_bytes, table, checked.format)
            await anyio.to_thread.run_sync(_save, path, data)
            row.row_count = count
            row.status = STATUS_CODES[ExportStatus.READY]
            row.completion_time = local_now()
            row.expiry_time = row.completion_time + EXPIRY
        except Exception:
            log.exception('analytics export %s failed', export_id)
            row.status = STATUS_CODES[ExportStatus.FAILED]
        await db.commit()


def _save(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


def _delete(path: Path) -> None:
    try:
        os.remove(path)
    except FileNotFoundError:
        pass


def status_of(row: AnalyticsExport, now: datetime) -> ExportStatus:
    status = {code: name for name, code in STATUS_CODES.items()}[row.status]
    if status is ExportStatus.RUNNING and now - row.creation_time > STALE:
        return ExportStatus.FAILED  # its worker died
    return status


async def read_export(db: AsyncSession, export_id: int, current: CurrentUser) -> AnalyticsExport:
    """The caller's own export. Another user's is not found; an expired one is gone (410)."""
    row = await db.get(AnalyticsExport, export_id)
    if row is None or row.user != current.user_id:
        raise not_found(AnalyticsExport, export_id)
    now = local_now()
    if row.expiry_time is not None and now >= row.expiry_time:
        if row.file_name:
            await anyio.to_thread.run_sync(_delete, _path(row))
        raise DomainError(
            ErrorCode.ANALYTICS_EXPORT_EXPIRED,
            'The export was deleted after 24 hours',
            {'expiry_time': row.expiry_time.isoformat()},
        )
    return row


def response_of(row: AnalyticsExport, now: datetime) -> AnalyticsExportResponse:
    sources = {code: name for name, code in SOURCE_CODES.items()}
    formats = {code: name for name, code in FORMAT_CODES.items()}
    status = status_of(row, now)
    return AnalyticsExportResponse(
        analytics_export_id=row.analytics_export_id,
        source=sources[row.source],
        format=formats[row.format],
        status=status,
        row_count=row.row_count,
        creation_time=row.creation_time,
        completion_time=row.completion_time,
        expiry_time=row.expiry_time,
        download_url=(
            f'{settings.api_v1_prefix}/analytics/exports/{row.analytics_export_id}/file'
            if status is ExportStatus.READY
            else None
        ),
    )


async def read_file(row: AnalyticsExport) -> bytes:
    status = status_of(row, local_now())
    if status is not ExportStatus.READY:
        raise DomainError(
            ErrorCode.ANALYTICS_EXPORT_NOT_READY,
            'The export is not ready',
            {'status': status.value},
        )
    return await anyio.to_thread.run_sync(_path(row).read_bytes)


async def purge(db: AsyncSession, now: datetime) -> int:
    """Delete the files and rows of every export past its expiry (the purge job)."""
    rows = (
        (await db.execute(select(AnalyticsExport).where(AnalyticsExport.expiry_time <= now)))
        .scalars()
        .all()
    )
    for row in rows:
        if row.file_name:
            await anyio.to_thread.run_sync(_delete, _path(row))
        await db.delete(row)
    await db.commit()
    return len(rows)


async def delete_for_user(db: AsyncSession, user_id: str) -> None:
    """Delete a user's exports, rows and files, and their saved views (spec 038), before the user
    is deleted. A view's shares, schedules and sends cascade with it. The caller commits."""
    await db.execute(delete(AnalyticsView).where(AnalyticsView.user == user_id))
    rows = (
        (await db.execute(select(AnalyticsExport).where(AnalyticsExport.user == user_id)))
        .scalars()
        .all()
    )
    for row in rows:
        if row.file_name:
            await anyio.to_thread.run_sync(_delete, _path(row))
        await db.delete(row)
