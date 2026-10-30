"""The error-code catalog (spec 036): every code a client can receive in a Problem body.

Each member carries its one HTTP status, so a raise can never pair a code with the wrong status.
Codes are never renamed or reused; a retired code stays here, marked deprecated. The published list,
with params and situations, is `specs/036-localizable-error-codes/contracts/error-codes.md`, and
`tests/unit/test_contract_docs.py` keeps the two in step.
"""

from enum import StrEnum


class ErrorCode(StrEnum):
    status: int

    def __new__(cls, value: str, status: int) -> 'ErrorCode':
        member = str.__new__(cls, value)
        member._value_ = value
        member.status = status
        return member

    # auth
    AUTH_ADMINISTRATOR_REQUIRED = 'auth.administrator_required', 403
    AUTH_FORBIDDEN = 'auth.forbidden', 403
    AUTH_INCORRECT_CURRENT_PASSWORD = 'auth.incorrect_current_password', 400
    AUTH_INVALID_CREDENTIALS = 'auth.invalid_credentials', 401
    AUTH_NOT_AUTHENTICATED = 'auth.not_authenticated', 401
    AUTH_PASSWORD_CHANGE_REQUIRED = 'auth.password_change_required', 403
    AUTH_RECOVERY_TOKEN_INVALID = 'auth.recovery_token_invalid', 400

    # cash_session
    CASH_SESSION_ALREADY_CLOSED = 'cash_session.already_closed', 409
    CASH_SESSION_CLOSED = 'cash_session.closed', 409
    CASH_SESSION_DRAWER_BUSY = 'cash_session.drawer_busy', 409
    CASH_SESSION_NOT_CLOSED = 'cash_session.not_closed', 409
    CASH_SESSION_NOT_OPEN = 'cash_session.not_open', 409
    CASH_SESSION_USER_BUSY = 'cash_session.user_busy', 409

    # commission_agent
    COMMISSION_AGENT_SALESPERSON_NOT_AGENT = 'commission_agent.salesperson_not_agent', 422

    # commission_period
    COMMISSION_PERIOD_ALREADY_CLOSED = 'commission_period.already_closed', 409
    COMMISSION_PERIOD_ALREADY_PAID = 'commission_period.already_paid', 409
    COMMISSION_PERIOD_LATER_CLOSED = 'commission_period.later_closed', 409
    COMMISSION_PERIOD_NOT_CLOSED = 'commission_period.not_closed', 409
    COMMISSION_PERIOD_NOT_ENDED = 'commission_period.not_ended', 409
    COMMISSION_PERIOD_PAID_CANNOT_REOPEN = 'commission_period.paid_cannot_reopen', 409
    COMMISSION_PERIOD_PREVIOUS_NOT_CLOSED = 'commission_period.previous_not_closed', 409

    # csd
    CSD_CERTIFICATE_NOT_RSA = 'csd.certificate_not_rsa', 422
    CSD_CERTIFICATE_UNREADABLE = 'csd.certificate_unreadable', 422
    CSD_KEY_MISMATCH = 'csd.key_mismatch', 422
    CSD_KEY_NOT_ENCRYPTED = 'csd.key_not_encrypted', 422
    CSD_KEY_NOT_RSA = 'csd.key_not_rsa', 422
    CSD_KEY_UNREADABLE = 'csd.key_unreadable', 422

    # customer
    CUSTOMER_CREDIT_HOLD = 'customer.credit_hold', 422
    CUSTOMER_CREDIT_LIMIT_EXCEEDED = 'customer.credit_limit_exceeded', 422
    CUSTOMER_CREDIT_LIMIT_REACHED = 'customer.credit_limit_reached', 422
    CUSTOMER_DEFAULT_NOT_DELETABLE = 'customer.default_not_deletable', 409
    CUSTOMER_NO_CREDIT_LIMIT = 'customer.no_credit_limit', 422
    CUSTOMER_WALK_IN_NO_CREDIT = 'customer.walk_in_no_credit', 422

    # customer_discount
    CUSTOMER_DISCOUNT_PRODUCT_NOT_FOUND = 'customer_discount.product_not_found', 422

    # delivery_order
    DELIVERY_ORDER_INSUFFICIENT_NOTICE = 'delivery_order.insufficient_notice', 422
    DELIVERY_ORDER_LINE_ALREADY_PRESENT = 'delivery_order.line_already_present', 409
    DELIVERY_ORDER_LINE_NOT_DELIVERABLE = 'delivery_order.line_not_deliverable', 422
    DELIVERY_ORDER_LINE_REPEATED = 'delivery_order.line_repeated', 422
    DELIVERY_ORDER_MIXED_NOT_ALLOWED = 'delivery_order.mixed_not_allowed', 422
    DELIVERY_ORDER_NO_LINES = 'delivery_order.no_lines', 409
    DELIVERY_ORDER_NO_LINES_FROM_WAREHOUSE = 'delivery_order.no_lines_from_warehouse', 422
    DELIVERY_ORDER_NOT_APPROVED = 'delivery_order.not_approved', 409
    DELIVERY_ORDER_NOT_EDITABLE = 'delivery_order.not_editable', 409
    DELIVERY_ORDER_NOT_FAILED = 'delivery_order.not_failed', 409
    DELIVERY_ORDER_NOT_IN_PREPARATION = 'delivery_order.not_in_preparation', 409
    DELIVERY_ORDER_NOT_PENDING_APPROVAL = 'delivery_order.not_pending_approval', 409
    DELIVERY_ORDER_PICKUP_NOT_LOADABLE = 'delivery_order.pickup_not_loadable', 409
    DELIVERY_ORDER_PROOF_INCOMPLETE = 'delivery_order.proof_incomplete', 422
    DELIVERY_ORDER_QUANTITY_EXCEEDS_REMAINING = 'delivery_order.quantity_exceeds_remaining', 422
    DELIVERY_ORDER_REASON_REQUIRED = 'delivery_order.reason_required', 422
    DELIVERY_ORDER_SALES_ORDER_FULLY_DELIVERED = 'delivery_order.sales_order_fully_delivered', 409
    DELIVERY_ORDER_SALES_ORDER_NOT_DELIVERABLE = 'delivery_order.sales_order_not_deliverable', 409
    DELIVERY_ORDER_SALES_ORDER_UNPAID = 'delivery_order.sales_order_unpaid', 422
    DELIVERY_ORDER_UNKNOWN_WAREHOUSE = 'delivery_order.unknown_warehouse', 422

    # delivery_order_line
    DELIVERY_ORDER_LINE_NOTHING_TO_LOAD = 'delivery_order_line.nothing_to_load', 422
    DELIVERY_ORDER_LINE_QUANTITY_UNAVAILABLE = 'delivery_order_line.quantity_unavailable', 422

    # document
    DOCUMENT_NOT_EDITABLE = 'document.not_editable', 409

    # email
    EMAIL_CONTACT_NO_ADDRESS = 'email.contact_no_address', 422
    EMAIL_CONTACT_NOT_OF_PARTY = 'email.contact_not_of_party', 422
    EMAIL_NO_RECIPIENTS = 'email.no_recipients', 422
    EMAIL_NOT_CONFIGURED = 'email.not_configured', 503
    EMAIL_SEND_FAILED = 'email.send_failed', 502
    EMAIL_TOO_MANY_RECIPIENTS = 'email.too_many_recipients', 422

    # expense_voucher
    EXPENSE_VOUCHER_NOT_CASHIER = 'expense_voucher.not_cashier', 403
    EXPENSE_VOUCHER_NOT_CONFIRMED = 'expense_voucher.not_confirmed', 409
    EXPENSE_VOUCHER_TOTAL_NOT_POSITIVE = 'expense_voucher.total_not_positive', 409
    EXPENSE_VOUCHER_UNKNOWN_CATEGORY = 'expense_voucher.unknown_category', 422

    # facility
    FACILITY_NO_DISPATCH_WAREHOUSE = 'facility.no_dispatch_warehouse', 422
    FACILITY_NO_IN_TRANSIT_LOCATION = 'facility.no_in_transit_location', 422
    FACILITY_OTHER_FACILITY = 'facility.other_facility', 403
    FACILITY_REQUIRED = 'facility.required', 422

    # fiscal_document
    FISCAL_DOCUMENT_ALREADY_RELATED = 'fiscal_document.already_related', 409
    FISCAL_DOCUMENT_AMOUNT_EXCEEDS_BALANCE = 'fiscal_document.amount_exceeds_balance', 422
    FISCAL_DOCUMENT_AMOUNTS_EXCEED_PAYMENT = 'fiscal_document.amounts_exceed_payment', 422
    FISCAL_DOCUMENT_AMOUNTS_NOT_EQUAL_PAYMENT = 'fiscal_document.amounts_not_equal_payment', 422
    FISCAL_DOCUMENT_CERTIFICATE_CANNOT_SIGN = 'fiscal_document.certificate_cannot_sign', 422
    FISCAL_DOCUMENT_CREDIT_EXCEEDS_INVOICE = 'fiscal_document.credit_exceeds_invoice', 422
    FISCAL_DOCUMENT_CREDIT_QUANTITY_EXCEEDS_LINE = (
        'fiscal_document.credit_quantity_exceeds_line',
        422,
    )
    FISCAL_DOCUMENT_FIELD_NOT_FOR_TYPE = 'fiscal_document.field_not_for_type', 422
    FISCAL_DOCUMENT_INVOICE_ALREADY_ON_RECEIPT = 'fiscal_document.invoice_already_on_receipt', 409
    FISCAL_DOCUMENT_INVOICE_NOT_CREDITABLE = 'fiscal_document.invoice_not_creditable', 422
    FISCAL_DOCUMENT_INVOICE_NOT_PAYABLE = 'fiscal_document.invoice_not_payable', 422
    FISCAL_DOCUMENT_ISSUE_DATE_OUT_OF_RANGE = 'fiscal_document.issue_date_out_of_range', 422
    FISCAL_DOCUMENT_ISSUER_HAS_NO_SERIES = 'fiscal_document.issuer_has_no_series', 422
    FISCAL_DOCUMENT_LINE_MISSING_SAT_KEY = 'fiscal_document.line_missing_sat_key', 422
    FISCAL_DOCUMENT_LINE_QUANTITY_NOT_POSITIVE = 'fiscal_document.line_quantity_not_positive', 422
    FISCAL_DOCUMENT_NO_INVOICEABLE_LINES = 'fiscal_document.no_invoiceable_lines', 422
    FISCAL_DOCUMENT_NO_LINES = 'fiscal_document.no_lines', 422
    FISCAL_DOCUMENT_NO_VALID_CERTIFICATE = 'fiscal_document.no_valid_certificate', 422
    FISCAL_DOCUMENT_NOT_DRAFT = 'fiscal_document.not_draft', 409
    FISCAL_DOCUMENT_NOT_EMAILABLE = 'fiscal_document.not_emailable', 409
    FISCAL_DOCUMENT_NOT_ISSUED = 'fiscal_document.not_issued', 409
    FISCAL_DOCUMENT_NOT_SEALED = 'fiscal_document.not_sealed', 409
    FISCAL_DOCUMENT_NOT_STAMPED = 'fiscal_document.not_stamped', 409
    FISCAL_DOCUMENT_ORDER_NOT_INVOICEABLE = 'fiscal_document.order_not_invoiceable', 422
    FISCAL_DOCUMENT_PAYMENT_ALREADY_RECEIPTED = 'fiscal_document.payment_already_receipted', 409
    FISCAL_DOCUMENT_PAYMENT_DATE_OUT_OF_RANGE = 'fiscal_document.payment_date_out_of_range', 422
    FISCAL_DOCUMENT_PAYMENT_FORM_REQUIRED = 'fiscal_document.payment_form_required', 422
    FISCAL_DOCUMENT_PAYMENT_OTHER_CUSTOMER = 'fiscal_document.payment_other_customer', 422
    FISCAL_DOCUMENT_PRODUCT_UNPRICED = 'fiscal_document.product_unpriced', 422
    FISCAL_DOCUMENT_QUANTITY_EXCEEDS_INVOICEABLE = (
        'fiscal_document.quantity_exceeds_invoiceable',
        422,
    )
    FISCAL_DOCUMENT_RECEIPT_NEEDS_PAYMENT_DATA = 'fiscal_document.receipt_needs_payment_data', 422
    FISCAL_DOCUMENT_RECEIPT_PAYS_NO_INVOICE = 'fiscal_document.receipt_pays_no_invoice', 422
    FISCAL_DOCUMENT_RECIPIENT_INCOMPLETE = 'fiscal_document.recipient_incomplete', 422
    FISCAL_DOCUMENT_RECIPIENT_NOT_LINKED = 'fiscal_document.recipient_not_linked', 422
    FISCAL_DOCUMENT_RELATION_NOT_ALLOWED = 'fiscal_document.relation_not_allowed', 422
    FISCAL_DOCUMENT_RELATION_TYPES_MIXED = 'fiscal_document.relation_types_mixed', 422
    FISCAL_DOCUMENT_SERIES_LOCKED = 'fiscal_document.series_locked', 409
    FISCAL_DOCUMENT_SERIES_MISSING = 'fiscal_document.series_missing', 422
    FISCAL_DOCUMENT_SERIES_NOT_OF_TYPE = 'fiscal_document.series_not_of_type', 422
    FISCAL_DOCUMENT_TYPE_NOT_SUPPORTED = 'fiscal_document.type_not_supported', 409
    FISCAL_DOCUMENT_UNKNOWN_FACILITY = 'fiscal_document.unknown_facility', 422
    FISCAL_DOCUMENT_UNKNOWN_USAGE = 'fiscal_document.unknown_usage', 422
    FISCAL_DOCUMENT_VERSION_NOT_PRINTABLE = 'fiscal_document.version_not_printable', 409
    FISCAL_DOCUMENT_XML_UNAVAILABLE = 'fiscal_document.xml_unavailable', 409

    # image
    IMAGE_TOO_LARGE = 'image.too_large', 422
    IMAGE_UNSUPPORTED_FORMAT = 'image.unsupported_format', 422

    # incidence
    INCIDENCE_REASON_REQUIRED = 'incidence.reason_required', 422

    # inventory_count
    INVENTORY_COUNT_BATCH_INVALID = 'inventory_count.batch_invalid', 422
    INVENTORY_COUNT_CONFIRMED_CANNOT_CANCEL = 'inventory_count.confirmed_cannot_cancel', 409

    # inventory_count_line
    INVENTORY_COUNT_LINE_NOT_STOCKABLE = 'inventory_count_line.not_stockable', 422
    INVENTORY_COUNT_LINE_PRODUCT_REFERENCE = 'inventory_count_line.product_reference', 422
    INVENTORY_COUNT_LINE_PRODUCT_UNKNOWN = 'inventory_count_line.product_unknown', 422
    INVENTORY_COUNT_LINE_REPEATED = 'inventory_count_line.repeated', 422

    # inventory_document
    INVENTORY_DOCUMENT_ALREADY_CANCELLED = 'inventory_document.already_cancelled', 409
    INVENTORY_DOCUMENT_INSUFFICIENT_STOCK = 'inventory_document.insufficient_stock', 409
    INVENTORY_DOCUMENT_LINE_WITHOUT_QUANTITY = 'inventory_document.line_without_quantity', 422
    INVENTORY_DOCUMENT_NO_LINES = 'inventory_document.no_lines', 409

    # inventory_receipt
    INVENTORY_RECEIPT_WAREHOUSE_FIXED = 'inventory_receipt.warehouse_fixed', 409
    INVENTORY_RECEIPT_COMPLEMENTARY_UNAVAILABLE = 'inventory_receipt.complementary_unavailable', 409

    # inventory_transfer
    INVENTORY_TRANSFER_LINE_REPEATED = 'inventory_transfer.line_repeated', 422
    INVENTORY_TRANSFER_NOT_IN_TRANSIT = 'inventory_transfer.not_in_transit', 409
    INVENTORY_TRANSFER_OVER_RECEIVED = 'inventory_transfer.over_received', 409
    INVENTORY_TRANSFER_SAME_WAREHOUSE = 'inventory_transfer.same_warehouse', 422

    # itinerary
    ITINERARY_CANCELLED = 'itinerary.cancelled', 409
    ITINERARY_LINES_OVERCOMMITTED = 'itinerary.lines_overcommitted', 422
    ITINERARY_NOT_DEPARTED = 'itinerary.not_departed', 409
    ITINERARY_NOT_OPEN = 'itinerary.not_open', 409
    ITINERARY_NOTHING_COMMITTED = 'itinerary.nothing_committed', 409
    ITINERARY_STOP_ALREADY_RESOLVED = 'itinerary.stop_already_resolved', 409
    ITINERARY_STOP_DELIVERED_EXCEEDS_SENT = 'itinerary.stop_delivered_exceeds_sent', 422
    ITINERARY_STOP_LINES_INCOMPLETE = 'itinerary.stop_lines_incomplete', 422
    ITINERARY_STOP_LINES_MALFORMED = 'itinerary.stop_lines_malformed', 422
    ITINERARY_STOP_SHORTFALL_NEEDS_REASON = 'itinerary.stop_shortfall_needs_reason', 422

    # kardex
    KARDEX_RANGE_REVERSED = 'kardex.range_reversed', 422
    KARDEX_RANGE_TOO_LONG = 'kardex.range_too_long', 422

    # lock
    LOCK_BUSY = 'lock.busy', 409

    # payment
    PAYMENT_ALREADY_VERIFIED = 'payment.already_verified', 409
    PAYMENT_APPLICATION_REVERSED = 'payment.application_reversed', 409
    PAYMENT_CURRENCY_MISMATCH = 'payment.currency_mismatch', 422
    PAYMENT_CUSTOMER_MISMATCH = 'payment.customer_mismatch', 422
    PAYMENT_EXCEEDS_ORDER_BALANCE = 'payment.exceeds_order_balance', 422
    PAYMENT_EXCEEDS_UNAPPLIED = 'payment.exceeds_unapplied', 422
    PAYMENT_NO_TICKET = 'payment.no_ticket', 409
    PAYMENT_ORDER_CANCELLED = 'payment.order_cancelled', 409
    PAYMENT_ORDER_NOT_COMPLETED = 'payment.order_not_completed', 409
    PAYMENT_ORDER_SETTLED = 'payment.order_settled', 409

    # point_of_sale
    POINT_OF_SALE_WAREHOUSE_FACILITY_MISMATCH = 'point_of_sale.warehouse_facility_mismatch', 422

    # price_list
    PRICE_LIST_COST_NOT_ASSIGNABLE = 'price_list.cost_not_assignable', 400
    PRICE_LIST_COST_NOT_DELETABLE = 'price_list.cost_not_deletable', 400
    PRICE_LIST_COST_READ_ONLY = 'price_list.cost_read_only', 400
    PRICE_LIST_REPLACE_WITH_ITSELF = 'price_list.replace_with_itself', 400

    # product
    PRODUCT_LOOKUP_CRITERIA_REQUIRED = 'product.lookup_criteria_required', 422
    PRODUCT_LOOKUP_TOO_MANY = 'product.lookup_too_many', 422
    PRODUCT_MERGE_WITH_ITSELF = 'product.merge_with_itself', 400
    PRODUCT_NOT_PURCHASABLE = 'product.not_purchasable', 422
    PRODUCT_NOT_STOCKABLE = 'product.not_stockable', 422

    # product_price
    PRODUCT_PRICE_DUPLICATE_ENTRY = 'product_price.duplicate_entry', 400
    PRODUCT_PRICE_REFERENCES_NOT_FOUND = 'product_price.references_not_found', 404

    # purchase_order
    PURCHASE_ORDER_ALREADY_CANCELLED = 'purchase_order.already_cancelled', 409
    PURCHASE_ORDER_CONFIRMED_CANNOT_CANCEL = 'purchase_order.confirmed_cannot_cancel', 409
    PURCHASE_ORDER_LINE_WITHOUT_QUANTITY = 'purchase_order.line_without_quantity', 409
    PURCHASE_ORDER_NO_LINES = 'purchase_order.no_lines', 409
    PURCHASE_ORDER_NO_SUPPLIER = 'purchase_order.no_supplier', 409
    PURCHASE_ORDER_NOT_CONFIRMED = 'purchase_order.not_confirmed', 409
    PURCHASE_ORDER_NOTHING_PENDING = 'purchase_order.nothing_pending', 409
    PURCHASE_ORDER_NOTHING_TO_ADD = 'purchase_order.nothing_to_add', 409
    PURCHASE_ORDER_REQUEST_NOT_APPROVED = 'purchase_order.request_not_approved', 409

    # purchase_request
    PURCHASE_REQUEST_ALREADY_CANCELLED = 'purchase_request.already_cancelled', 409
    PURCHASE_REQUEST_FACILITY_LOCKED = 'purchase_request.facility_locked', 409
    PURCHASE_REQUEST_LINE_ON_LIVE_ORDER = 'purchase_request.line_on_live_order', 409
    PURCHASE_REQUEST_LINE_WITHOUT_QUANTITY = 'purchase_request.line_without_quantity', 422
    PURCHASE_REQUEST_LINES_NOT_MARKABLE = 'purchase_request.lines_not_markable', 409
    PURCHASE_REQUEST_LINES_ON_LIVE_ORDER = 'purchase_request.lines_on_live_order', 409
    PURCHASE_REQUEST_NO_LINES = 'purchase_request.no_lines', 409
    PURCHASE_REQUEST_NOT_DRAFT = 'purchase_request.not_draft', 409
    PURCHASE_REQUEST_NOT_SUBMITTED = 'purchase_request.not_submitted', 409
    PURCHASE_REQUEST_WAREHOUSE_FACILITY_CHANGED = 'purchase_request.warehouse_facility_changed', 409

    # quote
    QUOTE_ALREADY_CANCELLED = 'quote.already_cancelled', 409
    QUOTE_CANCELLED = 'quote.cancelled', 409
    QUOTE_EXPIRED = 'quote.expired', 409
    QUOTE_NOT_CONFIRMED = 'quote.not_confirmed', 409

    # refund
    REFUND_ALREADY_CANCELLED = 'refund.already_cancelled', 409
    REFUND_COMPLETED_CANNOT_CANCEL = 'refund.completed_cannot_cancel', 409
    REFUND_LINE_INVOICED = 'refund.line_invoiced', 409
    REFUND_NO_RETURNABLE_LINES = 'refund.no_returnable_lines', 409
    REFUND_NOTHING_REFUNDABLE = 'refund.nothing_refundable', 409
    REFUND_ORDER_NOT_COMPLETED = 'refund.order_not_completed', 409
    REFUND_ORDER_NOT_PAID = 'refund.order_not_paid', 409
    REFUND_QUANTITY_EXCEEDS_REFUNDABLE = 'refund.quantity_exceeds_refundable', 422

    # request
    REQUEST_MALFORMED = 'request.malformed', 400
    REQUEST_METHOD_NOT_ALLOWED = 'request.method_not_allowed', 405
    REQUEST_ROUTE_NOT_FOUND = 'request.route_not_found', 404

    # resource
    RESOURCE_ALREADY_EXISTS = 'resource.already_exists', 409
    RESOURCE_CONFLICT = 'resource.conflict', 409
    RESOURCE_NOT_FOUND = 'resource.not_found', 404
    RESOURCE_STILL_REFERENCED = 'resource.still_referenced', 409

    # sales_order
    SALES_ORDER_ALREADY_CANCELLED = 'sales_order.already_cancelled', 409
    SALES_ORDER_BELOW_MIN_QUANTITY = 'sales_order.below_min_quantity', 422
    SALES_ORDER_HAS_PAYMENT_APPLICATIONS = 'sales_order.has_payment_applications', 409
    SALES_ORDER_INSUFFICIENT_STOCK = 'sales_order.insufficient_stock', 409
    SALES_ORDER_MISSING_DELIVERY_DETAILS = 'sales_order.missing_delivery_details', 409
    SALES_ORDER_NO_LINES = 'sales_order.no_lines', 409
    SALES_ORDER_NOT_CONFIRMED = 'sales_order.not_confirmed', 409
    SALES_ORDER_PAID_CANNOT_CANCEL = 'sales_order.paid_cannot_cancel', 409
    SALES_ORDER_ZERO_PRICED_LINES = 'sales_order.zero_priced_lines', 409

    # server
    SERVER_ERROR = 'server.error', 500

    # state
    STATE_TRANSITION_INVALID = 'state.transition_invalid', 409

    # status_filter
    STATUS_FILTER_UNSUPPORTED = 'status_filter.unsupported', 422

    # supplier_agreement
    SUPPLIER_AGREEMENT_END_BEFORE_START = 'supplier_agreement.end_before_start', 422

    # supplier_payment
    SUPPLIER_PAYMENT_ALREADY_CANCELLED = 'supplier_payment.already_cancelled', 409
    SUPPLIER_PAYMENT_AMOUNT_EXCEEDS_BALANCE = 'supplier_payment.amount_exceeds_balance', 409
    SUPPLIER_PAYMENT_AMOUNTS_EXCEED_CREDIT = 'supplier_payment.amounts_exceed_credit', 409
    SUPPLIER_PAYMENT_APPLICATION_ALREADY_REVERSED = (
        'supplier_payment.application_already_reversed',
        409,
    )
    SUPPLIER_PAYMENT_CANCELLED = 'supplier_payment.cancelled', 409
    SUPPLIER_PAYMENT_CUTOFF_NOT_SET = 'supplier_payment.cutoff_not_set', 409
    SUPPLIER_PAYMENT_FACILITY_NOT_NAMEABLE = 'supplier_payment.facility_not_nameable', 422
    SUPPLIER_PAYMENT_LEGACY_NOT_APPLICABLE = 'supplier_payment.legacy_not_applicable', 409
    SUPPLIER_PAYMENT_ORDER_BEFORE_CUTOFF = 'supplier_payment.order_before_cutoff', 409
    SUPPLIER_PAYMENT_ORDER_OTHER_SUPPLIER = 'supplier_payment.order_other_supplier', 409

    # taxpayer_batch
    TAXPAYER_BATCH_IN_USE = 'taxpayer_batch.in_use', 409

    # taxpayer_certificate
    TAXPAYER_CERTIFICATE_HOLDER_MISMATCH = 'taxpayer_certificate.holder_mismatch', 422

    # taxpayer_issuer
    TAXPAYER_ISSUER_UNKNOWN = 'taxpayer_issuer.unknown', 422

    # user
    USER_NO_CASH_DRAWER = 'user.no_cash_drawer', 422
    USER_NO_FACILITY = 'user.no_facility', 422
    USER_NO_POINT_OF_SALE = 'user.no_point_of_sale', 422

    # user_profile
    USER_PROFILE_NOT_ACTIVE = 'user_profile.not_active', 409

    # vehicle
    VEHICLE_OPEN_ITINERARY_EXISTS = 'vehicle.open_itinerary_exists', 409

    # warehouse
    WAREHOUSE_IN_TRANSIT_LOCATION = 'warehouse.in_transit_location', 422
    WAREHOUSE_IN_TRANSIT_MANAGED = 'warehouse.in_transit_managed', 403
    WAREHOUSE_REQUIRED = 'warehouse.required', 422
