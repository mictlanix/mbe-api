-- 011 One in-transit location per facility (specs/013-facility-transit-warehouses)
--
-- Migration 008 seeded ONE system-wide in-transit warehouse. Every warehouse belongs to exactly
-- one facility, so that row had to be parented on an arbitrary one -- `MIN(facility_id)`. The
-- consequence is that goods dispatched from any facility accumulate on one facility's books,
-- which contradicts the org chart the whole warehouse->facility edge exists to express.
--
-- Sharper than that, as measured below: the shared row sits on facility 1, which is INACTIVE.
-- `available_orders` filters on active facilities, so facility 1 can never dispatch -- yet it was
-- holding every other facility's in-transit stock.
--
-- MEASURED 2026-07-28 against the deployment database, and re-confirmed immediately before this
-- migration was written (research R6, task T002):
--
--   facilities                                          14  (7 ACTIVE, 7 INACTIVE)
--   facilities with at least one real warehouse         14  (none is warehouse-less)
--   warehouse rows                                      19  (18 real, 1 in-transit)
--   the in-transit row                    id 20, code 'IN-TRANSIT', facility 1
--   lot_serial_tracking rows against warehouse 20        0  <-- never posted to
--   nonzero in-transit balances                       none
--   itineraries in DEPARTED                              0
--
-- Because the shared location has never been posted to, there is NOTHING TO REDISTRIBUTE. The
-- balance guard in step 1 is therefore an assertion rather than a rewrite. It stays in the file so
-- the claim is checked at run time rather than only at planning time -- if this migration is ever
-- applied to a database where goods are genuinely in flight, it stops instead of guessing which
-- facility they belong to. A single (itinerary, product) ledger row can span two facilities and
-- cannot be split by an UPDATE, which is why guessing is not on offer.
--
-- AFTER RUNNING: nothing. Unlike 008, no id has to be captured into the environment --
-- `IN_TRANSIT_WAREHOUSE_ID` is retired by this feature and the rows are found by their flag.
--
-- The whole file is idempotent: ADD COLUMN IF NOT EXISTS, a conversion keyed on the old code, and
-- a NOT EXISTS-guarded backfill. Re-applying inserts nothing and changes nothing.
--
-- MariaDB 10.11. Rollback: 011_facility_transit_warehouses_rollback.sql

-- ---------------------------------------------------------------------------
-- Step 1 -- precondition: the shared location holds no stock
-- ---------------------------------------------------------------------------
--
-- A CHECK on a temporary table rather than SIGNAL: SIGNAL is only legal inside a compound
-- statement, and the migration runner splits this file on semicolons, so a BEGIN...END block
-- would be torn apart. The INSERT below fails on `chk_011_no_stock_in_transit` when the balance
-- is nonzero, and the runner prints the failing statement verbatim.

CREATE TEMPORARY TABLE `_011_precondition` (
  `in_transit_balance_is_zero` TINYINT NOT NULL,
  CONSTRAINT `chk_011_no_stock_in_transit` CHECK (`in_transit_balance_is_zero` = 1)
);

INSERT INTO `_011_precondition` (`in_transit_balance_is_zero`)
SELECT IF(
  COALESCE((
    SELECT SUM(t.`quantity`)
      FROM `lot_serial_tracking` t
      JOIN `warehouse` w ON w.`warehouse_id` = t.`warehouse`
     WHERE w.`code` = 'IN-TRANSIT'
  ), 0) = 0,
  1, 0
);

DROP TEMPORARY TABLE `_011_precondition`;

-- ---------------------------------------------------------------------------
-- Step 2 -- the flag
-- ---------------------------------------------------------------------------
--
-- DEFAULT 0 is load-bearing, not incidental. The legacy application writes to this table and does
-- not know the column exists, so its inserts default to 0 and it can never produce a second
-- in-transit row for a facility. That is why this invariant needs no database constraint: there
-- is no writer capable of violating it (research R3).

ALTER TABLE `warehouse`
  ADD COLUMN IF NOT EXISTS `in_transit` TINYINT(1) NOT NULL DEFAULT 0;

-- ---------------------------------------------------------------------------
-- Step 3 -- convert the existing shared row instead of discarding it
-- ---------------------------------------------------------------------------
--
-- It already belongs to facility 1, so it becomes facility 1's own in-transit location. Its code
-- is renumbered to match the scheme the backfill uses. Facility 1 is INACTIVE and can never
-- dispatch, so it keeps a location it will never use -- correct and harmless, and exactly what
-- "every facility has one" means. Recorded here so it is not read later as a bug.

UPDATE `warehouse`
   SET `in_transit` = 1,
       `code` = CONCAT('IN-TRANSIT-', `facility`),
       `comment` = 'Virtual location holding goods between itinerary departure and delivery (migration 011)'
 WHERE `code` = 'IN-TRANSIT';

-- ---------------------------------------------------------------------------
-- Step 4 -- one for every remaining facility
-- ---------------------------------------------------------------------------
--
-- Keyed on facility_id, not facility code: codes are editable, and a renamed facility would
-- otherwise strand its warehouse code. Status 0 is ACTIVE -- the location must work even for an
-- INACTIVE facility, so that deactivating one cannot strand goods already on a truck.
--
-- Expected: 13 rows inserted (14 facilities, 1 converted in step 3).

INSERT INTO `warehouse` (`facility`, `code`, `name`, `comment`, `status`, `in_transit`)
SELECT f.`facility_id`,
       CONCAT('IN-TRANSIT-', f.`facility_id`),
       'In Transit',
       'Virtual location holding goods between itinerary departure and delivery (migration 011)',
       0,
       1
  FROM `facility` f
 WHERE NOT EXISTS (
   SELECT 1 FROM `warehouse` w
    WHERE w.`facility` = f.`facility_id` AND w.`in_transit` = 1
 );

-- ---------------------------------------------------------------------------
-- Step 5 -- assert the invariants this migration exists to establish
-- ---------------------------------------------------------------------------
--
-- FR-001: exactly one in-transit location per facility -- no facility without, none with two.
-- FR-018: warehouse codes are still unique. `code_UNIQUE` would have rejected a collision during
-- step 4 already; this asserts it rather than trusting that it would have.

CREATE TEMPORARY TABLE `_011_postcondition` (
  `every_facility_has_exactly_one` TINYINT NOT NULL,
  `codes_are_unique` TINYINT NOT NULL,
  CONSTRAINT `chk_011_one_per_facility` CHECK (`every_facility_has_exactly_one` = 1),
  CONSTRAINT `chk_011_codes_unique` CHECK (`codes_are_unique` = 1)
);

INSERT INTO `_011_postcondition` (`every_facility_has_exactly_one`, `codes_are_unique`)
SELECT
  IF((
    SELECT COUNT(*) FROM (
      SELECT f.`facility_id`
        FROM `facility` f
        LEFT JOIN `warehouse` w
          ON w.`facility` = f.`facility_id` AND w.`in_transit` = 1
       GROUP BY f.`facility_id`
      HAVING COUNT(w.`warehouse_id`) <> 1
    ) AS offenders
  ) = 0, 1, 0),
  IF((
    SELECT COUNT(*) FROM (
      SELECT `code` FROM `warehouse` GROUP BY `code` HAVING COUNT(*) > 1
    ) AS dupes
  ) = 0, 1, 0);

DROP TEMPORARY TABLE `_011_postcondition`;
