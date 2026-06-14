from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8", extra="ignore")

    app_name: str = "mbe-api"
    debug: bool = False
    api_v1_prefix: str = "/api/v1"

    database_url: str = "mysql+aiomysql://user:password@localhost/mbe"

    jwt_secret_key: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 480  # 8 hours
    jwt_recovery_token_expire_hours: int = 24

    # "Managed" = admin sets store/POS/drawer; "SelfService" = user selects after login
    user_settings_mode: str = "Managed"


settings = Settings()
