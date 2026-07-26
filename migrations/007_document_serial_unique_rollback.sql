-- Rollback for 007_document_serial_unique.sql
--
-- Drops the three unique indexes. The data changes are NOT reversed and cannot be:
-- once serial = 0 has become NULL the original value is indistinguishable from a
-- draft, and once a duplicate has been renumbered the old folio is gone. Both were
-- corrections rather than conversions, so there is nothing to restore to.
--
-- Dropping these indexes returns folio uniqueness to being enforced only by the
-- application's FOR UPDATE lock on the facility row.

DROP INDEX `sales_order_facility_serial_uq` ON `sales_order`;
DROP INDEX `sales_quote_facility_serial_uq` ON `sales_quote`;
DROP INDEX `customer_refund_facility_serial_uq` ON `customer_refund`;
