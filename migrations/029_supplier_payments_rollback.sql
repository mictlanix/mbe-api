-- Rollback of 029 Supplier payments -- spec 032
--
-- Drops the balance cutoff, the applications, and the columns `supplier_payment` gained. Every
-- application, every payment's number, session and cancellation, and the cutoff date are lost with
-- them; a cancelled payment reads as live again. Export them first if they matter:
--   SELECT * FROM purchase_order_payment;  SELECT * FROM supplier_balance_cutoff;
--   SELECT supplier_payment_id, facility, serial, cash_session, cancelled, creation_time, updater,
--     modification_time FROM supplier_payment;
--
-- Payments made through the API stay, as rows of the original shape. The models still declare the
-- tables and columns afterwards, so revert the application code with it.
--
-- MariaDB 10.11.

DROP TABLE IF EXISTS `supplier_balance_cutoff`;

DROP TABLE IF EXISTS `purchase_order_payment`;

ALTER TABLE `supplier_payment` DROP FOREIGN KEY IF EXISTS `supplier_payment_updater_fk`;

ALTER TABLE `supplier_payment` DROP FOREIGN KEY IF EXISTS `supplier_payment_cash_session_fk`;

ALTER TABLE `supplier_payment` DROP FOREIGN KEY IF EXISTS `supplier_payment_facility_fk`;

DROP INDEX IF EXISTS `supplier_payment_updater_idx` ON `supplier_payment`;

DROP INDEX IF EXISTS `supplier_payment_cash_session_idx` ON `supplier_payment`;

DROP INDEX IF EXISTS `supplier_payment_facility_serial_uq` ON `supplier_payment`;

ALTER TABLE `supplier_payment`
    DROP COLUMN IF EXISTS `modification_time`,
    DROP COLUMN IF EXISTS `updater`,
    DROP COLUMN IF EXISTS `creation_time`,
    DROP COLUMN IF EXISTS `cancelled`,
    DROP COLUMN IF EXISTS `cash_session`,
    DROP COLUMN IF EXISTS `serial`,
    DROP COLUMN IF EXISTS `facility`;
