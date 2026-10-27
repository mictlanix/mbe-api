-- 029 Supplier payments: numbering, cash sessions, applications, the balance cutoff -- spec 032
--
-- 1. `supplier_payment` gains (research R4, R5, R11):
--    - `facility` and `serial`, numbered per facility with `UNIQUE (facility, serial)`. MariaDB
--      allows repeated NULLs, so the legacy rows, which have neither, don't collide;
--    - `cash_session`: the caller's open session for a cash payment, NULL otherwise;
--    - `cancelled`, set when a payment is cancelled;
--    - `creation_time`, `updater` and `modification_time`.
--    The legacy rows (5 on mbe_dev) are backfilled: `creation_time = modification_time = date`,
--    `updater = creator`. They stay without a facility, which is what marks them legacy: they
--    carry no credit (research R7).
-- 2. `purchase_order_payment`, new: one row per application of a payment to a purchase order,
--    shaped like `sales_order_payment` without `amount_change` and `confirmed`. A reversal sets
--    `cancelled`; it is never deleted.
-- 3. `supplier_balance_cutoff`, new: one row, `cutoff` NULL. A PO counts toward a supplier's
--    balance when its `modification_time >= cutoff`; while it is NULL none does (research R6). The
--    go-live migration sets it.
--
-- The current system's NHibernate mappings name their columns explicitly, so they neither read nor
-- write the new ones; every new `supplier_payment` column is nullable or has a default, so its
-- inserts still succeed.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 029_supplier_payments_rollback.sql

ALTER TABLE `supplier_payment`
    ADD COLUMN IF NOT EXISTS `facility` INT(11) NULL DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS `serial` INT(11) NULL DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS `cash_session` INT(11) NULL DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS `cancelled` TINYINT(1) NOT NULL DEFAULT 0,
    ADD COLUMN IF NOT EXISTS `creation_time` DATETIME NULL DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS `updater` INT(11) NULL DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS `modification_time` DATETIME NULL DEFAULT NULL;

-- The unique key leads with `facility`, so it also serves the facility foreign key below.
CREATE UNIQUE INDEX IF NOT EXISTS `supplier_payment_facility_serial_uq`
    ON `supplier_payment` (`facility`, `serial`);

CREATE INDEX IF NOT EXISTS `supplier_payment_cash_session_idx`
    ON `supplier_payment` (`cash_session`);

CREATE INDEX IF NOT EXISTS `supplier_payment_updater_idx`
    ON `supplier_payment` (`updater`);

ALTER TABLE `supplier_payment`
    ADD CONSTRAINT `supplier_payment_facility_fk`
    FOREIGN KEY IF NOT EXISTS (`facility`)
    REFERENCES `facility` (`facility_id`);

ALTER TABLE `supplier_payment`
    ADD CONSTRAINT `supplier_payment_cash_session_fk`
    FOREIGN KEY IF NOT EXISTS (`cash_session`)
    REFERENCES `cash_session` (`cash_session_id`);

ALTER TABLE `supplier_payment`
    ADD CONSTRAINT `supplier_payment_updater_fk`
    FOREIGN KEY IF NOT EXISTS (`updater`)
    REFERENCES `employee` (`employee_id`);

UPDATE `supplier_payment`
SET `creation_time` = `date`,
    `modification_time` = `date`,
    `updater` = `creator`
WHERE `creation_time` IS NULL;

CREATE TABLE IF NOT EXISTS `purchase_order_payment` (
  `purchase_order_payment_id` int(11) NOT NULL AUTO_INCREMENT,
  `supplier_payment` int(11) NOT NULL,
  `purchase_order` int(11) NOT NULL,
  `amount` decimal(18,2) NOT NULL,
  `applier` int(11) NOT NULL,
  `date` datetime NOT NULL,
  `cancelled` tinyint(1) NOT NULL DEFAULT 0 COMMENT 'Application reversed and no longer counted against the order',
  PRIMARY KEY (`purchase_order_payment_id`),
  KEY `purchase_order_payment_supplier_payment_idx` (`supplier_payment`),
  KEY `purchase_order_payment_purchase_order_idx` (`purchase_order`),
  KEY `purchase_order_payment_applier_idx` (`applier`),
  CONSTRAINT `purchase_order_payment_supplier_payment_fk` FOREIGN KEY (`supplier_payment`) REFERENCES `supplier_payment` (`supplier_payment_id`),
  CONSTRAINT `purchase_order_payment_purchase_order_fk` FOREIGN KEY (`purchase_order`) REFERENCES `purchase_order` (`purchase_order_id`),
  CONSTRAINT `purchase_order_payment_applier_fk` FOREIGN KEY (`applier`) REFERENCES `employee` (`employee_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `supplier_balance_cutoff` (
  `supplier_balance_cutoff_id` int(11) NOT NULL COMMENT 'Always 1',
  `cutoff` datetime DEFAULT NULL COMMENT 'NULL until go-live; no PO counts while it is NULL',
  PRIMARY KEY (`supplier_balance_cutoff_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

INSERT IGNORE INTO `supplier_balance_cutoff` (`supplier_balance_cutoff_id`, `cutoff`)
VALUES (1, NULL);
