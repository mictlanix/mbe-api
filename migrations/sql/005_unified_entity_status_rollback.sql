-- Rollback for 005_unified_entity_status.sql
--
-- Restores the legacy boolean lifecycle columns from `status` and drops `status`.
-- Lossy by design: ARCHIVED (2) rolls back to the "off" state like INACTIVE (1),
-- and the pre-migration employee active/disabled combination cannot be recovered
-- exactly (both columns are derived from the single status value).

-- user
ALTER TABLE `user` ADD COLUMN `disabled` TINYINT(1) NOT NULL DEFAULT 0;
UPDATE `user` SET `disabled` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `user` DROP COLUMN `status`;

-- customer
ALTER TABLE `customer` ADD COLUMN `disabled` TINYINT(1) NULL DEFAULT 0;
UPDATE `customer` SET `disabled` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `customer` DROP COLUMN `status`;

-- address
ALTER TABLE `address` ADD COLUMN `disabled` TINYINT(1) NULL DEFAULT 0;
UPDATE `address` SET `disabled` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `address` DROP COLUMN `status`;

-- facility
ALTER TABLE `facility` ADD COLUMN `disabled` TINYINT(1) NULL DEFAULT 0;
UPDATE `facility` SET `disabled` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `facility` DROP COLUMN `status`;

-- warehouse (was tinyint(4))
ALTER TABLE `warehouse` ADD COLUMN `disabled` TINYINT(4) NULL DEFAULT 0;
UPDATE `warehouse` SET `disabled` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `warehouse` DROP COLUMN `status`;

-- point_sale
ALTER TABLE `point_sale` ADD COLUMN `disabled` TINYINT(1) NULL DEFAULT 0;
UPDATE `point_sale` SET `disabled` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `point_sale` DROP COLUMN `status`;

-- cash_drawer
ALTER TABLE `cash_drawer` ADD COLUMN `disabled` TINYINT(1) NULL DEFAULT 0;
UPDATE `cash_drawer` SET `disabled` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `cash_drawer` DROP COLUMN `status`;

-- product (was tinyint(1) unsigned zerofill)
ALTER TABLE `product` ADD COLUMN `deactivated` TINYINT(1) UNSIGNED ZEROFILL NOT NULL DEFAULT 0;
UPDATE `product` SET `deactivated` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `product` DROP COLUMN `status`;

-- payment_method_option
ALTER TABLE `payment_method_option` ADD COLUMN `enabled` TINYINT(1) NOT NULL DEFAULT 1;
UPDATE `payment_method_option` SET `enabled` = CASE WHEN `status` = 0 THEN 1 ELSE 0 END;
ALTER TABLE `payment_method_option` DROP COLUMN `status`;

-- vehicle
ALTER TABLE `vehicle` ADD COLUMN `active` TINYINT(1) NOT NULL DEFAULT 1;
UPDATE `vehicle` SET `active` = CASE WHEN `status` = 0 THEN 1 ELSE 0 END;
ALTER TABLE `vehicle` DROP COLUMN `status`;

-- vehicle_operator
ALTER TABLE `vehicle_operator` ADD COLUMN `active` TINYINT(1) NOT NULL DEFAULT 1;
UPDATE `vehicle_operator` SET `active` = CASE WHEN `status` = 0 THEN 1 ELSE 0 END;
ALTER TABLE `vehicle_operator` DROP COLUMN `status`;

-- taxpayer_certificate
ALTER TABLE `taxpayer_certificate` ADD COLUMN `active` TINYINT(1) NOT NULL DEFAULT 1;
UPDATE `taxpayer_certificate` SET `active` = CASE WHEN `status` = 0 THEN 1 ELSE 0 END;
ALTER TABLE `taxpayer_certificate` DROP COLUMN `status`;

-- employee
ALTER TABLE `employee` ADD COLUMN `active` TINYINT(1) NOT NULL DEFAULT 1;
ALTER TABLE `employee` ADD COLUMN `disabled` TINYINT(1) NULL DEFAULT 0;
UPDATE `employee` SET
    `active` = CASE WHEN `status` = 0 THEN 1 ELSE 0 END,
    `disabled` = CASE WHEN `status` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `employee` DROP COLUMN `status`;
