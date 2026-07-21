-- 006 Facility logo optional (GitHub #91)
--
-- `facility.logo` holds the filename of an uploaded image (see POST
-- /api/v1/facilities/{id}/logo), resolved to `/images/<filename>` on response.
-- A facility can exist without one, so the column becomes nullable.
--
-- Empty strings and legacy ASP.NET virtual paths (`~/Content/images/...`, which
-- point at files the API does not serve and no client can render) are cleared to
-- NULL. Affected facilities show no logo until one is re-uploaded through the
-- endpoint above.
--
-- MariaDB. Rollback: 006_facility_logo_nullable_rollback.sql

ALTER TABLE `facility` MODIFY COLUMN `logo` VARCHAR(255) NULL;
UPDATE `facility` SET `logo` = NULL WHERE `logo` = '' OR `logo` LIKE '~/%';
