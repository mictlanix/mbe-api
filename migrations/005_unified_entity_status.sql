-- 005 Unified entity status (specs/005-unified-entity-status)
--
-- Replaces every boolean lifecycle flag (disabled / active / deactivated / enabled)
-- with a single non-nullable integer `status` column:
--   0 = ACTIVE, 1 = INACTIVE, 2 = ARCHIVED  (app/enums.py EntityStatus)
--
-- Mapping:
--   disabled-polarity columns: NULL/0 -> ACTIVE, else INACTIVE
--   active/enabled-polarity columns: 1 -> ACTIVE, else INACTIVE
--   employee (has both flags): ACTIVE only when active=1 AND disabled is not 1
--
-- MariaDB. DDL is not transactional -- statements are ordered so each table is
-- migrated completely before the next. Rollback: 005_unified_entity_status_rollback.sql

-- user (disabled tinyint(1) NOT NULL)
ALTER TABLE `user` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `user` SET `status` = CASE WHEN `disabled` IS NULL OR `disabled` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `user` DROP COLUMN `disabled`;

-- customer (disabled tinyint(1) NULL)
ALTER TABLE `customer` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `customer` SET `status` = CASE WHEN `disabled` IS NULL OR `disabled` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `customer` DROP COLUMN `disabled`;

-- address (disabled tinyint(1) NULL)
ALTER TABLE `address` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `address` SET `status` = CASE WHEN `disabled` IS NULL OR `disabled` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `address` DROP COLUMN `disabled`;

-- facility (disabled tinyint(1) NULL)
ALTER TABLE `facility` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `facility` SET `status` = CASE WHEN `disabled` IS NULL OR `disabled` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `facility` DROP COLUMN `disabled`;

-- warehouse (disabled tinyint(4) NULL)
ALTER TABLE `warehouse` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `warehouse` SET `status` = CASE WHEN `disabled` IS NULL OR `disabled` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `warehouse` DROP COLUMN `disabled`;

-- point_sale (disabled tinyint(1) NULL)
ALTER TABLE `point_sale` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `point_sale` SET `status` = CASE WHEN `disabled` IS NULL OR `disabled` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `point_sale` DROP COLUMN `disabled`;

-- cash_drawer (disabled tinyint(1) NULL)
ALTER TABLE `cash_drawer` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `cash_drawer` SET `status` = CASE WHEN `disabled` IS NULL OR `disabled` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `cash_drawer` DROP COLUMN `disabled`;

-- product (deactivated tinyint(1) unsigned zerofill NOT NULL)
ALTER TABLE `product` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `product` SET `status` = CASE WHEN `deactivated` IS NULL OR `deactivated` = 0 THEN 0 ELSE 1 END;
ALTER TABLE `product` DROP COLUMN `deactivated`;

-- payment_method_option (enabled tinyint(1) NOT NULL)
ALTER TABLE `payment_method_option` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `payment_method_option` SET `status` = CASE WHEN `enabled` = 1 THEN 0 ELSE 1 END;
ALTER TABLE `payment_method_option` DROP COLUMN `enabled`;

-- vehicle (active tinyint(1) NOT NULL)
ALTER TABLE `vehicle` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `vehicle` SET `status` = CASE WHEN `active` = 1 THEN 0 ELSE 1 END;
ALTER TABLE `vehicle` DROP COLUMN `active`;

-- vehicle_operator (active tinyint(1) NOT NULL)
ALTER TABLE `vehicle_operator` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `vehicle_operator` SET `status` = CASE WHEN `active` = 1 THEN 0 ELSE 1 END;
ALTER TABLE `vehicle_operator` DROP COLUMN `active`;

-- taxpayer_certificate (active tinyint(1) NOT NULL)
ALTER TABLE `taxpayer_certificate` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `taxpayer_certificate` SET `status` = CASE WHEN `active` = 1 THEN 0 ELSE 1 END;
ALTER TABLE `taxpayer_certificate` DROP COLUMN `active`;

-- employee (active tinyint(1) NOT NULL + disabled tinyint(1) NULL: restrictive flag wins)
ALTER TABLE `employee` ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0;
UPDATE `employee` SET `status` =
    CASE WHEN `active` = 1 AND (`disabled` IS NULL OR `disabled` = 0) THEN 0 ELSE 1 END;
ALTER TABLE `employee` DROP COLUMN `active`;
ALTER TABLE `employee` DROP COLUMN `disabled`;
