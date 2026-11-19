-- Rollback of 035 Widen fiscal_document.usage to varchar(4) -- #341
--
-- Narrows `fiscal_document.usage` back to varchar(3).
--
-- FAILS while any document has a 4-character usage, which every payment receipt made since 035
-- has (`CP01`). Step 1 checks for them and stops before changing anything, instead of relying on
-- the server's strict mode to refuse the narrowing. Find them with
--
--   SELECT `fiscal_document_id`, `usage` FROM `fiscal_document` WHERE LENGTH(`usage`) > 3;
--
-- Rewriting them to a 3-character code would change what a stamped CFDI says it is, so decide
-- what to do with them before rolling back rather than letting a script pick.
--
-- MariaDB 10.11.

-- Step 1 -- precondition: every usage still fits in 3 characters. A CHECK on a temporary table
-- rather than SIGNAL, as in 016: the runner splits on semicolons.

CREATE TEMPORARY TABLE `_035_precondition` (
  `every_usage_fits_in_3` TINYINT NOT NULL,
  CONSTRAINT `chk_035_no_long_usages` CHECK (`every_usage_fits_in_3` = 1)
);

INSERT INTO `_035_precondition` (`every_usage_fits_in_3`)
SELECT IF((SELECT COUNT(*) FROM `fiscal_document` WHERE LENGTH(`usage`) > 3) = 0, 1, 0);

DROP TEMPORARY TABLE `_035_precondition`;

-- Step 2 -- the narrowing, around the foreign key as in 035 (error 1832 otherwise).

ALTER TABLE `fiscal_document` DROP FOREIGN KEY IF EXISTS `fiscal_document_usage_fk`;

ALTER TABLE `fiscal_document` MODIFY COLUMN `usage` VARCHAR(3) NULL;

ALTER TABLE `fiscal_document`
  ADD CONSTRAINT `fiscal_document_usage_fk` FOREIGN KEY IF NOT EXISTS (`usage`)
  REFERENCES `sat_cfdi_usage` (`sat_cfdi_usage_id`) ON DELETE NO ACTION ON UPDATE NO ACTION;
