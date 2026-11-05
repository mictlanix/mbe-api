-- 031 Business analytics: the confirmation moment, date indexes, background exports -- spec 037
--
-- 1. `sales_order.confirmation_time` (FR-002, research R2): the moment an order was confirmed,
--    set by the one confirm path and never changed afterwards, NULL only on drafts. Every order
--    already confirmed takes its `date`, the best record of when it was confirmed. Sales orders
--    are retired from the current system, so no later order is confirmed without it.
-- 2. Date indexes for the analytics facts (research R10). Every date filter they run is a full
--    scan today (EXPLAIN `type ALL`).
-- 3. `analytics_export`, new: one background export (FR-054). Only its owner reads it, and its
--    file is deleted 24 hours after it completes.
--
-- The current system's NHibernate mappings name their columns explicitly, so they neither read nor
-- write `confirmation_time`.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 031_business_analytics_rollback.sql

ALTER TABLE `sales_order`
    ADD COLUMN IF NOT EXISTS `confirmation_time` DATETIME NULL DEFAULT NULL AFTER `date`;

UPDATE `sales_order`
SET `confirmation_time` = `date`
WHERE `completed` = 1 AND `confirmation_time` IS NULL;

CREATE INDEX IF NOT EXISTS `sales_order_confirmation_time_idx`
    ON `sales_order` (`confirmation_time`);

CREATE INDEX IF NOT EXISTS `customer_payment_date_idx`
    ON `customer_payment` (`date`);

CREATE INDEX IF NOT EXISTS `customer_refund_date_idx`
    ON `customer_refund` (`date`);

CREATE INDEX IF NOT EXISTS `fiscal_document_type_issued_idx`
    ON `fiscal_document` (`type`, `issued`);

CREATE TABLE IF NOT EXISTS `analytics_export` (
  `analytics_export_id` int(11) NOT NULL AUTO_INCREMENT,
  `user` varchar(20) NOT NULL COMMENT 'The owner; only they can read or download it',
  `source` tinyint(4) NOT NULL COMMENT '0 query, 1 dashboard, 2 detail',
  `format` tinyint(4) NOT NULL COMMENT '0 xlsx, 1 pdf, 2 csv',
  `definition` text NOT NULL COMMENT 'The normalized request as JSON',
  `status` tinyint(4) NOT NULL COMMENT '0 pending, 1 running, 2 ready, 3 failed',
  `file_name` varchar(100) DEFAULT NULL COMMENT 'Under settings.exports_dir; set when ready',
  `row_count` int(11) DEFAULT NULL,
  `creation_time` datetime NOT NULL,
  `completion_time` datetime DEFAULT NULL,
  `expiry_time` datetime DEFAULT NULL COMMENT 'completion_time + 24 h; the file is deleted after it',
  PRIMARY KEY (`analytics_export_id`),
  KEY `analytics_export_user_idx` (`user`),
  KEY `analytics_export_expiry_time_idx` (`expiry_time`),
  CONSTRAINT `analytics_export_user_fk` FOREIGN KEY (`user`) REFERENCES `user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;
