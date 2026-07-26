"""Cross-cutting invariants for the sales documents (Phase 11 polish).

These guard decisions that are easy to erode one endpoint at a time: documents are never deletable,
and every document reports one lifecycle state rather than three raw booleans a client would have
to combine identically everywhere.
"""

from app.main import app
from app.schemas.customer_refund import CustomerRefundResponse
from app.schemas.sales_order import DocumentStatus, SalesOrderResponse, derive_status
from app.schemas.sales_quote import SalesQuoteResponse

DOCUMENT_PREFIXES = (
    '/api/v1/sales-orders',
    '/api/v1/sales-quotes',
    '/api/v1/customer-refunds',
    '/api/v1/customer-payments',
    '/api/v1/credit-notes',
    '/api/v1/cash-sessions',
)


def _routes() -> list[tuple[str, set[str]]]:
    return [
        (route.path, set(route.methods))
        for route in app.routes
        if any(str(getattr(route, 'path', '')).startswith(p) for p in DOCUMENT_PREFIXES)
        and hasattr(route, 'methods')
    ]


class TestDocumentsAreNotDeletable:
    def test_no_document_root_exposes_delete(self) -> None:
        """FR-006 — cancellation is the only way to retire a document."""
        offenders = [
            path
            for path, methods in _routes()
            if 'DELETE' in methods and '/lines/' not in path
        ]

        assert offenders == []

    def test_line_level_delete_is_the_only_delete(self) -> None:
        """A draft line has no history worth keeping, so removing one is a genuine delete."""
        deletes = [path for path, methods in _routes() if 'DELETE' in methods]

        assert deletes
        assert all('/lines/' in path for path in deletes)

    def test_no_delete_on_payments_or_credit_notes_at_all(self) -> None:
        """An application is cancelled, never removed — the evidence has to survive."""
        offenders = [
            path
            for path, methods in _routes()
            if 'DELETE' in methods
            and (path.startswith('/api/v1/customer-payments') or
                 path.startswith('/api/v1/credit-notes'))
        ]

        assert offenders == []


class TestSingleLifecycleStatus:
    def test_every_document_response_carries_one_status_field(self) -> None:
        for model in (SalesOrderResponse, SalesQuoteResponse, CustomerRefundResponse):
            assert 'status' in model.model_fields, model.__name__

    def test_no_document_response_leaks_the_raw_flags(self) -> None:
        """Three booleans would let two clients disagree about what an order is."""
        for model in (SalesOrderResponse, SalesQuoteResponse, CustomerRefundResponse):
            fields = set(model.model_fields)
            assert 'completed' not in fields, model.__name__
            assert 'cancelled' not in fields, model.__name__
            assert 'paid' not in fields, model.__name__

    def test_derive_status_covers_every_flag_combination(self) -> None:
        assert derive_status(completed=False, cancelled=False) == DocumentStatus.DRAFT
        assert derive_status(completed=True, cancelled=False) == DocumentStatus.COMPLETED
        assert derive_status(completed=True, cancelled=False, paid=True) == DocumentStatus.PAID
        assert derive_status(completed=True, cancelled=True) == DocumentStatus.CANCELLED

    def test_cancelled_wins_over_paid(self) -> None:
        """Defensive: the two cannot legitimately coexist, and cancelled is the terminal truth."""
        assert (
            derive_status(completed=True, cancelled=True, paid=True) == DocumentStatus.CANCELLED
        )
