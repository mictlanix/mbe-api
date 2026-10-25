-- 025 Link a payment receipt to the customer payment it was made from (spec 028, research R1)
--
-- A payment receipt (CFDI type 200) can be created from a recorded customer payment. The receipt
-- keeps that link so a payment backs at most one live receipt, and so an auditor can go from the
-- CFDI to the money. In mbe_dev no receipt has ever been linked: the current system types every
-- receipt by hand, so every existing row stays NULL.
--
-- The current system's NHibernate mapping names its columns explicitly, so it neither reads nor
-- writes this one.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 025_fiscal_document_customer_payment_rollback.sql

ALTER TABLE `fiscal_document`
    ADD COLUMN IF NOT EXISTS `customer_payment` INT(11) NULL DEFAULT NULL;

CREATE INDEX IF NOT EXISTS `fiscal_document_customer_payment_idx`
    ON `fiscal_document` (`customer_payment`);

ALTER TABLE `fiscal_document`
    ADD CONSTRAINT `fiscal_document_customer_payment_fk`
    FOREIGN KEY IF NOT EXISTS (`customer_payment`)
    REFERENCES `customer_payment` (`customer_payment_id`);
