# Data Model: Master Data Access Privileges

No schema change, no migration and no data change.

## access_privilege (existing, read only)

| Column | Type | Use in this feature |
|---|---|---|
| `user` | varchar(20), FK → `user.user_id` | Matched to the caller |
| `object` | int | SystemObject. Unique per user (`access_privilege_user_object_uq`) |
| `privileges` | int | Bitmask: Create 1, Read 2, Update 4, Delete 8 |

**Rules** (scope 12 §2 and §5):
- A missing row, or a row whose mask lacks the bit, means no access.
- `user.administrator = 1` bypasses every check.
- The rules are evaluated on every request. Changing a privilege needs no new token.

## Grant (in code only)

A `(SystemObject, AccessRight)` pair. A route requires one grant (`require_privilege`) or any one of a list of grants (`require_any_privilege`). `contracts/route-privileges.md` lists the grants for every route.

## SystemObjects involved

| Value | Name | Role |
|---|---|---|
| 1 | LABELS | guarded |
| 2 | CUSTOMERS | guarded; host for employees, taxpayer recipients, price lists |
| 3 | SUPPLIERS | guarded |
| 5 | PRICE_LISTS | guarded |
| 6 | EMPLOYEES | guarded |
| 43 | EXCHANGE_RATES | guarded |
| 54 | TAXPAYER_RECIPIENTS | guarded |
| 81 | EXPENSES | guarded |
| 84 | PAYMENT_METHOD_OPTIONS | guarded |
| 88 | VEHICLE | guarded |
| 89 | VEHICLE_OPERATORS | guarded; host for employees |
| 0 | PRODUCTS | host for labels, suppliers |
| 7 | SALES_ORDERS | host for customers, employees, taxpayer recipients, payment method options; Update allows the customer link exception |
| 8 | CUSTOMER_PAYMENTS | host for payment method options |
| 30 | SALES_QUOTES | host for customers, employees; Update allows the customer link exception |
| 44 | POS | host for customers, employees, payment method options |
| 106 | PRICING | host for labels, suppliers, price lists |
