# Data Model: Commissions with Stable History

Migration `023_commission_periods.sql` (with a rollback) adds two tables and backfills `balance_zeroed_time`. The settings tables already exist and are mapped. `commissions_history` is not touched.

## New: `commission_period`

| Column | Type | Null | Notes |
|---|---|---|---|
| `commission_period_id` | int AUTO_INCREMENT | NO | PK |
| `year` | smallint | NO | |
| `month` | tinyint | NO | 1–12 |
| `status` | smallint | NO | 0 open (after a reopen), 1 closed, 2 paid |
| `closed_by` | int | YES | FK → `employee`, the last close |
| `closed_at` | datetime | YES | |
| `reopened_by` | int | YES | FK → `employee`, the last reopen |
| `reopened_at` | datetime | YES | |
| `paid_by` | int | YES | FK → `employee` |
| `paid_at` | datetime | YES | |

`UNIQUE (year, month)`. A month with no row is open. Rows are never deleted.

State transitions:

```
(no row) ──close──► closed(1) ──pay──► paid(2)   [final]
                       │
                       └──reopen──► open(0) ──close──► closed(1) …
```

Close is refused until the month has ended (business time). Pay is allowed only from closed. Reopen is allowed only from closed.

## New: `commission_period_line`

One row per (period, source line, agent, participation), frozen at close.

| Column | Type | Null | Notes |
|---|---|---|---|
| `commission_period_line_id` | int AUTO_INCREMENT | NO | PK |
| `commission_period` | int | NO | FK → `commission_period`; deleted with a reopen |
| `movement` | smallint | NO | 0 sale, 1 refund |
| `sales_order` | int | NO | id only, no FK |
| `sales_order_detail` | int | NO | id only |
| `order_date` | datetime | NO | `sales_order.date`, or `customer_refund.date` for a refund |
| `paid_date` | datetime | NO | `balance_zeroed_time`, or the refund's date |
| `customer` | int | NO | id only |
| `customer_name` | varchar(250) | NO | copied |
| `product` | int | NO | id only |
| `product_name` | varchar(250) | NO | copied |
| `label` | int | YES | `commission` id, NULL if unlabelled |
| `label_name` | varchar(50) | YES | copied |
| `label_rate` | decimal(20,6) | NO | copied; 0 if unlabelled |
| `salesperson` | int | NO | FK → `employee`, the agent credited |
| `order_salesperson` | int | NO | id only (osp) |
| `customer_salesperson` | int | NO | id only (csp at close) |
| `participation` | int | NO | 1, 2 or 3 (id only) |
| `participation_name` | varchar(50) | NO | copied |
| `participation_rate` | decimal(20,6) | NO | copied; 0 if no rate row |
| `rate` | decimal(20,8) | NO | `label_rate × participation_rate` |
| `price` | decimal(22,2) | NO | unit price after discount, tax included; negative for a refund |
| `quantity` | decimal(18,4) | NO | |
| `total` | decimal(22,2) | NO | `ROUND(price × quantity, 2)` |
| `commission` | decimal(22,2) | NO | `ROUND(price × quantity × rate, 2)` |

Index `(commission_period, salesperson)`.

## Changed: `sales_order.balance_zeroed_time`

- **Written by** `_sync_paid_flag` when an order becomes paid and the column is NULL or `0001-01-01`. It is never cleared.
- **Backfilled** for paid orders dated from 2025-10-01 that have no stamp: 38 rows in mbe_dev.

## Unchanged, now maintained by the API (shared with the current system)

`commission`, `commission_participation`, `commission_agent`, `commission_salesperson`, `commission_product`. Their columns are as in `docs/data-dictionary.md`.

## Audit

`incidence` with `source = 12` (`SourceType.COMMISSION`, API-only), one per settings change. Close, reopen and pay are recorded on `commission_period` itself.
