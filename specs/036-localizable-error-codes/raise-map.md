# Raise map

Every raise site and the code it takes, drafted per area during planning (research R10) and consolidated. The renames from consolidation are already applied in the rows: the open-session-required code is `cash_session.not_open`, the in-transit location code is `facility.no_in_transit_location`, both unsupported-status-filter codes are `status_filter.unsupported`, and inventory stock is `inventory_document.insufficient_stock`. `inventory_transfers.py:77` and `supplier_payment_service.py:332` take `facility.required`. Line numbers are as of 6a1fd4b. `contracts/error-codes.md` is the authority for names, statuses and params; where this map disagrees, the contract wins.

## Catalog A: error codes for master data, auth, pricing, sales, cash/payments, AR/AP

Catalog A has 240 raise rows (one per raise or per call site; the two image upload routers are listed once, in catalog B). Helper lines replaced by call-site rows: `account_email.py:27` (_unprocessable), `commission_service.py:459` (_conflict), `commission_service.py:647` (_not_found, via _get_or_404), `price_list_service.py:36` (assert_not_cost_list), `product_price_service.py:162` (_assert_none_missing). Rows for `customer_service.py:214/254` and `product_price_service.py:112/145/218/269` are call sites of assert_not_cost_list that raises.tsv does not list (they are in my files).

| file:line | status | today's detail | code | params | notes |
|---|---|---|---|---|---|
| app/api/v1/endpoints/accounts_payable.py:118 | 503 | 'Email is not configured' | email.not_configured | - |  |
| app/api/v1/endpoints/accounts_receivable.py:49 | 403 | "Seeing another facility's credits needs SearchCreditsFromAllStores" | facility.other_facility | resource: receivable, required: [{system_object: 101, right: 2}] | 'receivable' is not a table: the AR credit view (documents owed). Requested `facility` is another facility's. |
| app/api/v1/endpoints/accounts_receivable.py:146 | 503 | 'Email is not configured' | email.not_configured | - |  |
| app/api/v1/endpoints/addresses.py:49 | 404 | 'Address not found' | resource.not_found | resource: address, id: int |  |
| app/api/v1/endpoints/addresses.py:62 | 404 | 'Address not found' | resource.not_found | resource: address, id: int |  |
| app/api/v1/endpoints/addresses.py:75 | 404 | 'Address not found' | resource.not_found | resource: address, id: int |  |
| app/api/v1/endpoints/auth.py:25 | 401 | 'Invalid username or password' | auth.invalid_credentials | - |  |
| app/api/v1/endpoints/auth.py:43 | 404 | 'User not found' | resource.not_found | resource: user | No id: caller's own profile (edge case). |
| app/api/v1/endpoints/auth.py:59 | 400 | 'Invalid or expired recovery token' | auth.recovery_token_invalid | - | One exception object raised at 3 sites in confirm_recovery (wrong type, no sub, JWTError); one situation, one row. |
| app/api/v1/endpoints/auth.py:74 | 404 | 'User not found' | resource.not_found | resource: user | No id: the id comes from the signed token, not the request. |
| app/api/v1/endpoints/auth.py:88 | 404 | 'User not found' | resource.not_found | resource: user | No id: caller's own account. |
| app/api/v1/endpoints/auth.py:93 | 400 | 'Incorrect current password' | auth.incorrect_current_password | - | Old password failed verification on change-password. |
| app/api/v1/endpoints/bank_accounts.py:47 | 404 | 'Bank account not found' | resource.not_found | resource: bank_account, id: int |  |
| app/api/v1/endpoints/bank_accounts.py:62 | 404 | 'Bank account not found' | resource.not_found | resource: bank_account, id: int |  |
| app/api/v1/endpoints/bank_accounts.py:77 | 404 | 'Bank account not found' | resource.not_found | resource: bank_account, id: int |  |
| app/api/v1/endpoints/cash_drawers.py:49 | 404 | 'Cash drawer not found' | resource.not_found | resource: cash_drawer, id: int |  |
| app/api/v1/endpoints/cash_drawers.py:64 | 404 | 'Cash drawer not found' | resource.not_found | resource: cash_drawer, id: int |  |
| app/api/v1/endpoints/cash_drawers.py:79 | 404 | 'Cash drawer not found' | resource.not_found | resource: cash_drawer, id: int |  |
| app/api/v1/endpoints/cash_sessions.py:91 | 404 | 'Cash session not found' | resource.not_found | resource: cash_session, id: int |  |
| app/api/v1/endpoints/cash_sessions.py:105 | 404 | 'Cash session not found' | resource.not_found | resource: cash_session, id: int |  |
| app/api/v1/endpoints/cash_sessions.py:107 | 409 | 'Cash session is not closed' | cash_session.not_closed | - |  |
| app/api/v1/endpoints/cash_sessions.py:125 | 404 | 'Cash session not found' | resource.not_found | resource: cash_session, id: int |  |
| app/api/v1/endpoints/contacts.py:45 | 404 | 'Contact not found' | resource.not_found | resource: contact, id: int |  |
| app/api/v1/endpoints/contacts.py:58 | 404 | 'Contact not found' | resource.not_found | resource: contact, id: int |  |
| app/api/v1/endpoints/contacts.py:71 | 404 | 'Contact not found' | resource.not_found | resource: contact, id: int |  |
| app/api/v1/endpoints/credit_notes.py:50 | 403 | "Another facility's credit notes require PaymentsEditor read" | facility.other_facility | resource: credit_note, required: [{system_object: 100, right: 2}] |  |
| app/api/v1/endpoints/credit_notes.py:78 | 404 | 'Credit note not found' | resource.not_found | resource: credit_note, id: int | Also answers when the note is another facility's (404 hides existence, by design); same code. |
| app/api/v1/endpoints/customer_payments.py:35 | 404 | 'Payment not found' | resource.not_found | resource: customer_payment, id: int |  |
| app/api/v1/endpoints/customer_payments.py:50 | 404 | 'Payment not found' | resource.not_found | resource: customer_payment, id: int |  |
| app/api/v1/endpoints/customer_payments.py:80 | 403 | "Another facility's payments require PaymentsEditor read" | facility.other_facility | resource: customer_payment, required: [{system_object: 100, right: 2}] |  |
| app/api/v1/endpoints/customer_payments.py:221 | 409 | f'A {payment_type.name} payment has no ticket; only CREDIT_PAYMENT ... | payment.no_ticket | payment_type: enum:PaymentType | 409 as today. Only CREDIT_PAYMENT and PAYMENT_IN_ADVANCE have a ticket. |
| app/api/v1/endpoints/customer_payments.py:273 | 404 | 'Application not found' | resource.not_found | resource: sales_order_payment, id: int | A payment application; table is sales_order_payment. id = application_id. |
| app/api/v1/endpoints/customer_refunds.py:29 | 404 | 'Refund not found' | resource.not_found | resource: customer_refund, id: int |  |
| app/api/v1/endpoints/customer_refunds.py:87 | 404 | 'Line not found' | resource.not_found | resource: customer_refund_line, id: int | id = line_id |
| app/api/v1/endpoints/customers.py:83 | 404 | 'Customer not found' | resource.not_found | resource: customer, id: int |  |
| app/api/v1/endpoints/customers.py:103 | 403 | 'Insufficient privileges' | auth.forbidden | required: the `grants` list as [{system_object, right}] (CUSTOMERS/UPDATE, plus SALES_ORDERS/UPDATE and SALES_QUOTES/UPDATE when only addresses and contacts are sent) | Inline grants check; any one grant opens the route. |
| app/api/v1/endpoints/customers.py:108 | 404 | 'Customer not found' | resource.not_found | resource: customer, id: int |  |
| app/api/v1/endpoints/customers.py:121 | 404 | 'Customer not found' | resource.not_found | resource: customer, id: int |  |
| app/api/v1/endpoints/customers.py:127 | 404 | 'Customer not found' | resource.not_found | resource: customer, id: int |  |
| app/api/v1/endpoints/customers.py:134 | 404 | 'Discount not found' | resource.not_found | resource: customer_discount, id: int | id = customer_discount_id |
| app/api/v1/endpoints/employees.py:62 | 404 | 'Employee not found' | resource.not_found | resource: employee, id: int |  |
| app/api/v1/endpoints/employees.py:75 | 404 | 'Employee not found' | resource.not_found | resource: employee, id: int |  |
| app/api/v1/endpoints/employees.py:88 | 404 | 'Employee not found' | resource.not_found | resource: employee, id: int |  |
| app/api/v1/endpoints/exchange_rates.py:56 | 404 | 'Exchange rate not found' | resource.not_found | resource: exchange_rate, id: int |  |
| app/api/v1/endpoints/exchange_rates.py:69 | 404 | 'Exchange rate not found' | resource.not_found | resource: exchange_rate, id: int |  |
| app/api/v1/endpoints/exchange_rates.py:82 | 404 | 'Exchange rate not found' | resource.not_found | resource: exchange_rate, id: int |  |
| app/api/v1/endpoints/facilities.py:50 | 404 | 'Facility not found' | resource.not_found | resource: facility, id: int |  |
| app/api/v1/endpoints/facilities.py:68 | 404 | 'Facility not found' | resource.not_found | resource: facility, id: int |  |
| app/api/v1/endpoints/facilities.py:81 | 404 | 'Facility not found' | resource.not_found | resource: facility, id: int |  |
| app/api/v1/endpoints/facilities.py:94 | 404 | 'Facility not found' | resource.not_found | resource: facility, id: int |  |
| app/api/v1/endpoints/labels.py:54 | 404 | 'Label not found' | resource.not_found | resource: label, id: int |  |
| app/api/v1/endpoints/labels.py:67 | 404 | 'Label not found' | resource.not_found | resource: label, id: int |  |
| app/api/v1/endpoints/labels.py:80 | 404 | 'Label not found' | resource.not_found | resource: label, id: int |  |
| app/api/v1/endpoints/payment_method_options.py:67 | 404 | 'Payment method option not found' | resource.not_found | resource: payment_method_option, id: int |  |
| app/api/v1/endpoints/payment_method_options.py:84 | 404 | 'Payment method option not found' | resource.not_found | resource: payment_method_option, id: int |  |
| app/api/v1/endpoints/payment_method_options.py:101 | 404 | 'Payment method option not found' | resource.not_found | resource: payment_method_option, id: int |  |
| app/api/v1/endpoints/points_of_sale.py:56 | 404 | 'Point of sale not found' | resource.not_found | resource: point_of_sale, id: int |  |
| app/api/v1/endpoints/points_of_sale.py:71 | 404 | 'Point of sale not found' | resource.not_found | resource: point_of_sale, id: int |  |
| app/api/v1/endpoints/points_of_sale.py:86 | 404 | 'Point of sale not found' | resource.not_found | resource: point_of_sale, id: int |  |
| app/api/v1/endpoints/price_lists.py:63 | 404 | 'Price list not found' | resource.not_found | resource: price_list, id: int |  |
| app/api/v1/endpoints/price_lists.py:76 | 404 | 'Price list not found' | resource.not_found | resource: price_list, id: int |  |
| app/api/v1/endpoints/price_lists.py:92 | 404 | 'Price list not found' | resource.not_found | resource: price_list, id: int |  |
| app/api/v1/endpoints/price_lists.py:105 | 404 | 'Price list not found' | resource.not_found | resource: price_list, id: int |  |
| app/api/v1/endpoints/product_prices.py:72 | 404 | 'Product price not found' | resource.not_found | resource: product_price, id: int |  |
| app/api/v1/endpoints/product_prices.py:85 | 404 | 'Product price not found' | resource.not_found | resource: product_price, id: int |  |
| app/api/v1/endpoints/product_prices.py:98 | 404 | 'Product price not found' | resource.not_found | resource: product_price, id: int |  |
| app/api/v1/endpoints/products.py:193 | 404 | 'Product not found' | resource.not_found | resource: product, id: int |  |
| app/api/v1/endpoints/products.py:215 | 404 | 'Product not found' | resource.not_found | resource: product, id: int |  |
| app/api/v1/endpoints/products.py:230 | 404 | 'Product not found' | resource.not_found | resource: product, id: int |  |
| app/api/v1/endpoints/products.py:245 | 404 | 'Product not found' | resource.not_found | resource: product, id: int |  |
| app/api/v1/endpoints/sales_orders.py:33 | 404 | 'Sales order not found' | resource.not_found | resource: sales_order, id: int |  |
| app/api/v1/endpoints/sales_orders.py:47 | 404 | 'Sales order not found' | resource.not_found | resource: sales_order, id: int |  |
| app/api/v1/endpoints/sales_orders.py:83 | 403 | "Another facility's sales orders require SearchCreditsFromAllStores... | facility.other_facility | resource: sales_order, required: [{system_object: 101, right: 2}] |  |
| app/api/v1/endpoints/sales_orders.py:121 | 422 | 'Either pattern or products is required' | product.lookup_criteria_required | - | Lookup needs `pattern` or `products`. |
| app/api/v1/endpoints/sales_orders.py:126 | 422 | f'At most {PRODUCT_LOOKUP_MAX_IDS} products per lookup' | product.lookup_too_many | max: int | max = PRODUCT_LOOKUP_MAX_IDS |
| app/api/v1/endpoints/sales_orders.py:254 | 404 | 'Line not found' | resource.not_found | resource: sales_order_line, id: int | id = line_id |
| app/api/v1/endpoints/sales_orders.py:269 | 404 | 'Line not found' | resource.not_found | resource: sales_order_line, id: int | id = line_id |
| app/api/v1/endpoints/sales_quotes.py:34 | 404 | 'Sales quote not found' | resource.not_found | resource: sales_quote, id: int |  |
| app/api/v1/endpoints/sales_quotes.py:109 | 503 | 'Email is not configured' | email.not_configured | - |  |
| app/api/v1/endpoints/sales_quotes.py:206 | 404 | 'Line not found' | resource.not_found | resource: sales_quote_line, id: int | id = line_id |
| app/api/v1/endpoints/sales_quotes.py:221 | 404 | 'Line not found' | resource.not_found | resource: sales_quote_line, id: int | id = line_id |
| app/api/v1/endpoints/sat_catalogs.py:46 | 404 | 'Not found' | resource.not_found | resource: <sat_* table name of the requested catalog, e.g. sat_postal_code>, id: str | Resource is dynamic: `config.model.__tablename__` (sat_cfdi_usage, sat_country, sat_currency, sat_postal_code, sat_product_service, sat_reason_cancellation, sat_tax_regime, sat_unit_of_measurement). id = raw key string. |
| app/api/v1/endpoints/suppliers.py:63 | 404 | 'Supplier not found' | resource.not_found | resource: supplier, id: int |  |
| app/api/v1/endpoints/suppliers.py:76 | 404 | 'Supplier not found' | resource.not_found | resource: supplier, id: int |  |
| app/api/v1/endpoints/suppliers.py:89 | 404 | 'Supplier not found' | resource.not_found | resource: supplier, id: int |  |
| app/api/v1/endpoints/suppliers.py:95 | 404 | 'Supplier not found' | resource.not_found | resource: supplier, id: int |  |
| app/api/v1/endpoints/suppliers.py:102 | 404 | 'Agreement not found' | resource.not_found | resource: supplier_agreement, id: int | id = agreement_id |
| app/api/v1/endpoints/taxpayer_issuers.py:54 | 404 | 'Taxpayer issuer not found' | resource.not_found | resource: taxpayer_issuer, id: str |  |
| app/api/v1/endpoints/taxpayer_issuers.py:69 | 404 | 'Taxpayer issuer not found' | resource.not_found | resource: taxpayer_issuer, id: str |  |
| app/api/v1/endpoints/taxpayer_issuers.py:84 | 404 | 'Taxpayer issuer not found' | resource.not_found | resource: taxpayer_issuer, id: str |  |
| app/api/v1/endpoints/taxpayer_issuers.py:95 | 404 | 'Taxpayer issuer not found' | resource.not_found | resource: taxpayer_issuer, id: str |  |
| app/api/v1/endpoints/taxpayer_issuers.py:103 | 404 | 'Batch not found' | resource.not_found | resource: taxpayer_batch, id: int | id = batch_id |
| app/api/v1/endpoints/taxpayer_recipients.py:60 | 404 | 'Taxpayer recipient not found' | resource.not_found | resource: taxpayer_recipient, id: str |  |
| app/api/v1/endpoints/taxpayer_recipients.py:75 | 404 | 'Taxpayer recipient not found' | resource.not_found | resource: taxpayer_recipient, id: str |  |
| app/api/v1/endpoints/taxpayer_recipients.py:90 | 404 | 'Taxpayer recipient not found' | resource.not_found | resource: taxpayer_recipient, id: str |  |
| app/api/v1/endpoints/user_profiles.py:99 | 404 | 'User not found' | resource.not_found | resource: user, id: int | id = user_id |
| app/api/v1/endpoints/user_profiles.py:108 | 404 | 'Profile not found' | resource.not_found | resource: user_profile, id: int | id = profile_id |
| app/api/v1/endpoints/users.py:39 | 409 | 'Username already exists' | resource.already_exists | resource: user, field: username |  |
| app/api/v1/endpoints/users.py:50 | 404 | 'Profile not found' | resource.not_found | resource: user_profile, id: int | id = data.profile_id (body field, not URL) |
| app/api/v1/endpoints/users.py:67 | 404 | 'User not found' | resource.not_found | resource: user, id: int | id = user_id |
| app/api/v1/endpoints/users.py:80 | 404 | 'User not found' | resource.not_found | resource: user, id: int | id = user_id |
| app/api/v1/endpoints/users.py:93 | 404 | 'User not found' | resource.not_found | resource: user, id: int | id = user_id |
| app/api/v1/endpoints/users.py:106 | 404 | 'User not found' | resource.not_found | resource: user, id: int | id = user_id |
| app/api/v1/endpoints/warehouses.py:28 | 404 | 'Warehouse not found' | resource.not_found | resource: warehouse, id: int | id = warehouse_id |
| app/api/v1/endpoints/warehouses.py:32 | 403 | 'In-transit locations are managed by the system' | warehouse.in_transit_managed | - | 403 as today: in-transit warehouses are system-managed (spec 028-ish). Not a privilege refusal, so not auth.forbidden. |
| app/core/deps.py:44 | 401 | 'Invalid or expired credentials' | auth.not_authenticated | - | get_signed_in_user: token missing/invalid/expired. |
| app/core/deps.py:84 | 403 | 'password_change_required' | auth.password_change_required | - | detail stays 'password_change_required'. |
| app/core/deps.py:92 | 403 | 'Administrator access required' | auth.administrator_required | - | Its own code (analysis A1): administrator-only is a different situation from a missing privilege. |
| app/core/deps.py:117 | 403 | 'Insufficient privileges' | auth.forbidden | required: [{system_object, right}] (the one grant of require_privilege) | |
| app/core/deps.py:167 | 403 | 'Insufficient privileges' | auth.forbidden | required: [{system_object, right}] (every grant of require_any_privilege) | Any one grant opens the route. |
| app/services/account_email.py:40 | 422 | 'Choose at least one contact or type an address' | email.no_recipients | - | CALL SITE of _unprocessable (helper at line 27) in merge_recipients; raises.tsv has only line 27. |
| app/services/account_email.py:42 | 422 | f'At most {MAX_RECIPIENTS} recipients' | email.too_many_recipients | max: int | CALL SITE of _unprocessable (line 27). max = MAX_RECIPIENTS |
| app/services/account_email.py:70 | 422 | f'Contact {contact_id} is not one of this party' | email.contact_not_of_party | contact_id: int | CALL SITE of _unprocessable (line 27). 422 as today. |
| app/services/account_email.py:73 | 422 | f'Contact {contact_id} has no email address' | email.contact_no_address | contact_id: int | CALL SITE of _unprocessable (line 27). |
| app/services/account_email.py:116 | 404 | f'{side.party_label} not found' | resource.not_found | resource: <customer\|supplier>, id: int | resource = side.party_label lowercased ('customer' or 'supplier'); id = party_id |
| app/services/account_email.py:137 | 502 | f'The email could not be sent: {exc}' | email.send_failed | - | Exception text stays in detail only. |
| app/services/accounts_service.py:551 | 404 | f'{side.party_label} not found' | resource.not_found | resource: <customer\|supplier>, id: int | resource = side.party_label lowercased; id = party_id |
| app/services/cash_session_service.py:82 | 422 | 'No cash drawer is configured for your user; set one or supply it e... | user.no_cash_drawer | - |  |
| app/services/cash_session_service.py:148 | 404 | 'Cash session not found' | resource.not_found | resource: cash_session, id: int | id = cash_session_id |
| app/services/cash_session_service.py:150 | 409 | 'The cash session is closed' | cash_session.closed | - |  |
| app/services/cash_session_service.py:417 | 404 | 'Cash drawer not found' | resource.not_found | resource: cash_drawer, id: int | id = drawer_id |
| app/services/cash_session_service.py:420 | 409 | 'That cash drawer already has an open session' | cash_session.drawer_busy | cash_drawer_id: int |  |
| app/services/cash_session_service.py:425 | 409 | 'You already have an open session; close it before opening another' | cash_session.user_busy | cash_session_id: int | The blocking session's id is not held at the site (open_session_for_cashier's result is only tested); needs `existing = await ...`. Spec says only id the site has in hand: flag. |
| app/services/cash_session_service.py:463 | 409 | 'Session is already closed' | cash_session.already_closed | - |  |
| app/services/commission_service.py:511 | 409 | 'Period has not ended' | commission_period.not_ended | year: int, month: int | CALL SITE of _conflict (helper line 459). |
| app/services/commission_service.py:514 | 409 | 'Period is already paid' | commission_period.already_paid | year: int, month: int | CALL SITE of _conflict (459); closing a paid period. |
| app/services/commission_service.py:516 | 409 | 'Period is already closed' | commission_period.already_closed | year: int, month: int | CALL SITE of _conflict (459) |
| app/services/commission_service.py:520 | 409 | 'A later period is closed' | commission_period.later_closed | year: int, month: int | CALL SITE of _conflict (459); close path |
| app/services/commission_service.py:521 | 409 | 'The previous period is not closed' | commission_period.previous_not_closed | year: int, month: int | CALL SITE of _conflict (459) |
| app/services/commission_service.py:547 | 409 | 'A paid period cannot be reopened' | commission_period.paid_cannot_reopen | year: int, month: int | CALL SITE of _conflict (459). Distinct from already_paid (reopen vs close/pay). |
| app/services/commission_service.py:549 | 409 | 'Period is not closed' | commission_period.not_closed | year: int, month: int | CALL SITE of _conflict (459); reopen. Shares code with line 573. |
| app/services/commission_service.py:552 | 409 | 'A later period is closed' | commission_period.later_closed | year: int, month: int | CALL SITE of _conflict (459); reopen path, same situation as 520 |
| app/services/commission_service.py:571 | 409 | 'Period is already paid' | commission_period.already_paid | year: int, month: int | CALL SITE of _conflict (459); paying a paid period. |
| app/services/commission_service.py:573 | 409 | 'Close the period first' | commission_period.not_closed | year: int, month: int | CALL SITE of _conflict (459); 'Close the period first' = period not closed, shares with 549. |
| app/services/commission_service.py:684 | 404 | 'Commission label not found' | resource.not_found | resource: commission, id: int | CALL SITE of _get_or_404 -> _not_found (helper line 647). 'Commission label'. id = commission_id |
| app/services/commission_service.py:703 | 404 | 'Commission label not found' | resource.not_found | resource: commission, id: int | CALL SITE of _get_or_404 (647). 'Commission label'. id = commission_id |
| app/services/commission_service.py:737 | 404 | 'Commission participation not found' | resource.not_found | resource: commission_participation, id: int | CALL SITE of _get_or_404 (647). id = participation_id |
| app/services/commission_service.py:755 | 404 | 'Commission participation not found' | resource.not_found | resource: commission_participation, id: int | CALL SITE of _get_or_404 (647). id = participation_id |
| app/services/commission_service.py:786 | 404 | 'Employee not found' | resource.not_found | resource: employee, id: int | CALL SITE of _not_found (647). id = data.employee |
| app/services/commission_service.py:788 | 409 | 'Employee is already an agent' | resource.already_exists | resource: commission_agent, field: employee |  |
| app/services/commission_service.py:807 | 404 | 'Commission agent not found' | resource.not_found | resource: commission_agent, id: int | CALL SITE of _get_or_404 (647). id = employee |
| app/services/commission_service.py:821 | 404 | 'Commission agent not found' | resource.not_found | resource: commission_agent, id: int | CALL SITE of _get_or_404 (647). id = employee |
| app/services/commission_service.py:843 | 422 | 'Salesperson is not an agent' | commission_agent.salesperson_not_agent | salesperson_id: int | 422 as today; 'Salesperson is not an agent'. id = data.salesperson |
| app/services/commission_service.py:846 | 404 | 'Commission label not found' | resource.not_found | resource: commission, id: int | CALL SITE of _get_or_404 (647). 'Commission label'. id = data.commission |
| app/services/commission_service.py:847 | 404 | 'Commission participation not found' | resource.not_found | resource: commission_participation, id: int | CALL SITE of _get_or_404 (647). id = data.commission_participation |
| app/services/commission_service.py:856 | 409 | 'Rate already exists for this agent, label and participation' | resource.already_exists | resource: commission_salesperson, field: salesperson,commission,commission_participation | Composite uniqueness: comma-joined field names. |
| app/services/commission_service.py:883 | 404 | 'Commission agent rate not found' | resource.not_found | resource: commission_salesperson, id: int | CALL SITE of _get_or_404 (647). 'Commission agent rate'. id = rate_id |
| app/services/commission_service.py:897 | 404 | 'Commission agent rate not found' | resource.not_found | resource: commission_salesperson, id: int | CALL SITE of _get_or_404 (647). id = rate_id |
| app/services/commission_service.py:959 | 404 | 'Product not found' | resource.not_found | resource: product, id: int | CALL SITE of _get_or_404 (647). id = product |
| app/services/commission_service.py:960 | 404 | 'Commission label not found' | resource.not_found | resource: commission, id: int | CALL SITE of _get_or_404 (647). 'Commission label'. id = commission |
| app/services/commission_service.py:985 | 404 | 'Product commission label not found' | resource.not_found | resource: commission_product, id: int | CALL SITE of _not_found (647). 'Product commission label' (keyed by product). id = product |
| app/services/customer_discount_service.py:76 | 422 | 'Product not found' | customer_discount.product_not_found | product_id: int | 422 today, so it can not be resource.not_found (404). Flag. |
| app/services/customer_discount_service.py:88 | 409 | 'Customer already has a discount for this product' | resource.already_exists | resource: customer_discount, field: product |  |
| app/services/customer_payment_service.py:60 | 409 | 'A cancelled order cannot be paid' | payment.order_cancelled | - | Paying a cancelled sales order. |
| app/services/customer_payment_service.py:64 | 409 | 'Only a completed order can be paid; confirm it first' | payment.order_not_completed | - | Paying an unconfirmed order. |
| app/services/customer_payment_service.py:73 | 422 | 'Payment currency does not match the order currency' | payment.currency_mismatch | payment_currency: enum:CurrencyCode, order_currency: enum:CurrencyCode |  |
| app/services/customer_payment_service.py:81 | 422 | f'Amount {amount} exceeds the unapplied balance of {unapplied}' | payment.exceeds_unapplied | amount: decimal, unapplied: decimal |  |
| app/services/customer_payment_service.py:89 | 422 | 'Payment belongs to a different customer than the order' | payment.customer_mismatch | payment_customer_id: int, order_customer_id: int |  |
| app/services/customer_payment_service.py:99 | 422 | f'Amount {amount} exceeds the order balance of {balance}' | payment.exceeds_order_balance | amount: decimal, balance: decimal |  |
| app/services/customer_payment_service.py:109 | 409 | 'Sales order is already settled' | payment.order_settled | - | Nothing owed on the order. |
| app/services/customer_payment_service.py:243 | 404 | 'Customer not found' | resource.not_found | resource: customer, id: int | id = data.customer |
| app/services/customer_payment_service.py:454 | 404 | 'Sales order not found' | resource.not_found | resource: sales_order, id: int | id = data.sales_order |
| app/services/customer_payment_service.py:504 | 409 | 'Application is already reversed' | payment.application_reversed | - |  |
| app/services/customer_payment_service.py:577 | 409 | 'Payment is already verified' | payment.already_verified | - |  |
| app/services/customer_refund_service.py:59 | 409 | 'Order is not completed; only a completed, paid order can be refunded' | refund.order_not_completed | - |  |
| app/services/customer_refund_service.py:64 | 409 | 'Order is not paid; an unpaid or partly-paid order is unwound by ca... | refund.order_not_paid | - |  |
| app/services/customer_refund_service.py:98 | 409 | f'Units of line {product_code} are on invoice {invoice}; cancel the... | refund.line_invoiced | product_code: str, invoice: str | invoice is the live invoice's folio string (may be None at the site); 'cancel the invoice to refund them'. Params are keys the client knows. |
| app/services/customer_refund_service.py:109 | 422 | f'Return quantity {requested} exceeds the refundable quantity {refu... | refund.quantity_exceeds_refundable | requested: decimal, refundable: decimal |  |
| app/services/customer_refund_service.py:319 | 404 | 'Sales order not found' | resource.not_found | resource: sales_order, id: int | id = sales_order_id |
| app/services/customer_refund_service.py:342 | 409 | 'No refundable items remain on this order' | refund.nothing_refundable | - |  |
| app/services/customer_refund_service.py:494 | 409 | 'An open cash session is required to confirm a refund' | cash_session.not_open | - | Caller has no open cash session; shares the code with any other 'open session required' raise (other workers' files). Flag. |
| app/services/customer_refund_service.py:532 | 404 | 'Sales order not found' | resource.not_found | resource: sales_order, id: int | id = refund.sales_order |
| app/services/customer_refund_service.py:567 | 409 | 'Refund has no lines with a returnable quantity' | refund.no_returnable_lines | - |  |
| app/services/customer_refund_service.py:698 | 409 | 'A completed refund cannot be cancelled' | refund.completed_cannot_cancel | - |  |
| app/services/customer_refund_service.py:703 | 409 | 'Refund is already cancelled' | refund.already_cancelled | - |  |
| app/services/customer_service.py:214 | 400 | COST_LIST_NOT_ASSIGNABLE | price_list.cost_not_assignable | - | CALL SITE of assert_not_cost_list (price_list_service:36). NOT in raises.tsv (helper only); listed here for the implementer. |
| app/services/customer_service.py:254 | 400 | COST_LIST_NOT_ASSIGNABLE | price_list.cost_not_assignable | - | CALL SITE of assert_not_cost_list (36). Same note. |
| app/services/customer_service.py:278 | 409 | 'Cannot delete the system default customer' | customer.default_not_deletable | - |  |
| app/services/documents.py:32 | 404 | 'Facility not found' | resource.not_found | resource: facility, id: int | id = the `facility` argument |
| app/services/documents.py:65 | 409 | 'Document is cancelled and can no longer be edited' | document.not_editable | status: enum:DocumentStatus | status = cancelled |
| app/services/documents.py:71 | 409 | 'Document is completed and can no longer be edited' | document.not_editable | status: enum:DocumentStatus | status = completed |
| app/services/exchange_rate_service.py:58 | 409 | 'Exchange rate for this date and currency pair already exists' | resource.already_exists | resource: exchange_rate, field: date,base,target | Composite uniqueness. |
| app/services/point_sale_service.py:75 | 404 | 'Warehouse not found' | resource.not_found | resource: warehouse, id: int | id = warehouse |
| app/services/point_sale_service.py:77 | 422 | f'Warehouse {warehouse} does not belong to facility {facility}' | point_of_sale.warehouse_facility_mismatch | warehouse_id: int, facility_id: int | 422 as today. Not facility.other_facility (that is a 403 privilege refusal). |
| app/services/price_list_service.py:148 | 400 | 'The cost price list cannot be deleted' | price_list.cost_not_deletable | - | CALL SITE of assert_not_cost_list (helper line 36, 400): delete. |
| app/services/price_list_service.py:150 | 400 | COST_LIST_NOT_ASSIGNABLE | price_list.cost_not_assignable | - | CALL SITE of assert_not_cost_list (36): replacement is the cost list. |
| app/services/price_list_service.py:152 | 400 | 'Cannot replace a price list with itself' | price_list.replace_with_itself | - | 400 as today. |
| app/services/price_list_service.py:157 | 404 | 'Replacement price list not found' | resource.not_found | resource: price_list, id: int | id = replacement_id; 'Replacement price list not found' |
| app/services/product_price_service.py:108 | 404 | 'Product not found' | resource.not_found | resource: product, id: int | id = data.product |
| app/services/product_price_service.py:111 | 404 | 'Price list not found' | resource.not_found | resource: price_list, id: int | id = data.price_list |
| app/services/product_price_service.py:112 | 400 | COST_LIST_READ_ONLY | price_list.cost_read_only | - | CALL SITE of assert_not_cost_list (36). Not in raises.tsv. |
| app/services/product_price_service.py:123 | 409 | 'Price already exists for this product and price list' | resource.already_exists | resource: product_price, field: product,price_list | Composite. |
| app/services/product_price_service.py:145 | 400 | COST_LIST_READ_ONLY | price_list.cost_read_only | - | CALL SITE of assert_not_cost_list (36). Not in raises.tsv. |
| app/services/product_price_service.py:172 | 404 | 'Product not found: 1, 2' | product_price.references_not_found | resource: product, ids: list[int] | CALL SITE of _assert_none_missing (helper line 159; raises at 162). 404 as today. resource.not_found carries one id, this carries every missing id, so a new code. Flag. |
| app/services/product_price_service.py:182 | 404 | 'Price list not found: 1, 2' | product_price.references_not_found | resource: price_list, ids: list[int] | CALL SITE of _assert_none_missing (helper 159; raise at 162). Same code as 172. |
| app/services/product_price_service.py:210 | 400 | f'Duplicate entry for product {item.product} on price list {item.pr... | product_price.duplicate_entry | product_id: int, price_list_id: int | 400 today, so not resource.already_exists (409). Flag. |
| app/services/product_price_service.py:218 | 400 | COST_LIST_READ_ONLY | price_list.cost_read_only | - | CALL SITE of assert_not_cost_list (36). Not in raises.tsv. |
| app/services/product_price_service.py:269 | 400 | COST_LIST_READ_ONLY | price_list.cost_read_only | - | CALL SITE of assert_not_cost_list (36). Not in raises.tsv. |
| app/services/product_service.py:393 | 409 | 'Product code already exists' | resource.already_exists | resource: product, field: code |  |
| app/services/product_service.py:558 | 400 | 'Cannot merge a product with itself' | product.merge_with_itself | - |  |
| app/services/product_service.py:566 | 404 | 'Canonical product not found' | resource.not_found | resource: product, id: int | id = req.product_id ('Canonical product') |
| app/services/product_service.py:570 | 404 | 'Duplicate product not found' | resource.not_found | resource: product, id: int | id = req.duplicate_id ('Duplicate product') |
| app/services/references.py:86 | 409 | f'Still referenced by {shown} — remove those records first' | resource.still_referenced | references: [{resource: str, count: int}], more: int | Generic helper assert_not_referenced; resource names map from table names (FR-008). Call sites listed in the helper table below. |
| app/services/references.py:111 | 409 | f'{label} already exists' | resource.already_exists | resource: str, field: str | Generic helper assert_unique; resource/field per call site in the helper table below. |
| app/services/sales_order_service.py:77 | 422 | f'Quantity is below the product minimum of {min_order_qty}' | sales_order.below_min_quantity | min_order_qty: int, product_id: int | assert_quantity_allowed(quantity, *, min_order_qty) has no product id in hand; callers do, so the function needs a product_id arg. Flag. |
| app/services/sales_order_service.py:160 | 409 | 'Order is already cancelled' | sales_order.already_cancelled | - |  |
| app/services/sales_order_service.py:165 | 409 | 'A paid order cannot be cancelled — refund it instead' | sales_order.paid_cannot_cancel | - |  |
| app/services/sales_order_service.py:172 | 409 | f'Order still has payment applications ({ids}); reverse them before... | sales_order.has_payment_applications | application_ids: list[int] |  |
| app/services/sales_order_service.py:187 | 422 | 'No point of sale is configured for your user; set one or supply it... | user.no_point_of_sale | - |  |
| app/services/sales_order_service.py:197 | 422 | 'No facility is configured for your user' | user.no_facility | - |  |
| app/services/sales_order_service.py:720 | 404 | 'Customer not found' | resource.not_found | resource: customer, id: int | id = customer_id |
| app/services/sales_order_service.py:784 | 422 | f'Customer is over their credit limit: {debt + adding:.2f} of {limi... | customer.credit_limit_exceeded | customer_id: int, outstanding: decimal, limit: decimal | outstanding = debt + adding. Currency not in hand. |
| app/services/sales_order_service.py:820 | 422 | f'Customer is on credit hold: {expired} overdue credit order(s) mus... | customer.credit_hold | customer_id: int, overdue_count: int, action: confirm |  |
| app/services/sales_order_service.py:852 | 422 | f'Customer must settle their credit before buying: debt {debt:.2f} ... | customer.credit_limit_reached | customer_id: int, outstanding: decimal, limit: decimal | Cash order refused because debt >= limit (_assert_cash_allowed). Distinct from credit_limit_exceeded (debt + this order > limit). Flag. |
| app/services/sales_order_service.py:884 | 422 | 'The default walk-in customer cannot buy on credit' | customer.walk_in_no_credit | - |  |
| app/services/sales_order_service.py:889 | 422 | 'Customer has no credit limit' | customer.no_credit_limit | customer_id: int |  |
| app/services/sales_order_service.py:896 | 422 | f'Customer is on credit hold: {expired} overdue credit order(s) mus... | customer.credit_hold | customer_id: int, overdue_count: int, action: raise |  |
| app/services/sales_order_service.py:1267 | 404 | 'Product not found' | resource.not_found | resource: product, id: int | id = data.product |
| app/services/sales_order_service.py:1381 | 404 | 'Line not found' | resource.not_found | resource: sales_order_line, id: int | id = line_id |
| app/services/sales_order_service.py:1420 | 404 | 'Sales order not found' | resource.not_found | resource: sales_order, id: int | id = sales_order_id |
| app/services/sales_order_service.py:1456 | 409 | {'message': 'Order is missing delivery details', 'missing': missing} | sales_order.missing_delivery_details | - | dict detail kept ({message, missing}); no params. |
| app/services/sales_order_service.py:1463 | 409 | 'Cannot confirm an order with no lines' | sales_order.no_lines | - |  |
| app/services/sales_order_service.py:1469 | 409 | {'message': 'Order has lines priced at zero', 'lines': offenders} | sales_order.zero_priced_lines | - | dict detail kept ({message, lines}); no params. |
| app/services/sales_order_service.py:1486 | 409 | {'message': 'Insufficient stock', 'lines': problems} | sales_order.insufficient_stock | - | dict detail kept ({message, lines}); no params. |
| app/services/sales_quote_service.py:62 | 409 | 'A cancelled quote cannot be converted' | quote.cancelled | - |  |
| app/services/sales_quote_service.py:66 | 409 | 'Only a confirmed quote can be converted; confirm it first' | quote.not_confirmed | - |  |
| app/services/sales_quote_service.py:71 | 409 | 'Quote has expired and cannot be converted; duplicate it to re-quote' | quote.expired | - |  |
| app/services/sales_quote_service.py:172 | 404 | 'Customer not found' | resource.not_found | resource: customer, id: int | id = customer_id |
| app/services/sales_quote_service.py:181 | 422 | 'No facility is configured for your user' | user.no_facility | - |  |
| app/services/sales_quote_service.py:373 | 404 | 'Product not found' | resource.not_found | resource: product, id: int | id = data.product |
| app/services/sales_quote_service.py:482 | 409 | 'Quote is already cancelled' | quote.already_cancelled | - | Cancelling a cancelled quote; differs from quote.cancelled (convert). |
| app/services/sales_quote_service.py:537 | 409 | 'Only a confirmed quote can be emailed' | quote.not_confirmed | - | Condition is `not completed or cancelled`, so a cancelled quote also answers this code today (same message). |
| app/services/sales_quote_service.py:557 | 502 | f'The email could not be sent: {exc}' | email.send_failed | - |  |
| app/services/sales_quote_service.py:650 | 422 | 'No point of sale is configured for your user; set one or supply it... | user.no_point_of_sale | - |  |
| app/services/supplier_agreement_service.py:23 | 422 | 'End must not be before start' | supplier_agreement.end_before_start | start: date, end: date | 422 as today. |
| app/services/taxpayer_batch_service.py:93 | 409 | 'The issuer already has this series' | resource.already_exists | resource: taxpayer_batch, field: batch |  |
| app/services/taxpayer_batch_service.py:116 | 404 | 'Taxpayer issuer not found' | resource.not_found | resource: taxpayer_issuer, id: str | id = taxpayer (rfc) |
| app/services/taxpayer_batch_service.py:133 | 409 | 'The series is in use: only its template can change' | taxpayer_batch.in_use | action: update | Shares code with 148; action: update\|delete (plain string the client knows). |
| app/services/taxpayer_batch_service.py:148 | 409 | 'The series is in use and cannot be deleted' | taxpayer_batch.in_use | action: delete |  |
| app/services/user_profile_service.py:116 | 409 | 'Profile is not active' | user_profile.not_active | profile_id: int | 409 as today. |

### New codes introduced

| code | status | params | situation |
|---|---|---|---|
| auth.incorrect_current_password | 400 | - | The old password given to change-password did not verify. |
| auth.recovery_token_invalid | 400 | - | The password-recovery token is wrong type, has no subject, or is expired or malformed. |
| cash_session.not_open | 409 | - | The cashier has no open cash session and the action needs one (refund confirm). |
| commission_agent.salesperson_not_agent | 422 | salesperson_id: int | A commission rate names a salesperson who is not a commission agent. |
| commission_period.already_closed | 409 | year: int, month: int | Closing a commission period that is already closed. |
| commission_period.already_paid | 409 | year: int, month: int | Closing or paying a commission period that is already paid. |
| commission_period.later_closed | 409 | year: int, month: int | A later commission period is already closed, so this one cannot be closed or reopened. |
| commission_period.not_closed | 409 | year: int, month: int | The commission period is not closed (reopen or pay needs a closed one). |
| commission_period.not_ended | 409 | year: int, month: int | The commission period has not ended yet, so it cannot be closed. |
| commission_period.paid_cannot_reopen | 409 | year: int, month: int | A paid commission period is final and cannot be reopened. |
| commission_period.previous_not_closed | 409 | year: int, month: int | The previous commission period is not closed; months close in sequence. |
| customer.credit_limit_reached | 422 | customer_id: int, outstanding: decimal, limit: decimal | A credit customer already owes at or over their limit, so a cash order is refused too. |
| customer.default_not_deletable | 409 | - | The system default (walk-in) customer cannot be deleted. |
| customer_discount.product_not_found | 422 | product_id: int | A customer discount names a product that does not exist (422, not 404). |
| email.contact_no_address | 422 | contact_id: int | A chosen contact has no email address. |
| email.contact_not_of_party | 422 | contact_id: int | A chosen contact does not belong to the customer or supplier being emailed. |
| email.no_recipients | 422 | - | The statement email has no recipient after merging contacts and typed addresses. |
| email.too_many_recipients | 422 | max: int | The statement email has more than the maximum recipients. |
| payment.already_verified | 409 | - | The payment has already been verified. |
| payment.application_reversed | 409 | - | The payment application was already reversed. |
| payment.currency_mismatch | 422 | payment_currency: enum:CurrencyCode, order_currency: enum:CurrencyCode | The payment currency differs from the order currency. |
| payment.customer_mismatch | 422 | payment_customer_id: int, order_customer_id: int | The payment belongs to a different customer than the order. |
| payment.exceeds_order_balance | 422 | amount: decimal, balance: decimal | An application amount is more than the order's remaining balance. |
| payment.exceeds_unapplied | 422 | amount: decimal, unapplied: decimal | An application amount is more than the payment's unapplied balance. |
| payment.no_ticket | 409 | payment_type: enum:PaymentType | The payment type has no ticket to print (only credit payment and payment in advance do). |
| payment.order_cancelled | 409 | - | A cancelled sales order cannot be paid. |
| payment.order_not_completed | 409 | - | Only a completed (confirmed) sales order can be paid. |
| payment.order_settled | 409 | - | Nothing is owed on the sales order, so it takes no application. |
| point_of_sale.warehouse_facility_mismatch | 422 | warehouse_id: int, facility_id: int | The point of sale's warehouse is in a different facility than the point of sale. |
| price_list.cost_not_assignable | 400 | - | The cost price list cannot be assigned to a customer or used as a replacement. |
| price_list.cost_not_deletable | 400 | - | The cost price list cannot be deleted. |
| price_list.cost_read_only | 400 | - | The cost price list is read-only for price writes. |
| price_list.replace_with_itself | 400 | - | A price list cannot be replaced by itself on delete. |
| product.lookup_criteria_required | 422 | - | The product lookup needs a pattern or a list of product ids. |
| product.lookup_too_many | 422 | max: int | The product lookup names more ids than the maximum. |
| product.merge_with_itself | 400 | - | A product cannot be merged into itself. |
| product_price.duplicate_entry | 400 | product_id: int, price_list_id: int | A bulk price write names the same (product, price list) cell twice. |
| product_price.references_not_found | 404 | resource: str (product|price_list), ids: list[int] | A bulk price write names products or price lists that do not exist (params.ids lists every missing id; resource is product or price_list). |
| quote.already_cancelled | 409 | - | Cancelling a quote that is already cancelled. |
| refund.already_cancelled | 409 | - | The refund is already cancelled. |
| refund.completed_cannot_cancel | 409 | - | A completed refund is final and cannot be cancelled. |
| refund.line_invoiced | 409 | product_code: str, invoice: str | The units of a line are on a live invoice and must be un-invoiced before refunding. |
| refund.no_returnable_lines | 409 | - | The refund has no line with a returnable quantity. |
| refund.nothing_refundable | 409 | - | No item on the order remains to be refunded. |
| refund.order_not_completed | 409 | - | The order is not completed, so it cannot be refunded. |
| refund.order_not_paid | 409 | - | The order is not paid; unpaid orders are cancelled, not refunded. |
| refund.quantity_exceeds_refundable | 422 | requested: decimal, refundable: decimal | The requested return quantity is more than the refundable quantity. |
| supplier_agreement.end_before_start | 422 | start: date, end: date | The agreement's end date is before its start date. |
| taxpayer_batch.in_use | 409 | action: str (update|delete) | The series (batch) has issued documents; params.action is update or delete. |
| user_profile.not_active | 409 | profile_id: int | An inactive user profile cannot be applied to a user. |
| warehouse.in_transit_managed | 403 | - | In-transit warehouses are system-managed and cannot be edited by the user. |

### Helper call sites (generic codes)

`references.py:86` (assert_not_referenced), resource.still_referenced, resource per call site (the code builds the list from table names; mapping must cover every table):
supplier_service:278 supplier; bank_account_service:83 bank_account; facility_service:161 (transit warehouse) warehouse, :166 facility; taxpayer_recipient_service:132 taxpayer_recipient; contact_service:104 contact; taxpayer_issuer_service:109 taxpayer_issuer; payment_method_option_service:111 payment_method_option; customer_service:282 customer; address_service:114 address; user_profile_service:99 user_profile; product_service:526 product; vehicle_service:89 vehicle; vehicle_operator_service:140 vehicle_operator; warehouse_service:184 warehouse; cash_drawer_service:106 cash_drawer; user_service:255 user; commission_service:704 commission, :758 commission_participation, :822 commission_agent, :898 commission_salesperson, :986 commission_product; employee_service:104 employee; label_service:54 label; point_sale_service:136 point_of_sale; price_list_service:167 price_list.
(`resource` here names the thing being deleted; the referenced list uses public names of the referencing tables.)

`references.py:111` (assert_unique), resource.already_exists, `resource, field` per call site:
user_profile_service:124 user_profile, name; supplier_service:227 and :250 supplier, code; vehicle_service:47 and :65 vehicle, license_plate; warehouse_service:140 and :159 warehouse, code; cash_drawer_service:62 and :81 cash_drawer, code; point_sale_service:84 and :103 point_of_sale, code.

### Odd

- `auth.forbidden.required` for multi-grant checks (`deps.py:167` require_any_privilege, `customers.py:103`): **Resolved (analysis A1):** `required` is a list of `{system_object, right}` raw ints, the shape `/user-profiles` uses and mbe-ui's `SystemObject(int)` and `AccessRight` read. `deps.py:92` takes its own code, `auth.administrator_required`.
- `cash_session_service.py:425`: `cash_session.user_busy {cash_session_id}` needs the blocking session id; the site only tests `open_session_for_cashier(...) is not None`, so the code must keep the result.
- `sales_order_service.py:77`: `sales_order.below_min_quantity {product_id}`: `assert_quantity_allowed` has no product id; callers do, so a product_id argument is needed.
- `customer_discount_service.py:76`: 422 "Product not found". A 404 code cannot carry 422, so `customer_discount.product_not_found`. Status looks odd (404 elsewhere) but unchanged.
- `product_price_service.py:210`: 400 duplicate entry. Status 400 vs the 409 of resource.already_exists, so a separate code `product_price.duplicate_entry`. Same for the price-list 400s (`price_list.*`) which are 400 not 409/422.
- `product_price_service.py:172/182` (via 162): need every missing id; resource.not_found has one, so `product_price.references_not_found`.
- `sales_order_service.py:852` (`customer.credit_limit_reached`) vs `:784` (`customer.credit_limit_exceeded`): near-identical params, different situation (cash order at or over limit vs credit order that would pass limit). Unsure whether to merge.
- `refund_service.py:494` -> `cash_session.not_open` is a new cash_session code; other workers' "open session required" raises (cash supplier payments, vouchers) should reuse it.
- `sales_quote_service.py:537` answers `quote.not_confirmed` for a cancelled quote too (condition `not completed or cancelled`).
- `point_sale_service.py:77` is a 422 about a warehouse in another facility: NOT `facility.other_facility` (that is the 403 privilege rule); code `point_of_sale.warehouse_facility_mismatch`.
- `warehouses.py:32` is a 403 not tied to privileges: `warehouse.in_transit_managed`; facility_service's in-transit guards (other worker) may need the same code.
- Composite uniqueness `field` values (`date,base,target`; `product,price_list`; `salesperson,commission,commission_participation`) are comma-joined API field names. **Resolved** (research R10).
- `accounts_receivable.py:49` resource `receivable` is not a table (introduced name); AP counterpart (if any) would use the same.
- `sat_catalogs.py:46`: resource is the catalog's table name (8 sat_* tables), chosen at request time.
- `credit_notes.py:78` answers 404 for another facility's note (existence hidden) via resource.not_found, not facility.other_facility.
- `customer_refund_service.py:98` `refund.line_invoiced.invoice`: a folio string (nullable at the site). **Resolved (analysis U5):** allowed, as an identifier like a non-numeric id; omitted when null.
- `customer_payments.py:273`: application's public resource `sales_order_payment` (table name).
- `auth.py:43/74/88` not_found for `user` with no `id` (own profile / token subject).
- `users.py:50` is a 404 for a body field `profile_id`, not a URL id.
- `customer_service.py:214/254`, `product_price_service.py:112/145/218/269` are call sites of assert_not_cost_list absent from raises.tsv (it lists only the helper line 36); also `commission_service.py:459/647`, `account_email.py:27`, `product_price_service.py:162` are helpers, replaced by call sites.
- `taxpayer_certificate_service`/`taxpayer_certificates` router are not mine (excluded as instructed). `inventory_documents` was not in my list either.
- Not user-facing: none found; every raise here is reachable from a request.
- Collision note: `scratchpad/mine.tsv` was overwritten by another worker; mine is `mine_A.tsv`.

## Catalog B: deliveries, itineraries, inventory, image uploads

# Catalog B: deliveries, itineraries, inventory, image uploads

| file:line | status | today's detail | code | params | notes |
|---|---|---|---|---|---|
| app/api/v1/endpoints/delivery_itineraries.py:68 | 404 | 'Stop not found' | resource.not_found | resource: delivery_itinerary_stop, id: stop_id |  |
| app/api/v1/endpoints/delivery_itineraries.py:60 | 404 | 'Itinerary not found' | resource.not_found | resource: delivery_itinerary, id: itinerary_id |  |
| app/api/v1/endpoints/delivery_itineraries.py:172 | 409 | 'Itinerary has not departed' | itinerary.not_departed | status: enum:ItineraryStatus | print guard, status is OPEN; shares code with :793 (close stop) |
| app/api/v1/endpoints/delivery_itineraries.py:176 | 409 | 'Itinerary is cancelled' | itinerary.cancelled |  | print guard |
| app/api/v1/endpoints/delivery_itineraries.py:373 | 422 | '`lines` must be a JSON array of per-line outcomes' | itinerary.stop_lines_malformed |  | form field `lines` is not valid JSON |
| app/api/v1/endpoints/delivery_itineraries.py:381 | 422 | str(e) | image.* |  | the image_service code raised (see image_service section) |
| app/api/v1/endpoints/delivery_orders.py:51 | 404 | 'Delivery order not found' | resource.not_found | resource: delivery_order, id: delivery_order_id |  |
| app/api/v1/endpoints/delivery_orders.py:290 | 409 | 'Delivery order is not approved' | delivery_order.not_approved | status: enum:DeliveryOrderStatus | print guard; status is DRAFT or PENDING_APPROVAL |
| app/api/v1/endpoints/delivery_orders.py:386 | 404 | 'This delivery order has no proof of delivery yet' | resource.not_found | resource: proof_of_delivery, id: delivery_order_id | id is the delivery order (it has no proof yet) |
| app/api/v1/endpoints/delivery_orders.py:392 | 404 | 'Proof not found' | resource.not_found | resource: proof_of_delivery, id: order.proof_of_delivery (the dangling proof id) |  |
| app/api/v1/endpoints/delivery_orders.py:426 | 404 | 'Proof image is missing from storage' | resource.not_found | resource: proof_of_delivery_image, id: delivery_order_id | file missing in storage; server inconsistency, see Odd. `proof_of_delivery_image` is not a table |
| app/api/v1/endpoints/delivery_orders.py:310 | 422 | 'Unknown warehouse' | delivery_order.unknown_warehouse | warehouse_id: int | status stays 422 though an unknown id is normally 404 elsewhere; query param `warehouse` of the ticket print |
| app/api/v1/endpoints/delivery_orders.py:315 | 422 | 'No line of this order comes from that warehouse' | delivery_order.no_lines_from_warehouse | warehouse_id: int | ticket print filtered by warehouse |
| app/api/v1/endpoints/delivery_orders.py:368 | 422 | str(e) | image.* |  | the image_service code raised (see image_service section) |
| app/api/v1/endpoints/inventory_counts.py:34 | 404 | 'Count not found' | resource.not_found | resource: inventory_count, id: count_id |  |
| app/api/v1/endpoints/inventory_issues.py:30 | 404 | 'Issue not found' | resource.not_found | resource: inventory_issue, id: issue_id |  |
| app/api/v1/endpoints/inventory_receipts.py:31 | 404 | 'Receipt not found' | resource.not_found | resource: inventory_receipt, id: receipt_id |  |
| app/api/v1/endpoints/inventory_transfers.py:77 | 422 | 'facility is required' | `facility.required` |  | no ?facility given and the user has none; reuses the fixed code, see Odd |
| app/api/v1/endpoints/inventory_transfers.py:108 | 404 | 'Transfer not found' | resource.not_found | resource: inventory_transfer, id: inventory_transfer_id |  |
| app/api/v1/endpoints/vehicle_operators.py:60 | 404 | 'Vehicle operator not found' | resource.not_found | resource: vehicle_operator, id: vehicle_operator_id |  |
| app/api/v1/endpoints/vehicle_operators.py:75 | 404 | 'Vehicle operator not found' | resource.not_found | resource: vehicle_operator, id: vehicle_operator_id |  |
| app/api/v1/endpoints/vehicle_operators.py:92 | 404 | 'Vehicle operator not found' | resource.not_found | resource: vehicle_operator, id: vehicle_operator_id |  |
| app/api/v1/endpoints/vehicle_operators.py:109 | 404 | 'Vehicle operator not found' | resource.not_found | resource: vehicle_operator, id: vehicle_operator_id |  |
| app/api/v1/endpoints/vehicle_operators.py:140 | 404 | 'Vehicle operator not found' | resource.not_found | resource: vehicle_operator, id: vehicle_operator_id |  |
| app/api/v1/endpoints/vehicle_operators.py:144 | 404 | 'License photo not found' | resource.not_found | resource: license_photo, id: vehicle_operator_id | `license_photo` is not a table; id is the vehicle operator |
| app/api/v1/endpoints/vehicle_operators.py:149 | 404 | 'License photo is missing from storage' | resource.not_found | resource: license_photo, id: vehicle_operator_id | file missing in storage; server inconsistency, see Odd |
| app/api/v1/endpoints/vehicle_operators.py:164 | 404 | 'Vehicle operator not found' | resource.not_found | resource: vehicle_operator, id: vehicle_operator_id |  |
| app/api/v1/endpoints/vehicle_operators.py:168 | 404 | 'License photo not found' | resource.not_found | resource: license_photo, id: vehicle_operator_id | delete when no photo is set |
| app/api/v1/endpoints/vehicle_operators.py:116 | 422 | str(exc) | image.* |  | the image_service code raised (see image_service section) |
| app/api/v1/endpoints/vehicles.py:53 | 404 | 'Vehicle not found' | resource.not_found | resource: vehicle, id: vehicle_id |  |
| app/api/v1/endpoints/vehicles.py:66 | 404 | 'Vehicle not found' | resource.not_found | resource: vehicle, id: vehicle_id |  |
| app/api/v1/endpoints/vehicles.py:79 | 404 | 'Vehicle not found' | resource.not_found | resource: vehicle, id: vehicle_id |  |
| app/services/delivery_events.py:58 | 409 | f'{from_status.name} is terminal; cannot move to {to_status.name}' | state.transition_invalid | resource: delivery_order, from: enum:DeliveryOrderStatus, to: enum:DeliveryOrderStatus | from is terminal |
| app/services/delivery_events.py:64 | 409 | f'Cannot move a delivery order from {from_status.name} to {to_status.name}' | state.transition_invalid | resource: delivery_order, from: enum:DeliveryOrderStatus, to: enum:DeliveryOrderStatus | move not in LEGAL |
| app/services/delivery_events.py:71 | 409 | f'{from_status.name} to {to_status.name} is only valid for a {required.name} order' | state.transition_invalid | resource: delivery_order, from, to, required: enum:FulfillmentType | restricted to one fulfilment type |
| app/services/delivery_events.py:91 | 422 | f'A reason is required to move an order to {to_status.name}' | delivery_order.reason_required | to: enum:DeliveryOrderStatus | landing on DRAFT/FAILED/CANCELLED without a reason |
| app/services/delivery_itinerary_service.py:136 | 409 | f'This itinerary is {ItineraryStatus(itinerary.status).name} and accepts no further changes' | itinerary.not_open | status: enum:ItineraryStatus |  |
| app/services/delivery_itinerary_service.py:360 | 404 | 'Delivery order not found' | resource.not_found | resource: delivery_order, id: delivery_order_id |  |
| app/services/delivery_itinerary_service.py:364 | 409 | 'A counter-pickup order is handed over in store, never loaded onto a trip' | delivery_order.pickup_not_loadable |  | counter pickup cannot ride a trip |
| app/services/delivery_itinerary_service.py:369 | 409 | f'Only an order in IN_PREPARATION can be loaded; this one is {S(order.status).name}' | delivery_order.not_in_preparation | status: enum:DeliveryOrderStatus | required status is IN_PREPARATION, constant |
| app/services/delivery_itinerary_service.py:406 | 404 | 'Delivery order line not found' | resource.not_found | resource: delivery_order_line, id: line_id (delivery_order_detail) |  |
| app/services/delivery_itinerary_service.py:429 | 422 | 'This line has nothing left to load' | delivery_order_line.nothing_to_load |  | requested <= 0 (incl. nothing remaining) |
| app/services/delivery_itinerary_service.py:434 | 422 | f'Only {remaining} is available on this line; {requested} was requested' | delivery_order_line.quantity_unavailable | available: decimal, requested: decimal | shared with :488 |
| app/services/delivery_itinerary_service.py:481 | 404 | 'Itinerary line not found' | resource.not_found | resource: delivery_itinerary_line, id: line_id |  |
| app/services/delivery_itinerary_service.py:488 | 422 | f'Only {headroom} is available on this line; {quantity} was requested' | delivery_order_line.quantity_unavailable | available: decimal, requested: decimal | available = headroom; shared with :434 |
| app/services/delivery_itinerary_service.py:507 | 404 | 'Itinerary line not found' | resource.not_found | resource: delivery_itinerary_line, id: line_id |  |
| app/services/delivery_itinerary_service.py:523 | 404 | 'Stop not found' | resource.not_found | resource: delivery_itinerary_stop, id: stop_id |  |
| app/services/delivery_itinerary_service.py:565 | 422 | f'Facility {named} has no in-transit location' | facility.no_in_transit_location | facility_ids: list[int] | several facilities possible here; shared with inventory_documents.py:190 |
| app/services/delivery_itinerary_service.py:600 | 409 | 'Nothing is committed to this itinerary' | itinerary.nothing_committed |  |  |
| app/services/delivery_itinerary_service.py:611 | 422 | {'message': 'Lines are committed beyond what remains', 'lines': overcommitted} | itinerary.lines_overcommitted |  | fixed code; dict detail kept, no params |
| app/services/delivery_itinerary_service.py:793 | 409 | 'Only a departed itinerary has stops to close' | itinerary.not_departed | status: enum:ItineraryStatus | close-stop guard; shared with delivery_itineraries.py:172 |
| app/services/delivery_itinerary_service.py:798 | 409 | 'This stop is already resolved' | itinerary.stop_already_resolved |  |  |
| app/services/delivery_itinerary_service.py:808 | 422 | 'Every line on the stop must be accounted for' | itinerary.stop_lines_incomplete |  | outcomes do not cover exactly the stop lines |
| app/services/delivery_itinerary_service.py:185 | 404 | 'Vehicle not found' | resource.not_found | resource: vehicle, id: vehicle |  |
| app/services/delivery_itinerary_service.py:196 | 409 | f'Vehicle {vehicle} already has an open itinerary ({existing[0]})' | vehicle.open_itinerary_exists | vehicle_id: int, itinerary_id: int |  |
| app/services/delivery_itinerary_service.py:817 | 422 | f'Line {line_id} was sent {entry.sent_quantity}; {delivered} was accepted' | itinerary.stop_delivered_exceeds_sent | line_id: int (itinerary line), sent: decimal, delivered: decimal |  |
| app/services/delivery_itinerary_service.py:822 | 422 | f'Line {line_id} fell short and needs a reason code' | itinerary.stop_shortfall_needs_reason | line_id: int (itinerary line) |  |
| app/services/delivery_order_service.py:55 | 409 | f'A delivery order in {S(order.status).name} can no longer be edited' | delivery_order.not_editable | status: enum:DeliveryOrderStatus | not documents.assert_editable: that one reads status cancelled/completed |
| app/services/delivery_order_service.py:174 | 422 | f'Facility {facility} has no warehouse to dispatch from' | facility.no_dispatch_warehouse | facility_id: int |  |
| app/services/delivery_order_service.py:222 | 422 | 'MIXED describes a sale, not a delivery order; raise one delivery and one pickup' | delivery_order.mixed_not_allowed |  |  |
| app/services/delivery_order_service.py:231 | 404 | 'Sales order not found' | resource.not_found | resource: sales_order, id: sales_order_id |  |
| app/services/delivery_order_service.py:233 | 409 | 'Only a completed, uncancelled sales order can be delivered' | delivery_order.sales_order_not_deliverable |  | sales order not completed or cancelled |
| app/services/delivery_order_service.py:245 | 422 | 'This sales order must be paid or on credit terms before it can be delivered' | delivery_order.sales_order_unpaid |  | only when the setting is on |
| app/services/delivery_order_service.py:278 | 409 | 'This sales order is already fully delivered' | delivery_order.sales_order_fully_delivered |  |  |
| app/services/delivery_order_service.py:565 | 422 | f'Line {item.sales_order_detail} is not a deliverable line of this customer' | delivery_order.line_not_deliverable | sales_order_line_id: int | shared with :95 (same situation: no such line / other customer / already covered) |
| app/services/delivery_order_service.py:587 | 409 | f'Line {item.sales_order_detail} is already on this delivery order as line {existing}' | delivery_order.line_already_present | sales_order_line_id: int, delivery_order_line_id: int |  |
| app/services/delivery_order_service.py:598 | 422 | f'The sales order line has {sales_line.quantity - elsewhere} left to deliver; {item.quantity} was requested' | delivery_order.quantity_exceeds_remaining | sales_order_line_id: int, remaining: decimal, requested: decimal | remaining = sales_line.quantity - elsewhere; shared with :103 and :644 |
| app/services/delivery_order_service.py:636 | 404 | 'Line not found' | resource.not_found | resource: delivery_order_line, id: line_id |  |
| app/services/delivery_order_service.py:662 | 404 | 'Line not found' | resource.not_found | resource: delivery_order_line, id: line_id |  |
| app/services/delivery_order_service.py:714 | 409 | 'Cannot confirm a delivery order with no lines' | delivery_order.no_lines |  | confirm with no lines |
| app/services/delivery_order_service.py:749 | 409 | f'Only an order awaiting approval can be approved; this one is {S(order.status).name}' | delivery_order.not_pending_approval | status: enum:DeliveryOrderStatus, action: approve | shared with :769 via param `action` |
| app/services/delivery_order_service.py:769 | 409 | f'Only an order awaiting approval can be rejected; this one is {S(order.status).name}' | delivery_order.not_pending_approval | status: enum:DeliveryOrderStatus, action: reject | shared with :749 |
| app/services/delivery_order_service.py:805 | 409 | f'Only a failed delivery can be re-queued; this one is {S(order.status).name}' | delivery_order.not_failed | status: enum:DeliveryOrderStatus | re-queue |
| app/services/delivery_order_service.py:851 | 422 | 'Proof of delivery needs the receiver name and the identification shown' | delivery_order.proof_incomplete |  | receiver name or identification blank |
| app/services/delivery_order_service.py:84 | 422 | f'Line {item.sales_order_detail} was requested more than once' | delivery_order.line_repeated | sales_order_line_id: int | same line requested twice |
| app/services/delivery_order_service.py:95 | 422 | f'Line {item.sales_order_detail} is not an undelivered line of this sales order' | delivery_order.line_not_deliverable | sales_order_line_id: int | shared with :565 |
| app/services/delivery_order_service.py:103 | 422 | f'Line {item.sales_order_detail} has {remaining} undelivered, {item.quantity} requested' | delivery_order.quantity_exceeds_remaining | sales_order_line_id: int, remaining: decimal, requested: decimal | shared with :598 |
| app/services/delivery_order_service.py:722 | 422 | f'Deliveries need {settings.min_span_hours_for_deliveries} hours notice; the earliest available date is {earliest:%Y-%m-%d %H:%M}' | delivery_order.insufficient_notice | min_hours: int, earliest: datetime | earliest is local time without zone; ISO 8601 |
| app/services/delivery_order_service.py:644 | 422 | f'The sales order line has {sales_line.quantity - elsewhere} left to deliver; {quantity} was requested' | delivery_order.quantity_exceeds_remaining | sales_order_line_id: int (line.sales_order_detail), remaining: decimal, requested: decimal | shared with :598 |
| app/services/incidences.py:35 | 422 | 'A reason is required and cannot be blank' | incidence.reason_required |  | raised from every caller of incidences.record; see Odd |
| app/services/inventory_count_service.py:509 | 409 | 'Count has no lines' | inventory_document.no_lines | resource: inventory_count | shared with inventory_documents.py:580, inventory_transfer_service.py:233 |
| app/services/inventory_documents.py:749 | 422 | 'status must be one of draft, completed, cancelled' | status_filter.unsupported | allowed: list[str] (draft, completed, cancelled) | shared with inventory_transfer_service.py:140 |
| app/services/inventory_documents.py:190 | 422 | f'Facility {document.facility} has no in-transit location' | facility.no_in_transit_location | facility_ids: list[int] (one id) | shared with delivery_itinerary_service.py:565 |
| app/services/inventory_documents.py:239 | 404 | f'{kind.label} not found' | resource.not_found | resource: inventory_receipt|inventory_issue|inventory_transfer|inventory_count (by kind), id: document_id | resource must come from the Kind (add a resource field next to label) |
| app/services/inventory_documents.py:353 | 403 | 'Document belongs to another facility' | facility.other_facility | resource: inventory_receipt|inventory_issue|inventory_transfer|inventory_count | assert_facility needs a resource argument; no privilege, so no `required` (administrator only). Also called by expense_vouchers.py:40, purchase_request_service.py (other catalogs) |
| app/services/inventory_documents.py:369 | 409 | f'Not enough stock of {product_row.code} in {warehouse_row.code}: {have} available, {need} required' | inventory_document.insufficient_stock | product_id: int, warehouse_id: int, available: decimal, required: decimal | first shortage only |
| app/services/inventory_documents.py:386 | 422 | 'The origin and destination warehouses must differ' | inventory_transfer.same_warehouse |  |  |
| app/services/inventory_documents.py:520 | 404 | 'Line not found' | resource.not_found | resource: inventory_receipt_line|inventory_issue_line|inventory_transfer_line|inventory_count_line (by kind), id: line_id |  |
| app/services/inventory_documents.py:482 | 404 | 'Product not found' | resource.not_found | resource: product, id: product |  |
| app/services/inventory_documents.py:484 | 422 | f'Product {row.code} is not stockable' | product.not_stockable | product_code: str | shared with :600, inventory_transfer_service.py:253 |
| app/services/inventory_documents.py:580 | 409 | f'{kind.label} has no lines' | inventory_document.no_lines | resource: inventory_receipt|inventory_issue|inventory_count (by kind) | shared with inventory_count_service.py:509 |
| app/services/inventory_documents.py:659 | 409 | 'A confirmed count cannot be cancelled' | inventory_count.confirmed_cannot_cancel |  | counts only; other kinds are cancellable when completed |
| app/services/inventory_documents.py:664 | 409 | f'{kind.label} is already cancelled' | inventory_document.already_cancelled | resource: inventory_receipt|inventory_issue|inventory_transfer|inventory_count |  |
| app/services/inventory_documents.py:343 | 409 | detail | lock.busy | resource: warehouse | busy_as_conflict default. The only caller passing another detail is delivery_order_service.py:693 (resource: facility). Other callers in purchase_*/sales_order_service share this raise |
| app/services/inventory_documents.py:595 | 422 | f'Line {line.product_code} has no quantity' | inventory_document.line_without_quantity | product_code: str | shared with inventory_transfer_service.py:248 |
| app/services/inventory_documents.py:600 | 422 | f'Product {products[line.product].code} is not stockable' | product.not_stockable | product_code: str | shared with :484 |
| app/services/inventory_receipt_service.py:94 | 409 | reason | inventory_receipt.complementary_unavailable | reason: str (no_purchase_order | purchase_order_cancelled | purchase_order_not_completed | purchase_order_fully_received) | _refuse(reason) call sites :301 (no_purchase_order) and :339 (any of the four); complementary_refusal must return the enum string instead of English. One code with a reason param (single user situation: no complementary receipt can be created); split into four codes if the UI wants distinct messages without branching |
| app/services/inventory_receipt_service.py:197 | 409 | "A purchase order's receipt keeps its warehouse" | inventory_receipt.warehouse_fixed |  | Found unmapped during implementation (T081): a new code, not a rename. |
| app/services/inventory_transfer_service.py:140 | 422 | 'status must be one of draft, in_transit, received, cancelled' | status_filter.unsupported | allowed: list[str] (draft, in_transit, received, cancelled) | shared with inventory_documents.py:749 |
| app/services/inventory_transfer_service.py:196 | 409 | 'Transfer is not in transit' | inventory_transfer.not_in_transit |  |  |
| app/services/inventory_transfer_service.py:77 | 422 | f'Line {line_id} is received more than once' | inventory_transfer.line_repeated | line_id: int | line received twice in one request |
| app/services/inventory_transfer_service.py:84 | 404 | f'Line {line_id} not found on this transfer' | resource.not_found | resource: inventory_transfer_line, id: line_id |  |
| app/services/inventory_transfer_service.py:90 | 409 | f'Line {line.product_code}: {quantity} received, {outstanding} outstanding' | inventory_transfer.over_received | product_code: str, received: decimal, outstanding: decimal |  |
| app/services/inventory_transfer_service.py:233 | 409 | 'Transfer has no lines' | inventory_document.no_lines | resource: inventory_transfer | shared |
| app/services/inventory_transfer_service.py:348 | 404 | 'Sales order not found' | resource.not_found | resource: sales_order, id: sales_order |  |
| app/services/inventory_transfer_service.py:352 | 409 | 'Sales order is not confirmed, or is cancelled' | sales_order.not_confirmed | sales_order_id: int | covers not-completed or cancelled; a sales-order catalog may already own this name, rename on merge |
| app/services/inventory_transfer_service.py:248 | 422 | f'Line {line.product_code} has no quantity' | inventory_document.line_without_quantity | product_code: str | shared with inventory_documents.py:595 |
| app/services/inventory_transfer_service.py:253 | 422 | f'Product {products[line.product].code} is not stockable' | product.not_stockable | product_code: str | shared |
| app/services/kardex_service.py:75 | 422 | 'The range ends before it starts' | kardex.range_reversed |  |  |
| app/services/kardex_service.py:80 | 422 | f'The range may not exceed {MAX_DAYS} days' | kardex.range_too_long | max_days: int |  |
| app/services/kardex_service.py:202 | 404 | 'Product not found' | resource.not_found | resource: product, id: product |  |
| app/services/kardex_service.py:204 | 404 | 'Warehouse not found' | resource.not_found | resource: warehouse, id: warehouse |  |
| app/services/stock_query_service.py:208 | 404 | 'Product not found' | resource.not_found | resource: product, id: product |  |
| app/services/warehouse_service.py:131 | 404 | 'Warehouse not found' | resource.not_found | resource: warehouse, id: warehouse_id |  |
| app/services/warehouse_service.py:133 | 422 | f'Warehouse {warehouse_id} is an in-transit location' | warehouse.in_transit_location | warehouse_id: int | warehouse is a virtual in-transit location, cannot hold document lines |
| app/api/v1/endpoints/products.py:198 | 422 | str(exc) | image.* |  | the image_service code raised (see image_service section) |
| app/api/v1/endpoints/facilities.py:55 | 422 | str(exc) | image.* |  | the image_service code raised (see image_service section) |


### image_service

`app/services/image_service.py` raises plain `ValueError`; five routers turn it into 422 `detail=str(e)`. Limits are `_MAX_BYTES = 2*1024*1024`. Pillow accepts whatever it can decode; there is no allowed-format list in the code, so no formats param.

| file:line | status | today's message | code | params | notes |
|---|---|---|---|---|---|
| app/services/image_service.py:33 (normalise_to_png) | 422 | Image exceeds maximum allowed size of 2 MB | image.too_large | max_bytes: int (2097152) | reached by product image, facility logo, licence photo, both proof photos |
| app/services/image_service.py:39 (normalise_to_png) | 422 | Unsupported or unrecognizable image format | image.unsupported_format | — | Pillow UnidentifiedImageError |
| app/services/image_service.py:96 (delete_image) | n/a | Invalid image filename | image.invalid_filename | — | NOT user-facing: guards a bad DB value on delete; callers do not catch it, so it is a 500 today. Cataloged for completeness; give it no router mapping |

The five router rows (delivery_orders.py:368, delivery_itineraries.py:381, vehicle_operators.py:116, products.py:198, facilities.py:55) are the `image.too_large` / `image.unsupported_format` raised by image_service: the router must carry the service's code and params instead of `str(e)` (make image_service raise a typed exception holding code and params).

### New codes introduced (not in the fixed list)

| code | status | params | situation |
|---|---|---|---|
| delivery_order.insufficient_notice | 422 | min_hours: int, earliest: datetime | Confirming a delivery order dated sooner than the minimum notice allows. |
| delivery_order.line_already_present | 409 | sales_order_line_id: int, delivery_order_line_id: int | The sales order line is already on this delivery order. |
| delivery_order.line_not_deliverable | 422 | sales_order_line_id: int | Requested sales order line is not an undelivered line of this customer or sale. |
| delivery_order.line_repeated | 422 | sales_order_line_id: int | The same sales order line was requested twice. |
| delivery_order.mixed_not_allowed | 422 | — | A delivery order cannot have MIXED fulfilment type. |
| delivery_order.no_lines | 409 | — | Confirming a delivery order with no lines. |
| delivery_order.no_lines_from_warehouse | 422 | warehouse_id: int | Ticket filtered by a warehouse none of the lines come from. |
| delivery_order.not_approved | 409 | status: enum:DeliveryOrderStatus | Printing an order that is still draft or awaiting approval. |
| delivery_order.not_editable | 409 | status: enum:DeliveryOrderStatus | Editing a delivery order that is no longer a draft. |
| delivery_order.not_failed | 409 | status: enum:DeliveryOrderStatus | Re-queueing an order that did not fail. |
| delivery_order.not_in_preparation | 409 | status: enum:DeliveryOrderStatus | Loading onto a trip an order that is not IN_PREPARATION. |
| delivery_order.not_pending_approval | 409 | status: enum:DeliveryOrderStatus, action: approve|reject | Approving or rejecting an order not awaiting approval. |
| delivery_order.pickup_not_loadable | 409 | — | A counter-pickup order cannot be loaded onto a trip. |
| delivery_order.proof_incomplete | 422 | — | Proof of delivery lacks receiver name or identification shown. |
| delivery_order.quantity_exceeds_remaining | 422 | sales_order_line_id: int, remaining: decimal, requested: decimal | Requested quantity is more than the sales line still owes. |
| delivery_order.reason_required | 422 | to: enum:DeliveryOrderStatus | Moving an order to DRAFT/FAILED/CANCELLED without a reason. |
| delivery_order.sales_order_fully_delivered | 409 | — | Sales order has nothing left to deliver. |
| delivery_order.sales_order_not_deliverable | 409 | — | Sales order is not completed or is cancelled. |
| delivery_order.sales_order_unpaid | 422 | — | Sales order must be paid or on credit terms (setting-gated). |
| delivery_order.unknown_warehouse | 422 | warehouse_id: int | Ticket filter names a warehouse that does not exist (422, not 404, today). |
| delivery_order_line.nothing_to_load | 422 | — | Committing a line with nothing left to load. |
| delivery_order_line.quantity_unavailable | 422 | available: decimal, requested: decimal | Committing or adjusting more than the line has available. |
| facility.no_dispatch_warehouse | 422 | facility_id: int | Facility has no non-transit warehouse to dispatch from. |
| facility.no_in_transit_location | 422 | facility_ids: list[int] | Facility has no in-transit warehouse. |
| image.invalid_filename | 500 | — | delete_image given a path-like name (not user-facing). |
| image.too_large | 422 | max_bytes: int | Uploaded image exceeds the size limit. |
| image.unsupported_format | 422 | — | Uploaded file is not a decodable image. |
| incidence.reason_required | 422 | — | Audit entry reason is blank. |
| inventory_document.insufficient_stock | 409 | product_id: int, warehouse_id: int, available: decimal, required: decimal | A stock-out document would take a product below what can be promised. |
| inventory_count.confirmed_cannot_cancel | 409 | — | Cancelling a confirmed count. |
| inventory_document.already_cancelled | 409 | resource: inventory_receipt|inventory_issue|inventory_transfer|inventory_count | Document is already cancelled. |
| status_filter.unsupported | 422 | allowed: list[str] | Status list filter value not in the allowed set. |
| inventory_document.line_without_quantity | 422 | product_code: str | Confirming a document with a zero-quantity line. |
| inventory_document.no_lines | 409 | resource: inventory_receipt|inventory_issue|inventory_transfer|inventory_count | Confirming a document with no lines. |
| inventory_receipt.complementary_unavailable | 409 | reason: str enum (no_purchase_order, purchase_order_cancelled, purchase_order_not_completed, purchase_order_fully_received) | No complementary receipt can be created for this receipt. |
| inventory_transfer.line_repeated | 422 | line_id: int | Same transfer line received twice in one request. |
| inventory_transfer.not_in_transit | 409 | — | Receiving a transfer that is not in transit. |
| inventory_transfer.over_received | 409 | product_code: str, received: decimal, outstanding: decimal | Receiving more than outstanding on a transfer line. |
| inventory_transfer.same_warehouse | 422 | — | Origin and destination warehouses are the same. |
| itinerary.cancelled | 409 | — | Printing a cancelled itinerary. |
| itinerary.not_departed | 409 | status: enum:ItineraryStatus | Printing or closing stops of an itinerary that has not departed. |
| itinerary.not_open | 409 | status: enum:ItineraryStatus | Changing an itinerary that is no longer OPEN. |
| itinerary.nothing_committed | 409 | — | Departing an itinerary with nothing committed. |
| itinerary.stop_already_resolved | 409 | — | Closing a stop that is already resolved. |
| itinerary.stop_delivered_exceeds_sent | 422 | line_id: int, sent: decimal, delivered: decimal | Stop close accepts more than was sent. |
| itinerary.stop_lines_incomplete | 422 | — | Stop close does not account for every line. |
| itinerary.stop_lines_malformed | 422 | — | `lines` form field is not a JSON array. |
| itinerary.stop_shortfall_needs_reason | 422 | line_id: int | Delivered less than sent with no reason code. |
| kardex.range_reversed | 422 | — | Kardex range ends before it starts. |
| kardex.range_too_long | 422 | max_days: int | Kardex range exceeds the maximum. |
| product.not_stockable | 422 | product_code: str | Product is not stockable. |
| sales_order.not_confirmed | 409 | sales_order_id: int | Sales order is not confirmed or is cancelled (loading its lines into a transfer). |
| vehicle.open_itinerary_exists | 409 | vehicle_id: int, itinerary_id: int | Vehicle already has an open itinerary. |
| warehouse.in_transit_location | 422 | warehouse_id: int | Document line names an in-transit warehouse. |

### Notes and odd

Resource names introduced that are not tables: proof_of_delivery_image, license_photo (id = vehicle operator). `delivery_order_line`, `delivery_itinerary_line`, `inventory_*_line`, `sales_order_line`, `inventory_transfer_line` follow the *_detail rule.

- ODD user.no_facility reuse: inventory_transfers.py:77 ("facility is required", missing ?facility and user has none). Unsure between user.no_facility and a query-validation code.
- ODD status: delivery_orders.py:310 "Unknown warehouse" is 422 where unknown warehouses elsewhere are 404 (warehouse_service.assert_sellable keeps 404 for the same thing). Kept 422, own code.
- ODD not user-facing: delivery_orders.py:426, vehicle_operators.py:149 (file missing from storage while the row says it exists: server inconsistency, coded as resource.not_found), image_service.py:96 (invalid filename, uncaught, 500 today).
- ODD shared raise sites used by other areas: inventory_documents.py:343 (busy_as_conflict, many purchase/sales callers; only delivery_order_service.py:693 passes `facility`), inventory_documents.py:353 (assert_facility also called from expense_vouchers.py:40 and purchase_request_service.py; needs a resource arg), incidences.py:35 (all incidences.record callers).
- ODD dual statuses: image.invalid_filename listed as 500 only because it is never mapped; sales_order.not_confirmed may collide with a sales-order catalog name.
- ODD dead-ish check: delivery_order_service.py:95 vs :565 deliberately share one message (to avoid leaking ids), so they share one code.
- ODD unsure: inventory_receipt.complementary_unavailable is one code with a `reason` param; four codes if the UI needs separate copy.
- ODD: delivery_itineraries.py:60/68 and delivery_orders.py:51 etc. are helper `_x_or_404` with one raise site each, so one row each.
- ODD: delivery_events.py:58 vs :64 are both 409 state.transition_invalid with no `required`; :71 adds required.

## Catalog C: purchasing, expenses, fiscal and leftovers (spec 036)

Scope: 53 rows of raises.tsv (purchasing and expenses, fiscal, no leftovers from other workers; every other file in raises.tsv belongs to lists A or B). 9 of the 53 are shared helper definitions (`_conflict`, `_not_found`, `_unprocessable`, `_error`), expanded here into one row per call site. Rows where one raise site emits several reasons (a `*_refusal` helper) get one row per reason with the same file:line.

### 1. Raises

| file:line | status | today's detail (short) | code | params (name: type, ...) | notes |
|---|---|---|---|---|---|
| app/api/v1/endpoints/expense_vouchers.py:37 | 404 | Expense voucher not found | `resource.not_found` | resource: str='expense_voucher', id: int |  |
| app/api/v1/endpoints/expense_vouchers.py:180 | 409 | Only a confirmed voucher can be printed | `expense_voucher.not_confirmed` | status: enum:DocumentStatus, action: str='print' | Shared with expense_voucher_service.py:443 (action 'void'). status = voucher_state() |
| app/api/v1/endpoints/expenses.py:49 | 404 | Expense not found | `resource.not_found` | resource: str='expense', id: int |  |
| app/api/v1/endpoints/expenses.py:62 | 404 | Expense not found | `resource.not_found` | resource: str='expense', id: int |  |
| app/api/v1/endpoints/expenses.py:75 | 404 | Expense not found | `resource.not_found` | resource: str='expense', id: int |  |
| app/api/v1/endpoints/fiscal_documents.py:63 | 404 | Fiscal document not found | `resource.not_found` | resource: str='fiscal_document', id: int |  |
| app/api/v1/endpoints/fiscal_documents.py:122 | 409 | Fiscal document has not been issued (XML) | `fiscal_document.not_issued` | - | Call site of _conflict; shared with :142 |
| app/api/v1/endpoints/fiscal_documents.py:125 | 409 | Stamped XML is missing or unreadable | `fiscal_document.xml_unavailable` | - | Call site of _conflict; shared with :149, :153 and fiscal_email.py:101 |
| app/api/v1/endpoints/fiscal_documents.py:142 | 409 | Fiscal document has not been issued (PDF) | `fiscal_document.not_issued` | - | Call site of _conflict |
| app/api/v1/endpoints/fiscal_documents.py:144 | 409 | Only CFDI 4.0 documents can be printed | `fiscal_document.version_not_printable` | version: str | Call site of _conflict. document.version is the SAT version string; in hand |
| app/api/v1/endpoints/fiscal_documents.py:146 | 409 | Fiscal document is not stamped | `fiscal_document.not_stamped` | - | Call site of _conflict |
| app/api/v1/endpoints/fiscal_documents.py:149 | 409 | Stamped XML is missing or unreadable | `fiscal_document.xml_unavailable` | - | Call site of _conflict |
| app/api/v1/endpoints/fiscal_documents.py:153 | 409 | Stamped XML is missing or unreadable (CfdiError) | `fiscal_document.xml_unavailable` | - | Call site of _conflict (`from None`) |
| app/api/v1/endpoints/fiscal_documents.py:252 | 403 | Insufficient privileges | `auth.forbidden` | required: [{system_object: 103, right: 4}] | Moving a draft to another store needs Update on IssuedLocationId. |
| app/api/v1/endpoints/fiscal_documents.py:402 | 503 | Email is not configured | `email.not_configured` | - |  |
| app/api/v1/endpoints/purchase_orders.py:42 | 404 | Purchase order not found | `resource.not_found` | resource: str='purchase_order', id: int |  |
| app/api/v1/endpoints/purchase_orders.py:254 | 503 | Email is not configured | `email.not_configured` | - |  |
| app/api/v1/endpoints/purchase_requests.py:40 | 404 | Purchase request not found | `resource.not_found` | resource: str='purchase_request', id: int |  |
| app/api/v1/endpoints/supplier_payments.py:88 | 404 | Supplier payment not found | `resource.not_found` | resource: str='supplier_payment', id: int |  |
| app/api/v1/endpoints/taxpayer_certificates.py:50 | 422 | str(exc) from csd_service.parse_csd | `csd.* (one of six, see csd_service section)` | - | Route re-raises ValueError text; the service must raise a coded error instead (FR-014). Status stays 422 |
| app/api/v1/endpoints/taxpayer_certificates.py:71 | 404 | Taxpayer certificate not found | `resource.not_found` | resource: str='taxpayer_certificate', id: int |  |
| app/services/expense_voucher_service.py:62 | 422 | status must be one of draft, completed, cancelled | `status_filter.unsupported` | resource: str='expense_voucher', status: enum:DocumentStatus | Only reachable for a DocumentStatus member the clause does not handle (e.g. 'paid'). Shared with purchase_order_service.py:68 and purchase_request_service.py:81 |
| app/services/expense_voucher_service.py:75 | 403 | Only the voucher's cashier can change it | `expense_voucher.not_cashier` | - | Not the facility rule and not a privilege: the draft belongs to its creator. Employee ids not given since the user already knows who they are |
| app/services/expense_voucher_service.py:80 | 409 | Only a draft expense voucher can be changed | `document.not_editable` | status: enum:DocumentStatus | Call site of _conflict. Same situation as documents.assert_editable; status = voucher_state() (completed or cancelled) |
| app/services/expense_voucher_service.py:85 | 409 | Only a confirmed voucher can be voided | `expense_voucher.not_confirmed` | status: enum:DocumentStatus, action: str='void' | Call site of _conflict |
| app/services/expense_voucher_service.py:221 | 404 | Expense voucher not found | `resource.not_found` | resource: str='expense_voucher', id: int |  |
| app/services/expense_voucher_service.py:248 | 409 | An open cash session is required | `cash_session.not_open` | - | Call site of _conflict (_SESSION_REQUIRED). Shared with :373, :380 and supplier_payment_service.py:351. Cash worker may already have a code for this; consolidate |
| app/services/expense_voucher_service.py:283 | 422 | Unknown expense category | `expense_voucher.unknown_category` | expense_id: int | A 422 for a missing lookup, so not resource.not_found (404) |
| app/services/expense_voucher_service.py:320 | 404 | Line not found | `resource.not_found` | resource: str='expense_voucher_line', id: int | id = line_id |
| app/services/expense_voucher_service.py:368 | 404 | Expense voucher not found | `resource.not_found` | resource: str='expense_voucher', id: int |  |
| app/services/expense_voucher_service.py:373 | 409 | An open cash session is required | `cash_session.not_open` | - | Call site of _conflict |
| app/services/expense_voucher_service.py:380 | 409 | An open cash session is required (second check) | `cash_session.not_open` | - | Call site of _conflict |
| app/services/expense_voucher_service.py:393 | 409 | The total must be above 0 | `expense_voucher.total_not_positive` | - | Call site of _conflict. 409 today though it reads like a 422; not changed |
| app/services/expense_voucher_service.py:430 | 404 | Expense voucher not found | `resource.not_found` | resource: str='expense_voucher', id: int |  |
| app/services/expense_voucher_service.py:443 | 409 | Only a confirmed voucher can be voided | `expense_voucher.not_confirmed` | status: enum:DocumentStatus, action: str='void' | Call site of _conflict |
| app/services/fiscal_email.py:95 | 409 | Only a stamped CFDI 4.0 can be emailed | `fiscal_document.not_emailable` | - | Call site of _conflict. Combines not issued / cancelled / not 4.0 / no stamp / no XML, so it cannot reuse the print codes without a split |
| app/services/fiscal_email.py:101 | 409 | Stamped XML is missing or unreadable | `fiscal_document.xml_unavailable` | - | Call site of _conflict (`from None`) |
| app/services/fiscal_email.py:119 | 502 | The email could not be sent: {exc} | `email.send_failed` | - | Exception text in detail only |
| app/services/invoice_service.py:136 | 409 | Fiscal document is not a draft | `fiscal_document.not_draft` | - | Call site of _error. document.status is a free string ('draft'/'sealed'), so no enum param |
| app/services/invoice_service.py:141 | 409 | Not available for this document type | `fiscal_document.type_not_supported` | type: enum:FiscalDocumentType | Call site of _error. FiscalDocumentType is an IntEnum (ints) |
| app/services/invoice_service.py:203 | 422 | The issuer has no {series name} series | `fiscal_document.issuer_has_no_series` | type: enum:FiscalDocumentType | Call site of _unprocessable |
| app/services/invoice_service.py:215 | 422 | The recipient is not one of the customer's taxpayers | `fiscal_document.recipient_not_linked` | customer_id: int, recipient: str | Call site of _unprocessable. recipient = RFC |
| app/services/invoice_service.py:246 | 404 | Line not found | `resource.not_found` | resource: str='fiscal_document_line', id: int | Call site of _error; id = line_id |
| app/services/invoice_service.py:268 | 422 | No facility is configured for your user | `user.no_facility` | - |  |
| app/services/invoice_service.py:271 | 422 | No facility is configured for your user (facility row missing) | `user.no_facility` | - |  |
| app/services/invoice_service.py:274 | 422 | Taxpayer issuer not found | `taxpayer_issuer.unknown` | taxpayer: str | Call site of _unprocessable. 422, so not resource.not_found. Shared with taxpayer_certificate_service.py:80. taxpayer = RFC |
| app/services/invoice_service.py:277 | 404 | Customer not found | `resource.not_found` | resource: str='customer', id: int | Call site of _error |
| app/services/invoice_service.py:284 | 422 | The series is not a/an {type} series of the issuer | `fiscal_document.series_not_of_type` | batch: str, type: enum:FiscalDocumentType | Call site of _unprocessable. Shared with :790 (type invoice) |
| app/services/invoice_service.py:410 | 404 | Sales order not found | `resource.not_found` | resource: str='sales_order', id: int | Call site of _error |
| app/services/invoice_service.py:412 | 422 | The sales order is not completed or is cancelled | `fiscal_document.order_not_invoiceable` | sales_order_id: int | Call site of _unprocessable |
| app/services/invoice_service.py:458 | 422 | No invoiceable lines were found | `fiscal_document.no_invoiceable_lines` | - | Call site of _unprocessable |
| app/services/invoice_service.py:474 | 404 | Product not found | `resource.not_found` | resource: str='product', id: int | Call site of _error |
| app/services/invoice_service.py:485 | 422 | The product has no price in the customer's price list | `fiscal_document.product_unpriced` | product_id: int | Call site of _unprocessable |
| app/services/invoice_service.py:530 | 422 | Line quantity exceeds the {remaining} still invoiceable | `fiscal_document.quantity_exceeds_invoiceable` | remaining: decimal, line?: int | Call site of _unprocessable. Shared with :581 (which adds line) |
| app/services/invoice_service.py:559 | 422 | The invoice has no lines | `fiscal_document.no_lines` | - | Call site of _unprocessable. Shared with :1448 |
| app/services/invoice_service.py:562 | 422 | Line {n}: missing SAT product key or unit | `fiscal_document.line_missing_sat_key` | line: int | Call site of _unprocessable. line = 1-based position, in hand. Shared with :1451 |
| app/services/invoice_service.py:564 | 422 | Line {n}: the quantity must be positive | `fiscal_document.line_quantity_not_positive` | line: int | Call site of _unprocessable |
| app/services/invoice_service.py:581 | 422 | Line {n}: quantity exceeds the {remaining} still invoiceable | `fiscal_document.quantity_exceeds_invoiceable` | remaining: decimal, line?: int | Call site of _unprocessable |
| app/services/invoice_service.py:586 | 422 | The recipient has no tax regime or postal code | `fiscal_document.recipient_incomplete` | - | Call site of _unprocessable. Shared with :1453 |
| app/services/invoice_service.py:604 | 422 | The issuer has no active certificate valid at the issue date | `fiscal_document.no_valid_certificate` | - | Call site of _unprocessable |
| app/services/invoice_service.py:622 | 422 | The invoice series no longer exists | `fiscal_document.series_missing` | batch: str | Call site of _unprocessable. Race: series deleted while stamping. batch may be None; omit then |
| app/services/invoice_service.py:719 | 422 | The issue date must be within the last 72 hours and not in the future | `fiscal_document.issue_date_out_of_range` | - | Call site of _unprocessable. Shared with :805. The 72 h window is a fixed rule, so no param |
| app/services/invoice_service.py:730 | 422 | The issuer's certificate cannot sign: {error} | `fiscal_document.certificate_cannot_sign` | - | Call site of _unprocessable. Exception text stays in detail only |
| app/services/invoice_service.py:763 | 422 | Not a field of this document type: {fields} | `fiscal_document.field_not_for_type` | fields: list[str], type: enum:FiscalDocumentType | Call site of _unprocessable. fields = request field names |
| app/services/invoice_service.py:766 | 422 | A payment receipt needs the actual payment form | `fiscal_document.payment_form_required` | type: enum:FiscalDocumentType | Call site of _unprocessable. Shared with :1225 and :1455 (credit note) |
| app/services/invoice_service.py:771 | 422 | Store not found | `fiscal_document.unknown_facility` | facility_id: int | Call site of _unprocessable. 422 so not resource.not_found |
| app/services/invoice_service.py:785 | 409 | The series cannot change once the invoice has a serial | `fiscal_document.series_locked` | - | Call site of _error |
| app/services/invoice_service.py:790 | 422 | The series is not an invoice series of the issuer | `fiscal_document.series_not_of_type` | batch: str, type: enum:FiscalDocumentType | Call site of _unprocessable. type = 0 (INVOICE) |
| app/services/invoice_service.py:794 | 422 | Unknown CFDI usage | `fiscal_document.unknown_usage` | usage: str | Call site of _unprocessable. usage = SAT key |
| app/services/invoice_service.py:805 | 422 | The issue date must be within the last 72 hours and not in the future | `fiscal_document.issue_date_out_of_range` | - | Call site of _unprocessable |
| app/services/invoice_service.py:849 | 404 | Related document not found | `resource.not_found` | resource: str='fiscal_document', id: int | Call site of _error; id = data.relation |
| app/services/invoice_service.py:852 | 422 | The related document does not qualify for relation type {type} | `fiscal_document.relation_not_allowed` | relation_type: str | Call site of _unprocessable. relation_type = SAT relation key |
| app/services/invoice_service.py:865 | 409 | The document is already related | `fiscal_document.already_related` | relation_id: int | Call site of _error. Id of the related document is in hand (data.relation) |
| app/services/invoice_service.py:867 | 422 | All relations must share one type | `fiscal_document.relation_types_mixed` | - | Call site of _unprocessable |
| app/services/invoice_service.py:890 | 404 | Relation not found | `resource.not_found` | resource: str='fiscal_document_relation', id: int | Call site of _error |
| app/services/invoice_service.py:906 | 409 | Fiscal document is not sealed | `fiscal_document.not_sealed` | - | Call site of _error |
| app/services/invoice_service.py:1001 | 422 | The payment date must be between the invoices' dates and today | `fiscal_document.payment_date_out_of_range` | - | Call site of _unprocessable (_PAYMENT_DATE). Shared with :1227 |
| app/services/invoice_service.py:1006 | 422 | Amount exceeds the invoice balance of {balance} | `fiscal_document.amount_exceeds_balance` | balance: decimal | Call site of _unprocessable. Also fires when amount <= 0 (same message today) |
| app/services/invoice_service.py:1008 | 422 | The amounts paid exceed the payment amount | `fiscal_document.amounts_exceed_payment` | - | Call site of _unprocessable (_OVER_PAYMENT). Shared with :1212 |
| app/services/invoice_service.py:1039 | 422 | The invoice cannot be paid by this receipt | `fiscal_document.invoice_not_payable` | invoice_id: int | Call site of _unprocessable (_RECEIPT_REFUSED). Shared with :1245 |
| app/services/invoice_service.py:1041 | 409 | The invoice is already on this receipt | `fiscal_document.invoice_already_on_receipt` | invoice_id: int | Call site of _error |
| app/services/invoice_service.py:1083 | 404 | Customer payment not found | `resource.not_found` | resource: str='customer_payment', id: int | Call site of _error |
| app/services/invoice_service.py:1085 | 422 | The payment belongs to another customer | `fiscal_document.payment_other_customer` | customer_payment_id: int | Call site of _unprocessable |
| app/services/invoice_service.py:1097 | 409 | This payment already has receipt {folio} | `fiscal_document.payment_already_receipted` | fiscal_document_id: int | Call site of _error. The folio is formatted text, so the receipt's id goes in params; detail keeps the folio |
| app/services/invoice_service.py:1157 | 404 | Invoice not found | `resource.not_found` | resource: str='fiscal_document', id: int | Call site of _error; id = data.invoice |
| app/services/invoice_service.py:1169 | 404 | Paid invoice not found | `resource.not_found` | resource: str='fiscal_document_relation', id: int | Call site of _error; id = relation_id |
| app/services/invoice_service.py:1212 | 422 | The amounts paid exceed the payment amount | `fiscal_document.amounts_exceed_payment` | - | Call site of _unprocessable |
| app/services/invoice_service.py:1221 | 422 | The receipt pays no invoice | `fiscal_document.receipt_pays_no_invoice` | - | Call site of _unprocessable |
| app/services/invoice_service.py:1223 | 422 | The receipt needs its payment date and amount | `fiscal_document.receipt_needs_payment_data` | - | Call site of _unprocessable |
| app/services/invoice_service.py:1225 | 422 | A payment receipt needs the actual payment form | `fiscal_document.payment_form_required` | type: enum:FiscalDocumentType | Call site of _unprocessable |
| app/services/invoice_service.py:1227 | 422 | The payment date must be between the invoices' dates and today | `fiscal_document.payment_date_out_of_range` | - | Call site of _unprocessable |
| app/services/invoice_service.py:1245 | 422 | The invoice cannot be paid by this receipt | `fiscal_document.invoice_not_payable` | invoice_id: int | Call site of _unprocessable |
| app/services/invoice_service.py:1264 | 422 | The amounts paid must equal the payment amount | `fiscal_document.amounts_not_equal_payment` | - | Call site of _unprocessable |
| app/services/invoice_service.py:1328 | 404 | Invoice not found | `resource.not_found` | resource: str='fiscal_document', id: int | Call site of _error; id = data.invoice |
| app/services/invoice_service.py:1330 | 422 | The invoice cannot be credited | `fiscal_document.invoice_not_creditable` | invoice_id: int | Call site of _unprocessable. Shared with :1471 |
| app/services/invoice_service.py:1399 | 422 | Quantity exceeds the invoice line's {max} | `fiscal_document.credit_quantity_exceeds_line` | max: decimal | Call site of _unprocessable. Shared with :1414 |
| app/services/invoice_service.py:1411 | 404 | Invoice line not found | `resource.not_found` | resource: str='fiscal_document_line', id: int | Call site of _error; id = data.line |
| app/services/invoice_service.py:1414 | 422 | Quantity exceeds the invoice line's {quantity} | `fiscal_document.credit_quantity_exceeds_line` | max: decimal | Call site of _unprocessable |
| app/services/invoice_service.py:1448 | 422 | The invoice has no lines | `fiscal_document.no_lines` | - | Call site of _unprocessable |
| app/services/invoice_service.py:1451 | 422 | Line {n}: missing SAT product key or unit | `fiscal_document.line_missing_sat_key` | line: int | Call site of _unprocessable |
| app/services/invoice_service.py:1453 | 422 | The recipient has no tax regime or postal code | `fiscal_document.recipient_incomplete` | - | Call site of _unprocessable |
| app/services/invoice_service.py:1455 | 422 | A credit note needs the actual payment form | `fiscal_document.payment_form_required` | type: enum:FiscalDocumentType | Call site of _unprocessable. type = 3 or whatever CREDIT_NOTE is, from the document |
| app/services/invoice_service.py:1471 | 422 | The invoice cannot be credited | `fiscal_document.invoice_not_creditable` | invoice_id: int | Call site of _unprocessable |
| app/services/invoice_service.py:1482 | 422 | The credit note exceeds what remains of invoice {folio}: {creditable} | `fiscal_document.credit_exceeds_invoice` | invoice_id: int, remaining: decimal | Call site of _unprocessable. Folio is formatted text, so id goes in params |
| app/services/purchase_cost_service.py:368 | 404 | Product not found | `resource.not_found` | resource: str='product', id: int |  |
| app/services/purchase_order_service.py:68 | 422 | status must be one of draft, completed, cancelled | `status_filter.unsupported` | resource: str='purchase_order', status: enum:DocumentStatus |  |
| app/services/purchase_order_service.py:271 | 404 | Supplier not found | `resource.not_found` | resource: str='supplier', id: int |  |
| app/services/purchase_order_service.py:278 | 404 | Product not found | `resource.not_found` | resource: str='product', id: int |  |
| app/services/purchase_order_service.py:280 | 422 | Product {code} is not purchasable | `product.not_purchasable` | product_id: int, product_code: str | Shared with purchase_request_service.py:292 |
| app/services/purchase_order_service.py:300 | 404 | Purchase order not found | `resource.not_found` | resource: str='purchase_order', id: int |  |
| app/services/purchase_order_service.py:310 | 409 | Only a draft purchase order can be changed | `document.not_editable` | status: enum:DocumentStatus | Call site of _conflict (_begin_draft); status = order_status() |
| app/services/purchase_order_service.py:382 | 422 | A warehouse is required | `warehouse.required` | resource: str='purchase_order' | Shared with purchase_request_service.py:307 (resource 'purchase_request') |
| app/services/purchase_order_service.py:412 | 404 | Line not found | `resource.not_found` | resource: str='purchase_order_line', id: int | id = line_id |
| app/services/purchase_order_service.py:450 | 409 | The purchase order is already cancelled | `purchase_order.already_cancelled` | - | Call site of _conflict |
| app/services/purchase_order_service.py:452 | 409 | A confirmed purchase order cannot be cancelled | `purchase_order.confirmed_cannot_cancel` | - | Call site of _conflict |
| app/services/purchase_order_service.py:653 | 404 | Purchase request not found | `resource.not_found` | resource: str='purchase_request', id: int |  |
| app/services/purchase_order_service.py:672 | 409 | Only an approved request's lines can be ordered | `purchase_order.request_not_approved` | purchase_request_id: int | Call site of _conflict |
| app/services/purchase_order_service.py:675 | 409 | Nothing left to add from this request | `purchase_order.nothing_to_add` | purchase_request_id: int | Call site of _conflict |
| app/services/purchase_order_service.py:700 | 409 | Nothing is pending for this supplier and facility | `purchase_order.nothing_pending` | supplier_id: int, facility_id: int | Call site of _conflict. Shared with :708 |
| app/services/purchase_order_service.py:708 | 409 | Nothing is pending for this supplier and facility (after re-check) | `purchase_order.nothing_pending` | supplier_id: int, facility_id: int | Call site of _conflict. Same condition after losing a race |
| app/services/purchase_order_service.py:752 | 409 | Only a draft purchase order can be confirmed | `document.not_editable` | status: enum:DocumentStatus | Call site of _conflict(reason) via confirm_refusal (a); status = order_status() |
| app/services/purchase_order_service.py:752 | 409 | The purchase order has no supplier | `purchase_order.no_supplier` | - | confirm_refusal (b) |
| app/services/purchase_order_service.py:752 | 409 | The purchase order has no lines | `purchase_order.no_lines` | - | confirm_refusal (c) |
| app/services/purchase_order_service.py:752 | 409 | Line {product_code} has no quantity | `purchase_order.line_without_quantity` | product_code: str | confirm_refusal (d). 409 here, 422 for the same situation in purchase_request:481, so codes differ |
| app/services/purchase_order_service.py:938 | 409 | Only a confirmed purchase order can be printed or emailed | `purchase_order.not_confirmed` | purchase_order_id: int | Call site of _conflict (assert_printable). Shared with supplier_payment_service.py:106 |
| app/services/purchase_order_service.py:992 | 502 | The email could not be sent: {exc} | `email.send_failed` | - |  |
| app/services/purchase_request_service.py:81 | 422 | status must be one of draft, submitted, approved, cancelled | `status_filter.unsupported` | resource: str='purchase_request', status: enum:RequestStatus | RequestStatus is a StrEnum; all four members are handled, so this looks unreachable |
| app/services/purchase_request_service.py:247 | 404 | Purchase request not found | `resource.not_found` | resource: str='purchase_request', id: int |  |
| app/services/purchase_request_service.py:274 | 409 | The warehouse changed facility, retry | `purchase_request.warehouse_facility_changed` | - | Call site of _conflict. Shared with :467 and :616. Race on a lock-free pre-read |
| app/services/purchase_request_service.py:284 | 409 | Only a draft request can be changed | `purchase_request.not_draft` | status: enum:RequestStatus | Call site of _conflict. RequestStatus has submitted/approved, which DocumentStatus lacks, so document.not_editable does not fit. Shared with :481(a) |
| app/services/purchase_request_service.py:290 | 404 | Product not found | `resource.not_found` | resource: str='product', id: int |  |
| app/services/purchase_request_service.py:292 | 422 | Product {code} is not purchasable | `product.not_purchasable` | product_id: int, product_code: str |  |
| app/services/purchase_request_service.py:307 | 422 | A warehouse is required | `warehouse.required` | resource: str='purchase_request' |  |
| app/services/purchase_request_service.py:355 | 409 | A numbered request cannot move to another facility | `purchase_request.facility_locked` | - | Call site of _conflict via facility_move_refusal |
| app/services/purchase_request_service.py:409 | 404 | Line not found | `resource.not_found` | resource: str='purchase_request_line', id: int | id = line_id |
| app/services/purchase_request_service.py:467 | 409 | The warehouse changed facility, retry | `purchase_request.warehouse_facility_changed` | - | Call site of _conflict |
| app/services/purchase_request_service.py:471 | 422 | Facility {n} has no in-transit location | `facility.no_in_transit_location` | facility_id: int |  |
| app/services/purchase_request_service.py:481 | 409 | Only a draft request can be submitted | `purchase_request.not_draft` | status: enum:RequestStatus | HTTPException(status_code=code, detail=reason) via submit_refusal (a) |
| app/services/purchase_request_service.py:481 | 409 | The request has no lines | `purchase_request.no_lines` | - | submit_refusal (b) |
| app/services/purchase_request_service.py:481 | 422 | Line {product_name} has no quantity | `purchase_request.line_without_quantity` | line_id: int | submit_refusal (c). 422 here, 409 for the PO twin. detail carries a product name; params carry the line id |
| app/services/purchase_request_service.py:511 | 409 | Only a submitted request can be approved or rejected (approve) | `purchase_request.not_submitted` | status: enum:RequestStatus | Call site of _conflict via approve_refusal. Shared with :547 |
| app/services/purchase_request_service.py:547 | 409 | Only a submitted request can be approved or rejected (reject) | `purchase_request.not_submitted` | status: enum:RequestStatus | Call site of _conflict via reject_refusal |
| app/services/purchase_request_service.py:584 | 404 | Line not found | `resource.not_found` | resource: str='purchase_request_line', id: int | id = line_id |
| app/services/purchase_request_service.py:588 | 409 | Lines can be marked only on a submitted or approved request | `purchase_request.lines_not_markable` | status: enum:RequestStatus | Call site of _conflict via mark_refusal (a) |
| app/services/purchase_request_service.py:588 | 409 | The line is on a live purchase order | `purchase_request.line_on_live_order` | line_id: int | mark_refusal (b) |
| app/services/purchase_request_service.py:616 | 409 | The warehouse changed facility, retry | `purchase_request.warehouse_facility_changed` | - | Call site of _conflict |
| app/services/purchase_request_service.py:625 | 409 | The request is already cancelled | `purchase_request.already_cancelled` | - | Call site of _conflict via cancel_refusal (a) |
| app/services/purchase_request_service.py:625 | 409 | {n} line(s) of the request is/are on a live purchase order | `purchase_request.lines_on_live_order` | count: int | cancel_refusal (b) |
| app/services/supplier_payment_service.py:85 | 409 | The payment is cancelled | `supplier_payment.cancelled` | - | Call site of _conflict |
| app/services/supplier_payment_service.py:87 | 409 | A legacy payment cannot be applied | `supplier_payment.legacy_not_applicable` | - | Call site of _conflict |
| app/services/supplier_payment_service.py:101 | 409 | Balances start at the cutoff, which is not set yet | `supplier_payment.cutoff_not_set` | - | Call site of _conflict (CUTOFF_NOT_SET) |
| app/services/supplier_payment_service.py:106 | 409 | Purchase order {po} is not confirmed | `purchase_order.not_confirmed` | purchase_order_id: int | Call site of _conflict |
| app/services/supplier_payment_service.py:108 | 409 | Purchase order {po} belongs to another supplier | `supplier_payment.order_other_supplier` | purchase_order_id: int | Call site of _conflict |
| app/services/supplier_payment_service.py:110 | 409 | Purchase order {po} was confirmed before the cutoff | `supplier_payment.order_before_cutoff` | purchase_order_id: int | Call site of _conflict |
| app/services/supplier_payment_service.py:112 | 409 | The amount exceeds the balance of purchase order {po}: {balance} | `supplier_payment.amount_exceeds_balance` | purchase_order_id: int, balance: decimal | Call site of _conflict |
| app/services/supplier_payment_service.py:116 | 409 | The amounts exceed the payment's credit: {credit} | `supplier_payment.amounts_exceed_credit` | credit: decimal | Call site of _conflict |
| app/services/supplier_payment_service.py:279 | 404 | Supplier payment not found (_not_found) | `resource.not_found` | resource: str='supplier_payment', id: int | Call site of _not_found; id = payment_id |
| app/services/supplier_payment_service.py:301 | 404 | Purchase order not found (_not_found) | `resource.not_found` | resource: str='purchase_order' | Call site of _not_found. Locks a set of PO ids and cannot say which is missing, so id is omitted |
| app/services/supplier_payment_service.py:329 | 422 | Only an administrator without a facility can name one | `supplier_payment.facility_not_nameable` | - | Call site of _unprocessable |
| app/services/supplier_payment_service.py:332 | 422 | A facility is required | `facility.required` | - | Call site of _unprocessable. Reached when the caller has no facility and named none; flagged in odd list |
| app/services/supplier_payment_service.py:346 | 404 | Supplier not found (_not_found) | `resource.not_found` | resource: str='supplier', id: int | Call site of _not_found; id = data.supplier |
| app/services/supplier_payment_service.py:351 | 409 | An open cash session is required | `cash_session.not_open` | - | Call site of _conflict |
| app/services/supplier_payment_service.py:432 | 404 | Application not found (_not_found) | `resource.not_found` | resource: str='purchase_order_payment', id: int | Call site of _not_found; id = application_id. Table name purchase_order_payment |
| app/services/supplier_payment_service.py:445 | 409 | The application is already reversed | `supplier_payment.application_already_reversed` | application_id: int | Call site of _conflict |
| app/services/supplier_payment_service.py:473 | 404 | Supplier payment not found (_not_found) | `resource.not_found` | resource: str='supplier_payment', id: int | Call site of _not_found; id = payment_id |
| app/services/supplier_payment_service.py:481 | 409 | The payment is already cancelled | `supplier_payment.already_cancelled` | - | Call site of _conflict |
| app/services/supplier_payment_service.py:651 | 404 | Supplier not found (_not_found) | `resource.not_found` | resource: str='supplier', id: int | Call site of _not_found; id = supplier_id |
| app/services/taxpayer_certificate_service.py:74 | 422 | Certificate belongs to {holder}, not to {taxpayer} | `taxpayer_certificate.holder_mismatch` | certificate_taxpayer: str, taxpayer: str | RFCs |
| app/services/taxpayer_certificate_service.py:80 | 422 | Taxpayer issuer not found | `taxpayer_issuer.unknown` | taxpayer: str |  |
| app/services/taxpayer_certificate_service.py:86 | 409 | Certificate {id} is already registered | `resource.already_exists` | resource: str='taxpayer_certificate', field: str='certificate_id' |  |

Rows: 170 (call-site expanded).

### csd_service

All six are plain `ValueError`s raised from `_load_certificate`, `_load_key` and `_parse_sync`, all reaching the client as 422 through `taxpayer_certificates.py:50` (`detail=str(exc)`). Each becomes a coded error raised by the service (status 422, no params: nothing to read or act on beyond the code). The `except ValueError` clauses at csd_service.py:46, :50, :64, :72 are control flow, not raises to the user. `invoice_service.py:730` also catches a ValueError from certificate signing (see its row).

| file:line | status | today's message | code | params |
|---|---|---|---|---|
| app/services/csd_service.py:51 | 422 | Unreadable certificate — expected a DER or PEM encoded .cer | `csd.certificate_unreadable` | - |
| app/services/csd_service.py:67 | 422 | Private key is not RSA — not a valid CSD key | `csd.key_not_rsa` | - |
| app/services/csd_service.py:75 | 422 | Private key is not password protected — not a valid CSD key | `csd.key_not_encrypted` | - |
| app/services/csd_service.py:76 | 422 | Wrong password, or unreadable private key — expected a DER or PEM .key | `csd.key_unreadable` | - |
| app/services/csd_service.py:119 | 422 | Certificate does not carry an RSA public key — not a valid CSD | `csd.certificate_not_rsa` | - |
| app/services/csd_service.py:121 | 422 | Private key does not match the certificate | `csd.key_mismatch` | - |

Situations: `csd.certificate_unreadable` = The .cer is neither DER nor PEM. `csd.key_not_rsa` = The key file holds a non-RSA key. `csd.key_not_encrypted` = The key opens without a password, so it is not a SAT CSD key. `csd.key_unreadable` = The key password is wrong or the .key file is unreadable (the library cannot tell which). `csd.certificate_not_rsa` = The certificate public key is not RSA. `csd.key_mismatch` = The key and the certificate are not a pair.

### schema validators

21 `raise ValueError(` sites in app/schemas (the spec says 23: it counts the shared `_not_past` and `_distinct` once per field they are applied to).

Each becomes `raise PydanticCustomError(<type>, <English message template>, <ctx>)`; the English message stays the `msg`. FastAPI keeps `type`, `loc`, `msg`, `ctx` in its validation list. `ctx` values are raw (dates ISO via pydantic, decimals as strings, ints). Precedent: `app/core/password_policy.py` (`password_policy`, ctx `failed: list[str]`). A `ctx` value must be JSON-serializable: dates and decimals must be turned into ISO strings / decimal strings in the ctx dict, as pydantic does not coerce them there.

| file:line | message today | type | ctx keys (name: type) | notes |
|---|---|---|---|---|
| app/schemas/sales_quote.py:138 | Not a valid email address: {raw!r} | `email_address_invalid` | value: str (the rejected address as sent) | Field validator `to` (QuoteEmailRequest); also reached via accounts.py:123, which delegates. Max 254 chars or regex mismatch share one situation. |
| app/schemas/user.py:52 | Unknown system object: {v} | `profile_unknown_system_object` | system_object: int |  |
| app/schemas/user.py:64 | Duplicate system_object in privileges | `profile_duplicate_system_object` | - | Helper _reject_duplicate_objects. The duplicated object is not computed today; `ctx` stays empty (could add system_objects: list[int] if wanted). |
| app/schemas/user.py:163 | Username must contain only alphanumeric characters | `user_id_not_alphanumeric` | - | Field `user_id`. |
| app/schemas/accounts.py:128 | Choose at least one contact or type an address | `statement_email_no_recipient` | - | Model validator on the statement-email request (contacts and to both empty). |
| app/schemas/purchases.py:39 | The estimated receipt date cannot be in the past | `purchase_receipt_date_in_past` | date: date (the value sent), today: date | Shared function `_not_past`, applied at purchases.py:151 and :159 (create and update). `today` is local_today() at validation time. |
| app/schemas/purchases.py:83 | A reason is required and cannot be blank | `purchase_request_reason_blank` | - | RejectRequest.reason. |
| app/schemas/supplier_payments.py:34 | Each purchase order can appear only once | `supplier_payment_duplicate_order` | - | Shared function `_distinct`, applied at supplier_payments.py:58 and :80 (create and applications). |
| app/schemas/supplier_payments.py:64 | The payment date cannot be in the future | `supplier_payment_date_in_future` | date: datetime |  |
| app/schemas/supplier_payments.py:71 | A cash payment is dated now; send no date | `supplier_payment_cash_has_date` | - | Model validator `_cash_takes_its_session`. |
| app/schemas/supplier_payments.py:73 | A cash payment takes its session's facility; send no facility | `supplier_payment_cash_has_facility` | - | Same validator. |
| app/schemas/core.py:103 | Name must not be blank | `contact_name_blank` | - | ContactCreate.name (core.py). Check whether ContactUpdate subclasses it; same type then. |
| app/schemas/product.py:16 | Code must not contain whitespace | `product_code_has_whitespace` | - | Function `_validate_code`. |
| app/schemas/product.py:22 | Barcode must be empty or exactly 13 digits (EAN-13) | `product_barcode_invalid` | - | Function `_validate_bar_code`. |
| app/schemas/supplier.py:56 | End must not be before start | `supplier_agreement_end_before_start` | start: date, end: date | SupplierAgreementCreate model validator. |
| app/schemas/fiscal.py:196 | The series cannot be blank | `taxpayer_batch_series_blank` | - | Field `batch` of the series create schema. |
| app/schemas/fiscal.py:249 | The payment date cannot be in the future | `fiscal_document_payment_date_in_future` | date: date | Different domain from supplier_payment_date_in_future (a CFDI receipt, a date not a datetime). |
| app/schemas/fiscal.py:256 | Not a SAT payment form | `fiscal_document_payment_form_unknown` | payment_form: int | Field `payment_method` (SAT forma de pago, int key). |
| app/schemas/fiscal.py:283 | The tax rate must be 0.16, 0 or -1 (exempt) | `fiscal_document_tax_rate_invalid` | tax_rate: decimal | TAX_RATES is a fixed set; no allowed list in ctx. |
| app/schemas/inventory.py:108 | Each line may be received only once per request | `inventory_transfer_duplicate_line` | - | TransferReceive.lines. |
| app/schemas/inventory.py:295 | Give exactly one of product and product_code | `inventory_count_product_reference` | - | CountLineSet; fires both when none and when both are given (one message today). Single type; add `given: int` (0 or 2) if the UI wants to differ. |

Other ValueError validators: `app/core` has none besides `password_policy`, which already uses `PydanticCustomError` (grep of `raise ValueError(` in app/core is empty). Other `raise ValueError(` outside schemas are not validators: services (csd_service, image_service), `stock_ledger.py:38`, `:88`, `rendering/fetcher.py:35`; see Odd.

### 2. New codes introduced (not in the fixed list)

| code | status | params | situation |
|---|---|---|---|
| `cash_session.not_open` | 409 | - | The cashier has no open cash session. |
| `expense_voucher.not_cashier` | 403 | - | A draft voucher can be changed only by the cashier who created it. |
| `expense_voucher.not_confirmed` | 409 | status: enum:DocumentStatus, action: str='print', action: str='void' | A voucher is not confirmed, so it cannot be printed or voided. |
| `expense_voucher.total_not_positive` | 409 | - | The voucher total is zero or negative at confirmation. |
| `expense_voucher.unknown_category` | 422 | expense_id: int | The expense category named on the voucher line does not exist. |
| `facility.no_in_transit_location` | 422 | facility_id: int | The facility has no in-transit location configured. |
| `fiscal_document.already_related` | 409 | relation_id: int | The document is already related to this one. |
| `fiscal_document.amount_exceeds_balance` | 422 | balance: decimal | The amount paid is zero/negative or more than the invoice balance. |
| `fiscal_document.amounts_exceed_payment` | 422 | - | The amounts paid exceed the receipt's payment amount. |
| `fiscal_document.amounts_not_equal_payment` | 422 | - | The amounts paid do not add up to the receipt's payment amount. |
| `fiscal_document.certificate_cannot_sign` | 422 | - | The CSD could not sign the document. |
| `fiscal_document.credit_exceeds_invoice` | 422 | invoice_id: int, remaining: decimal | The credit note total is more than what is left to credit on the invoice. |
| `fiscal_document.credit_quantity_exceeds_line` | 422 | max: decimal | The credited quantity is more than the invoice line's quantity. |
| `fiscal_document.field_not_for_type` | 422 | fields: list[str], type: enum:FiscalDocumentType | The request sets header fields that this document type does not take. |
| `fiscal_document.invoice_already_on_receipt` | 409 | invoice_id: int | The invoice is already listed on the receipt. |
| `fiscal_document.invoice_not_creditable` | 422 | invoice_id: int | The invoice is not eligible for a credit note. |
| `fiscal_document.invoice_not_payable` | 422 | invoice_id: int | The invoice cannot be paid by this receipt. |
| `fiscal_document.issue_date_out_of_range` | 422 | - | The issue date is in the future or more than 72 hours ago. |
| `fiscal_document.issuer_has_no_series` | 422 | type: enum:FiscalDocumentType | The issuer has no series for this document type. |
| `fiscal_document.line_missing_sat_key` | 422 | line: int | A line lacks its SAT product/service key or unit of measurement. |
| `fiscal_document.line_quantity_not_positive` | 422 | line: int | A line's quantity is zero or negative. |
| `fiscal_document.no_invoiceable_lines` | 422 | - | The sales order has no lines left to invoice. |
| `fiscal_document.no_lines` | 422 | - | The document has no lines. |
| `fiscal_document.no_valid_certificate` | 422 | - | The issuer has no active CSD valid at the issue date. |
| `fiscal_document.not_draft` | 409 | - | The fiscal document has been issued and can no longer be edited. |
| `fiscal_document.not_emailable` | 409 | - | The document is not a stamped, uncancelled CFDI 4.0 with its XML, so it cannot be emailed. |
| `fiscal_document.not_issued` | 409 | - | The fiscal document is still a draft (not issued), so its XML or PDF does not exist. |
| `fiscal_document.not_sealed` | 409 | - | The document has not been sealed (issued) yet. |
| `fiscal_document.not_stamped` | 409 | - | The document has no stamp UUID, so it cannot be printed. |
| `fiscal_document.order_not_invoiceable` | 422 | sales_order_id: int | The sales order cannot be invoiced because it is not completed or was cancelled. |
| `fiscal_document.payment_already_receipted` | 409 | fiscal_document_id: int | A payment receipt already exists for this customer payment. |
| `fiscal_document.payment_date_out_of_range` | 422 | - | The receipt's payment date is before an invoice's date or after today. |
| `fiscal_document.payment_form_required` | 422 | type: enum:FiscalDocumentType | A payment receipt or credit note cannot use the 'to be defined' payment form. |
| `fiscal_document.payment_other_customer` | 422 | customer_payment_id: int | The customer payment named belongs to another customer. |
| `fiscal_document.product_unpriced` | 422 | product_id: int | The product has no price in the customer's price list. |
| `fiscal_document.quantity_exceeds_invoiceable` | 422 | remaining: decimal, line?: int | A line's quantity is more than what is still invoiceable from the sales order. |
| `fiscal_document.receipt_needs_payment_data` | 422 | - | The payment receipt has no payment date or amount. |
| `fiscal_document.receipt_pays_no_invoice` | 422 | - | The payment receipt lists no invoices. |
| `fiscal_document.recipient_incomplete` | 422 | - | The recipient lacks its tax regime or postal code. |
| `fiscal_document.recipient_not_linked` | 422 | customer_id: int, recipient: str | The recipient RFC is not linked to the customer. |
| `fiscal_document.relation_not_allowed` | 422 | relation_type: str | The related document does not qualify for the relation type. |
| `fiscal_document.relation_types_mixed` | 422 | - | A draft's relations must all use one relation type. |
| `fiscal_document.series_locked` | 409 | - | The series cannot change because the invoice already has a serial number. |
| `fiscal_document.series_missing` | 422 | batch: str | The series was removed before the document could be numbered. |
| `fiscal_document.series_not_of_type` | 422 | batch: str, type: enum:FiscalDocumentType | The series named is not one of the issuer's series for this document type. |
| `fiscal_document.type_not_supported` | 409 | type: enum:FiscalDocumentType | The operation does not apply to this fiscal document type. |
| `fiscal_document.unknown_facility` | 422 | facility_id: int | The store named for the draft does not exist. |
| `fiscal_document.unknown_usage` | 422 | usage: str | The CFDI usage key is not in the SAT catalog. |
| `fiscal_document.version_not_printable` | 409 | version: str | The document is not CFDI 4.0, so it cannot be printed. |
| `fiscal_document.xml_unavailable` | 409 | - | The stored stamped XML is missing or cannot be parsed. |
| `product.not_purchasable` | 422 | product_id: int, product_code: str | The product is not flagged as purchasable. |
| `purchase_order.already_cancelled` | 409 | - | The purchase order was already cancelled. |
| `purchase_order.confirmed_cannot_cancel` | 409 | - | A confirmed purchase order cannot be cancelled. |
| `purchase_order.line_without_quantity` | 409 | product_code: str | A line has no quantity at confirmation. |
| `purchase_order.no_lines` | 409 | - | A purchase order cannot be confirmed without lines. |
| `purchase_order.no_supplier` | 409 | - | A purchase order cannot be confirmed without a supplier. |
| `purchase_order.not_confirmed` | 409 | purchase_order_id: int | The purchase order is not confirmed. |
| `purchase_order.nothing_pending` | 409 | supplier_id: int, facility_id: int | No approved request lines are pending for this supplier and facility. |
| `purchase_order.nothing_to_add` | 409 | purchase_request_id: int | All the request's lines are already on a purchase order. |
| `purchase_order.request_not_approved` | 409 | purchase_request_id: int | The purchase request is not approved, so its lines cannot be ordered. |
| `purchase_request.already_cancelled` | 409 | - | The request was already cancelled. |
| `purchase_request.facility_locked` | 409 | - | Once a request has a serial number it cannot change facility. |
| `purchase_request.line_on_live_order` | 409 | line_id: int | The line cannot be unmarked because it is on a live purchase order. |
| `purchase_request.line_without_quantity` | 422 | line_id: int | A line has no quantity at submit. |
| `purchase_request.lines_not_markable` | 409 | status: enum:RequestStatus | Lines can be marked only while the request is submitted or approved. |
| `purchase_request.lines_on_live_order` | 409 | count: int | The request cannot be cancelled while some lines are on a live purchase order. |
| `purchase_request.no_lines` | 409 | - | The request has no lines at submit. |
| `purchase_request.not_draft` | 409 | status: enum:RequestStatus | The request is no longer a draft. |
| `purchase_request.not_submitted` | 409 | status: enum:RequestStatus | The request is not in the submitted state. |
| `purchase_request.warehouse_facility_changed` | 409 | - | The warehouse moved to another facility while the request was being changed. |
| `status_filter.unsupported` | 422 | resource: str='expense_voucher', status: enum:DocumentStatus, resource: str='purchase_order', resource: str='purchase_request', status: enum:RequestStatus | A status filter value that the list does not support. |
| `supplier_payment.already_cancelled` | 409 | - | The payment was already cancelled. |
| `supplier_payment.amount_exceeds_balance` | 409 | purchase_order_id: int, balance: decimal | An application is more than the purchase order's balance. |
| `supplier_payment.amounts_exceed_credit` | 409 | credit: decimal | The applications total more than the payment's remaining credit. |
| `supplier_payment.application_already_reversed` | 409 | application_id: int | The application was already reversed. |
| `supplier_payment.cancelled` | 409 | - | A cancelled payment cannot receive applications. |
| `supplier_payment.cutoff_not_set` | 409 | - | The supplier balance cutoff has not been configured. |
| `supplier_payment.facility_not_nameable` | 422 | - | The caller may not name a facility for the payment. |
| `supplier_payment.legacy_not_applicable` | 409 | - | A legacy payment (no facility) cannot receive applications. |
| `supplier_payment.order_before_cutoff` | 409 | purchase_order_id: int | The purchase order predates the balance cutoff. |
| `supplier_payment.order_other_supplier` | 409 | purchase_order_id: int | The purchase order belongs to a different supplier than the payment. |
| `taxpayer_certificate.holder_mismatch` | 422 | certificate_taxpayer: str, taxpayer: str | The certificate names a different holder than the issuer it is attached to. |
| `taxpayer_issuer.unknown` | 422 | taxpayer: str | The taxpayer issuer named does not exist. |
| `warehouse.required` | 422 | resource: str='purchase_order', resource: str='purchase_request' | No warehouse was sent and the user's point of sale has none. |
| `csd.certificate_unreadable` | 422 | - | The .cer is neither DER nor PEM. |
| `csd.key_not_rsa` | 422 | - | The key file holds a non-RSA key. |
| `csd.key_not_encrypted` | 422 | - | The key opens without a password, so it is not a SAT CSD key. |
| `csd.key_unreadable` | 422 | - | The key password is wrong or the .key file is unreadable (the library cannot tell which). |
| `csd.certificate_not_rsa` | 422 | - | The certificate public key is not RSA. |
| `csd.key_mismatch` | 422 | - | The key and the certificate are not a pair. |

Distinct new codes: 90 (including 6 csd.*). Distinct schema types: 21 (21 raise sites; the spec counts 23 as `_not_past` and `_distinct` once per field they are applied to).

### 3. Odd

- Leftovers: none. Every raises.tsv row outside my two lists belongs to list A or B. The spec's area counts (Purchasing 42, Fiscal 25) exceed my 53 because the `_conflict`/`_error` helpers count as one row each in raises.tsv; call-site expansion gives 170 rows.
- `status_filter.unsupported` at expense_voucher_service.py:62 and purchase_order_service.py:68: reachable only for a DocumentStatus member the clause does not handle (likely `paid`); purchase_request_service.py:81 looks unreachable (all four RequestStatus members handled).
- 409 but reads like a validation: `expense_voucher.total_not_positive` (expense_voucher_service.py:393). Status kept.
- Status split for one situation: "line has no quantity at confirm/submit" is 409 for purchase orders (`purchase_order.line_without_quantity`) and 422 for purchase requests (`purchase_request.line_without_quantity`). Two codes, flagged.
- `cash_session.not_open` (409, "An open cash session is required") is used by expense_voucher_service.py:248/:373/:380 and supplier_payment_service.py:351. The cash worker may have named this situation already (e.g. for payments or refunds); consolidate to one spelling.
- supplier_payment_service.py:332 ("A facility is required") mapped to the fixed `user.no_facility`; the message does not say "your user", so confirm this is the same situation.
- `taxpayer_issuer.unknown` (422) is shared by invoice_service.py:274 and taxpayer_certificate_service.py:80; the taxpayer_issuers worker may name its own 404 differently.
- `expense_voucher.not_confirmed` carries `action` ('print'|'void'); `document.not_editable` is used for PO/voucher "only a draft can be changed/confirmed" (status completed|cancelled). Purchase requests cannot use it because RequestStatus has submitted/approved.
- fiscal_documents.py:252 `auth.forbidden` required: **Resolved (analysis A1):** `[{system_object: 103, right: 4}]`, raw ints.
- Resource names introduced that are not model tables or straightforward: `fiscal_document_line`, `fiscal_document_relation` (assumed from tables fiscal_document_detail / fiscal_document_relation), `purchase_order_payment` (application row), `expense_voucher_line`, `purchase_order_line`, `purchase_request_line`.
- supplier_payment_service.py:301 raises not_found without an id (a set of PO ids is locked, the missing one is not computed).
- invoice_service.py:730 and `csd.*`: the ValueError caught at :730 comes from the certificate signing build, not `csd_service`; its text only goes in `detail`.
- Non-schema `ValueError`s not user-facing as HTTP errors: `app/services/stock_ledger.py` (:38, :88: programming-contract checks, impossible via the API) and `app/rendering/fetcher.py:35` (Refused to fetch URL, internal). `image_service.py:33/:39/:96` belong to the image owner (the six `str(e)` routers).
- Schema validators reused across fields: `_not_past` (purchases.py:151, :159) and `_distinct` (supplier_payments.py:58, :80) produce one type each; accounts.py:123 delegates to sales_quote's validator.
- `fiscal_document.series_missing` params batch may be None (omit); `fiscal_document.payment_already_receipted` carries `fiscal_document_id` since the folio is formatted text.

