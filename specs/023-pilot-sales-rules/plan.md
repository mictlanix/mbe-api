# Implementation Plan: Pilot Sales Rules

**Branch**: `023-pilot-sales-rules` | **Date**: 2026-10-22 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/023-pilot-sales-rules/spec.md`

## Summary

There are four independent changes to the sales services, and no schema change.

1. **Line discounts.** A line added without a discount takes the customer's override for the product. The line-create `discount_rate` becomes nullable so that "not sent" differs from 0.
2. **Credit terms.**
   - Quotes default to credit for credit customers, as orders already do.
   - Moving an open document onto a credit customer sets credit.
   - A credit customer whose debt is at or over their limit cannot use cash on an order, at create, update, confirm or quote conversion.
   - Quotes are never refused on credit grounds.
3. **Delivery details.** Confirming a back-office order (origin 1) requires a fulfilment intent, a `ship_to` for delivery or mixed, and a promise date of today or later.
4. **PaymentOnDelivery.** The dead model and its documentation go.

The route-level behaviour is in [contracts/sales-rules.md](contracts/sales-rules.md).

## Technical Context

**Language/Version**: Python 3.12

**Primary Dependencies**: FastAPI, SQLAlchemy 2.0 async, Pydantic v2 (no new dependency)

**Storage**: MariaDB `mbe_dev`. No migration; reads `customer_discount` and `customer`.

**Testing**: pytest + pytest-asyncio + httpx `ASGITransport`. Integration tests on the SQLite schema (`tests/integration/`).

**Target Platform**: Linux server (xolotl, test deployment)

**Project Type**: web service (REST API under `/api/v1`)

**Performance Goals**: at most one indexed query more per line add (only when no discount is sent), and one debt query on cash orders for credit customers. That is the same query the limit check already runs for credit orders.

**Constraints**: no UI change. Existing credit-hold behaviour unchanged (FR-014). Quotes never refused on credit grounds (FR-019).

**Scale/Scope**: 2 services (`sales_order_service`, `sales_quote_service`), 1 helper in `customer_discount_service`, 2 schemas, 1 model removal, 4 docs

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Check | Status |
|---|---|---|
| I. Simplicity First | Three small helpers: `rate_for`, `is_credit_customer`, `_assert_cash_allowed`, plus the pure `missing_delivery_details`. Each is called from more than one place, or is the unit-testable core of a refusal. No configuration. | PASS |
| II. Think Before Coding | The four clarifications are recorded in the spec. The non-obvious consequences are surfaced in research: converted quotes need an intent; attaching a held credit customer is refused earlier; the 019 label row is kept. | PASS |
| III. Surgical Changes | Touches only the line-create schemas, the two sales services, the discount service, the model file and the named docs. `update_line`, `_reprice_lines` and the credit-hold code are untouched. | PASS |
| IV. Goal-Driven Execution | Success criteria are the tests in quickstart.md and the SC-005 re-measurement. | PASS |
| V. Reuse Over Rebuild | Reuses `_customer_credit_debt`, `_assert_credit_allowed`, the 409/422 refusal shapes and the existing integration fixtures. No new model or service. | PASS |
| VI. Async-First | New DB reads are `async` and use `AsyncSession`. | PASS |
| VII. Security by Default | No new routes, and the guards are unchanged. | PASS |
| VIII. Ruff Compliance | Gate in quickstart.md. | PASS |
| Testing | Tests are written first for every branch: unit tests for the pure helpers, integration tests for flows across services. | PASS |
| Changelog | A `CHANGELOG.md` `[Unreleased]` entry: Changed (rules) and Removed (model). | PASS |

Re-check after Phase 1 design: unchanged, all PASS.

## Project Structure

### Documentation (this feature)

```text
specs/023-pilot-sales-rules/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   └── sales-rules.md
├── checklists/
│   └── requirements.md
└── tasks.md              # /speckit-tasks
```

### Source Code (repository root)

```text
app/
├── schemas/
│   ├── sales_order.py        # SalesOrderLineCreate.discount_rate → nullable
│   └── sales_quote.py        # SalesQuoteLineCreate.discount_rate → nullable
├── services/
│   ├── customer_discount_service.py   # + rate_for
│   ├── sales_order_service.py         # + is_credit_customer, _assert_cash_allowed,
│   │                                  #   missing_delivery_details; add_line, create/update/confirm
│   └── sales_quote_service.py         # add_line, create/update defaults, convert cash rule
└── models/
    └── sales.py              # − PaymentOnDelivery

tests/
├── unit/test_sales_order_service.py       # helpers' branches
├── unit/test_sales_quote_service.py
├── integration/test_customer_discount_lines.py   # new
└── integration/test_sales_rules_flow.py          # new

docs/data-dictionary.md, docs/mbe_schema.sql      # − payment_on_delivery
docs/specs/02-sales.md                            # §2 and §4 rules
specs/019-document-printing/data-model.md         # note on the Contraentrega label (R7)
CHANGELOG.md
```

**Structure Decision**: single web-service project. Each rule lives in the service that owns the document. The shared credit helpers stay in `sales_order_service`, which `sales_quote_service` already imports for `_assert_credit_allowed`.

## Complexity Tracking

No violations.
