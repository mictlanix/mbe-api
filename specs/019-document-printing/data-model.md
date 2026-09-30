# Data Model: Document Printing

**No schema change.** The feature adds no table, no column and no migration. The documents read existing rows. It adds one setting and three in-memory structures: the render contexts and the cash-cut figures.

## Setting

| Setting | Type | Default |
|---|---|---|
| `promissory_note_template` | `str` | Legacy `Web.config:14`, rewritten with `{customer}`, `{balance}`, `{due_date}` and `{issuer}` in place of `{0}`, `{1:c}`, `{2:d}` and `{3}` |

## Rows read

| Document | Rows | Via |
|---|---|---|
| All | `Facility` → `Address`, `TaxpayerIssuer` (name, RFC), logo file in `settings.images_dir` | FK ints |
| Sale ticket, order document | `SalesOrder`, and its lines, totals and balance | `sales_order_service.get_order` + `attach_derived` (unchanged) |
| | `Customer.name`; `SalesOrder.customer_name` when `customer == settings.default_customer_id` | |
| | `Employee` salesperson: `first_name last_name` (pre-payment ticket, order document) or `nickname` (final receipt), as in legacy | |
| | `SalesOrderPayment` ⋈ `CustomerPayment` ⋈ `PaymentMethodOption` (via `CustomerPayment.payment_charge`), non-cancelled applications only | `customer_payment_service.list_order_applications` (which includes cancelled ones), plus the option's `name` and `display_on_ticket` |
| | `CustomerRefund` where `sales_order = id`, completed and not cancelled | direct `select` |
| | `CreditNote` where `sales_order = id` | direct `select` |
| Order document only | `Contact.name`, `Address` (ship-to), `promise_date`, `comment` | FK ints |
| Cash cut | `CashSession` → `CashDrawer` (name, facility), cashier `Employee` | `cash_session_service.get_session` + `attach_derived` |
| | `CustomerPayment` where `cash_session = id`, plus `SalesOrderPayment.amount_change` | `cut_figures` (new) |
| | `ExpenseVoucher` where `cash_session = id`, completed and not cancelled; totals = Σ `ExpenseVoucherDetail.amount` | `cut_figures` (new) |
| | `CashCount` rows of the session: STARTING_CASH and COUNTED_CASH | `cut_figures` (new) |

## CashCutFigures (new, computed)

Returned by `cash_session_service.cut_figures(db, session_id)`. Every amount is a `Decimal` rounded to cents.

| Field | Rule (ports `CashCountReport.cs`) |
|---|---|
| `sales_by_method: list[(method, amount)]` | **Sales**: payments with `amount > 0` and `payment_type ≠ CREDIT_NOTE`, any other type including 0 (N/A, pre-2025 legacy), grouped by `method`, each summing `amount − Σ amount_change` over its non-cancelled applications |
| `sales_total` | Σ `sales_by_method` |
| `refunds_by_method: list[(method, amount)]` | **Refunds**: payments with `payment_type = CREDIT_NOTE` or `amount < 0`, grouped by `method`, each summing `abs(amount)` |
| `expenses_total` | Σ expense voucher totals (all counted as cash) |
| `starting_cash` | Σ `denomination × quantity`, STARTING_CASH |
| `cash_sales` | Cash-method sales, summing `amount − Σ amount_change` |
| `cash_refunds` | Cash-method refunds, summing `abs(amount)` |
| `cash_in_drawer` | `starting_cash + cash_sales − expenses_total − cash_refunds` |
| `counted_cash` | Σ `denomination × quantity`, COUNTED_CASH; 0 when there are no rows |
| `difference` | `abs(counted_cash − cash_in_drawer)` |
| `is_shortage` | `cash_in_drawer > counted_cash` |

Precondition: the session is closed (`end` is not null). Otherwise the endpoint returns 409 before this is called.

> **Why classify by sign and type** (probed read-only in `mbe_dev`, 2026-09-25). The shared database holds four conventions:
>
> | Convention | Rows | Period |
> |---|---|---|
> | Store-credit refund: CREDIT_NOTE, method N/A, positive, with a `credit_note` row | 860 | 2025-07 → 2026-07 (current legacy) |
> | Refund paid out: CREDIT_NOTE, cash/transfer/N/A, negative | 216 | 2025-04 → 2025-07 |
> | mbe-api cash payout: IMMEDIATE, cash, negative (`customer_refund_service.py:506-527`) | 0 so far | — |
> | Payment type 0 (N/A), positive tender | ~227k | 2012 → 2025-03 |
>
> Legacy's type-only filters handle only the first. They show zero sales for sessions before 2025-03, and they mis-sum the two negative conventions. Classifying by sign and type gives the correct drawer total under all four.
>
> Departure from legacy: legacy's "Ventas en Efectivo" includes cash credit-note payments and then subtracts them again as cash refunds, which nets them to zero in the drawer. Here they count only as refunds. For a negative cash credit note (money paid out), the drawer total therefore differs from legacy. Ours is lower by the amount paid out, which is what physically left the drawer. `mbe_dev` has no positive cash credit notes, so that case does not arise.

## Render contexts (new, in-memory)

Each is a plain dict passed to a Jinja template. Values are pre-formatted strings where the format is fixed (research R9), so templates stay logic-light.

- **Common header**: store name, RFC, address lines, logo `file://` URL or `None`, title.
- **Sale ticket**:
  - Folio, date, salesperson, customer (and the per-order name), terms, due date.
  - Lines: quantity, price, discount %, code, name, comment, line total.
  - Subtotal, discount, taxes, total, refunds, payment rows (label, amount), change, credit notes, paid/balance.
  - Flags: `completed`, `cancelled`, `is_credit`, `has_card_payment`, `on_delivery_payments`, `por_cobrar`.
  - Pagaré text, receipt message, cancellation time, barcode data URI.
- **Order document**:
  - Customer, contact, ship-to lines, comment, order id, salesperson, date, promise date, terms, due date.
  - Lines: quantity, code, name + comment, net price, amount.
  - Totals, amount in words, savings text.
- **Cash cut**: drawer, start, end, cashier, the `CashCutFigures` rows formatted, shortage/overage label, barcode data URI.

## Status → template

| Order state | `GET /sales-orders/{id}/ticket` renders |
|---|---|
| not completed (draft), cancelled or not | pre-payment ticket (`POS/Print`) |
| completed | final receipt (`Payments/Print`), with the stamp if cancelled |

## Labels (from `mbe/Resources/Resources.resx`)

| Key | Text |
|---|---|
| Serial | Folio |
| Date | Fecha |
| SalesPerson | Vendedor |
| Customer | Cliente |
| DueDate | Fecha de Vencimiento |
| PaymentTerms | Forma de Pago |
| Taxes | IVA |
| Subtotal | Subtotal |
| Discount | Descuento |
| Total | Total |
| Paid | Pagado |
| Balance | Saldo |
| Change | Cambio |
| Refund | Devolución |
| PointOfSale | Punto de Venta |
| SalesReceipt | Ticket de Venta |
| PaymentOnDelivery | Contraentrega |
| Cancelled | Cancelado |
| NotAPaymentReceipt | Este recibo no es un comprobante de pago. |
| CardPaymentNoteContent | Cobro con tarjeta de crédito o débito |
| Accept | Acepto |
| SalesOrder | Pedido |
| Contact | Contacto |
| ShipTo | Datos de Entrega |
| Comment | Comentario |
| PromiseDate | Fecha Promesa |
| Quantity | Cantidad |
| ProductCode | Código |
| ProductName | Producto |
| Price | Precio |
| Amount | Importe |
| SavingsOnSalesOrder | ¡Usted ahorró {0} en esta compra! |
| Title_CloseSession | Corte de Caja |
| Title_SessionInfo | Información de la Sesión |
| CashDrawer | Caja |
| StartDate | Fecha de Inicio |
| End | Fecha de Fin |
| Cashier | Cajero |
| Sales | Ventas |
| Refunds | Devoluciones |
| Expenses | Gastos |
| Title_CashMovements | Movimientos en Caja |
| StartingCash | Efectivo Inicial |
| CashSales | Ventas en Efectivo |
| Title_CashBalance | Balance de Caja |
| CountedCash | Efectivo Final |
| Shortage | Faltante |
| Overage | Sobrante |

"Por Cobrar : " is hard-coded in legacy (`Payments/Print.cshtml:189`) and stays as it is.

## Spanish names for enums (legacy `[Display]` attributes)

| Enum | Values |
|---|---|
| PaymentMethod | 0 N/A · 1 Efectivo · 2 Cheque · 3 Transferencia Electrónica · 4 T. de Crédito · 5 Monedero Electrónico · 6 Dinero Electrónico · 8 Vales de Despensa · 12 Dación · 27 A Satisfacción Del Acreedor · 28 T. de Débito · 29 Tarjeta de Servicio · 30 Aplicación de Anticipos · 99 Por Definir · 1001 FONACOT |
| PaymentTerms | 0 Contado · 1 Crédito |
| PaymentType | 1 Contado · 2 Pago de Crédito · 3 Pago Anticipado · 4 Nota de Crédito |

A code the table lacks prints as its number. Legacy rows can carry SAT codes the enum does not name.
