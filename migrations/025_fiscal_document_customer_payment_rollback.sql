-- Rollback for 025_fiscal_document_customer_payment.sql
--
-- Drops the link from payment receipts to customer payments. The receipts themselves are kept;
-- only the record of which payment each was made from is lost.
--
-- MariaDB 10.11.

ALTER TABLE `fiscal_document` DROP FOREIGN KEY IF EXISTS `fiscal_document_customer_payment_fk`;

DROP INDEX IF EXISTS `fiscal_document_customer_payment_idx` ON `fiscal_document`;

ALTER TABLE `fiscal_document` DROP COLUMN IF EXISTS `customer_payment`;
