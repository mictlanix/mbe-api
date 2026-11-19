from datetime import date
from decimal import Decimal
from typing import Literal
from zoneinfo import ZoneInfo

from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from app.enums import CurrencyCode


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', env_file_encoding='utf-8', extra='ignore')

    app_name: str = 'mbe-api'
    debug: bool = False
    api_v1_prefix: str = '/api/v1'

    database_url: str = 'mysql+aiomysql://user:password@localhost/mbe'

    # Every stored datetime is naive wall-clock time in this zone — the zone the legacy monolith
    # writes into the same database. Required, with no default: falling back to the host's clock
    # is how "local" silently meant whatever the server was set to (#228).
    business_timezone: ZoneInfo

    jwt_secret_key: str = 'change-me-in-production'
    jwt_algorithm: str = 'HS256'
    jwt_access_token_expire_minutes: int = 480  # 8 hours
    jwt_recovery_token_expire_hours: int = 24

    # Password strength, enforced whenever a password is set and published at
    # `GET /auth/password-policy` (#241). Existing passwords are not re-checked.
    password_min_length: int = 8
    password_require_upper: bool = True
    password_require_lower: bool = True
    password_require_digit: bool = True
    password_require_symbol: bool = False

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

    # Delivery defaults (replaces legacy WebConfig values)
    delivery_order_approval_required: bool = False
    # Refuse a delivery unless the sale is paid or on NET_D terms. The name says what it checks;
    # what it *assumes* is that money is collected before goods leave, on every channel (#211).
    # That holds at a register and does not hold for an order taken for delivery on immediate
    # terms and paid on receipt — ordinary cash-on-delivery, which this refuses outright for every
    # customer without a credit line. Switching it on disables that workflow; it is off by default.
    delivery_order_requires_paid_or_credit_sales_order: bool = False
    # Minimum lead time between now and a delivery order's scheduled date; 0 disables the check
    min_span_hours_for_deliveries: int = 0
    # An order confirmed this many days ago that is still neither paid nor delivered is cancelled
    # by the expiry sweep, releasing the stock it reserved. 0 disables the sweep entirely.
    unpaid_order_expiry_days: int = 2
    # The same rule, but for an order somebody has *scheduled* — one carrying a live delivery order
    # or a promise date still in the future. Confirmed, unpaid and undelivered is the resting state
    # of an order taken for later delivery, so the short window above cancels it while it is
    # waiting rather than because it was abandoned (#210). 0 exempts scheduled orders entirely.
    scheduled_order_expiry_days: int = 30

    # Pagaré wording (replaces legacy Web.config:14 PromissoryNoteContent). {customer}, {balance},
    # {due_date} and {issuer} replace legacy's {0}, {1:c}, {2:d} and {3}; the values are already
    # formatted strings by the time they reach this template.
    promissory_note_template: str = (
        'Por este PAGARÉ, yo {customer} prometo incondicionalmente pagar a la orden de {issuer}, '
        'la cantidad de {balance} el día {due_date}. Obligándome a pagar para el caso de mora un '
        'interés equivalente al ____ mensual durante todo el tiempo que permanezca insoluto.'
    )

    # Directory where uploaded product images are stored
    images_dir: str = 'images'
    # Base URL used to construct full image URLs in API responses (e.g. https://api.example.com)
    images_base_url: str = ''
    # Directory holding proof-of-delivery captures. Deliberately NOT under `images_dir`, which is
    # served by an unauthenticated static mount; a signature is personal data (FR-044a).
    pod_dir: str = 'pod'
    # Directory holding vehicle operators' driver license photos (mictlanix/mbe-roadmap#20). Private
    # like `pod_dir`: outside `images_dir` and never mounted, because a license is personal data.
    # Served only through the authenticated license-photo route.
    license_photos_dir: str = 'license-photos'
    # Background analytics exports (spec 037). Private like `pod_dir`: never mounted, served only to
    # the export's owner through the authenticated download route, and purged after 24 hours.
    exports_dir: str = 'exports'
    # Operations analytics (spec 038). Stock history starts after the ledger reset of 2025-02-06
    # (research R3); a cost of 0 or at least the placeholder leaves a pair uncosted (R4).
    stock_history_start: date = date(2025, 2, 7)
    placeholder_cost: Decimal = Decimal('99999')
    # Restock defaults, overridable per request (R5).
    restock_lead_days: int = 7
    restock_cover_days: int = 30
    # Scheduled report sends (R15). On for one instance only: dev and test share a database.
    scheduled_reports_enabled: bool = False
    report_attachment_max_bytes: int = 10 * 1024 * 1024

    # Outgoing mail (spec 024): the quote email now, the invoice email later. Optional: mail is off,
    # and routes that send it answer 503, while either `smtp_host` or `mail_from` is empty. Plain
    # SMTP authentication over TLS; Google Workspace needs an app password rather than OAuth2.
    smtp_host: str = ''
    smtp_port: int = 587
    smtp_security: Literal['starttls', 'ssl', 'none'] = 'starttls'
    smtp_user: str = ''
    smtp_password: str = ''
    # Seconds allowed per connection and per command, so a dead server cannot hold a request open.
    smtp_timeout: int = 30
    mail_from: str = ''
    # Where customer replies go, e.g. a sales inbox; `mail_from` is usually a no-reply address.
    mail_reply_to: str = ''

    # CFDI stamping and cancellation (specs 039, 040). `app_env` fails closed: an instance that
    # forgets it is treated as production, where the simulated PAC must never run (R5). The PAC in
    # use is set explicitly in each `.env` and has no default: None means not configured, and
    # `check_pac_settings` refuses it at startup. `none` answers stamp, cancel and refresh with
    # `pac.not_configured`.
    app_env: Literal['development', 'staging', 'production'] = 'production'
    pac_provider: Literal['none', 'simulated', 'multifacturas'] | None = None
    # The Multifacturas account and connection (spec 040, research R6). Never logged.
    multifacturas_user: str | None = None
    multifacturas_password: SecretStr | None = None
    multifacturas_production: bool = False
    multifacturas_url: str = 'https://ws.multifacturas.com'
    multifacturas_connect_timeout: float = 10.0
    multifacturas_read_timeout: float = 60.0


def check_pac_settings(config: Settings) -> None:
    """Refuse a PAC configuration that is missing or unsafe, naming the settings and never their
    values (spec 039 R5, spec 040 R6). The API's startup and the resolve job call it."""
    if config.pac_provider is None:
        raise RuntimeError(
            'PAC_PROVIDER is not set: name the PAC in use (none, simulated or multifacturas) '
            'in this instance\'s .env'
        )
    if config.pac_provider == 'simulated' and config.app_env == 'production':
        raise RuntimeError(
            'pac_provider=simulated is not allowed with app_env=production: '
            'set APP_ENV to development or staging, or PAC_PROVIDER to none'
        )
    if config.pac_provider == 'multifacturas':
        _check_multifacturas(config)


def _check_multifacturas(config: Settings) -> None:
    missing = [
        name
        for name, value in (
            ('MULTIFACTURAS_USER', config.multifacturas_user),
            ('MULTIFACTURAS_PASSWORD', config.multifacturas_password),
        )
        if not value
    ]
    if missing:
        raise RuntimeError(f'PAC_PROVIDER=multifacturas needs {" and ".join(missing)}')
    production = config.app_env == 'production'
    if config.multifacturas_production != production:
        mode = 'production' if config.multifacturas_production else 'test'
        raise RuntimeError(
            f'MULTIFACTURAS_PRODUCTION={str(config.multifacturas_production).lower()} '
            f'(Multifacturas {mode} mode) is not allowed with APP_ENV={config.app_env}: '
            'production mode goes only with APP_ENV=production, and test mode with any other'
        )
    from mictlanix.cfdi.pac import MultifacturasSettings

    try:
        MultifacturasSettings(
            user='',
            password='',
            production=config.multifacturas_production,
            base_url=config.multifacturas_url,
            connect_timeout=config.multifacturas_connect_timeout,
            read_timeout=config.multifacturas_read_timeout,
        )
    except ValueError as error:
        names = {
            'base_url': 'MULTIFACTURAS_URL (it must be https://)',
            'connect_timeout': 'MULTIFACTURAS_CONNECT_TIMEOUT (it must be positive)',
            'read_timeout': 'MULTIFACTURAS_READ_TIMEOUT (it must be positive)',
        }
        named = next((text for key, text in names.items() if key in str(error)), str(error))
        raise RuntimeError(f'Invalid {named}') from None


settings = Settings()
