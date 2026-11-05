"""Spec 037, T044: the export rules, without a database or a route."""

import io
from datetime import date, datetime
from decimal import Decimal

import openpyxl
import pytest

from app.schemas.analytics import (
    AnalyticsDetailRequest,
    AnalyticsFilter,
    DetailFlag,
    DetailKind,
    DimensionKey,
    ExportFormat,
)
from app.services.analytics import exports
from app.services.analytics.exports import Column, Table


@pytest.mark.parametrize(
    ('format_', 'rows', 'mode'),
    [
        (ExportFormat.XLSX, 10_000, 'inline'),
        (ExportFormat.XLSX, 10_001, 'background'),
        (ExportFormat.CSV, 10_000, 'inline'),
        (ExportFormat.CSV, 10_001, 'background'),
        (ExportFormat.PDF, 2_000, 'inline'),
        (ExportFormat.PDF, 2_001, 'refused'),
    ],
)
def test_export_mode(format_: ExportFormat, rows: int, mode: str) -> None:
    assert exports.export_mode(format_, rows) == mode


TABLE = Table(
    'Consulta',
    [
        Column('date', 'Fecha', 'date'),
        Column('product', 'Producto', 'text'),
        Column('net_sales', 'Ventas netas', 'money'),
        Column('units', 'Unidades', 'quantity'),
        Column('margin_pct', 'Margen %', 'ratio'),
    ],
    [
        [date(2026, 5, 4), 'P1 ' + 'x' * 80, Decimal('1234.50'), Decimal('3.5000'), None],
        [date(2026, 5, 5), 'P2', Decimal('-10.00'), Decimal('1.0000'), Decimal('0.2530')],
    ],
    ['TOTAL', None, Decimal('1224.50'), Decimal('4.5000'), Decimal('0.2400')],
    [('Periodo', '2026-05-01 a 2026-05-31')],
)


def test_column_widths_follow_the_longest_value_capped_at_sixty() -> None:
    widths = exports.column_widths(TABLE)

    assert widths[0] == len('2026-05-04') + 2
    assert widths[1] == 60
    assert widths[2] == len('Ventas netas') + 2


def test_csv_values() -> None:
    assert exports.formatted(date(2026, 5, 4), 'date') == '2026-05-04'
    assert exports.formatted(Decimal('1234.50'), 'money') == '1234.50'
    assert exports.formatted(Decimal('0.2530'), 'ratio') == '0.2530'
    assert exports.formatted(None, 'money') == ''
    assert exports.formatted(datetime(2026, 5, 4, 9, 5, 30), 'datetime') == '2026-05-04 09:05'


def test_the_csv_has_a_bom_keys_and_a_total_row() -> None:
    buffer = io.BytesIO()
    exports.write_csv(TABLE, buffer)
    text = buffer.getvalue()

    assert text.startswith('﻿'.encode())
    lines = text.decode('utf-8-sig').splitlines()
    assert lines[0] == 'date,product,net_sales,units,margin_pct'
    assert lines[2] == '2026-05-05,P2,-10.00,1.0000,0.2530'
    assert lines[-1] == 'TOTAL,,1224.50,4.5000,0.2400'


def test_the_xlsx_cells_are_typed_and_formatted() -> None:
    buffer = io.BytesIO()
    exports.write_xlsx(TABLE, buffer)
    book = openpyxl.load_workbook(io.BytesIO(buffer.getvalue()))

    assert book.sheetnames == ['Resumen', 'Datos']
    data = book['Datos']
    assert data.freeze_panes == 'A2'
    assert data.auto_filter.ref is not None
    assert isinstance(data['A2'].value, datetime) and data['A2'].number_format == 'yyyy-mm-dd'
    assert data['C2'].value == 1234.5 and data['C2'].number_format == '#,##0.00'
    assert data['E3'].number_format == '0.0%'
    assert data['A4'].value == 'TOTAL' and data['C4'].font.b
    assert book['Resumen']['A3'].value == 'Periodo'


def _detail(kind: DetailKind, **kw: object) -> AnalyticsDetailRequest:
    return AnalyticsDetailRequest(kind=kind, start=date(2026, 5, 1), end=date(2026, 5, 31), **kw)


def test_bulk_detail_is_lines_payments_or_purchases_over_days_without_filters() -> None:
    narrowed = [AnalyticsFilter(dimension=DimensionKey.CUSTOMER, values=['2'])]
    one_day = AnalyticsDetailRequest(
        kind=DetailKind.LINES, start=date(2026, 5, 4), end=date(2026, 5, 4)
    )

    assert exports.is_bulk_detail(_detail(DetailKind.LINES))
    assert exports.is_bulk_detail(_detail(DetailKind.PAYMENTS))
    # Spec 038, legacy row 38: the purchases CSV is under the same rule.
    assert exports.is_bulk_detail(_detail(DetailKind.PURCHASES))
    assert not exports.is_bulk_detail(_detail(DetailKind.PURCHASES, flag=DetailFlag.PRICE_CHANGE))
    assert not exports.is_bulk_detail(_detail(DetailKind.ORDERS))
    assert not exports.is_bulk_detail(_detail(DetailKind.LINES, filters=narrowed))
    assert not exports.is_bulk_detail(_detail(DetailKind.PAYMENTS, flag=DetailFlag.COLLECTED))
    assert not exports.is_bulk_detail(one_day)
