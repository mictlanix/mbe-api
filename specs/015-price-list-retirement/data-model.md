# Phase 1 Data Model: Price List Retirement

**Feature**: `015-price-list-retirement` | **Date**: 2026-08-29

**No schema change.** No table, column, index, constraint or migration. Everything below already
exists; this document records what the retirement reads and what it does to each relation.

## The relations that reference `price_list`

Read from `Base.metadata` by `referencing_columns(PriceList)`, sorted by `(table, column)`. Two
today:

| Relation | Column in the database | Nullable | What a retirement does | Why |
|----------|------------------------|----------|------------------------|-----|
| `customer.price_list` | `price_list` | No | Moved to the named replacement, or the retirement is refused | The assignment survives the list and must land on some tier. Which tier is a commercial decision, so the operator names it (FR-003, FR-004). |
| `product_price.list` | `list` | No | Deleted with the list | The price of a product *in this list*. Unique on `(product, list)`, unreachable and meaningless once the list is gone, and it records no event (FR-001). |

A relation added later is neither: it blocks, with a 409 naming it, until someone decides which of
the two it is (FR-011). Nothing has to be edited for that to happen — `find_blocking_references`
already reads it off the metadata.

Note that `ProductPrice.price_list` is the mapped attribute; the underlying column is named `list`,
because `list` had to be aliased away from the Python builtin. The category the report prints and
the column the cascade filters on both come from the database name, so both read `product_price.list`
— the name the client sees in today's 409 and in `docs/data-dictionary.md`.

## Entities

### PriceList (`price_list`) — the record being retired

| Field | Type | Note |
|-------|------|------|
| `price_list_id` | int, PK | What every relation above points at |
| `name` | str(250) | |
| `high_profit_margin` | Decimal(5,4) | |
| `low_profit_margin` | Decimal(5,4) | |

Unchanged. A price list has no status column, so retiring one is a deletion, not a state change.

### ProductPrice (`product_price`) — the list's contents

| Field | Type | Note |
|-------|------|------|
| `product_price_id` | int, PK | |
| `product` | int, FK → `product.product_id` | Swept when the *product* is deleted, by `delete_product` |
| `price_list` | int, FK → `price_list.price_list_id`, column `list` | Swept when the *list* is retired, by this feature |
| `price`, `low_profit`, `high_profit` | Decimal | |

The row is per-pair, unique on `(product, list)`. Both halves of the pair now sweep it, which is the
symmetry GH #181 asks for.

### Customer (`customer`) — assigned to exactly one list

Only `customer.price_list` is read or written. It is a non-nullable FK, which is the whole reason
the retirement needs a replacement rather than a cascade: there is no value to leave behind.

## The cascade set

```text
_DELETE_CASCADE = frozenset({'product_price'})
```

Table names, not model classes, because that is what `assert_not_referenced(exempt=...)` takes and
what `referencing_columns` returns. Read in exactly two places, both inside `delete_price_list`:

1. as `exempt=` on the blocker check, so its members do not refuse the retirement;
2. as the filter on `referencing_columns(PriceList)`, so its members are deleted.

Adding a member does both. There is no third place, and no second list.

## Statements a retirement issues

In order, on one session, with one commit at the end:

| # | Statement | Issued when |
|---|-----------|-------------|
| 1 | `UPDATE customer SET price_list = :replacement WHERE price_list = :retired` | A replacement was named |
| 2 | `SELECT ... UNION ALL ...` — one count per non-exempt relation | Always (`assert_not_referenced`) |
| 3 | `DELETE FROM product_price WHERE list = :retired` | Always, one per cascade-set member |
| 4 | `DELETE FROM price_list WHERE price_list_id = :retired` | Always |

Statement 3 is built with SQLAlchemy Core (`delete(table).where(column == id)`) rather than
interpolated SQL text, so the identifier `list` is quoted by the dialect rather than trusted to be
safe unreserved — the merge's raw-text loop never had to name that column.

Statement 1 precedes statement 2 deliberately: after the move, no customer references the list, so
the generic check passes for the ordinary reason rather than through an exemption that would depend
on a request parameter (research R2).

## What the report counts

`find_blocking_references(db, pl)` with **no** `exempt` — every relation, which is the union of what
statement 1 moves and what statement 3 deletes, plus anything added later. That is FR-008 by
construction rather than by agreement between two lists.

Reported as `{items: [{category: "table.column", count: n}], total: n}`, largest count first, ties
broken by name — the ordering `find_blocking_references` already applies for the 409 message.
