-- Rollback of 030 Customer payment serials, and the ledger and reservation indexes -- spec 035
--
-- Drops the three indexes. The backfilled payment serials stay: they are valid folios, and tickets
-- printed since may already show them. Setting them back to 0 would hide those folios.
--
-- MariaDB 10.11.

DROP INDEX IF EXISTS `lot_serial_rqmt_source_reference_idx` ON `lot_serial_rqmt`;

DROP INDEX IF EXISTS `lot_serial_tracking_source_reference_idx` ON `lot_serial_tracking`;

DROP INDEX IF EXISTS `customer_payment_facility_serial_idx` ON `customer_payment`;
