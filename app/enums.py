from enum import IntEnum, IntFlag


class CurrencyCode(IntEnum):
    MXN = 0
    USD = 1
    EUR = 2


class AddressType(IntEnum):
    OTHER = 0
    HOME = 1
    WORK = 2
    BUSINESS = 3
    FISCAL = 4


class FacilityType(IntEnum):
    STORE = 0
    PRODUCTION_SITE = 1


class FiscalCertificationProvider(IntEnum):
    """PAC (Proveedor Autorizado de Certificación) integration used for CFDI stamping."""

    NONE = 0
    DIVERZA = 1
    FISCOCLIC = 2
    SERVISIM = 3
    PROFACT = 4


class EntityStatus(IntEnum):
    """Unified lifecycle state shared by all status-bearing entities."""

    ACTIVE = 0
    INACTIVE = 1
    ARCHIVED = 2


class PaymentTerms(IntEnum):
    """`sales_order.payment_terms`, `sales_quote.payment_terms`."""

    IMMEDIATE = 0
    NET_D = 1


class PaymentMethod(IntEnum):
    """`customer_payment.method` — SAT forma de pago catalog codes."""

    NA = 0
    CASH = 1
    CHECK = 2
    EFT = 3
    CREDIT_CARD = 4
    ELECTRONIC_PURSE = 5
    ELECTRONIC_MONEY = 6
    FOOD_VOUCHERS = 8
    GIVING = 12
    TO_THE_SATISFACTION_OF_THE_CREDITOR = 27
    DEBIT_CARD = 28
    SERVICE_CARD = 29
    ADVANCE_PAYMENTS = 30
    TO_BE_DEFINED = 99
    GOVERNMENT_FUNDING = 1001


class PaymentType(IntEnum):
    """`customer_payment.payment_type` — what the payment record represents.

    The column is `payment_type`, not `type` as the legacy sales spec claims.
    """

    NA = 0
    IMMEDIATE = 1
    CREDIT_PAYMENT = 2
    PAYMENT_IN_ADVANCE = 3
    CREDIT_NOTE = 4
    EXPENSE = 5


class Priority(IntEnum):
    """`sales_order.priority`."""

    LOW = 0
    NORMAL = 1
    HIGH = 2
    CRITICAL = 3


class CashCountType(IntEnum):
    """`cash_count.type`."""

    STARTING_CASH = 0
    COUNTED_CASH = 1


class TransactionType(IntEnum):
    """`lot_serial_tracking.source` — classifies an inventory ledger entry.

    The column is `source`, not `transaction_type` as the legacy spec claims;
    `lot_serial_tracking.reference` carries the source document's id.
    """

    SALES_ORDER = 1
    CUSTOMER_REFUND = 2
    INVENTORY_ISSUE = 3
    INVENTORY_RECEIPT = 4


class SourceType(IntEnum):
    """`incidence.source` — which entity type an audit entry references."""

    DELIVERY_ORDER = 1
    CUSTOMER_PAYMENT = 2
    SALES_ORDER = 3
    PURCHASE_REQUEST = 4
    PURCHASE_ORDER = 5
    PRICING = 6
    CUSTOMER = 7
    USER_SETTINGS = 8
    PRODUCT = 9


class AccessRight(IntFlag):
    NONE = 0
    CREATE = 1
    READ = 2
    UPDATE = 4
    DELETE = 8


class SystemObject(IntEnum):
    PRODUCTS = 0
    LABELS = 1
    CUSTOMERS = 2
    SUPPLIERS = 3
    WAREHOUSES = 4
    PRICE_LISTS = 5
    EMPLOYEES = 6
    SALES_ORDERS = 7
    CUSTOMER_PAYMENTS = 8
    POINTS_OF_SALE = 9
    CASH_DRAWERS = 10
    ADDRESSES = 11
    CONTACTS = 12
    BANK_ACCOUNTS = 13
    SUPPLIER_AGREEMENTS = 14
    INVENTORY_RECEIPTS = 15
    INVENTORY_ISSUES = 16
    INVENTORY_TRANSFERS = 17
    ACCOUNTS_RECEIVABLE = 18
    ACCOUNTS_PAYABLE = 19
    PURCHASES_ORDERS = 20
    SUPPLIER_PAYMENT = 21
    CUSTOMER_REFUNDS = 22
    FISCAL_DOCUMENTS = 23
    TAXPAYERS = 24
    SUPPLIER_RETURNS = 25
    SALES_ORDERS_HISTORIC = 26
    CUSTOMER_REFUNDS_HISTORIC = 27
    SUPPLIER_RETURN_HISTORIC = 28
    FACILITIES = 29
    SALES_QUOTES = 30
    # 31 absent
    KARDEX = 32
    RECEIVED_PAYMENTS = 33
    SALES_BY_CUSTOMER = 34
    SALES_BY_SALES_PERSON = 35
    SALES_BY_PRODUCT = 36
    GROSS_PROFITS_BY_CUSTOMER = 37
    GROSS_PROFITS_BY_SALES_PERSON = 38
    GROSS_PROFITS_BY_PRODUCT = 39
    BEST_SELLING_PRODUCTS_BY_CUSTOMER = 40
    BEST_SELLING_PRODUCTS_BY_SALES_PERSON = 41
    LOT_SERIAL_NUMBERS = 42
    EXCHANGE_RATES = 43
    POS = 44
    SERIAL_NUMBER_KARDEX = 45
    CUSTOMER_DEBT_REPORT = 46
    SALES_ORDER_SUMMARY_REPORT = 47
    FISCAL_DOCUMENTS_REPORT = 48
    SALES_PERSON_ORDERS_REPORT = 49
    CUSTOMER_SALES_ORDERS_REPORT = 50
    PRODUCT_SALES_BY_CUSTOMER_REPORT = 51
    PRODUCT_SALES_BY_MODEL_REPORT = 52
    PRODUCT_SALES_BY_BRAND_REPORT = 53
    TAXPAYER_RECIPIENTS = 54
    PRODUCT_SALES_BY_SALES_PERSON = 55
    STANDALONE_FISCAL_DOCUMENTS = 56
    PRODUCTION_ORDERS = 57
    TECHNICAL_SERVICE_REPORTS = 58
    TRANSLATION_REQUESTS = 59
    NOTARIZATIONS = 60
    PRODUCT_SALES_BY_SALES_PERSON_AND_LABEL = 61
    PRODUCT_SALES_BY_SALES_PERSON_AND_BRAND = 62
    PRODUCT_SALES_BY_SALES_PERSON_AND_MODEL = 63
    TECHNICAL_SERVICE_REQUESTS = 64
    TECHNICAL_SERVICE_RECEIPTS = 65
    CUSTOMERS_REPORT = 66
    WAREHOUSE_STOCK_REPORT = 67
    WAREHOUSE_STOCK_BY_LOT_REPORT = 68
    WAREHOUSE_STOCK_BY_SERIAL_NUMBER_REPORT = 69
    # 70 absent
    DELIVERY_ORDERS = 71
    SALES_PERSON_ORDERS_AND_REFUNDS_REPORT = 72
    PRODUCTS_MERGE = 73
    PHYSICAL_COUNT_ADJUSTMENT = 74
    PRODUCTS_BY_SUPPLIER_REPORT = 75
    # 76, 77, 78 absent
    PRODUCTS_ORDERS_AND_REFUNDS_BY_SALES_PERSON = 79
    PENDANT_DELIVERIES = 80
    EXPENSES = 81
    EXPENSE_TICKET = 82
    CREDIT_PAYMENTS = 83
    PAYMENT_METHOD_OPTIONS = 84
    PAYMENT_RECEIPT = 85
    PURCHASE_REQUEST = 86
    DELIVERY_ITINERARIES = 87
    VEHICLE = 88
    VEHICLE_OPERATORS = 89
    VEHICLE_SERVICE_ORDERS = 90
    FOR_DELIVER = 91
    USERS = 92
    INVENTORY_ADJUSTMENTS = 93
    DELIVERY_ORDER_APPROVAL = 94
    PURCHASE_ORDER_APPROVAL = 95
    PURCHASE_REQUEST_APPROVAL = 96
    RECEIVED_PAYMENTS_ADVANCED_SEARCH_FILTER = 97
    CREDIT_CUSTOMER_CONFIGURATION = 98
    STORE_MOVEMENTS_SUMMARY = 99
    PAYMENTS_EDITOR = 100
    SEARCH_CREDITS_FROM_ALL_STORES = 101
    EXCLUDE_PRICE_RANGE_VALIDATION = 102
    ISSUED_LOCATION_ID = 103
    # 104, 105 absent
    PRICING = 106
    # 107 absent
    PAYMENTS_VERIFICATION = 108
    RECEIVED_PAYMENTS_SUMMARY = 109
    CUSTOMER_REFUND_CONFIRM = 110
    CASH_SESSION_CLOSE = 111
    COMMISSIONS_BY_SALES_PERSON = 112
    DOWNLOAD_CSV_FILES = 113
