# Changelog

All notable changes to this project will be documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- `docs/constants.md` — full enum reference extracted from `Model/Constants/` with integer values and descriptions
- VS Code debug configuration (`.vscode/launch.json`) for F5 launch via debugpy + uvicorn
- README with setup, environment variables, run, migration, test, and lint instructions

### Docs
- Updated specs: `02-sales`, `03-production`, `04-inventory`, `05-purchases`, `07-administration`, `08-technical-service`, `09-front-desk`, `10-fiscal-documents`, `11-reports`
- `docs/README.md` index updated to reference `constants.md`

### Changed
- Password hashing simplified to SHA1-only; `verify_password` now compares hashes case-insensitively
- `currency` columns in all affected models now use `Mapped[CurrencyCode]` instead of `Mapped[int]`
  (models: `core.ExchangeRate`, `product.Product`, `sales.*`, `supplier.SupplierReturnDetail`,
  `purchases.PurchaseOrderDetail`, `fiscal.FiscalDocument`, `fiscal.FiscalDocumentDetail`)
- `ExchangeRate.base` and `ExchangeRate.target` now typed as `Mapped[CurrencyCode]`

### Removed
- `password_scheme` column from `User` model (not present in the real DB schema)
- bcrypt hashing and `passlib` dependency; `bcrypt_hash`, `verify_bcrypt`, `verify_sha1` removed
- SHA1→bcrypt migration logic on login

### Fixed
- All ruff rule violations across the codebase
  - E501: wrapped long lines (> 100 chars) in `mapped_column` calls, function signatures, and `raise` statements
  - F401: removed unused imports (`SmallInteger` in `technical_service.py`, `UTC` and `random_password` in `user_service.py`)
  - I001: fixed unsorted import block in `migrations/env.py`

## [0.1.0] - 2026-06-13

### Added
- FastAPI project bootstrap with `uv`, async SQLAlchemy 2.0, MariaDB via `aiomysql`
- JWT authentication with `session_version` invalidation pattern and SHA1→bcrypt migration on login
- User management module (spec §12): `User`, `AccessPrivilege`, `UserSettings` models with full CRUD API
- `CurrencyCode`, `AccessRight`, and `SystemObject` enums in `app/enums.py`
- All 98 database models from the data dictionary across 14 domain files:
  `sat_catalog`, `core`, `product`, `customer`, `supplier`, `sales`, `inventory`,
  `purchases`, `logistics`, `fiscal`, `technical_service`, `front_desk`, `commission`, `incidence`
- Async Alembic migration environment wired to application settings
- OpenAPI/Swagger UI available at `/docs` (ReDoc at `/redoc`)
