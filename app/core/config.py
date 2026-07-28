from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict

from app.enums import CurrencyCode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'mbe-api'
    debug: bool = False
    api_v1_prefix: str = '/api/v1'

    database_url: str = 'mysql+aiomysql://user:password@localhost/mbe'

    jwt_secret_key: str = 'change-me-in-production'
    jwt_algorithm: str = 'HS256'
    jwt_access_token_expire_minutes: int = 480  # 8 hours
    jwt_recovery_token_expire_hours: int = 24

    # "Managed" = admin sets facility/POS/drawer; "SelfService" = user selects after login
    user_settings_mode: str = 'Managed'

    # Origins allowed to call this API from a browser (CORS). Defaults to "*"
    # for local development; set to a JSON array of explicit origins in
    # production, e.g. CORS_ORIGINS=["https://app.example.com"]
    cors_origins: list[str] = ['*']

    # Product creation defaults (replaces legacy WebConfig values)
    default_vat: Decimal = Decimal('0.160000')
    is_tax_included: bool = False
    default_price_type: int = 0  # 0 = Fixed
    default_photo_file: str = 'no-image.png'
    default_customer_id: int = 1

    # Sales defaults (replaces legacy WebConfig values)
    default_currency: CurrencyCode = CurrencyCode.MXN
    default_quotation_due_days: int = 30
    max_days_to_deliver_stockables: int = 7
    price_validation_in_range_required: bool = True
    # Price list holding cost rather than sale price; read when snapshotting a line's cost
    cost_price_list_id: int = 0

    # Delivery defaults (replaces legacy WebConfig values)
    delivery_order_approval_required: bool = False
    delivery_order_requires_paid_or_credit_sales_order: bool = False
    # Minimum lead time between now and a delivery order's scheduled date; 0 disables the check
    min_span_hours_for_deliveries: int = 0
    # Virtual warehouse holding goods between itinerary departure and delivery. Seeded by
    # migration 008; 0 means "not configured" and is refused at startup rather than silently
    # posting ledger entries against a non-existent warehouse.
    in_transit_warehouse_id: int = 0

    # Directory where uploaded product images are stored
    images_dir: str = 'images'
    # Base URL used to construct full image URLs in API responses (e.g. https://api.example.com)
    images_base_url: str = ''
    # Directory holding proof-of-delivery captures. Deliberately NOT under `images_dir`, which is
    # served by an unauthenticated static mount; a signature is personal data (FR-044a).
    pod_dir: str = 'pod'


settings = Settings()
