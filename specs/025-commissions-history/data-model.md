# Data Model: Commissions with Stable History

Migration `023_commission_periods.sql` (with a rollback) does three things:
- adds `commission_agent.scheme`;
- creates `commission_period` and `commission_period_line`;
- backfills `sales_order.balance_zeroed_time`.

`commissions_history` is not touched.

## Changed: `commission_agent` (shared with the current system)

| Column | Type | Null | Notes |
|---|---|---|---|
| `employee` | int | NO | PK, FK → `employee` (existing) |
| `scheme` | smallint | NO | **new**, `DEFAULT 1`. 1 = fully paid orders (the current system's rule), 2 = partial payments. The current system never reads it. |

## New: `commission_period`

| Column | Type | Null | Notes |
|---|---|---|---|
| `commission_period_id` | int AUTO_INCREMENT | NO | PK |
| `year` | smallint | NO | |
| `month` | tinyint | NO | 1–12 |
| `status` | smallint | NO | 0 open (after a reopen), 1 closed, 2 paid |
| `closed_by` / `closed_at` | int / datetime | YES | FK → `employee`, the last close |
| `reopened_by` / `reopened_at` | int / datetime | YES | FK → `employee`, the last reopen |
| `paid_by` / `paid_at` | int / datetime | YES | FK → `employee` |

`UNIQUE (year, month)`. A month with no row is open. Rows are never deleted.

```
(no row) ──close──► closed(1) ──pay──► paid(2)   [final]
                       └──reopen──► open(0) ──close──► closed(1)
```

- **Close:** only after the month ends, and only if the previous month is closed. The first close ever starts the chain, so periods are contiguous.
- **Reopen:** only the latest closed month.
- **Pay:** only from closed, in any order.

## New: `commission_period_line`

One row per (period, source line, agent, participation, movement), frozen at close. Scheme 2 sale rows repeat month after month for the same key, one per month that earned a non-zero share.

| Column | Type | Null | Notes |
|---|---|---|---|
| `commission_period_line_id` | int AUTO_INCREMENT | NO | PK |
| `commission_period` | int | NO | FK → `commission_period`; deleted on reopen |
| `scheme` | smallint | NO | the agent's scheme at close |
| `movement` | smallint | NO | 0 sale, 1 refund |
| `sales_order` | int | NO | id only |
| `sales_order_detail` | int | NO | id only |
| `customer_refund_detail` | int | YES | refunds only; id only |
| `order_date` | datetime | NO | |
| `paid_date` | datetime | NO | scheme 1: `balance_zeroed_time`; scheme 2: the month's last application date; refunds: `customer_refund.date` |
| `customer` / `customer_name` | int / varchar(250) | NO | the name is copied |
| `product` / `product_name` | int / varchar(250) | NO | the name is copied |
| `label` / `label_name` | int / varchar(50) | YES | copied; NULL if unlabelled |
| `label_rate` | decimal(20,6) | NO | copied |
| `salesperson` | int | NO | FK → `employee`, the agent credited |
| `order_salesperson` | int | NO | osp, id only |
| `customer_salesperson` | int | NO | csp when first stored, id only |
| `participation` / `participation_name` | int / varchar(50) | NO | copied |
| `participation_rate` | decimal(20,6) | NO | copied |
| `rate` | decimal(20,8) | NO | `label_rate × participation_rate` |
| `price` | decimal(22,2) | NO | unit price after discount, tax included; negative for a refund |
| `quantity` | decimal(18,4) | NO | |
| `total` | decimal(22,2) | NO | `ROUND(price × quantity, 2)` |
| `full_commission` | decimal(22,2) | NO | `ROUND(total × rate, 2)` |
| `share` | decimal(9,6) | YES | scheme 2 sales: the cumulative share at month end |
| `commission` | decimal(22,2) | NO | **what this month pays**: scheme 1 sale = full − earlier stored; scheme 2 sale = cumulative − earlier stored; refund = negative |

Indexes:
- `(commission_period, salesperson)`, for reads;
- `(sales_order_detail, salesperson, participation)`, for the per-key sums that enforce FR-017 and freeze keys (FR-009a).

**Invariant (FR-017, SC-006):** for each (`sales_order_detail`, `salesperson`, `participation`), Σ `commission` over sale rows ≤ `full_commission`.

**Uniqueness:** each `customer_refund_detail` appears at most once per (`salesperson`, `participation`).

## Changed: `sales_order.balance_zeroed_time`

It is stamped by `_sync_paid_flag` when an order becomes paid, if NULL or the `0001-01-01` sentinel, and never cleared. It is backfilled for paid orders dated from 2025-10-01 with no stamp (38 rows in mbe_dev).

## Unchanged, now maintained by the API

`commission`, `commission_participation`, `commission_salesperson`, `commission_product`.

## Audit

`incidence`, `source = 12` (`SourceType.COMMISSION`), one per settings change, scheme changes included. Close, reopen and pay are recorded on `commission_period`.
