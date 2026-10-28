-- 030 Customer payment serials, and the ledger and reservation indexes -- spec 035
--
-- 1. Backfill the customer payments this API wrote with `serial = 0` (research R7, FR-015). Until
--    spec 035 the API did not number its payments; the current system numbers every payment per
--    store. Within each facility, in id order, they take `MAX(serial) + ROW_NUMBER()`, as migration
--    007 renumbered duplicates. Only `serial = 0` rows change: on mbe_dev those are the API's 20,
--    all at facility 51. A second run finds no zeros and changes nothing.
-- 2. `customer_payment (facility, serial)`, so a payment's `MAX(serial)` is an index lookup rather
--    than a filesort over the facility's rows (FR-016). Not unique: the current system's duplicate
--    serials (11 groups on mbe_dev) stay.
-- 3. `lot_serial_tracking (source, reference)`: an inventory document's ledger entries, read when
--    it is cancelled and for its warehouse locks, are found without scanning the ledger (FR-022).
-- 4. `lot_serial_rqmt (source, reference)`: a sales order cancel's `release_reservations` deletes
--    by source and reference; without an index that delete scans and next-key-locks the whole
--    table until the cancel commits. Departure and return look reservations up the same way.
--
-- Run it right after the deploy (research R7): run before, a payment the old code writes in
-- between would still be 0; it takes no facility lock, so it should not run while the new code is
-- numbering payments.
--
-- The current system's NHibernate mappings name their columns and ignore indexes, so neither
-- change affects it.
--
-- Idempotent. MariaDB 10.11 (ROW_NUMBER requires 10.2+).
-- Rollback: 030_serials_ledger_index_rollback.sql

-- Step 1 — number the API's unnumbered payments
UPDATE `customer_payment` p
JOIN (
    SELECT z.`customer_payment_id`,
           m.mx + ROW_NUMBER() OVER (PARTITION BY z.`facility` ORDER BY z.`customer_payment_id`) AS new_serial
    FROM `customer_payment` z
    JOIN (SELECT `facility` AS f, MAX(`serial`) AS mx FROM `customer_payment` GROUP BY `facility`) m
      ON m.f = z.`facility`
    WHERE z.`serial` = 0
) x ON x.`customer_payment_id` = p.`customer_payment_id`
SET p.`serial` = x.new_serial;

-- Step 2 — the payment folio's MAX through an index
CREATE INDEX IF NOT EXISTS `customer_payment_facility_serial_idx`
    ON `customer_payment` (`facility`, `serial`);

-- Step 3 — an inventory document's ledger entries
CREATE INDEX IF NOT EXISTS `lot_serial_tracking_source_reference_idx`
    ON `lot_serial_tracking` (`source`, `reference`);

-- Step 4 — a document's reservations
CREATE INDEX IF NOT EXISTS `lot_serial_rqmt_source_reference_idx`
    ON `lot_serial_rqmt` (`source`, `reference`);
