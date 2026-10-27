# Purchases Specs

Covers the complete procurement cycle: purchase requests, purchase order creation, goods receipt (handled in Inventory), expense vouchers, and supplier payments.

---

## 1. Purchase Requests

**Route**: `/api/v1/purchase-requests`
**SystemObject**: `PurchaseRequest` (86)
**Spec**: `specs/031-purchase-requests-orders` (mictlanix/mbe-roadmap#68)

### Purpose
Internal requests for goods before a formal purchase order is sent to a supplier. Stores submit requests, a supervisor approves them, and purchasing turns their marked lines into purchase orders.

### States
Derived from the legacy flags, NULL counting as false; nothing new is stored:

| State | `completed` | `approved` | `cancelled` |
|-------|-------------|------------|-------------|
| `draft` | 0 | 0 | 0 |
| `submitted` | 1 | 0 | 0 |
| `approved` | 1 | 1 | 0 |
| `cancelled` | any | any | 1 |

### List View
- Default: requests the caller created or updated, without cancelled; `all=true` lists everyone's.
- Filters: `status`, `warehouse`, `facility` (through the warehouse), `creator`, `serial`, `id`, `from`, `to`.
- Sort: newest first.

### Header Fields

| Field | Column | Notes |
|-------|--------|-------|
| Warehouse | `purchase_request.warehouse` | Defaults to the caller's point-of-sale warehouse; its facility numbers the request |
| Serial | `purchase_request.serial` | Set on the first submit; kept when a rejected request is resubmitted |
| Date | `purchase_request.date` | Set on create |
| Notes | `purchase_request.comment` | Up to 500 characters |
| Approver, approval time | `purchase_request.approver`, `approval_time` | Set on approve, cleared on reject (migration 028) |

### Line Item Fields (`purchase_request_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Product | `product` | Purchasable products only |
| Product Name | `product_name` | Defaults to the product's name; can be overwritten |
| Quantity | `quantity` | Above 0, 2 decimals. Defaults to the product's minimum order quantity, or 1 when that is 0 |
| Destination Warehouse | `warehouse` | Defaults to the header's; may belong to another facility; never in transit |
| Mark for Purchase | `to_purchase` | Written as 1 on new lines (the column default is 0) |
| Customer, Accepted | `customer`, `Accepted` | Not offered. New lines store the default customer and 0 |

### Actions
- **Edit** (Update): header and lines, draft only. Once numbered, the header warehouse may move only within its facility (409 otherwise).
- **Submit** (Update): refused without lines (409) or with a line of quantity 0 (422). On the first submit the request takes its facility's highest serial + 1, under the facility's transit-warehouse lock that every facility numbering step queues on, so concurrent submits never share a serial and a refused submit uses none.
- **Mark lines** (`PUT /{id}/lines/{line_id}/to-purchase`, PurchasesOrders (20) Update): on submitted and approved requests only. Unmarking a line that is on a live purchase order is refused.
- **Cancel** (Delete): from draft, submitted or approved; refused while any line is on a live purchase order. A cancelled request is read-only.

### Business Rules
- A quantity below the product's minimum order quantity is saved with a warning naming the minimum.
- Each line shows the live purchase-order line it is on, if any, and that order's state.
- **Facility:** a non-administrator edits, submits and cancels only requests whose header warehouse is in their facility (403 otherwise). Approving, rejecting and marking are not limited by facility.

---

## 2. Purchase Request Approval

**Route**: `GET /api/v1/purchase-requests/approval-queue`, `POST /{id}/approve`, `POST /approve`, `POST /{id}/reject`
**SystemObject**: `PurchaseRequestApproval` (96): Read for the queue, Update to decide

### Purpose
Supervisor queue to review and approve or reject submitted purchase requests.

### Approval Queue Filter
- Submitted requests (`completed = 1`, `approved = 0`, not cancelled) of every facility, oldest first, filterable by `facility`, with each request's line count.

### Features
- **Approve:** a submitted request only. Records the approving employee and the time. The requester may approve their own request.
- **Bulk approve:** 1 to 100 ids; each is approved or refused on its own and reported with its reason.
- **Reject:** a submitted request only, with a reason of up to 500 characters. The reason is recorded as an incidence (source 4), and the request returns to draft keeping its serial.

---

## 3. Purchase Orders

**Route**: `/api/v1/purchase-orders`
**SystemObject**: `PurchasesOrders` (20): Read to list, view, print and see to-purchase; Create to create; Update to edit, confirm and email; Delete to cancel
**Spec**: `specs/031-purchase-requests-orders` (mictlanix/mbe-roadmap#70)

### Purpose
Formal purchase orders sent to suppliers. They are created by hand, from an approved request's lines, or from the "to purchase by supplier" groups. Purchase orders are numbered by their id and are not limited by facility.

### States
`draft`, `completed` (confirmed) and `cancelled`, the same values receipts use.

### List View
- Default: orders the caller created or updated, without cancelled; `all=true` lists everyone's.
- Filters: `status`, `supplier`, `creator`, `id`, `from`, `to`.
- Sort: newest first.

### To Purchase by Supplier
`GET /to-purchase` lists the pending lines: marked lines of approved, non-cancelled requests that are not on a live purchase order.
- **Groups:** one per product supplier and destination facility (the facility of the line's warehouse). Each shows its line count and its estimated total at cost. Lines whose product has no supplier form a separate group per facility, and can only be added by hand.
- **Create:** `POST /to-purchase {supplier, facility}` creates one draft purchase order from every pending line of that group, each priced from the cost list (0 with a warning when the product has no cost).

### Header Fields

| Field | Column | Notes |
|-------|--------|-------|
| Supplier | `purchase_order.supplier` | Required to confirm |
| Estimated Receipt Date | `purchase_order.estimated_receipt_date` | Not in the past |
| Notes | `purchase_order.comment` | Up to 500 characters |
| Invoice Number, Approved, Approver | `invoice_number`, `approved`, `approver` | Not used; stored as NULL, 0 and NULL |

### Line Item Fields (`purchase_order_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Product | `product` | Purchasable products only; code and name snapshotted |
| Destination Warehouse | `warehouse` | Defaults to the caller's point-of-sale warehouse; never in transit |
| Quantity | `quantity` | Above 0, 4 decimals |
| Unit Price | `price` | Before tax, 7 decimals. Defaults to the product's cost, or 0 |
| Tax Rate | `tax_rate` | Defaults to the product's |
| Discount, Currency, Exchange Rate, Tax Included | `discount`, `currency`, `exchange_rate`, `tax_included` | Fixed at 0, MXN, 1 and off; not editable |
| Origin PR Line | `purchase_request_detail` | Indexed with a foreign key (migration 028) |

### Actions
- **Edit**: header and lines, draft only.
- **Add a request's lines** (`POST /{id}/request-lines`): the pending lines of one approved request, priced from cost. A line whose product's supplier differs from the order's is added with a warning.
- **Confirm**: refused unless the order is a draft with a supplier, at least one line, and every quantity above 0. Lines priced at 0 are confirmed with a warning. In the same transaction, confirm creates one draft inventory receipt per destination warehouse in that warehouse's facility, linked to the order. Each receipt has one line per stockable order line still pending: its ordered quantity, less what live receipts already hold. Non-stockable lines get no receipt line. A second or concurrent confirm is refused, so receipts are never duplicated.
- **Cancel**: drafts only. The lines stay on the order as history, and their request lines become pending again.
- **Print** (`GET /{id}/document`) and **Email** (`POST /{id}/email`): see below.

### Business Rules
- **One live order per request line:** a request line is on at most one line of a non-cancelled purchase order. Every step that orders request lines locks their rows first, so under concurrency a line that was taken in the meantime is left out and listed in the response's `skipped`.
- **Receiving progress:** each line shows what confirmed receipts hold and what is still pending.
- **Receipts:** generated receipts are spec 029 receipts. They keep their warehouse, and a complementary receipt covers whatever is still pending, leaving non-stockable lines out.

### Average Cost
Confirming a receipt linked to a purchase order updates the cost list (list 0) for each product on a line linked to an order line, in the same transaction:
- **Formula:** new cost = (Q × C + R × U) / (Q + R), rounded half up to 4 decimals. Q is the product's company-wide on hand before the receipt (transit included, negative taken as 0), C the current cost (0 when none), R the received quantity, and U the order line's price × (1 − discount). Prices and cost are both before tax.
- **Exceptions:** several lines of one product are averaged one after another. A line priced at 0 changes nothing and is reported. Receipts and lines without a purchase order never change cost.
- **History:** every change is recorded in `product_cost_change` with its receipt, order line, Q, R, U and the previous and new cost. `GET /cost-changes?product=` lists them newest first.
- **Cancel:** cancelling such a receipt restores the cost from before it when its change is still the product's latest, and records the restore. Otherwise cost stays and the cancel warns, naming the later receipt.
- **No other writer:** cost is written nowhere else, and no stored sale price is recalculated from it.

### Print and Email
- The PDF shows the order number and date, the issuing facility (that of the lowest line's warehouse), the supplier and its address, the issuing employee, each line's code, name, warehouse, quantity, unit price and amount, and the subtotal, the tax added at each line's rate and the total. Only confirmed orders print.
- Email sends the PDF to 1 to 10 addresses, usually the supplier's contacts, with an optional message. Each successful send is recorded as an incidence (source 5). A refused send answers 502 and is not recorded; mail not configured answers 503.

---

## 4. Purchase Order Approval

Not built in the new MBE. The current system's approval (`PurchaseOrderApproval`, 95) was used only from 2024-11 to 2025-03 and left confirmed orders without receipts. Orders are confirmed directly, and `purchase_order.approved` and `approver` stay unused.

---

## 5. Expense Vouchers (Ticket de Gastos)

**Route**: `/api/v1/expense-vouchers`
**SystemObject**: `ExpenseTicket` (82): Read to list, view and print; Create to create; Update to edit and confirm; Delete to void
**Spec**: `specs/032-expense-supplier-payments` (mictlanix/mbe-roadmap#72)

### Purpose
Petty cash outflows from the drawer, made in the caller's open cash session. Voucher numbers are the id, global, with no serial.

### States
Derived from the legacy flags; nothing new is stored:

| State | `completed` | `cancelled` |
|-------|-------------|-------------|
| `draft` | 0 | 0 |
| `completed` | 1 | 0 |
| `cancelled` | any | 1 |

A confirmed voucher that was then voided is `completed` and `cancelled`; the API marks it `voided`, which tells it from a cancelled draft.

### List View
- Filters: `status`, `facility`, `cash_session`, `creator`, `from`, `to`.
- Sort: newest first.
- **Facility:** a non-administrator sees and acts on their own facility's vouchers only (403 otherwise). An administrator sees every facility.

### Header Fields

| Field | Column | Notes |
|-------|--------|-------|
| Facility | `expense_voucher.facility` | Its session's drawer's; set on create and again on confirm |
| Cash Session | `expense_voucher.cash_session` | The caller's open session; moved to the caller's then-open session on confirm |
| Creator | `expense_voucher.creator` | The caller, who is the cashier |
| Date | `expense_voucher.date` | Creation time, then confirmation time |
| Notes | `expense_voucher.comment` | Up to 500 characters |

### Line Item Fields (`expense_voucher_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Expense Category | `expense` | Must exist in `expenses` (Expenses, 81) |
| Amount | `amount` | Above 0, 2 decimals; 0 or less is refused (422), not ignored |
| Note | `comment` | Up to 500 characters; defaults to the category's comment |

### Actions
- **Create** (Create): needs the caller's open session (409 without one).
- **Edit** (Update): header and lines, drafts only and by the creator only (403 otherwise).
- **Confirm** (Update): the creator only, with the caller's open session and a total above 0 (409 otherwise). The voucher moves into the caller's session open at that moment, so a draft left in a closed session is confirmed in the new one. It is then read-only except for void.
- **Cancel** (Update): drafts only, by the creator, without a reason.
- **Void** (Delete, `POST /{id}/void`): a confirmed voucher, by any user with the right in its facility, with a reason of 1 to 500 characters, recorded as an incidence (source 14). Allowed only while the voucher's own session is open (409 otherwise); the caller needs no session of their own.
- **Ticket** (Read, `GET /{id}/ticket`): confirmed vouchers only. The PDF shows the facility header, the voucher number, the cashier, the date, each line's category, note and amount, the total, and "Entregó" and "Recibió" signature lines.

### Business Rules
- **Cash cut:** the session's confirmed, non-voided vouchers are subtracted from expected cash.
- **Closed sessions:** once its session is closed a voucher never changes. Confirm and void lock the session's row, as closing does, so a close waits for them and the closed figures never change.
- **Set cashier:** not built. The current system's button was removed in 2025-08; the cashier is always the caller whose session holds the voucher.
- **Current system:** a confirmed voucher could never be voided, and confirm did not always need an open session. Both are corrected here.

---

## 6. Supplier Payments

**Route**: `/api/v1/supplier-payments`, `GET /api/v1/purchase-orders/{id}/payments`
**SystemObject**: `SupplierPayment` (21): Read to list, view and see balances; Create to record; Update to apply; Delete to reverse an application or cancel
**Spec**: `specs/032-expense-supplier-payments` (mictlanix/mbe-roadmap#74)

### Purpose
Payments made to suppliers, applied to their confirmed purchase orders. Accounts payable reports (#79) build on the balances.

### States
`completed` and `cancelled`. Payments are never edited: a wrong one is cancelled and recorded again.

### List View
- Filters: `supplier`, `facility`, `method`, `status`, `from`, `to`.
- Sort: newest first. There is no facility limit on reading or applying, as for purchase orders.

### Fields (`supplier_payment`)

| Field | Column | Notes |
|-------|--------|-------|
| Supplier | `supplier_payment.supplier` | Required |
| Amount | `supplier_payment.amount` | Above 0, 2 decimals |
| Payment Method | `supplier_payment.method` | SAT `PaymentMethod`, as customer payments |
| Date | `supplier_payment.date` | Default now, not in the future; always now for cash (a body `date` is refused 422) |
| Reference | `supplier_payment.reference` | Up to 50 characters |
| Notes | `supplier_payment.comment` | Up to 500 characters |
| Facility, Serial | `facility`, `serial` | Serial is the facility's next, under the facility lock (spec 027). Facility is the session's for cash, otherwise the caller's; an administrator without a facility names one, for a non-cash payment only. NULL on the 5 legacy rows (migration 029) |
| Cash Session | `supplier_payment.cash_session` | A cash payment's session, NULL otherwise |
| Cancelled | `supplier_payment.cancelled` | 0 or 1 |

### Actions
- **Record** (Create): optionally with `applications` to purchase orders.
- **Apply** (Update, `POST /{id}/applications`): 1 to 50 applications to distinct purchase orders. Each is refused (409), and with it the whole call, when the order is not confirmed or is cancelled, is another supplier's, falls before the cutoff, or the amount exceeds the order's balance or the payment's credit, or when the payment is cancelled or legacy.
- **Reverse** (Delete, `POST /{id}/applications/{application_id}/reverse`): with a reason; the application is kept as cancelled and its amount returns to the payment's credit and the order's balance. Recorded as an incidence (source 15). Reversing twice answers 409.
- **Cancel** (Delete, `POST /{id}/cancel`): with a reason; reverses every live application. Recorded as an incidence (source 15). Cancelling twice answers 409.

### Cash Payments
- A cash payment needs the caller's open cash session (409 without one) and belongs to it.
- The cash cut subtracts the session's non-cancelled cash payments as a separate figure, "Pagos a proveedores" (`supplier_payments_total`); `cash_in_drawer` and `expected_cash` drop by it.
- Cancelling one is refused once its session is closed. Any user with Delete on 21 may cancel it.

### Balances
- **Applications:** `purchase_order_payment`, one row per application: payment, order, amount, applier, date, cancelled. Never deleted.
- **Payment credit:** amount minus live applications; 0 when cancelled or legacy. Computed, not stored.
- **Order balance** (`GET /purchase-orders/{id}/payments`): the order's total (tax added) minus its live applications. It is fixed once confirmed: receipts do not change it. `balance` is `null` for an order that does not count.
- **Supplier balance** (`GET /supplier-payments/suppliers/{id}/balance`): `owed` (balances of counting orders), `credit` (payments' credit) and `net`, with the orders that have a balance and the payments that have credit, oldest first.
- **Cutoff:** only orders confirmed on or after the cutoff date (`supplier_balance_cutoff`, one company-wide row) count. It is NULL until the go-live migration sets it, so until then no order accepts applications and payments are all credit. No route changes it.
- **Concurrency:** applications and cancels lock the payment and order rows, so credit and balance never go below 0.

### Current System
- Payments were standalone rows with no facility, serial, cancel, cash session or link to an order. Edit and delete did nothing, and no payment could be applied or corrected. Accounts payable summed payments against completed orders, including the 31 that are also cancelled.
- Supplier returns are not modelled; none exist. Payments are in MXN. Which company bank account paid is not recorded.
