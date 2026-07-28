-- Rollback for 011_facility_transit_warehouses.sql
--
-- ============================================================================
-- AFTER RUNNING THIS, PUT THE SETTING BACK:
--
--     IN_TRANSIT_WAREHOUSE_ID=20
--
-- The code this rolls back to reads that setting and refuses to start without it. Rolling back
-- the schema without restoring the environment leaves an API that will not boot.
-- ============================================================================
--
-- Restores the single system-wide in-transit warehouse of migration 008: row 20, code
-- 'IN-TRANSIT', facility 1.
--
-- Deliberately NOT guarded against stock sitting in the per-facility locations. If any of them
-- has ledger history, `lot_serial_tracking`'s foreign key rejects the DELETE with error 1451 and
-- names itself -- which is the right answer, and the same one migration 010's rollback relies on.
-- Clear the references first if you really mean it. A rollback that silently stranded ledger rows
-- pointing at deleted warehouses would be worse than one that refuses.
--
-- MariaDB 10.11.

-- Step 1 -- remove the per-facility locations, keeping the original shared row (id 20).
--
-- Keyed on the flag rather than on a code pattern, so a row whose code was somehow edited is
-- still removed. Expected: 13 rows.

DELETE FROM `warehouse`
 WHERE `in_transit` = 1
   AND `warehouse_id` <> 20;

-- Step 2 -- restore row 20 to exactly what 008 seeded.

UPDATE `warehouse`
   SET `code` = 'IN-TRANSIT',
       `comment` = 'Virtual location holding goods between itinerary departure and delivery (migration 008)'
 WHERE `warehouse_id` = 20;

-- Step 3 -- drop the flag.
--
-- Last, because steps 1 and 2 key on it.

ALTER TABLE `warehouse`
  DROP COLUMN IF EXISTS `in_transit`;
