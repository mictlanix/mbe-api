# Error codes

Every code the API serves, with its HTTP status, its `params` and the situation it names (spec FR-017). `tests/unit/test_contract_docs.py` checks this table against `ErrorCode` in both directions, code and status.

- `params` hold raw values only: `int`, `decimal` (a string with the stored scale, `"18450.00"`), `date` / `datetime` (ISO 8601), `enum:<Name>` (the value the API serializes for that field: IntEnums as ints, StrEnums as strings), and plain `str` only for keys the client already knows (a product code, a SAT key, a field name). A `?` marks a param that can be absent.
- A code is never renamed or reused. A retired code stays here, marked deprecated.
- Codes are grouped by domain, alphabetically.

## auth

| Code | Status | Params | Situation |
|---|---|---|---|
| `auth.administrator_required` | 403 | — | The route is for administrators only. |
| `auth.forbidden` | 403 | required: list[{system_object: int, right: int}] | The user lacks the privileges the route needs. Each entry is one grant that would open it: a `SystemObject` value and an `AccessRight` value, the raw ints `/user-profiles` privileges use. Any one entry is enough. |
| `auth.incorrect_current_password` | 400 | — | The old password given to change-password did not verify. |
| `auth.invalid_credentials` | 401 | — | The user name or password is wrong. |
| `auth.not_authenticated` | 401 | — | No token, or the token is invalid or expired. |
| `auth.password_change_required` | 403 | — | The user must change their password first. `detail` is exactly `password_change_required`. |
| `auth.recovery_token_invalid` | 400 | — | The password-recovery token is wrong type, has no subject, or is expired or malformed. |

## cash_session

| Code | Status | Params | Situation |
|---|---|---|---|
| `cash_session.already_closed` | 409 | — | The cash session is already closed. |
| `cash_session.closed` | 409 | — | The cash session was closed while the request was running. |
| `cash_session.drawer_busy` | 409 | cash_drawer_id: int | Another cashier has an open session on this drawer. |
| `cash_session.not_closed` | 409 | — | The cash session is still open, so it cannot be counted or printed as closed. |
| `cash_session.not_open` | 409 | — | The action needs an open cash session and the user has none. |
| `cash_session.user_busy` | 409 | cash_session_id: int | The user already has an open session. |

## commission_agent

| Code | Status | Params | Situation |
|---|---|---|---|
| `commission_agent.salesperson_not_agent` | 422 | salesperson_id: int | A commission rate names a salesperson who is not a commission agent. |

## commission_period

| Code | Status | Params | Situation |
|---|---|---|---|
| `commission_period.already_closed` | 409 | year: int, month: int | Closing a commission period that is already closed. |
| `commission_period.already_paid` | 409 | year: int, month: int | Closing or paying a commission period that is already paid. |
| `commission_period.later_closed` | 409 | year: int, month: int | A later commission period is already closed, so this one cannot be closed or reopened. |
| `commission_period.not_closed` | 409 | year: int, month: int | The commission period is not closed (reopen or pay needs a closed one). |
| `commission_period.not_ended` | 409 | year: int, month: int | The commission period has not ended yet, so it cannot be closed. |
| `commission_period.paid_cannot_reopen` | 409 | year: int, month: int | A paid commission period is final and cannot be reopened. |
| `commission_period.previous_not_closed` | 409 | year: int, month: int | The previous commission period is not closed; months close in sequence. |

## csd

| Code | Status | Params | Situation |
|---|---|---|---|
| `csd.certificate_not_rsa` | 422 | — | The certificate public key is not RSA. |
| `csd.certificate_unreadable` | 422 | — | The .cer is neither DER nor PEM. |
| `csd.key_mismatch` | 422 | — | The key and the certificate are not a pair. |
| `csd.key_not_encrypted` | 422 | — | The key opens without a password, so it is not a SAT CSD key. |
| `csd.key_not_rsa` | 422 | — | The key file holds a non-RSA key. |
| `csd.key_unreadable` | 422 | — | The key password is wrong or the .key file is unreadable (the library cannot tell which). |

## customer

| Code | Status | Params | Situation |
|---|---|---|---|
| `customer.credit_hold` | 422 | customer_id: int, overdue_count: int, action: confirm or raise | The customer has overdue credit orders, so no new credit order can be raised or confirmed. |
| `customer.credit_limit_exceeded` | 422 | customer_id: int, outstanding: decimal, limit: decimal | This credit order would take the customer over their credit limit. |
| `customer.credit_limit_reached` | 422 | customer_id: int, outstanding: decimal, limit: decimal | A credit customer already owes at or over their limit, so a cash order is refused too. |
| `customer.default_not_deletable` | 409 | — | The system default (walk-in) customer cannot be deleted. |
| `customer.no_credit_limit` | 422 | customer_id: int | The customer has no credit limit, so they cannot buy on credit. |
| `customer.walk_in_no_credit` | 422 | — | The walk-in customer cannot buy on credit. |

## customer_discount

| Code | Status | Params | Situation |
|---|---|---|---|
| `customer_discount.product_not_found` | 422 | product_id: int | A customer discount names a product that does not exist (422, not 404). |

## delivery_order

| Code | Status | Params | Situation |
|---|---|---|---|
| `delivery_order.insufficient_notice` | 422 | min_hours: int, earliest: datetime | Confirming a delivery order dated sooner than the minimum notice allows. |
| `delivery_order.line_already_present` | 409 | sales_order_line_id: int, delivery_order_line_id: int | The sales order line is already on this delivery order. |
| `delivery_order.line_not_deliverable` | 422 | sales_order_line_id: int | Requested sales order line is not an undelivered line of this customer or sale. |
| `delivery_order.line_repeated` | 422 | sales_order_line_id: int | The same sales order line was requested twice. |
| `delivery_order.mixed_not_allowed` | 422 | — | A delivery order cannot have MIXED fulfilment type. |
| `delivery_order.no_lines` | 409 | — | Confirming a delivery order with no lines. |
| `delivery_order.no_lines_from_warehouse` | 422 | warehouse_id: int | Ticket filtered by a warehouse none of the lines come from. |
| `delivery_order.not_approved` | 409 | status: enum:DeliveryOrderStatus | Printing an order that is still draft or awaiting approval. |
| `delivery_order.not_editable` | 409 | status: enum:DeliveryOrderStatus | Editing a delivery order that is no longer a draft. |
| `delivery_order.not_failed` | 409 | status: enum:DeliveryOrderStatus | Re-queueing an order that did not fail. |
| `delivery_order.not_in_preparation` | 409 | status: enum:DeliveryOrderStatus | Loading onto a trip an order that is not IN_PREPARATION. |
| `delivery_order.not_pending_approval` | 409 | status: enum:DeliveryOrderStatus, action: str (`approve` or `reject`) | Approving or rejecting an order not awaiting approval. |
| `delivery_order.pickup_not_loadable` | 409 | — | A counter-pickup order cannot be loaded onto a trip. |
| `delivery_order.proof_incomplete` | 422 | — | Proof of delivery lacks receiver name or identification shown. |
| `delivery_order.quantity_exceeds_remaining` | 422 | sales_order_line_id: int, remaining: decimal, requested: decimal | Requested quantity is more than the sales line still owes. |
| `delivery_order.reason_required` | 422 | to: enum:DeliveryOrderStatus | Moving an order to DRAFT/FAILED/CANCELLED without a reason. |
| `delivery_order.sales_order_fully_delivered` | 409 | — | Sales order has nothing left to deliver. |
| `delivery_order.sales_order_not_deliverable` | 409 | — | Sales order is not completed or is cancelled. |
| `delivery_order.sales_order_unpaid` | 422 | — | Sales order must be paid or on credit terms (setting-gated). |
| `delivery_order.unknown_warehouse` | 422 | warehouse_id: int | Ticket filter names a warehouse that does not exist (422, not 404, today). |

## delivery_order_line

| Code | Status | Params | Situation |
|---|---|---|---|
| `delivery_order_line.nothing_to_load` | 422 | — | Committing a line with nothing left to load. |
| `delivery_order_line.quantity_unavailable` | 422 | available: decimal, requested: decimal | Committing or adjusting more than the line has available. |

## document

| Code | Status | Params | Situation |
|---|---|---|---|
| `document.not_editable` | 409 | status: enum:DocumentStatus | The document is completed or cancelled and can no longer be edited. |

## email

| Code | Status | Params | Situation |
|---|---|---|---|
| `email.contact_no_address` | 422 | contact_id: int | A chosen contact has no email address. |
| `email.contact_not_of_party` | 422 | contact_id: int | A chosen contact does not belong to the customer or supplier being emailed. |
| `email.no_recipients` | 422 | — | The statement email has no recipient after merging contacts and typed addresses. |
| `email.not_configured` | 503 | — | Outgoing email is not configured on the server. |
| `email.send_failed` | 502 | — | The mail server refused or failed the send; the reason stays in `detail`. |
| `email.too_many_recipients` | 422 | max: int | The statement email has more than the maximum recipients. |

## expense_voucher

| Code | Status | Params | Situation |
|---|---|---|---|
| `expense_voucher.not_cashier` | 403 | — | A draft voucher can be changed only by the cashier who created it. |
| `expense_voucher.not_confirmed` | 409 | status: enum:DocumentStatus, action: str='print', action: str='void' | A voucher is not confirmed, so it cannot be printed or voided. |
| `expense_voucher.total_not_positive` | 409 | — | The voucher total is zero or negative at confirmation. |
| `expense_voucher.unknown_category` | 422 | expense_id: int | The expense category named on the voucher line does not exist. |

## facility

| Code | Status | Params | Situation |
|---|---|---|---|
| `facility.no_dispatch_warehouse` | 422 | facility_id: int | Facility has no non-transit warehouse to dispatch from. |
| `facility.no_in_transit_location` | 422 | facility_ids: list[int] | A facility has no in-transit location. |
| `facility.other_facility` | 403 | resource: str, required?: list[{system_object: int, right: int}] | The record belongs to another facility and the user lacks the cross-facility privilege. `required` has the same shape as `auth.forbidden`'s; it is omitted where no privilege opens the record (inventory documents: administrators only). |
| `facility.required` | 422 | — | The request must name a facility (the user has none to default to). |

## fiscal_document

| Code | Status | Params | Situation |
|---|---|---|---|
| `fiscal_document.already_related` | 409 | relation_id: int | The document is already related to this one. |
| `fiscal_document.amount_exceeds_balance` | 422 | balance: decimal | The amount paid is zero/negative or more than the invoice balance. |
| `fiscal_document.amounts_exceed_payment` | 422 | — | The amounts paid exceed the receipt's payment amount. |
| `fiscal_document.amounts_not_equal_payment` | 422 | — | The amounts paid do not add up to the receipt's payment amount. |
| `fiscal_document.certificate_cannot_sign` | 422 | — | The CSD could not sign the document. |
| `fiscal_document.credit_exceeds_invoice` | 422 | invoice_id: int, remaining: decimal | The credit note total is more than what is left to credit on the invoice. |
| `fiscal_document.credit_quantity_exceeds_line` | 422 | max: decimal | The credited quantity is more than the invoice line's quantity. |
| `fiscal_document.field_not_for_type` | 422 | fields: list[str], type: enum:FiscalDocumentType | The request sets header fields that this document type does not take. |
| `fiscal_document.invoice_already_on_receipt` | 409 | invoice_id: int | The invoice is already listed on the receipt. |
| `fiscal_document.invoice_not_creditable` | 422 | invoice_id: int | The invoice is not eligible for a credit note. |
| `fiscal_document.invoice_not_payable` | 422 | invoice_id: int | The invoice cannot be paid by this receipt. |
| `fiscal_document.issue_date_out_of_range` | 422 | — | The issue date is in the future or more than 72 hours ago. |
| `fiscal_document.issuer_has_no_series` | 422 | type: enum:FiscalDocumentType | The issuer has no series for this document type. |
| `fiscal_document.line_missing_sat_key` | 422 | line: int | A line lacks its SAT product/service key or unit of measurement. |
| `fiscal_document.line_quantity_not_positive` | 422 | line: int | A line's quantity is zero or negative. |
| `fiscal_document.no_invoiceable_lines` | 422 | — | The sales order has no lines left to invoice. |
| `fiscal_document.no_lines` | 422 | — | The document has no lines. |
| `fiscal_document.no_valid_certificate` | 422 | — | The issuer has no active CSD valid at the issue date. |
| `fiscal_document.not_draft` | 409 | — | The fiscal document has been issued and can no longer be edited. |
| `fiscal_document.not_emailable` | 409 | — | The document is not a stamped, uncancelled CFDI 4.0 with its XML, so it cannot be emailed. |
| `fiscal_document.not_issued` | 409 | — | The fiscal document is still a draft (not issued), so its XML or PDF does not exist. |
| `fiscal_document.not_sealed` | 409 | — | The document has not been sealed (issued) yet. |
| `fiscal_document.not_stamped` | 409 | — | The document has no stamp UUID, so it cannot be printed. |
| `fiscal_document.order_not_invoiceable` | 422 | sales_order_id: int | The sales order cannot be invoiced because it is not completed or was cancelled. |
| `fiscal_document.payment_already_receipted` | 409 | fiscal_document_id: int | A payment receipt already exists for this customer payment. |
| `fiscal_document.payment_date_out_of_range` | 422 | — | The receipt's payment date is before an invoice's date or after today. |
| `fiscal_document.payment_form_required` | 422 | type: enum:FiscalDocumentType | A payment receipt or credit note cannot use the 'to be defined' payment form. |
| `fiscal_document.payment_other_customer` | 422 | customer_payment_id: int | The customer payment named belongs to another customer. |
| `fiscal_document.product_unpriced` | 422 | product_id: int | The product has no price in the customer's price list. |
| `fiscal_document.quantity_exceeds_invoiceable` | 422 | remaining: decimal, line?: int | A line's quantity is more than what is still invoiceable from the sales order. |
| `fiscal_document.receipt_needs_payment_data` | 422 | — | The payment receipt has no payment date or amount. |
| `fiscal_document.receipt_pays_no_invoice` | 422 | — | The payment receipt lists no invoices. |
| `fiscal_document.recipient_incomplete` | 422 | — | The recipient lacks its tax regime or postal code. |
| `fiscal_document.recipient_not_linked` | 422 | customer_id: int, recipient: str | The recipient RFC is not linked to the customer. |
| `fiscal_document.relation_not_allowed` | 422 | relation_type: str | The related document does not qualify for the relation type. |
| `fiscal_document.relation_types_mixed` | 422 | — | A draft's relations must all use one relation type. |
| `fiscal_document.series_locked` | 409 | — | The series cannot change because the invoice already has a serial number. |
| `fiscal_document.series_missing` | 422 | batch: str | The series was removed before the document could be numbered. |
| `fiscal_document.series_not_of_type` | 422 | batch: str, type: enum:FiscalDocumentType | The series named is not one of the issuer's series for this document type. |
| `fiscal_document.type_not_supported` | 409 | type: enum:FiscalDocumentType | The operation does not apply to this fiscal document type. |
| `fiscal_document.unknown_facility` | 422 | facility_id: int | The store named for the draft does not exist. |
| `fiscal_document.unknown_usage` | 422 | usage: str | The CFDI usage key is not in the SAT catalog. |
| `fiscal_document.version_not_printable` | 409 | version: str | The document is not CFDI 4.0, so it cannot be printed. |
| `fiscal_document.xml_unavailable` | 409 | — | The stored stamped XML is missing or cannot be parsed. |

## image

| Code | Status | Params | Situation |
|---|---|---|---|
| `image.too_large` | 422 | max_bytes: int | Uploaded image exceeds the size limit. |
| `image.unsupported_format` | 422 | — | Uploaded file is not a decodable image. |

## incidence

| Code | Status | Params | Situation |
|---|---|---|---|
| `incidence.reason_required` | 422 | — | Audit entry reason is blank. |

## inventory_count

| Code | Status | Params | Situation |
|---|---|---|---|
| `inventory_count.batch_invalid` | 422 | — (`errors[]`: index, product or product_code, reason, code) | A batch of counted lines has bad entries; nothing was applied. |
| `inventory_count.confirmed_cannot_cancel` | 409 | — | Cancelling a confirmed count. |

## inventory_count_line

| Code | Status | Params | Situation |
|---|---|---|---|
| `inventory_count_line.not_stockable` | 422 | — (an `errors[]` item) | An entry names a product that is not stockable. |
| `inventory_count_line.product_reference` | 422 | — (an `errors[]` item) | An entry gives both or neither of product and product_code. |
| `inventory_count_line.product_unknown` | 422 | — (an `errors[]` item) | An entry names an unknown product. |
| `inventory_count_line.repeated` | 422 | — (an `errors[]` item) | An entry repeats a product already in the batch. |

## inventory_document

| Code | Status | Params | Situation |
|---|---|---|---|
| `inventory_document.already_cancelled` | 409 | resource: str (`inventory_receipt`, `inventory_issue`, `inventory_transfer` or `inventory_count`) | The document is already cancelled. |
| `inventory_document.insufficient_stock` | 409 | product_id: int, warehouse_id: int, requested: decimal, available: decimal | A stock-out document would take a product below what is available. |
| `inventory_document.line_without_quantity` | 422 | product_code: str | Confirming a document with a zero-quantity line. |
| `inventory_document.no_lines` | 409 | resource: str (`inventory_receipt`, `inventory_issue`, `inventory_transfer` or `inventory_count`) | Confirming a document with no lines. |

## inventory_receipt

| Code | Status | Params | Situation |
|---|---|---|---|
| `inventory_receipt.warehouse_fixed` | 409 | — | A purchase order's receipt cannot move to another warehouse: its lines are that warehouse's order lines. |
| `inventory_receipt.complementary_unavailable` | 409 | reason: str enum (no_purchase_order, purchase_order_cancelled, purchase_order_not_completed, purchase_order_fully_received) | No complementary receipt can be created for this receipt. |

## inventory_transfer

| Code | Status | Params | Situation |
|---|---|---|---|
| `inventory_transfer.line_repeated` | 422 | line_id: int | Same transfer line received twice in one request. |
| `inventory_transfer.not_in_transit` | 409 | — | Receiving a transfer that is not in transit. |
| `inventory_transfer.over_received` | 409 | product_code: str, received: decimal, outstanding: decimal | Receiving more than outstanding on a transfer line. |
| `inventory_transfer.same_warehouse` | 422 | — | Origin and destination warehouses are the same. |

## itinerary

| Code | Status | Params | Situation |
|---|---|---|---|
| `itinerary.cancelled` | 409 | — | Printing a cancelled itinerary. |
| `itinerary.lines_overcommitted` | 422 | — (dict `detail` kept: message, lines) | Lines are committed beyond what remains to deliver. Phase 3 moves the lines to `errors[]`. |
| `itinerary.not_departed` | 409 | status: enum:ItineraryStatus | Printing or closing stops of an itinerary that has not departed. |
| `itinerary.not_open` | 409 | status: enum:ItineraryStatus | Changing an itinerary that is no longer OPEN. |
| `itinerary.nothing_committed` | 409 | — | Departing an itinerary with nothing committed. |
| `itinerary.stop_already_resolved` | 409 | — | Closing a stop that is already resolved. |
| `itinerary.stop_delivered_exceeds_sent` | 422 | line_id: int, sent: decimal, delivered: decimal | Stop close accepts more than was sent. |
| `itinerary.stop_lines_incomplete` | 422 | — | Stop close does not account for every line. |
| `itinerary.stop_lines_malformed` | 422 | — | `lines` form field is not a JSON array. |
| `itinerary.stop_shortfall_needs_reason` | 422 | line_id: int | Delivered less than sent with no reason code. |

## kardex

| Code | Status | Params | Situation |
|---|---|---|---|
| `kardex.range_reversed` | 422 | — | Kardex range ends before it starts. |
| `kardex.range_too_long` | 422 | max_days: int | Kardex range exceeds the maximum. |

## lock

| Code | Status | Params | Situation |
|---|---|---|---|
| `lock.busy` | 409 | resource: str (`warehouse`, `facility` or `sales_order`) | Another request holds the lock; retry. |

## payment

| Code | Status | Params | Situation |
|---|---|---|---|
| `payment.already_verified` | 409 | — | The payment has already been verified. |
| `payment.application_reversed` | 409 | — | The payment application was already reversed. |
| `payment.currency_mismatch` | 422 | payment_currency: enum:CurrencyCode, order_currency: enum:CurrencyCode | The payment currency differs from the order currency. |
| `payment.customer_mismatch` | 422 | payment_customer_id: int, order_customer_id: int | The payment belongs to a different customer than the order. |
| `payment.exceeds_order_balance` | 422 | amount: decimal, balance: decimal | An application amount is more than the order's remaining balance. |
| `payment.exceeds_unapplied` | 422 | amount: decimal, unapplied: decimal | An application amount is more than the payment's unapplied balance. |
| `payment.no_ticket` | 409 | payment_type: enum:PaymentType | The payment type has no ticket to print (only credit payment and payment in advance do). |
| `payment.order_cancelled` | 409 | — | A cancelled sales order cannot be paid. |
| `payment.order_not_completed` | 409 | — | Only a completed (confirmed) sales order can be paid. |
| `payment.order_settled` | 409 | — | Nothing is owed on the sales order, so it takes no application. |

## point_of_sale

| Code | Status | Params | Situation |
|---|---|---|---|
| `point_of_sale.warehouse_facility_mismatch` | 422 | warehouse_id: int, facility_id: int | The point of sale's warehouse is in a different facility than the point of sale. |

## price_list

| Code | Status | Params | Situation |
|---|---|---|---|
| `price_list.cost_not_assignable` | 400 | — | The cost price list cannot be assigned to a customer or used as a replacement. |
| `price_list.cost_not_deletable` | 400 | — | The cost price list cannot be deleted. |
| `price_list.cost_read_only` | 400 | — | The cost price list is read-only for price writes. |
| `price_list.replace_with_itself` | 400 | — | A price list cannot be replaced by itself on delete. |

## product

| Code | Status | Params | Situation |
|---|---|---|---|
| `product.lookup_criteria_required` | 422 | — | The product lookup needs a pattern or a list of product ids. |
| `product.lookup_too_many` | 422 | max: int | The product lookup names more ids than the maximum. |
| `product.merge_with_itself` | 400 | — | A product cannot be merged into itself. |
| `product.not_purchasable` | 422 | product_id: int, product_code: str | The product is not flagged as purchasable. |
| `product.not_stockable` | 422 | product_code: str | Product is not stockable. |

## product_price

| Code | Status | Params | Situation |
|---|---|---|---|
| `product_price.duplicate_entry` | 400 | product_id: int, price_list_id: int | A bulk price write names the same (product, price list) cell twice. |
| `product_price.references_not_found` | 404 | resource: str (product | price_list), ids: list[int] |

## purchase_order

| Code | Status | Params | Situation |
|---|---|---|---|
| `purchase_order.already_cancelled` | 409 | — | The purchase order was already cancelled. |
| `purchase_order.confirmed_cannot_cancel` | 409 | — | A confirmed purchase order cannot be cancelled. |
| `purchase_order.line_without_quantity` | 409 | product_code: str | A line has no quantity at confirmation. |
| `purchase_order.no_lines` | 409 | — | A purchase order cannot be confirmed without lines. |
| `purchase_order.no_supplier` | 409 | — | A purchase order cannot be confirmed without a supplier. |
| `purchase_order.not_confirmed` | 409 | purchase_order_id: int | The purchase order is not confirmed. |
| `purchase_order.nothing_pending` | 409 | supplier_id: int, facility_id: int | No approved request lines are pending for this supplier and facility. |
| `purchase_order.nothing_to_add` | 409 | purchase_request_id: int | All the request's lines are already on a purchase order. |
| `purchase_order.request_not_approved` | 409 | purchase_request_id: int | The purchase request is not approved, so its lines cannot be ordered. |

## purchase_request

| Code | Status | Params | Situation |
|---|---|---|---|
| `purchase_request.already_cancelled` | 409 | — | The request was already cancelled. |
| `purchase_request.facility_locked` | 409 | — | Once a request has a serial number it cannot change facility. |
| `purchase_request.line_on_live_order` | 409 | line_id: int | The line cannot be unmarked because it is on a live purchase order. |
| `purchase_request.line_without_quantity` | 422 | line_id: int | A line has no quantity at submit. |
| `purchase_request.lines_not_markable` | 409 | status: enum:RequestStatus | Lines can be marked only while the request is submitted or approved. |
| `purchase_request.lines_on_live_order` | 409 | count: int | The request cannot be cancelled while some lines are on a live purchase order. |
| `purchase_request.no_lines` | 409 | — | The request has no lines at submit. |
| `purchase_request.not_draft` | 409 | status: enum:RequestStatus | The request is no longer a draft. |
| `purchase_request.not_submitted` | 409 | status: enum:RequestStatus | The request is not in the submitted state. |
| `purchase_request.warehouse_facility_changed` | 409 | — | The warehouse moved to another facility while the request was being changed. |

## quote

| Code | Status | Params | Situation |
|---|---|---|---|
| `quote.already_cancelled` | 409 | — | Cancelling a quote that is already cancelled. |
| `quote.cancelled` | 409 | — | A cancelled quote cannot be converted. |
| `quote.expired` | 409 | — | The quote has expired. |
| `quote.not_confirmed` | 409 | — | The quote is not confirmed. |

## refund

| Code | Status | Params | Situation |
|---|---|---|---|
| `refund.already_cancelled` | 409 | — | The refund is already cancelled. |
| `refund.completed_cannot_cancel` | 409 | — | A completed refund is final and cannot be cancelled. |
| `refund.line_invoiced` | 409 | product_code: str, invoice?: str | The units of a line are on a live invoice and must be un-invoiced before refunding. `invoice` is the invoice's folio, an identifier like a non-numeric id; omitted when the invoice has none. |
| `refund.no_returnable_lines` | 409 | — | The refund has no line with a returnable quantity. |
| `refund.nothing_refundable` | 409 | — | No item on the order remains to be refunded. |
| `refund.order_not_completed` | 409 | — | The order is not completed, so it cannot be refunded. |
| `refund.order_not_paid` | 409 | — | The order is not paid; unpaid orders are cancelled, not refunded. |
| `refund.quantity_exceeds_refundable` | 422 | requested: decimal, refundable: decimal | The requested return quantity is more than the refundable quantity. |

## request

| Code | Status | Params | Situation |
|---|---|---|---|
| `request.malformed` | 400 | — | The framework could not parse the request body. |
| `request.method_not_allowed` | 405 | — | The route does not accept this method. |
| `request.route_not_found` | 404 | — | No route matches the URL. |

## resource

| Code | Status | Params | Situation |
|---|---|---|---|
| `resource.already_exists` | 409 | resource: str, field: str | A record with this value already exists. A composite key joins its field names with commas. |
| `resource.conflict` | 409 | — | A write broke a database constraint no service check caught first. |
| `resource.not_found` | 404 | resource: str, id?: int or str | The requested record does not exist. `id` is omitted when the request names none. |
| `resource.still_referenced` | 409 | references: list[{resource: str, count: int}], more: int | Other records still point at the one being deleted; the five largest are listed, `more` counts the rest. |

## sales_order

| Code | Status | Params | Situation |
|---|---|---|---|
| `sales_order.already_cancelled` | 409 | — | The sales order is already cancelled. |
| `sales_order.below_min_quantity` | 422 | product_id: int, min_order_qty: decimal | A line orders less than the product's minimum order quantity. |
| `sales_order.has_payment_applications` | 409 | application_ids: list[int] | The sales order still has payment applications to reverse first. |
| `sales_order.insufficient_stock` | 409 | — (dict `detail` kept: message, lines) | Lines lack stock or a warehouse. Phase 3 moves the lines to `errors[]`. |
| `sales_order.missing_delivery_details` | 409 | — (dict `detail` kept: message, missing) | A back-office order lacks delivery details. Phase 3 moves the parts to `errors[]`. |
| `sales_order.no_lines` | 409 | — | The sales order has no lines to confirm. |
| `sales_order.not_confirmed` | 409 | sales_order_id: int | Sales order is not confirmed or is cancelled (loading its lines into a transfer). |
| `sales_order.paid_cannot_cancel` | 409 | — | A paid sales order cannot be cancelled. |
| `sales_order.zero_priced_lines` | 409 | — (dict `detail` kept: message, lines) | Lines are priced at zero. Phase 3 moves the lines to `errors[]`. |

## server

| Code | Status | Params | Situation |
|---|---|---|---|
| `server.error` | 500 | — | An unexpected server failure. Nothing about it is returned. |

## state

| Code | Status | Params | Situation |
|---|---|---|---|
| `state.transition_invalid` | 409 | resource: str, from: enum, to: enum, required?: enum | The record cannot move from its current status to the one asked for (or only for another fulfillment type). |

## status_filter

| Code | Status | Params | Situation |
|---|---|---|---|
| `status_filter.unsupported` | 422 | allowed: list[str] | The status filter is not one of the values the list accepts. |

## supplier_agreement

| Code | Status | Params | Situation |
|---|---|---|---|
| `supplier_agreement.end_before_start` | 422 | start: date, end: date | The agreement's end date is before its start date. |

## supplier_payment

| Code | Status | Params | Situation |
|---|---|---|---|
| `supplier_payment.already_cancelled` | 409 | — | The payment was already cancelled. |
| `supplier_payment.amount_exceeds_balance` | 409 | purchase_order_id: int, balance: decimal | An application is more than the purchase order's balance. |
| `supplier_payment.amounts_exceed_credit` | 409 | credit: decimal | The applications total more than the payment's remaining credit. |
| `supplier_payment.application_already_reversed` | 409 | application_id: int | The application was already reversed. |
| `supplier_payment.cancelled` | 409 | — | A cancelled payment cannot receive applications. |
| `supplier_payment.cutoff_not_set` | 409 | — | The supplier balance cutoff has not been configured. |
| `supplier_payment.facility_not_nameable` | 422 | — | The caller may not name a facility for the payment. |
| `supplier_payment.legacy_not_applicable` | 409 | — | A legacy payment (no facility) cannot receive applications. |
| `supplier_payment.order_before_cutoff` | 409 | purchase_order_id: int | The purchase order predates the balance cutoff. |
| `supplier_payment.order_other_supplier` | 409 | purchase_order_id: int | The purchase order belongs to a different supplier than the payment. |

## taxpayer_batch

| Code | Status | Params | Situation |
|---|---|---|---|
| `taxpayer_batch.in_use` | 409 | action: str (`update` or `delete`) | The series (batch) has issued documents. |

## taxpayer_certificate

| Code | Status | Params | Situation |
|---|---|---|---|
| `taxpayer_certificate.holder_mismatch` | 422 | certificate_taxpayer: str, taxpayer: str | The certificate names a different holder than the issuer it is attached to. |

## taxpayer_issuer

| Code | Status | Params | Situation |
|---|---|---|---|
| `taxpayer_issuer.unknown` | 422 | taxpayer: str | The taxpayer issuer named does not exist. |

## user

| Code | Status | Params | Situation |
|---|---|---|---|
| `user.no_cash_drawer` | 422 | — | The user has no cash drawer configured. |
| `user.no_facility` | 422 | — | The user has no facility configured. |
| `user.no_point_of_sale` | 422 | — | The user has no point of sale configured. |

## user_profile

| Code | Status | Params | Situation |
|---|---|---|---|
| `user_profile.not_active` | 409 | profile_id: int | An inactive user profile cannot be applied to a user. |

## vehicle

| Code | Status | Params | Situation |
|---|---|---|---|
| `vehicle.open_itinerary_exists` | 409 | vehicle_id: int, itinerary_id: int | Vehicle already has an open itinerary. |

## warehouse

| Code | Status | Params | Situation |
|---|---|---|---|
| `warehouse.in_transit_location` | 422 | warehouse_id: int | Document line names an in-transit warehouse. |
| `warehouse.in_transit_managed` | 403 | — | In-transit warehouses are system-managed and cannot be edited by the user. |
| `warehouse.required` | 422 | resource: str (`purchase_order` or `purchase_request`) | No warehouse was sent and the user's point of sale has none. |

## Validation types

Not codes: these are the `type` of an entry in FastAPI's request-validation 422 list (`loc`, `msg`, `type`, `ctx`), which keeps its shape and `application/json` (spec FR-015). Each replaces a plain `value_error`. `msg` keeps today's sentence, without the "Value error, " prefix. Precedent: `password_policy` (`ctx.failed`).

| Type | Raised in | Today's message | ctx |
|---|---|---|---|
| `password_policy` | app/core/password_policy.py | (unchanged) | failed: list[str] |
| `contact_name_blank` | app/schemas/core.py | Name must not be blank | — |
| `email_address_invalid` | app/schemas/sales_quote.py | Not a valid email address: {raw!r} | value: str (the rejected address as sent) |
| `fiscal_document_payment_date_in_future` | app/schemas/fiscal.py | The payment date cannot be in the future | date: date |
| `fiscal_document_payment_form_unknown` | app/schemas/fiscal.py | Not a SAT payment form | payment_form: int |
| `fiscal_document_tax_rate_invalid` | app/schemas/fiscal.py | The tax rate must be 0.16, 0 or -1 (exempt) | tax_rate: decimal |
| `inventory_count_product_reference` | app/schemas/inventory.py | Give exactly one of product and product_code | — |
| `inventory_transfer_duplicate_line` | app/schemas/inventory.py | Each line may be received only once per request | — |
| `product_barcode_invalid` | app/schemas/product.py | Barcode must be empty or exactly 13 digits (EAN-13) | — |
| `product_code_has_whitespace` | app/schemas/product.py | Code must not contain whitespace | — |
| `profile_duplicate_system_object` | app/schemas/user.py | Duplicate system_object in privileges | — |
| `profile_unknown_system_object` | app/schemas/user.py | Unknown system object: {v} | system_object: int |
| `purchase_receipt_date_in_past` | app/schemas/purchases.py | The estimated receipt date cannot be in the past | date: date (the value sent), today: date |
| `purchase_request_reason_blank` | app/schemas/purchases.py | A reason is required and cannot be blank | — |
| `statement_email_no_recipient` | app/schemas/accounts.py | Choose at least one contact or type an address | — |
| `supplier_agreement_end_before_start` | app/schemas/supplier.py | End must not be before start | start: date, end: date |
| `supplier_payment_cash_has_date` | app/schemas/supplier_payments.py | A cash payment is dated now; send no date | — |
| `supplier_payment_cash_has_facility` | app/schemas/supplier_payments.py | A cash payment takes its session's facility; send no facility | — |
| `supplier_payment_date_in_future` | app/schemas/supplier_payments.py | The payment date cannot be in the future | date: datetime |
| `supplier_payment_duplicate_order` | app/schemas/supplier_payments.py | Each purchase order can appear only once | — |
| `taxpayer_batch_series_blank` | app/schemas/fiscal.py | The series cannot be blank | — |
| `user_id_not_alphanumeric` | app/schemas/user.py | Username must contain only alphanumeric characters | — |
