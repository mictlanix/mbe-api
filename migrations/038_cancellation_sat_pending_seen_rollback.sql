-- Rollback of 038 Record when SAT first showed a cancellation request in process (#349)
--
-- Drops the column. The requests themselves are kept; what is lost is whether SAT was seen with
-- each one in process. Run it together with a deploy of code from before 038, which neither reads
-- nor writes the column; code from 038 on fails without it.
--
-- MariaDB 10.11.

ALTER TABLE `fiscal_document_cancellation` DROP COLUMN IF EXISTS `sat_pending_at`;
