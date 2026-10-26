-- Rollback of 027 Physical inventory counts -- spec 030
--
-- Drops the count lines, then the counts. Every count, draft or confirmed, is lost with them;
-- export them first if they matter:
--   SELECT * FROM inventory_count;  SELECT * FROM inventory_count_detail;
--
-- The ledger entries confirmed counts posted (`lot_serial_tracking.source = 12`) are kept, so stock
-- is unchanged; what is lost is the document they reference. The models still declare both tables
-- afterwards, so revert the application code with it.
--
-- MariaDB 10.11.

DROP TABLE IF EXISTS `inventory_count_detail`;

DROP TABLE IF EXISTS `inventory_count`;
