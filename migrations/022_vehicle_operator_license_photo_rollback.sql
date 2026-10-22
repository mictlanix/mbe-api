-- Rollback for 022_vehicle_operator_license_photo.sql
--
-- Drops `vehicle_operator`.`license_photo`.
--
-- WHAT IS LOST. Which file belongs to which operator. The image files themselves stay in
-- LICENSE_PHOTOS_DIR, orphaned. Their names are random, so once this column is gone nothing can map a
-- file back to its driver. Before running this, list them if that matters:
--
--   SELECT `vehicle_operator_id`, `license_photo`
--   FROM `vehicle_operator`
--   WHERE `license_photo` IS NOT NULL;
--
-- The model still declares the column afterwards, so revert the application code with it, or every
-- read of `vehicle_operator` raises "Unknown column".
--
-- MariaDB 10.11.

ALTER TABLE `vehicle_operator`
  DROP COLUMN IF EXISTS `license_photo`;
