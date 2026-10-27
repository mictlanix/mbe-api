# Sales Specs

Covers the complete sales cycle: pricing, quoting, POS transactions, orders, payments, and refunds.

---

## 1. Pricing

**Route**: `GET /pricing`  
**Controller**: `PricingController`  
**SystemObject**: `Pricing` (106)

### Purpose
Bulk price management tool. Edit prices for all products across all price lists in a single grid, with real-time margin feedback.

### Features
- Grid view: products as rows, price lists as columns
- Edit price inline; computed margin shown vs cost
- Warn when price is below `low_profit_margin` or above `high_profit_margin`
- Filter by product label, supplier, brand, model
- Import/export via CSV

### Key Tables
- `product_price` (read/write)
- `product` (reference: cost, tax rate)
- `price_list` (column headers)

---

## 2. Sales Quotes

**Route**: `GET /quotations`  
**Controller**: `QuotationsController`  
**SystemObject**: `SalesQuotes` (30)

### Purpose
Customer quotations (presales). A quote can be converted to a sales order.

### List View
- Default: shows quotes created or updated by the current user
- Search by: customer name, salesperson nickname; numeric = ID or Serial
- Wildcard `*`: shows all quotes across users
- Sort: open quotes first, then by date descending

### Create Defaults
- Customer: `WebConfig.DefaultCustomer`
- Salesperson: current user's employee
- Terms: `Immediate`. **API (spec 023):** `NetD` for a credit customer (credit limit above 0, not `DefaultCustomer`), `Immediate` otherwise. Moving an `Immediate` quote onto a credit customer, without naming terms, makes it `NetD`.
- DueDate: `today + WebConfig.DefaultQuotationDueDays`
- Currency: `WebConfig.DefaultCurrency`

### Header Fields

| Field | Column | Notes |
|-------|--------|-------|
| Facility | `sales_quote.facility` | From user context |
| Serial/Folio | `sales_quote.serial` | Auto-generated on create |
| Date | `sales_quote.date` | Defaults to today |
| Salesperson | `sales_quote.salesperson` | Auto from `customer.salesperson` if set, else current user |
| Customer | `sales_quote.customer` | FK → `customer` |
| Contact | `sales_quote.contact` | FK → `contact` |
| Ship To | `sales_quote.ship_to` | FK → `address` |
| Payment Terms | `sales_quote.terms` | Enum: `Immediate`, `NetD` |
| Due Date | `sales_quote.due_date` | Quote expiry; auto = today + `DefaultQuotationDueDays` |
| Currency | `sales_quote.currency` | |
| Exchange Rate | `sales_quote.exchange_rate` | Auto from today's rates |
| Notes | `sales_quote.comment` | |

### Line Item Fields (`sales_quote_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Product | `product` | FK → `product` |
| Quantity | `quantity` | Min = `product.min_order_qty` |
| Price | `price` | From customer's price list |
| Price Increment | `price_increment` | Absolute markup over base price |
| Price Increment Rate | `price_increment_rate` | Percentage markup (0–1); sets `price_increment` |
| Discount % | `discount_rate` | 0–1. Starts at the customer's override for the product (`customer_discount`), else 0, when the line is added without one. Never re-read afterwards (spec 023) |
| Tax Rate | `tax_rate` | From product |
| Line Note | `comment` | |

### Actions
- **Confirm**: sets `IsCompleted = true`, makes quote read-only, redirects to View
- **Cancel**: sets `IsCancelled = true`
- **Duplicate**: creates a new editable copy with today's date; prices re-fetched from price list
- **Convert to Order** (`CreateFromSalesQuote`): creates `sales_order` from this quote (quote must be completed and not expired)
- **Print / PDF**: generates printable quote document. **API (spec 024):** `GET /sales-quotes/{id}/document` returns a letter-size PDF inline (`cotizacion-{id:08d}.pdf`) for a quote in any state. It shows:
  - the facility header and logo;
  - the customer and contact;
  - the date, the validity (due date) and the payment terms (`Contado`, or `Crédito – {credit_days} días`);
  - the lines at their net unit price;
  - the quote's own totals with the amount in words;
  - "Elaborada por" with the salesperson.
- **Send Email**: sends PDF attachment to a specified email address. **API (spec 024):** `POST /sales-quotes/{id}/email` with `{"to": [...]}`.
  - **Who and when:** it requires sales quotes Read, and sends only a confirmed quote (409 otherwise).
  - **Addresses:** 1–10, validated and deduplicated (422 otherwise).
  - **Message:** subject `{facility} Cotización {id}`, the current system's body text, the PDF attached, From `MAIL_FROM` and Reply-To `MAIL_REPLY_TO` when set.
  - **Synchronous:** a server refusal is a 502 and nothing is recorded, and unconfigured mail is a 503. Each successful send writes one `incidence` (source 11, SalesQuote) naming the employee, the time and every recipient.
  - **Unlike the current system,** which sent in the background, always reported success and kept no record.

### Business Rules
- Expired quotes (`HasExpired`: date > DueDate) cannot be converted to orders.
- Price below `low_profit_margin` or above `high_profit_margin` is warned via `WebConfig.PriceValidationInRangeRequired`.
- `NetD` terms require `customer.HasCredit`; customer credit check is performed.
- **API (spec 023):** a quote is never refused on credit grounds while it is a quote. Conversion applies every order credit check, and the cash rule in §4.
- Converting and duplicating copy each line's discount; they do not re-read the customer's override.

---

## 3. Point of Sale (POS)

**Route**: `GET /pos`  
**Controller**: `POSController` (extends `SalesOrdersController`)  
**SystemObject**: `POS` (44)

### Purpose
Fast cash register screen for walk-in sales. Requires an open `cash_session`. Inherits all Sales Order logic with POS-specific overrides.

### Session Management
- User must open a cash session (select cash drawer, enter opening cash amount) before selling.
- If session was opened on a prior day, redirects to `CloseSession` before allowing sales.
- Session close requires entering denominations (`cash_count`) and confirming totals.

### POS-Specific Overrides
- **Confirm override**: after completing the order:
  - If `Terms == Immediate` → redirect to `Payments/PayOrder` (collect payment immediately)
  - If `Terms == NetD` → redirect to `POS/View`
- **Pdf override**: if order is completed → redirect to `Payments/Print` (thermal receipt); else → thermal ticket view

### POS Screen Elements
- Customer search/select (default: `WebConfig.DefaultCustomer` = "General Public")
- Product search by name, code, SKU, brand, model; also by **barcode scan** (13-digit numeric pattern auto-detected)
- Cart with quantity, price, discount per line; initial quantity = `product.min_order_qty`
- Payment collection: supports multiple payment methods per transaction
- Change calculation for cash payments
- Receipt print trigger after completion

### Created Records
- `sales_order` with `point_sale`, `payment_terms = Immediate`
- `sales_order_detail` per line
- `customer_payment` per payment method used
- `sales_order_payment` linking order to payments

### Business Rules
- POS sales default to `Terms = Immediate` (no credit). Credit terms allowed if customer has credit.
- Price validation: `WebConfig.PriceValidationInRangeRequired && !ExcludePriceRangeValidation.AllowUpdate` → error if outside `[low_profit_margin, high_profit_margin]`.
- Stock validation: if `product.StockRequired && product.IsStockable` → check `LotSerialTracking` balance for warehouse.
- Cash session must be open; drawer must be configured.

---

## 4. Sales Orders

**Route**: `GET /sales-orders`  
**Controller**: `SalesOrdersController`  
**SystemObject**: `SalesOrders` (7)

### Purpose
Full sales order management for credit customers and pre-invoiced sales. Supports partial deliveries, multi-currency, and fiscal document generation.

### List View
- Default: shows orders where current user is creator, updater, or salesperson; non-cancelled
- Search numeric: matches `Id` OR `Serial` (shows across all users)
- Search text: matches `customer.Name` or `salesperson.Nickname`; scoped to current facility
- Wildcard `*` (admin only): shows all orders across users and facilities
- Sort: open orders first, then descending by ID

### Create Defaults
- Facility: from `WebConfig.PointOfSale.Store`
- Customer: `WebConfig.DefaultCustomer`
- Salesperson: current user's employee
- Date: now; PromiseDate: `now + WebConfig.MaxDaysToDeliverStockables`; DueDate: now
- Terms: `NetD` if customer has credit (and is not `DefaultCustomer`), else `Immediate`. **API:** "has credit" means a credit limit above 0, with credit days ignored. Moving an `Immediate` order onto a credit customer, without naming terms, makes it `NetD` (spec 023).
- Currency: `WebConfig.DefaultCurrency`; ExchangeRate: today's rate

### Header Fields

| Field | Column | Notes |
|-------|--------|-------|
| Facility | `sales_order.facility` | |
| Serial/Folio | `sales_order.serial` | Assigned on Confirm (MAX+1 for facility) |
| Point of Sale | `sales_order.point_sale` | FK → `point_sale` |
| Salesperson | `sales_order.salesperson` | FK → `employee` (sales_person=1) |
| Customer | `sales_order.customer` | FK → `customer` |
| Customer Name Override | `sales_order.customer_name` | Optional free-text override |
| Origin Quote | `sales_order.sales_quote` | Optional FK → `sales_quote` |
| Payment Terms | `sales_order.payment_terms` | `Immediate` or `NetD` |
| Date | `sales_order.date` | |
| Promise Date | `sales_order.promise_date` | Expected fulfillment date |
| Due Date | `sales_order.due_date` | Payment due date |
| Contact | `sales_order.contact` | FK → `contact` |
| Ship To | `sales_order.ship_to` | FK → `address` |
| Recipient RFC | `sales_order.recipient` | FK → `taxpayer_recipient` |
| Recipient Name | `sales_order.recipient_name` | Auto-filled from recipient |
| Currency | `sales_order.currency` | |
| Exchange Rate | `sales_order.exchange_rate` | Changing currency updates all lines |
| Priority | `sales_order.priority` | `Priority` enum (Low/Medium/High); can be changed after completion |
| Notes | `sales_order.comment` | |

### Line Item Fields (`sales_order_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Product | `product` | |
| Quantity | `quantity` | Min = `product.min_order_qty` (enforced on change) |
| Cost | `cost` | Cost at time of sale (from cost price list, id=0) |
| Price | `price` | From customer's price list; must be ≥ list price (cannot sell below list without privilege) |
| Discount % | `discount_rate` | 0–1. Starts at the customer's override for the product when the line is added without one, else 0. Never re-read on edits or a customer change (spec 023) |
| Tax Rate | `tax_rate` | From product |
| Warehouse | `warehouse` | Fulfillment source; required when `StockRequired && IsStockable` |
| Requires Delivery | `delivery` | Boolean — appears in delivery order creation |
| Currency | `currency` | |
| Tax Included | `tax_included` | |
| Note | `comment` | Initialized from `product.comment` |

### Product Search on Line Add
- Normal pattern: matches `name`, `code`, `sku`, `brand`, `model` (SQL LIKE)
- 13-digit numeric pattern: treated as barcode scan → matches `bar_code`
- Appending `*` to pattern: show all warehouses instead of only user's POS warehouse
- Returns: product info, stock quantity per warehouse, price from customer's price list

### Confirm Action
1. Validates: not already completed or cancelled
2. Checks credit expiry (advisory if `DeliveryOrderRequiresPaidOrCreditSalesOrder`)
3. Aborts with `ZeroPriceError` view if any line has `price = 0`
4. Runs stock and price validation messages per line (displayed as warnings)
5. Assigns `Serial = MAX(serial FOR facility) + 1`
6. Posts `InventoryHelpers.ChangeNotification` (negative quantity) for each stockable line with a warehouse
7. Sets `IsCompleted = true`

**API additions (spec 023):**
- **Back-office orders** (`origin = 1`) are refused with 409, naming each missing item, unless they have:
  - a `fulfillment_intent`;
  - a `ship_to` when the intent is delivery or mixed;
  - a `promise_date` that has not passed.

  POS orders and orders with no recorded origin are not checked.
- **Cash rule:** an `Immediate` order for a credit customer whose credit debt is at or over their limit is refused (422). This applies to every origin.

### Cancel Action
- Blocked if `IsPaid = true`
- Sets `IsCancelled = true`

### Actions
- **Apply Payment**: link a `customer_payment` to this order
- **Create Delivery Order**: from lines with `delivery = true` (see Logistics spec)
- **Create Fiscal Document**: generate CFDI from this order (see Fiscal Documents spec)
- **Cancel**
- **Print** / **PDF**

### Business Rules
- `Terms = NetD` requires: customer has credit limit set (`HasCredit`), no expired credits, not over credit limit; `DefaultCustomer` cannot use `NetD`.
- `DueDate`: `Immediate` → same as order date; `NetD` → order date + `customer.credit_days`.
- **API (spec 023):** a credit customer may use `Immediate` only while their credit debt is below their limit. At or over it, cash is refused on create, update, confirm and quote conversion, for every origin. Buying in cash is not a way around an unpaid debt. The current system never refused cash.
- Price validation (if `WebConfig.PriceValidationInRangeRequired` and user lacks `ExcludePriceRangeValidation.AllowUpdate`): price must be in `[low_profit_margin, high_profit_margin]` of product.
- Stock validation (if `product.StockRequired && product.IsStockable`): warehouse must be set; available stock must cover all SO lines for same product+warehouse.
- `balance_zeroed_time` is set when a supervisor manually zeros the remaining balance.
- `InventoryHelpers.ChangeNotification` posts to `lot_serial_tracking` (negative = outbound stock).

---

## 5. Customer Payments

**Route**: `GET /payments` (cash session context)  
**Controller**: `PaymentsController`  
**SystemObject**: `CustomerPayments` (8)

### Purpose
Record and manage payments received from customers. The payments list is scoped to the current cash session and cashier.

### List View
- Search scoped to current cash session's cashier; shows unpaid completed orders
- Numeric search: matches SO ID or Serial (shows across all users)
- Pattern search: matches customer name, customer.salesperson nickname, SO.salesperson nickname, or `customer_name` field
- Wildcard `*`: shows all facilities if user has `SearchCreditsFromAllStores.AllowRead`

### Form Fields (`customer_payment`)

| Field | Column | Notes |
|-------|--------|-------|
| Customer | `customer_payment.customer` | FK → `customer` |
| Amount | `customer_payment.amount` | |
| Currency | `customer_payment.currency` | |
| Payment Method | `customer_payment.method` | Enum: Cash, Card, Transfer, etc. |
| Payment Option | `customer_payment.payment_charge` | FK → `payment_method_option` |
| Reference | `customer_payment.reference` | Check/transfer reference |
| Date | `customer_payment.date` | |
| Facility | `customer_payment.facility` | |
| Cash Session | `customer_payment.cash_session` | Active session if POS payment |
| Payment Type | `customer_payment.payment_type` | `Normal`, `CreditNote`, `COD` |

### Actions
- **Apply to Order**: creates `sales_order_payment` record; marks order `IsPaid = true` when fully covered
- **Verify**: supervisor confirms receipt (`verifier` field set to employee)
- **Unapply**: cancels `sales_order_payment` application (`cancelled = 1`)
- **API (spec 033):** a payment's `unapplied` is its amount less its live applications and the change they handed back, as the payment ticket shows. An application locks the payment and the order, and is refused above the order's balance (422) or on an order already settled (409); `amount + amount_change` must fit what the payment has left. `paid` follows the one order balance (07-administration §1): set at 0.01 or less, cleared only when a reversal takes it back above.

---

## 6. Customer Refunds

**Route**: `GET /customer-refunds`  
**Controller**: `CustomerRefundsController`  
**SystemObject**: `CustomerRefunds` (22)

### Purpose
Process product returns from customers. Generates credit note or cash refund.

### List View
- Default: shows refunds created or updated by current user
- Numeric search: matches refund ID, refund serial, or source `sales_order` ID
- Text search: matches customer name
- Sort: open first, then by date/ID descending

### Creation
- Enter a Sales Order ID; system validates: must be `completed = 1` and `cancelled = 0`
- Auto-populates all refundable lines (where `GetRefundableQuantity() > 0`)
- Lines with `GetRefundableQuantity() = 0` are excluded
- If no refundable lines exist, returns error `RefundableItemsNotFound`

### Header Fields

| Field | Column | Notes |
|-------|--------|-------|
| Original Sales Order | `customer_refund.sales_order` | FK → `sales_order` |
| Customer | `customer_refund.customer` | Auto-filled from order |
| Salesperson | `customer_refund.sales_person` | FK → `employee`; auto-filled from order |
| Facility | `customer_refund.facility` | From user context |
| Date | `customer_refund.date` | Set on Confirm |
| Currency | `customer_refund.currency` | From original order |
| Exchange Rate | `customer_refund.exchange_rate` | |

### Line Item Fields (`customer_refund_detail`)

| Field | Column | Notes |
|-------|--------|-------|
| Original Order Line | `sales_order_detail` | FK → `sales_order_detail` |
| Product | `product` | Auto-filled |
| Return Quantity | `quantity` | Initially 0; user enters quantity; max = `GetRefundableQuantity()` |
| Price | `price` | From original order line |
| Return-to Warehouse | `warehouse` | Where stock is restocked |
| Discount | `discount_rate` | From original order line |
| Tax Rate | `tax_rate` | From original order line |

**`GetRefundableQuantity(SODetail)`** = `detail.Quantity` − SUM(completed, non-cancelled refund details for same SO line)

### Confirm Action
1. Requires an open `cash_session` for the current drawer
2. Re-validates all quantities (concurrent-change protection): adjusts or removes lines if quantities changed
3. Removes lines with `quantity = 0`
4. Posts `InventoryHelpers.ChangeNotification` (positive quantity) for each stockable line → restores stock
5. If `refund.Total ≥ order.Balance`: marks `sales_order.IsPaid = true`, sets `BalanceZeroedTime`
6. If cashback (`refund.Total > order.Balance`): creates a `CreditNote` + associated `CustomerPayment` with `PaymentType.CreditNote`
7. Sets `IsCompleted = true`, assigns `Serial = MAX(serial FOR facility) + 1`

### Cancel Action
- Blocked if `IsCompleted = true`

### Business Rules
- Return quantity is capped at `GetRefundableQuantity()` — quantity sold minus previously completed refunds.
- Stock restoration posted to `lot_serial_tracking` as positive entry (`TransactionType.CustomerRefund`).
- Cashback excess is automatically issued as a `CreditNote`, visible under Credit Payments.

---

## 7. Credit Payments

**Route**: `GET /payments/credit`  
**SystemObject**: `CreditPayments` (83)

### Purpose
Apply credit notes toward outstanding orders. Credit notes are generated by refunds when the refund total exceeds the order balance.

### Features
- List open credit notes per customer with remaining balance
- Apply credit note balance toward one or more outstanding orders
- Shows origin: which refund generated the credit note

---

## 8. Payments Editor

**Route**: `GET /payments/edit`  
**SystemObject**: `PaymentsEditor` (100)

### Purpose
Supervisor tool to re-apply, split, or reassign payments between orders. Handles edge cases where payments were applied incorrectly.

### Features
- Search by customer, payment reference, date
- View all `sales_order_payment` applications for a payment
- Cancel an application (sets `cancelled = 1`)
- Reapply to different order

---

## 9. Payments Verification

**Route**: `GET /payments/validation`  
**SystemObject**: `PaymentsVerification` (108)

### Purpose
Supervisory queue to verify unverified customer payments — typically used when payments arrive by bank transfer and must be confirmed against bank statements.

### List View
- Shows payments where `verifier IS NULL`
- Filter by: facility, date, payment method, amount range

### Actions
- **Verify**: sets `verifier = current_employee_id`
- **Reject**: flag for investigation via `incidence` log

---

## 10. Commissions

**Current system**: `ReportsController.CommissionsBySalesPerson` (report, ticket, CSV); `CommissionsController` (no working screen)
**SystemObject**: `CommissionsBySalesPerson` (112)
**API (spec 025)**: `/api/v1/commissions/...`

### Purpose
Agents earn a share of the sales they bring in, by product category. Commissions are paid by month.

### Settings (tables shared with the current system)
- **Labels** (`commission`): a category with a rate, e.g. CONCRETOS 2.5%, TABIQUES 4%, FERRETERÍA 4%, ARMEX 0.2%.
- **Product labels** (`commission_product`): one label per product. The current system can add a missing label but never change one; the API can change it.
- **Agents** (`commission_agent`): the employees who earn commissions, each with a **scheme** (API only; see below).
- **Participation types** (`commission_participation`): 1 CLIENTE VITALICIO, 2 CANALIZACIÓN, 3 ATENCIÓN EN CAMPO.
- **Agent rates** (`commission_salesperson`): an agent's share for each (label, participation). Today it is 100% as CLIENTE VITALICIO and 50% as ATENCIÓN EN CAMPO.

The current system has no screen for these: they are edited in the database. The API maintains them (`/commissions/labels`, `/participations`, `/agents`, `/agent-rates`, `/product-labels`), and each change writes an `incidence` (source 12).

### How a line earns (both systems)
Each line of a completed, non-cancelled order is credited by comparing the **order's** salesperson (osp) with the **customer's** (csp):
- **CLIENTE VITALICIO:** csp = osp, and that agent earns at their rate.
- Otherwise, osp earns as **CANALIZACIÓN** and csp as **ATENCIÓN EN CAMPO**, each at their own rate.
- Only agents earn. A customer with no salesperson matches no branch, so its orders earn nothing.

The line amount is the price after discount, tax included, times the quantity. The rate is the label's rate × the agent's share, and the commission is the amount × the rate, rounded to cents. An unlabelled product, or a missing rate row, earns 0 and still lists, so that someone labels it.

### The current system: a live report
- **No periods.** A period is whatever date range the user types. The report recomputes from live sales on every run, and nothing is ever closed or marked paid.
- **Which orders:** paid orders whose `modification_time` falls in the range.
- **Reassignment rewrites history:** csp is read **at report time**, so reassigning a customer re-credits every past period. A rate change rewrites the past too.
- **Refunds** are negative lines:
  - in the month the order is paid, for refunds dated up to that month's end;
  - in the refund's month, for orders paid earlier.

  Before mbe PR #58 (September 2026), a refund booked before its order was paid was charged twice, which over-debited agents $23,196.76 over 15 months.
- **Output:** the report lists the lines, the agent ticket (`PrintCommissionTicket`) sums them by label, and `DownloadCSV` exports every agent's lines.
- **Access:** a login only. SystemObject 112 controls the menu entry.
- **`commissions_history`** is a stale snapshot (May–September 2025) that nothing reads or writes.

### The new MBE: periods with stable history (spec 025)
- **Monthly periods.** A month moves from open to closed to paid.
  - **Closing:** allowed only after the month ends, and only if the previous month is closed. The first close starts the chain, and months before it stay open and are computed live.
  - **Reopening:** only the latest closed, unpaid month.
  - **Paying:** a paid month is final.
- **Stable history.** Closing stores every line with its agent, participation, rates, amounts, and customer, product and label names copied. Reassigning a customer, changing a rate or relabelling a product never changes a closed month. Once a line has been stored, its agents and rates stay those stored, so a later refund is charged to **the agent who was paid**, at the rate that was paid.
- **Paid date.** An order belongs to the month of its `balance_zeroed_time`, set once when payments bring its balance to zero. The current system stamps it in every payment path, and since spec 025 so does the API. A refund belongs to the month of its `date`, set on completion.
- **Two schemes, chosen per agent:**
  - **Scheme 1, fully paid** (the default, and the current system's rule): a line earns its full commission, less anything already stored, in the month its order is paid. An order whose payment was reversed before its month closed is picked up by the month it is paid again.
  - **Scheme 2, partial payments:** a line earns in proportion to what has been paid, as of each month's end:

    `ROUND(full commission × min(applied ÷ (order total − refunds), 1), 2) − what earlier months stored`

    Example: an order of $10,000 with one line at 1% ($100), paid $4,000 in March, $3,000 returned in April, and $3,000 paid in May. March earns $40; April earns $17.14 for the share and −$30 for the refund; May earns $42.86. The total is $70, the commission on the $7,000 net sale. A reversed payment comes back as a negative amount in the next month.
  - **Both schemes:** a refund is a negative line, deducted once. No line ever earns more than its full commission across all months, even across a scheme change.
- **Access.** Every route requires SystemObject 112: Read to view, Create/Update/Delete to change settings, Update to close, reopen or pay.
- **Reading.** `GET /commissions/periods/{year}/{month}?salesperson=` returns the lines and the totals per agent and label, with `final` true once closed. `GET /commissions/periods` lists the months closed so far.
