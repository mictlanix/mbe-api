-- 035 Widen fiscal_document.usage to varchar(4) -- issue #341
--
-- A payment receipt's CFDI 4.0 usage is `CP01` (`invoice_service.RECEIPT_USAGE`), four characters.
-- `fiscal_document.usage` is the legacy `varchar(3)`, so `POST /fiscal-documents/payment-receipts`
-- failed every time with error 1406, "Data too long for column 'usage'", answered as a 500. The
-- column's foreign key target, `sat_cfdi_usage.sat_cfdi_usage_id`, is already `varchar(4)` and
-- already holds `CP01`. The test database is SQLite, which does not enforce a varchar length.
--
-- MEASURED against mbe_dev 2026-11-18: no row is longer than 3 characters. The legacy application
-- wrote `P01` on its 88 payment receipts (type 200); its 3-character codes (G01, G03, P01...) all
-- fit the wider column unchanged.
--
-- WHY THREE STATEMENTS: MariaDB refuses to change a column a foreign key uses, error 1832, "Cannot
-- change column 'usage': used in a foreign key constraint". So the key is dropped, the column
-- widened, and the key re-added exactly as it was: same name, same target, NO ACTION both ways. Its
-- index, `fiscal_document_usage_idx`, stays, and the column keeps the table's utf8mb3_unicode_ci,
-- which matches the target's. Widening a varchar rewrites no value.
--
-- The current system's mappings name the column and never write more than 3 characters to it.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 035_fiscal_document_usage_width_rollback.sql

ALTER TABLE `fiscal_document` DROP FOREIGN KEY IF EXISTS `fiscal_document_usage_fk`;

ALTER TABLE `fiscal_document` MODIFY COLUMN `usage` VARCHAR(4) NULL;

ALTER TABLE `fiscal_document`
  ADD CONSTRAINT `fiscal_document_usage_fk` FOREIGN KEY IF NOT EXISTS (`usage`)
  REFERENCES `sat_cfdi_usage` (`sat_cfdi_usage_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;
