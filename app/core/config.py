from decimal import Decimal

from pydantic_settings import BaseSettings, SettingsConfigDict


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

    # Directory where uploaded product images are stored
    images_dir: str = 'images'
    # Base URL used to construct full image URLs in API responses (e.g. https://api.example.com)
    images_base_url: str = ''


settings = Settings()
