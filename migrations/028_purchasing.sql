-- 028 Purchasing: request approvals, the request-line link on PO lines, cost history -- spec 031
--
-- 1. `purchase_request` gains `approver` and `approval_time`, set when a request is approved and
--    cleared when it is rejected back to draft (FR-005). Past approvals stay NULL: the current
--    system never recorded who approved.
-- 2. `purchase_order_detail.purchase_request_detail` gets an index and a foreign key. Every step
--    that decides whether a request line is on a live PO reads by it (research R3). Measured on
--    mbe_dev: 8,369 linked rows and no orphans, so the key applies cleanly.
-- 3. `product_cost_change`, new: one row per average-cost step on a PO-linked receipt confirm,
--    and one per restore on its cancel (FR-036, FR-037; research R5, R6). API-only.
--
-- The current system's NHibernate mappings name their columns explicitly, so they neither read nor
-- write the new ones.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 028_purchasing_rollback.sql

ALTER TABLE `purchase_request`
    ADD COLUMN IF NOT EXISTS `approver` INT(11) NULL DEFAULT NULL,
    ADD COLUMN IF NOT EXISTS `approval_time` DATETIME NULL DEFAULT NULL;

CREATE INDEX IF NOT EXISTS `purchase_request_approver_idx`
    ON `purchase_request` (`approver`);

ALTER TABLE `purchase_request`
    ADD CONSTRAINT `purchase_request_approver_fk`
    FOREIGN KEY IF NOT EXISTS (`approver`)
    REFERENCES `employee` (`employee_id`);

CREATE INDEX IF NOT EXISTS `purchase_order_detail_request_detail_idx`
    ON `purchase_order_detail` (`purchase_request_detail`);

ALTER TABLE `purchase_order_detail`
    ADD CONSTRAINT `purchase_order_detail_request_detail_fk`
    FOREIGN KEY IF NOT EXISTS (`purchase_request_detail`)
    REFERENCES `purchase_request_detail` (`purchase_request_detail_id`);

CREATE TABLE IF NOT EXISTS `product_cost_change` (
  `product_cost_change_id` int(11) NOT NULL AUTO_INCREMENT,
  `product` int(11) NOT NULL,
  `receipt` int(11) NOT NULL,
  `purchase_order_detail` int(11) DEFAULT NULL COMMENT 'NULL on a restore',
  `previous_cost` decimal(18,4) NOT NULL COMMENT 'C; 0 when the product had no cost row',
  `new_cost` decimal(18,4) NOT NULL COMMENT 'Rounded half up to 4 decimals',
  `quantity_on_hand` decimal(18,4) DEFAULT NULL COMMENT 'Q, company-wide, floored at 0; NULL on a restore',
  `quantity_received` decimal(18,4) DEFAULT NULL COMMENT 'R; NULL on a restore',
  `unit_cost` decimal(18,7) DEFAULT NULL COMMENT 'U = price x (1 - discount); NULL on a restore',
  `reverted_change` int(11) DEFAULT NULL COMMENT 'On a restore: the first change of the receipt it undid',
  `creation_time` datetime NOT NULL,
  `creator` int(11) NOT NULL,
  PRIMARY KEY (`product_cost_change_id`),
  KEY `product_cost_change_product_idx` (`product`, `product_cost_change_id`),
  KEY `product_cost_change_receipt_idx` (`receipt`),
  KEY `product_cost_change_order_detail_idx` (`purchase_order_detail`),
  KEY `product_cost_change_reverted_idx` (`reverted_change`),
  KEY `product_cost_change_creator_idx` (`creator`),
  CONSTRAINT `product_cost_change_product_fk` FOREIGN KEY (`product`) REFERENCES `product` (`product_id`),
  CONSTRAINT `product_cost_change_receipt_fk` FOREIGN KEY (`receipt`) REFERENCES `inventory_receipt` (`inventory_receipt_id`),
  CONSTRAINT `product_cost_change_order_detail_fk` FOREIGN KEY (`purchase_order_detail`) REFERENCES `purchase_order_detail` (`purchase_order_detail_id`),
  CONSTRAINT `product_cost_change_reverted_fk` FOREIGN KEY (`reverted_change`) REFERENCES `product_cost_change` (`product_cost_change_id`),
  CONSTRAINT `product_cost_change_creator_fk` FOREIGN KEY (`creator`) REFERENCES `employee` (`employee_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;
