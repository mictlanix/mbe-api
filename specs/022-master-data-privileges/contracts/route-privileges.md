# Contract: Route Privileges

The required privilege for every route on the 11 routers. "Hosts" are the alternative grants accepted under FR-005, all with the Read right. Administrators bypass every row. A refusal is `403 {"detail": "Insufficient privileges"}`. An unauthenticated call is `401` and a pending password change is `403 password_change_required`, both unchanged.

Responses for allowed callers do not change (FR-008).

## Customers (2)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/customers` | CUSTOMERS Read | SALES_ORDERS, SALES_QUOTES, POS |
| POST | `/customers` | CUSTOMERS Create | — |
| GET | `/customers/{customer_id}` | CUSTOMERS Read | SALES_ORDERS, SALES_QUOTES, POS |
| PUT | `/customers/{customer_id}` | CUSTOMERS Update; for a link-only body, also SALES_ORDERS Update or SALES_QUOTES Update (see below) | — |
| DELETE | `/customers/{customer_id}` | CUSTOMERS Delete | — |
| GET | `/customers/{customer_id}/discounts` | CUSTOMERS Read | — |
| POST | `/customers/{customer_id}/discounts` | CUSTOMERS Update | — |
| PUT | `/customers/{customer_id}/discounts/{customer_discount_id}` | CUSTOMERS Update | — |
| DELETE | `/customers/{customer_id}/discounts/{customer_discount_id}` | CUSTOMERS Update | — |

**Link-only body**: after dropping `null` values, the body holds no key other than `addresses` and `contacts`. Example: `{"addresses": [12, 40]}`. Body `{"addresses": [12], "name": "X"}` requires CUSTOMERS Update.

## Employees (6)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/employees` | EMPLOYEES Read | SALES_ORDERS, SALES_QUOTES, POS, CUSTOMERS, VEHICLE_OPERATORS |
| POST | `/employees` | EMPLOYEES Create | — |
| GET | `/employees/{employee_id}` | EMPLOYEES Read | SALES_ORDERS, SALES_QUOTES, POS, CUSTOMERS, VEHICLE_OPERATORS |
| PUT | `/employees/{employee_id}` | EMPLOYEES Update | — |
| DELETE | `/employees/{employee_id}` | EMPLOYEES Delete | — |

## Exchange rates (43)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/exchange-rates` | EXCHANGE_RATES Read | — |
| POST | `/exchange-rates` | EXCHANGE_RATES Create | — |
| GET | `/exchange-rates/{exchange_rate_id}` | EXCHANGE_RATES Read | — |
| PUT | `/exchange-rates/{exchange_rate_id}` | EXCHANGE_RATES Update | — |
| DELETE | `/exchange-rates/{exchange_rate_id}` | EXCHANGE_RATES Delete | — |

## Expenses (81)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/expenses` | EXPENSES Read | — |
| POST | `/expenses` | EXPENSES Create | — |
| GET | `/expenses/{expense_id}` | EXPENSES Read | — |
| PUT | `/expenses/{expense_id}` | EXPENSES Update | — |
| DELETE | `/expenses/{expense_id}` | EXPENSES Delete | — |

## Labels (1)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/labels` | LABELS Read | PRODUCTS, PRICING |
| POST | `/labels` | LABELS Create | — |
| GET | `/labels/{label_id}` | LABELS Read | PRODUCTS, PRICING |
| PUT | `/labels/{label_id}` | LABELS Update | — |
| DELETE | `/labels/{label_id}` | LABELS Delete | — |

## Payment method options (84)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/payment-method-options` | PAYMENT_METHOD_OPTIONS Read | SALES_ORDERS, POS, CUSTOMER_PAYMENTS |
| POST | `/payment-method-options` | PAYMENT_METHOD_OPTIONS Create | — |
| GET | `/payment-method-options/{payment_method_option_id}` | PAYMENT_METHOD_OPTIONS Read | SALES_ORDERS, POS, CUSTOMER_PAYMENTS |
| PUT | `/payment-method-options/{payment_method_option_id}` | PAYMENT_METHOD_OPTIONS Update | — |
| DELETE | `/payment-method-options/{payment_method_option_id}` | PAYMENT_METHOD_OPTIONS Delete | — |

## Price lists (5)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/price-lists` | PRICE_LISTS Read | CUSTOMERS, PRICING |
| POST | `/price-lists` | PRICE_LISTS Create | — |
| GET | `/price-lists/{price_list_id}` | PRICE_LISTS Read | CUSTOMERS, PRICING |
| PUT | `/price-lists/{price_list_id}` | PRICE_LISTS Update | — |
| DELETE | `/price-lists/{price_list_id}` | PRICE_LISTS Delete | — |
| GET | `/price-lists/{price_list_id}/delete/preview` | PRICE_LISTS Read | — |

## Suppliers (3)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/suppliers` | SUPPLIERS Read | PRODUCTS, PRICING |
| POST | `/suppliers` | SUPPLIERS Create | — |
| GET | `/suppliers/{supplier_id}` | SUPPLIERS Read | PRODUCTS, PRICING |
| PUT | `/suppliers/{supplier_id}` | SUPPLIERS Update | — |
| DELETE | `/suppliers/{supplier_id}` | SUPPLIERS Delete | — |
| GET | `/suppliers/{supplier_id}/agreements` | SUPPLIERS Read | — |
| POST | `/suppliers/{supplier_id}/agreements` | SUPPLIERS Update | — |
| PUT | `/suppliers/{supplier_id}/agreements/{supplier_agreement_id}` | SUPPLIERS Update | — |
| DELETE | `/suppliers/{supplier_id}/agreements/{supplier_agreement_id}` | SUPPLIERS Update | — |

## Taxpayer recipients (54)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/taxpayer-recipients` | TAXPAYER_RECIPIENTS Read | SALES_ORDERS, CUSTOMERS |
| POST | `/taxpayer-recipients` | TAXPAYER_RECIPIENTS Create | — |
| GET | `/taxpayer-recipients/{rfc}` | TAXPAYER_RECIPIENTS Read | SALES_ORDERS, CUSTOMERS |
| PUT | `/taxpayer-recipients/{rfc}` | TAXPAYER_RECIPIENTS Update | — |
| DELETE | `/taxpayer-recipients/{rfc}` | TAXPAYER_RECIPIENTS Delete | — |

## Vehicle operators (89)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/vehicle-operators` | VEHICLE_OPERATORS Read | — |
| POST | `/vehicle-operators` | VEHICLE_OPERATORS Create | — |
| GET | `/vehicle-operators/{vehicle_operator_id}` | VEHICLE_OPERATORS Read | — |
| PUT | `/vehicle-operators/{vehicle_operator_id}` | VEHICLE_OPERATORS Update | — |
| DELETE | `/vehicle-operators/{vehicle_operator_id}` | VEHICLE_OPERATORS Delete | — |
| POST | `/vehicle-operators/{vehicle_operator_id}/license-photo` | VEHICLE_OPERATORS Update | — |
| GET | `/vehicle-operators/{vehicle_operator_id}/license-photo` | VEHICLE_OPERATORS Read | — |
| DELETE | `/vehicle-operators/{vehicle_operator_id}/license-photo` | VEHICLE_OPERATORS Update | — |

## Vehicles (88)

| Method | Path | Requires | Hosts |
|---|---|---|---|
| GET | `/vehicles` | VEHICLE Read | — |
| POST | `/vehicles` | VEHICLE Create | — |
| GET | `/vehicles/{vehicle_id}` | VEHICLE Read | — |
| PUT | `/vehicles/{vehicle_id}` | VEHICLE Update | — |
| DELETE | `/vehicles/{vehicle_id}` | VEHICLE Delete | — |

Total: 68 routes.
