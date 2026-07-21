-- Rollback for 006_facility_logo_nullable.sql
--
-- Restores the NOT NULL constraint on `facility.logo`. Lossy by design: rows
-- with no logo are given an empty string, which the forward script cannot
-- distinguish from a genuinely empty pre-migration value.

UPDATE `facility` SET `logo` = '' WHERE `logo` IS NULL;
ALTER TABLE `facility` MODIFY COLUMN `logo` VARCHAR(255) NOT NULL;
