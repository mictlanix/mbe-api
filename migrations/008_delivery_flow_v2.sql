-- 008 Delivery flow v2 (specs/012-delivery-logistics-endpoints)
--
-- Replaces the legacy delivery lifecycle -- five booleans on `delivery_order` and two on
-- `deliveries_itinerary` -- with explicit status columns, adds the v2 quantity model, and
-- introduces stops, proof of delivery and a transition audit trail.
--
-- THIS MIGRATION IS DESTRUCTIVE. Read before running.
--
-- 1. Every one of the 26,763 existing delivery orders is settled into a TERMINAL status.
--    Any delivery genuinely in flight at cutover is cancelled and must be re-raised from its
--    sales order. Cancelled delivery orders do not count as coverage, so re-raising produces
--    the correct lines. Schedule for a quiet period and take a backup first.
-- 2. Seven columns are dropped. A reader that still expects them will break. This was
--    authorised against `mbe_dev`, which is a development copy; the production cutover is a
--    separate decision and MUST confirm no other application still reads these columns.
--
-- Audit figures (measured 2026-07-27, research R12) that this migration relies on:
--
--   delivery_order rows                     26,763
--     cancelled = 1                          1,059  -> CANCELLED
--     delivered = 1, not cancelled           3,769  -> DELIVERED
--     picked_up = 1, neither of the above    4,160  -> PICKED_UP
--     everything else                       17,775  -> CANCELLED (abandoned)
--     picked_up = 1 (any status)             4,218  -> fulfillment_type = COUNTER_PICKUP
--                                                     58 more than reach PICKED_UP status,
--                                                     because those 58 were cancelled. Type and
--                                                     status are independent by design.
--   delivery_order.serial = 0 placeholders   1,219  -> NULL
--   duplicate (facility, serial) groups          0  -> NO renumbering step needed
--   delivery_order.ship_to IS NULL           6,693  -> 807 picked up, 5,886 delivery
--   delivery_order_detail rows              54,962  -> 54,579 inherit a warehouse from their
--                                                      sales-order line, 383 take the facility
--                                                      fallback
--   deliveries_itinerary rows                3,617  -> 244 CANCELLED, 3,373 CLOSED, 0 OPEN
--   deliveries_itinerary_detail rows         9,957  -> all have a valid itinerary; no orphans
--
-- Why no itinerary may be left OPEN: FR-034 refuses a second open itinerary per vehicle, so a
-- stale OPEN row would permanently block its vehicle from ever receiving another itinerary.
-- Exactly one legacy row (cancelled = 0, completed = 0) is affected and becomes CLOSED.
--
-- MariaDB 10.11. DDL is not transactional -- statements are ordered so each table is complete
-- before the next. Rollback: 008_delivery_flow_v2_rollback.sql

-- ---------------------------------------------------------------------------
-- Step 1 -- delivery_order: folio placeholders, then the status columns
-- ---------------------------------------------------------------------------

-- `serial = 0` is the legacy placeholder for "not numbered", not folio zero (1,219 rows).
-- The new model expresses that as NULL, which a MySQL unique index permits any number of.
ALTER TABLE `delivery_order` MODIFY COLUMN `serial` INT(11) NULL;
UPDATE `delivery_order` SET `serial` = NULL WHERE `serial` = 0;

-- No renumbering step: the audit found zero duplicate (facility, serial) groups.
ALTER TABLE `delivery_order` ADD UNIQUE KEY `uq_delivery_order_folio` (`facility`, `serial`);

ALTER TABLE `delivery_order`
  ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN `fulfillment_type` SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN `parent_delivery_order` INT(11) NULL,
  ADD COLUMN `rejection_reason` VARCHAR(500) NULL,
  ADD COLUMN `proof_of_delivery` INT(11) NULL;

-- Fulfilment type first: it is read from `picked_up`, which step 1c drops.
-- 6,693 rows have no ship_to to match against a facility address, so `picked_up` decides.
UPDATE `delivery_order` SET `fulfillment_type` = CASE WHEN `picked_up` = 1 THEN 1 ELSE 0 END;

-- Status mapping (DeliveryOrderStatus: 4 = PICKED_UP, 7 = DELIVERED, 10 = CANCELLED).
-- Order matters: cancelled wins over delivered, delivered over picked up.
UPDATE `delivery_order` SET `status` = CASE
    WHEN `cancelled` = 1 THEN 10
    WHEN `delivered` = 1 THEN 7
    WHEN `picked_up` = 1 THEN 4
    ELSE 10
  END;

ALTER TABLE `delivery_order`
  DROP COLUMN `completed`,
  DROP COLUMN `cancelled`,
  DROP COLUMN `confirmed`,
  DROP COLUMN `delivered`,
  DROP COLUMN `picked_up`;

-- ---------------------------------------------------------------------------
-- Step 2 -- delivery_order_detail: quantities and the snapshotted warehouse
-- ---------------------------------------------------------------------------

ALTER TABLE `delivery_order_detail`
  ADD COLUMN `committed_quantity` DECIMAL(18,4) NOT NULL DEFAULT 0,
  ADD COLUMN `delivered_quantity` DECIMAL(18,4) NOT NULL DEFAULT 0,
  ADD COLUMN `returned_quantity` DECIMAL(18,4) NOT NULL DEFAULT 0,
  ADD COLUMN `warehouse` INT(11) NULL;

-- Preferred source: the originating sales-order line (54,579 rows).
UPDATE `delivery_order_detail` d
  JOIN `sales_order_detail` s ON s.`sales_order_detail_id` = d.`sales_order_detail`
  SET d.`warehouse` = s.`warehouse`
  WHERE s.`warehouse` IS NOT NULL;

-- Fallback for the remaining 383: the lowest-id warehouse of the order's facility. Every one
-- of the 14 facilities has at least one, so this leaves no NULL behind. These rows are all on
-- delivery orders that step 1 settled as terminal, so the value is never used for a movement.
UPDATE `delivery_order_detail` d
  JOIN `delivery_order` o ON o.`delivery_order_id` = d.`delivery_order`
  SET d.`warehouse` = (
    SELECT MIN(w.`warehouse_id`) FROM `warehouse` w WHERE w.`facility` = o.`facility`
  )
  WHERE d.`warehouse` IS NULL;

ALTER TABLE `delivery_order_detail` MODIFY COLUMN `warehouse` INT(11) NOT NULL;

-- ---------------------------------------------------------------------------
-- Step 3 -- new tables
-- ---------------------------------------------------------------------------

CREATE TABLE `proof_of_delivery` (
  `proof_of_delivery_id` INT(11)      NOT NULL AUTO_INCREMENT,
  `receiver_name`        VARCHAR(250) NOT NULL,
  `receiver_id_shown`    VARCHAR(100) NOT NULL,
  `captured_time`        DATETIME     NOT NULL,
  `captured_by`          INT(11)      NOT NULL,
  -- UUID filename under settings.pod_dir; never content-addressed, so two identical captures
  -- cannot alias and one order's proof can never be pulled out from under another (FR-044b).
  `image_file`           VARCHAR(255) NOT NULL,
  PRIMARY KEY (`proof_of_delivery_id`),
  KEY `ix_pod_captured_by` (`captured_by`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `delivery_order_event` (
  `delivery_order_event_id` INT(11)      NOT NULL AUTO_INCREMENT,
  `delivery_order`          INT(11)      NOT NULL,
  `from_status`             SMALLINT     NULL,
  `to_status`               SMALLINT     NOT NULL,
  `employee`                INT(11)      NOT NULL,
  `event_time`              DATETIME     NOT NULL,
  `reason`                  VARCHAR(500) NULL,
  PRIMARY KEY (`delivery_order_event_id`),
  KEY `ix_delivery_order_event_order` (`delivery_order`, `delivery_order_event_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE `deliveries_itinerary_stop` (
  `deliveries_itinerary_stop_id` INT(11)      NOT NULL AUTO_INCREMENT,
  `deliveries_itinerary`         INT(11)      NOT NULL,
  `sequence`                     SMALLINT     NOT NULL,
  `arrival_time`                 DATETIME     NULL,
  `outcome`                      SMALLINT     NOT NULL DEFAULT 0,
  `proof_of_delivery`            INT(11)      NULL,
  `comment`                      VARCHAR(500) NULL,
  PRIMARY KEY (`deliveries_itinerary_stop_id`),
  UNIQUE KEY `uq_itinerary_stop_sequence` (`deliveries_itinerary`, `sequence`),
  KEY `ix_stop_pod` (`proof_of_delivery`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ---------------------------------------------------------------------------
-- Step 4 -- deliveries_itinerary: status replaces the two booleans
-- ---------------------------------------------------------------------------

ALTER TABLE `deliveries_itinerary`
  ADD COLUMN `status` SMALLINT NOT NULL DEFAULT 0,
  ADD COLUMN `departure_time` DATETIME NULL,
  ADD COLUMN `return_time` DATETIME NULL;

-- ItineraryStatus: 2 = CLOSED, 3 = CANCELLED. Nothing may remain OPEN (see header).
UPDATE `deliveries_itinerary` SET `status` = CASE
    WHEN `cancelled` = 1 THEN 3
    ELSE 2
  END;

ALTER TABLE `deliveries_itinerary`
  DROP COLUMN `cancelled`,
  DROP COLUMN `completed`;

ALTER TABLE `deliveries_itinerary` ADD KEY `ix_deliveries_itinerary_status_date` (`status`, `date`);

-- ---------------------------------------------------------------------------
-- Step 5 -- deliveries_itinerary_detail: stops, per-trip quantities
-- ---------------------------------------------------------------------------

-- One synthetic stop per existing itinerary, so the new NOT NULL stop FK is satisfiable.
INSERT INTO `deliveries_itinerary_stop` (`deliveries_itinerary`, `sequence`, `outcome`, `comment`)
  SELECT `deliveries_itinerary_id`, 1, 1, 'Synthetic stop created by migration 008'
  FROM `deliveries_itinerary`;

ALTER TABLE `deliveries_itinerary_detail`
  CHANGE COLUMN `quantity` `committed_quantity` DECIMAL(20,6) NOT NULL,
  ADD COLUMN `sent_quantity` DECIMAL(20,6) NOT NULL DEFAULT 0,
  ADD COLUMN `delivered_quantity` DECIMAL(20,6) NOT NULL DEFAULT 0,
  ADD COLUMN `returned_quantity` DECIMAL(20,6) NOT NULL DEFAULT 0,
  ADD COLUMN `reason_code` SMALLINT NULL,
  ADD COLUMN `deliveries_itinerary_stop` INT(11) NULL;

-- Legacy lines are historical: what was loaded was sent, and what was sent was delivered.
UPDATE `deliveries_itinerary_detail`
  SET `sent_quantity` = `committed_quantity`,
      `delivered_quantity` = `committed_quantity`,
      `returned_quantity` = 0;

UPDATE `deliveries_itinerary_detail` d
  JOIN `deliveries_itinerary_stop` s ON s.`deliveries_itinerary` = d.`deliveries_itinerary`
  SET d.`deliveries_itinerary_stop` = s.`deliveries_itinerary_stop_id`;

ALTER TABLE `deliveries_itinerary_detail`
  MODIFY COLUMN `deliveries_itinerary_stop` INT(11) NOT NULL;

-- The stop is now the sole path to the trip. Keeping a direct itinerary FK alongside it would
-- permit a line claiming itinerary A while its stop belongs to itinerary B.
--
-- The constraint must go before the column: MariaDB refuses to drop a column whose index is
-- still needed by a foreign key (error 1553).
ALTER TABLE `deliveries_itinerary_detail`
  DROP FOREIGN KEY `FK_deliveries_itinerary_detail_deliveries_itinerary`;
ALTER TABLE `deliveries_itinerary_detail` DROP COLUMN `deliveries_itinerary`;

ALTER TABLE `deliveries_itinerary_detail`
  ADD KEY `ix_itinerary_detail_stop` (`deliveries_itinerary_stop`);

-- ---------------------------------------------------------------------------
-- Step 6 -- the in-transit virtual warehouse
-- ---------------------------------------------------------------------------
--
-- Goods that have left a warehouse and not yet reached a customer live here, so warehouse
-- on-hand never counts stock that is physically on a truck (SC-005). It is an ordinary
-- warehouse row, which is why `stock_ledger.on_hand` reports its balance with no new code.
--
-- AFTER RUNNING: set `IN_TRANSIT_WAREHOUSE_ID` in the environment to the id created here.
-- Until it is set the API refuses to start, rather than posting ledger entries against
-- warehouse 0. Recover the id with:
--   SELECT warehouse_id FROM warehouse WHERE code = 'IN-TRANSIT';

INSERT INTO `warehouse` (`facility`, `code`, `name`, `comment`, `status`)
  SELECT MIN(`facility_id`), 'IN-TRANSIT', 'In Transit',
         'Virtual location holding goods between itinerary departure and delivery (migration 008)',
         0
  FROM `facility`;
