-- Rollback of 032 Operations analytics -- spec 038
--
-- Drops the seven tables, children first, and then the ledger date index. Saved views, schedules,
-- their send history and the receivable month-ends are lost; the month-ends can be rebuilt with
-- `uv run python -m app.jobs.snapshot_receivables --from 2025-01` after reapplying 032.
--
-- MariaDB 10.11.

DROP TABLE IF EXISTS `analytics_send`;

DROP TABLE IF EXISTS `analytics_schedule_recipient`;

DROP TABLE IF EXISTS `analytics_schedule`;

DROP TABLE IF EXISTS `analytics_view_share`;

DROP TABLE IF EXISTS `analytics_view`;

DROP TABLE IF EXISTS `receivable_snapshot_month`;

DROP TABLE IF EXISTS `receivable_snapshot`;

DROP INDEX IF EXISTS `lot_serial_tracking_date_idx` ON `lot_serial_tracking`;
