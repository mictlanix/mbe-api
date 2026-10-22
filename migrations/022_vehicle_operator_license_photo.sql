-- 022 Store a photo of each vehicle operator's driver license -- mictlanix/mbe-roadmap#20
--
-- Logistics keeps a copy of each driver's license for document control (scope 01 §16). The file
-- itself lives on disk under LICENSE_PHOTOS_DIR, which is private: it is outside IMAGES_DIR, never
-- mounted, and served only through the authenticated `GET /vehicle-operators/{id}/license-photo`.
-- A driver license is personal data, so it must not sit behind the public `/images` URL that
-- product photos use. This column holds only the bare filename (`<uuid4 hex>.png`) inside that
-- directory. The API never returns it; responses carry `has_license_photo` instead.
--
-- NULL means "no photo", which is what every existing row gets. Nothing is backfilled: mbe_dev has 14
-- operators and none of them has a photo anywhere. The legacy application never selects this column,
-- so a nullable column with no default is invisible to it.
--
-- VARCHAR(255), matching `product.photo`. Adding a nullable column cannot fail on existing data.
-- Idempotent: `IF NOT EXISTS`, so re-running it is a no-op rather than error 1060.
--
-- MariaDB 10.11. Rollback: 022_vehicle_operator_license_photo_rollback.sql

ALTER TABLE `vehicle_operator`
  ADD COLUMN IF NOT EXISTS `license_photo` VARCHAR(255) NULL
  COMMENT 'Filename under LICENSE_PHOTOS_DIR; NULL = no photo (mictlanix/mbe-roadmap#20)';
