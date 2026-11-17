-- Rollback of 034 CFDI stamping and cancellation -- spec 039
--
-- Drops the two tables and the two `fiscal_document` columns. The cancellation requests and the
-- simulator's state are lost. A document being stamped or with a cancellation pending loses that
-- marker: it reads as sealed or issued again, so refresh or finish those first.
--
-- MariaDB 10.11.

DROP TABLE IF EXISTS `pac_simulation`;

DROP TABLE IF EXISTS `fiscal_document_cancellation`;

ALTER TABLE `fiscal_document`
  DROP COLUMN IF EXISTS `cancelling_since`,
  DROP COLUMN IF EXISTS `stamping_since`;
