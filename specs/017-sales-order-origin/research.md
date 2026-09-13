# Phase 0 Research: Sales Order Origin

**Feature**: [spec.md](./spec.md) | **Branch**: `017-sales-order-origin` | **Date**: 2026-09-13

Every decision below was taken against the code on `main` and, where a number is quoted, against
`mbe_dev` read-only on 2026-09-13.

## Measured baseline

| Fact | Value | Why it matters |
|---|---|---|
| `sales_order` rows | **335,816** | The migration must add a column and write no row data. |
| Rows with `sales_quote IS NOT NULL` | **6,261** (1.9%) | Orders that came from a quote — the population FR-006 is about. |
| Rows with `point_sale IS NULL` | **0** | Confirms `point_sale` is universally populated and therefore useless as a discriminator. |
| Distinct `point_sale` values | **21** | Small enough that a "back office register row" workaround would have been *possible*; it was rejected on semantics, not scale. |
| Rows with `fulfillment_intent IS NOT NULL` | **7** | The precedent column, shipped in migration 017, is recorded on 7 of 335,816 rows. |
| `sales_order.origin` exists | **no** | Nothing to reconcile; this is a clean addition. |

That last row is the most useful number in this table. `fulfillment_intent` has been available for
weeks and is recorded on 0.002% of orders, because no client has been changed to send it. It is a
live demonstration of what this feature's `NULL` means and how long it can persist — and the
strongest available argument for FR-012's exclusion filter, which is the only formulation that
gives a correct answer while adoption is still near zero.

## R1: The vocabulary and its numbering

**Decision**: a new `OrderOrigin(IntEnum)` in `app/enums.py` with `POINT_OF_SALE = 0` and
`BACK_OFFICE = 1`, placed immediately after `FulfillmentType` (which ends at line 333).

**Rationale**: `FulfillmentType` sets the numbering convention explicitly — `PICKUP` is 0 "because
it is the ordinary counter sale", justified in its docstring with row counts. The register is the
ordinary capture surface here by the same argument, so it takes 0. Placement beside
`FulfillmentType` puts the two `sales_order` enum columns together; `app/enums.py` is grouped
structurally, not alphabetically, so there is no ordering rule to satisfy.

**Alternatives considered**:
- *A boolean `is_back_office`*. Rejected by FR-001: a third capture surface would need another
  column, and the issue asks for the enum specifically so it does not.
- *Reusing `SourceType`*. It names a different axis (where a document's data came from) and its
  members do not mean capture surface. Reuse here would be reuse of a name, not of a meaning.
- *Starting at 1 to keep 0 free for "unknown"*. Rejected: "unknown" is `NULL`, which is the whole
  design. A sentinel member would be a second way to say the same thing and would immediately be
  ambiguous with it.

## R2: How the list filter expresses selection and exclusion

**Decision**: two independent query parameters on `GET /sales-orders` —
`origin: OrderOrigin | None` (selection) and `exclude_origin: OrderOrigin | None` (exclusion).
Selection compiles to `SalesOrder.origin == value`. **Exclusion compiles to
`or_(SalesOrder.origin.is_(None), SalesOrder.origin != value)`.**

**Rationale**: the `or_` is the entire point of the research note, because the obvious spelling is
wrong in a way no reviewer would see from the diff. In SQL, `origin != 1` evaluates to `NULL` for a
row whose `origin` is `NULL`, and a `WHERE` clause keeps only rows evaluating to true — so the
natural expression silently drops all 335,816 existing rows, which is precisely the failure FR-012
exists to prevent and precisely the outcome the register cannot tolerate. The `IS NULL` arm is
load-bearing, not defensive, and a test pins it.

Two parameters rather than one modifier keeps each one a plain value, matches how every other
filter on this endpoint reads, and needs no parsing. Supplying both is allowed and needs no special
case: `origin=1&exclude_origin=1` returns nothing, which is the honest answer to the question asked.

**Alternatives considered**:
- *A list-valued `origin` (`?origin=0&origin=1` → `.in_()`)*. There is precedent for this shape
  (`product_prices.py:24`, `products.py:33`), and the issue's body argues for set filtering. It is
  rejected here because a set of members cannot express "recorded nothing": the register needs
  `NULL` rows *included*, and `.in_()` can never return them. It would look like it solved the
  problem while leaving the register list wrong.
- *One parameter with a sentinel or prefix (`origin=not:1`)*. Rejected: no precedent in this
  codebase — `split(',')` has zero hits in `app/` — and it makes a typed enum parameter a string
  the router must parse.
- *A generic negation modifier applied to every filter*. Rejected by Constitution I: speculative
  flexibility nobody asked for. This feature needs exclusion on one field.

**Note on precedent**: caller-supplied negation does not exist anywhere in this API today. The
closest things are internal: `price_list_service.py:48` excludes the cost price list server-side,
and `order_expiry.py:130` is the codebase's only `not_()`. So this establishes the pattern, which
is an argument for keeping it as narrow and as readable as possible — one field, one parameter.

## R3: Making the value immutable after creation

**Decision**: add the field to `SalesOrderCreate` only. `SalesOrderUpdate` gains nothing, and no
`model_config` anywhere changes.

**Rationale**: `SalesOrderUpdate` sets no `model_config`, so Pydantic v2's default `extra='ignore'`
already applies — a client that sends `origin` on a `PUT` has it dropped before `update_order` sees
it, the request succeeds, and the stored value is untouched. That satisfies FR-005 with zero code,
which is the correct amount of code to write for a requirement already met. The behaviour is not
obvious from reading the schema, so an integration test asserts it directly rather than leaving it
to inference.

**Alternatives considered**:
- *`extra='forbid'` on `SalesOrderUpdate`*. It would turn the silent drop into a 422, which is
  arguably better API design — and it would do so for **every** unknown field on that schema, not
  just this one. That is a behaviour change to an existing endpoint that no requirement asks for,
  and it would break any client currently sending a stray field. Out of scope by Constitution III.
- *Accepting `origin` on update and rejecting it in the service with a 422*. Writes code to refuse
  something the schema already refuses, and invents an error path for an impossible scenario
  (Constitution I).

## R4: Putting the two facts on the list row

**Decision**: add `origin` and `sales_quote` to `SalesOrderSummary` as plain fields. No new query,
no new helper, no change to `attach_summary_totals` or `attach_customer_names`.

**Rationale**: this is cheaper than the issue thread assumed. Both are real mapped columns on
`SalesOrder` (`sales_quote` at `app/models/sales.py:63`), and `SalesOrderSummary` is built by
`model_validate` over ORM instances with `from_attributes=True`, so both are already loaded on
every row the list query returns. Unlike `customer_display_name` — which needed
`attach_customer_names` because there is no such column — these require only the field
declarations. The list stays at three queries per page regardless of page size, and
`tests/unit/test_list_query_counts.py` proves it rather than the plan asserting it.

**Alternatives considered**:
- *Exposing `point_sale` and `fulfillment_intent` on the summary too*, since the spec's Overview
  notes their absence. Rejected: not asked for by any requirement, and each one widens a response
  every list screen already receives (Constitution III).

## R5: The migration

**Decision**: `migrations/020_sales_order_origin.sql` plus `020_sales_order_origin_rollback.sql`,
following 017 exactly: `ALTER TABLE ... ADD COLUMN IF NOT EXISTS origin SMALLINT NULL COMMENT ...
AFTER fulfillment_intent`, with the measured table, the "why this column ships empty" section, and
the value scale documented in the header. The rollback is `DROP COLUMN IF EXISTS` with an explicit
`WHAT IS LOST` section and a `SELECT` to run first.

**Rationale**: 019 is the highest number present, so 020 is next; registration is implicit —
`app/db/migrate.py` discovers `migrations/NNN_*.sql` in numeric order, and `README.md:69-76` says
creating the file is the whole registration step. `SMALLINT NULL` matches `SmallInteger` on the
model and 017's precedent. `IF NOT EXISTS` makes it idempotent, as every migration here is.

**No backfill** (spec Assumptions, issue #209 option A): the `ALTER` writes no row data, which is
also why it is safe on 335,816 rows.

## R6: Stamping the converted order

**Decision**: `convert_to_order` passes `origin=int(OrderOrigin.BACK_OFFICE)` in its `SalesOrder(...)`
construction (`app/services/sales_quote_service.py:545-583`), with a comment in the style of the
`fulfillment_intent=None` comment two lines above it explaining why this path decides for itself.

**Rationale**: FR-006, and the reason the issue calls it load-bearing — this path builds the ORM
object directly, takes no request body, and would otherwise leave all future converted orders
`NULL` forever. `int(...)` coercion matches how `priority` and `fulfillment_intent` are written on
both creation paths.

Note the deliberate asymmetry with the line above it: conversion sets `fulfillment_intent=None`
because a quote genuinely has no intent to carry, and sets `origin` because a converted order
genuinely does belong to a workflow. Recording one and not the other is the design, not an
oversight, and the comment says so.

## R7: What the repository's own checks will demand

Three existing tests turn parts of this change into hard requirements rather than good practice:

1. `tests/unit/test_model_schema.py` — every mapped column must appear in `docs/mbe_schema.sql` or
   in `migrations/*.sql`. The model change without the migration fails here.
2. `tests/unit/test_data_dictionary.py:184` — every column an applied migration adds must be
   described in `docs/data-dictionary.md`; its failure message states the rule outright. The
   `sales_order` section is at line 630, with `fulfillment_intent` at 667, so the new row goes
   directly after it.
3. `tests/unit/test_field_descriptions.py` — a described schema field is registered as a
   `(component, field, phrase)` tuple in `DESCRIBED` (lines 28-38); the three `fulfillment_intent`
   entries at 35-37 are the pattern.

None of these is optional, and all three are cheap. They are listed here so they land in `tasks.md`
as tasks rather than as a failing test run.
