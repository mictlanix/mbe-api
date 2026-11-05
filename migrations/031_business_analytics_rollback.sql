-- Rollback of 031 Business analytics -- spec 037
--
-- Drops the export table, the four indexes and the confirmation column. Export files left under
-- `settings.exports_dir` are not referenced by anything afterwards and can be deleted by hand.
--
-- MariaDB 10.11.

DROP TABLE IF EXISTS `analytics_export`;

DROP INDEX IF EXISTS `fiscal_document_type_issued_idx` ON `fiscal_document`;

DROP INDEX IF EXISTS `customer_refund_date_idx` ON `customer_refund`;

DROP INDEX IF EXISTS `customer_payment_date_idx` ON `customer_payment`;

DROP INDEX IF EXISTS `sales_order_confirmation_time_idx` ON `sales_order`;

ALTER TABLE `sales_order` DROP COLUMN IF EXISTS `confirmation_time`;
