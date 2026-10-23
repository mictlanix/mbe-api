-- Rollback of 023 Commission periods -- spec 025
--
-- Drops the stored lines, then the periods, then the scheme column. Every closed or paid month and
-- every agent's scheme is lost with them; export them first if they matter:
--   SELECT * FROM commission_period;  SELECT * FROM commission_period_line;
--   SELECT employee, scheme FROM commission_agent WHERE scheme <> 1;
--
-- The `balance_zeroed_time` backfill is not undone: the stamps it wrote are what the current
-- system would have written when those orders were paid.
--
-- MariaDB 10.11.

DROP TABLE IF EXISTS `commission_period_line`;

DROP TABLE IF EXISTS `commission_period`;

ALTER TABLE `commission_agent`
  DROP COLUMN IF EXISTS `scheme`;
