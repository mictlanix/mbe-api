# Data Model: Sales Order Origin

**Feature**: [spec.md](./spec.md) | **Research**: [research.md](./research.md)

## Column

One column is added. Nothing else about the schema changes.

| Table | Column | Type | Null | Default | Position |
|---|---|---|---|---|---|
| `sales_order` | `origin` | `SMALLINT` | YES | none (`NULL`) | after `fulfillment_intent` |

`NULL` means **not recorded**. It is not a member of the vocabulary, not a default, and never
written by the system on a caller's behalf. All 335,816 existing rows carry it, and so does every
future order whose client declares nothing.

Mapped as `origin: Mapped[int | None] = mapped_column(SmallInteger)` on `SalesOrder`
(`app/models/sales.py`, after `fulfillment_intent` at line 95), carrying a `#`-comment in the style
of the one above `fulfillment_intent`.

## Vocabulary

`OrderOrigin(IntEnum)` in `app/enums.py`, after `FulfillmentType`:

| Member | Value | Meaning |
|---|---|---|
| `POINT_OF_SALE` | `0` | The order was raised at a register, as a counter sale. |
| `BACK_OFFICE` | `1` | The order was raised by a back-office workflow: captured for delivery, or converted from a quote. |

Zero is the register for the reason `FulfillmentType.PICKUP` is zero — it is the ordinary case.
The vocabulary is open: a third capture surface adds a member, not a column.

**What the vocabulary does not carry**: "converted from a quote". That is a different fact about a
different question, and `sales_order.sales_quote` already records it — non-null on exactly the 6,261
orders that came from a quote, written only by `convert_to_order`, and absent from both
`SalesOrderCreate` and `SalesOrderUpdate`, so a client cannot forge it. The two stay separate.

## Field exposure

| Schema | `origin` | `sales_quote` | Note |
|---|---|---|---|
| `SalesOrderCreate` | **added**, optional | — (already absent, stays absent) | The only way a value is ever recorded. |
| `SalesOrderUpdate` | **not added** | — | Write-once. An `origin` sent on a `PUT` is dropped by Pydantic's default `extra='ignore'`. |
| `SalesOrderResponse` | **added** | already present | |
| `SalesOrderSummary` | **added** | **added** | Both are mapped columns already loaded by the list query — no join, no helper, no extra query. |

## Write paths

| Path | What sets `origin` |
|---|---|
| `POST /sales-orders` | The client, via `SalesOrderCreate.origin`. Omitted → `NULL`. Never inferred from `point_sale`, `customer` or `fulfillment_intent`. |
| `POST /sales-quotes/{id}/convert` | The service, unconditionally `BACK_OFFICE`. The endpoint takes no body; there is nothing for a client to supply. |
| `PUT /sales-orders/{id}` | Nothing. The value is fixed at creation. |
| Migration 020 | Nothing. No existing row is written. |

## Filter semantics

Two independent query parameters on `GET /sales-orders`, each optional.

| Given | Rows returned |
|---|---|
| `origin=1` | `origin = 1` only. Rows recording nothing are **excluded**. |
| `exclude_origin=1` | Every row whose `origin` is not 1, **including** rows recording nothing. |
| `origin=1&exclude_origin=1` | None. The honest answer; no special case in the code. |
| neither | Every row, as today. |

The exclusion arm is `or_(SalesOrder.origin.is_(None), SalesOrder.origin != value)`. Written as a
bare `!=`, SQL's three-valued logic would evaluate `NULL != 1` to `NULL`, the `WHERE` would discard
the row, and every one of the 335,816 unrecorded orders would vanish from the register's list — the
exact failure FR-012 forbids.

## Validation

| Rule | Where it is enforced | Failure |
|---|---|---|
| Value must be a vocabulary member | `SalesOrderCreate.origin: OrderOrigin \| None` | 422 from Pydantic |
| Value may be omitted | Field default `None` | none — records nothing |
| Value cannot change after creation | Field absent from `SalesOrderUpdate` | none — silently ignored, per R3 |
| Query parameters must be members | `Query(None)` typed `OrderOrigin \| None` | 422 from FastAPI |

## Non-changes

- No index. Neither filter is selective enough to justify one on this table today: with adoption at
  zero, `exclude_origin` matches every row. Revisit when the column is actually populated.
- No `CHECK` constraint, following 017's reasoning: the scale is documented in the column comment
  and enforced by the schema layer, and MariaDB's enforcement would duplicate it.
- No change to `point_sale`, `fulfillment_intent`, `customer`, or any existing filter.
