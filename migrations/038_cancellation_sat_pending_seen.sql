-- 038 Record when SAT first showed a cancellation request in process (#349)
--
-- SAT's status names no request. After a receiver rejects a cancellation, SAT keeps answering
-- `valid` + `rejected` until it registers the next request, so a refresh could close a second
-- request against the first one's rejection: the document stayed issued here while SAT cancelled
-- it once the receiver accepted (#349, document 63952). `sat_pending_at` is when a refresh first
-- saw SAT report `pending` for this request. A `rejected` answer then closes a request the PAC
-- answered only once SAT has shown it in process, or when SAT rejected no earlier request of the
-- document. Nullable, set by the API only; no backfill.
--
-- MEASURED against mbe_dev 2026-11-24: 37 requests on 29 documents, 1 open (`pending`, no earlier
-- rejection). 6 requests follow an earlier rejected one; one of them is #33 of #349, closed
-- `rejected` with no answer. NULL on every existing row is right: a closed request never reads it,
-- and the open one keeps the flow it has today.
--
-- The current system has no cancellation request table (034 created it), so it neither reads nor
-- writes this column.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 038_cancellation_sat_pending_seen_rollback.sql

ALTER TABLE `fiscal_document_cancellation`
  ADD COLUMN IF NOT EXISTS `sat_pending_at` DATETIME NULL DEFAULT NULL AFTER `last_checked_at`;
