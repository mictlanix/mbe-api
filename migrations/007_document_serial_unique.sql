-- 007 One folio per facility, enforced by the database (spec 011, SC-005)
--
-- `sales_order`, `sales_quote` and `customer_refund` each number themselves per
-- facility via MAX(serial)+1. No unique index backed that up, so two concurrent
-- confirmations could take the same folio and nothing would notice. The API takes
-- a FOR UPDATE lock on the facility row to serialise them, but a lock is only as
-- good as every future code path remembering it. This adds the constraint.
--
-- Two classes of existing violation are cleared first, in this order.
--
-- 1. serial = 0 is the legacy application's placeholder for "not numbered", not
--    folio zero. It appears on 4,065 sales quotes (3,974 of them completed), 172
--    customer refunds and 3 sales orders, all from 2024 onward. These become NULL,
--    which is what "no folio yet" means in the new model and which a MySQL unique
--    index permits any number of. No real folio changes.
--
-- 2. Thirty-four rows dating 2018-2023 genuinely share a folio within a facility:
--    9 groups in sales_quote, 4 in customer_refund, none in sales_order. In each
--    group the earliest document (lowest id) keeps its folio and the later ones are
--    reassigned to the next free serials for that facility. Twenty-one rows move.
--    A reassigned customer_refund folio will no longer match a receipt printed years
--    ago; that was accepted deliberately when this migration was authorised.
--
-- Step 1 MUST run before step 2. Renumbering first would treat the 4,240 serial = 0
-- placeholder rows as duplicates and issue them real folios.
--
-- MariaDB 10.11 (ROW_NUMBER requires 10.2+).
-- Rollback: 007_document_serial_unique_rollback.sql

-- Step 1 — the placeholder is not a folio
UPDATE `sales_order` SET `serial` = NULL WHERE `serial` = 0;
UPDATE `sales_quote` SET `serial` = NULL WHERE `serial` = 0;
UPDATE `customer_refund` SET `serial` = NULL WHERE `serial` = 0;

-- Step 2 — the earliest document keeps the folio; later ones move to the end
UPDATE `sales_quote` q
JOIN (
    SELECT s.`sales_quote_id`,
           m.mx + ROW_NUMBER() OVER (PARTITION BY s.`facility` ORDER BY s.`sales_quote_id`) AS new_serial
    FROM (
        SELECT `sales_quote_id`, `facility`,
               ROW_NUMBER() OVER (PARTITION BY `facility`, `serial` ORDER BY `sales_quote_id`) AS rn
        FROM `sales_quote`
        WHERE `serial` IS NOT NULL
    ) s
    JOIN (SELECT `facility` AS f, MAX(`serial`) AS mx FROM `sales_quote` GROUP BY `facility`) m
      ON m.f = s.`facility`
    WHERE s.rn > 1
) x ON x.`sales_quote_id` = q.`sales_quote_id`
SET q.`serial` = x.new_serial;

UPDATE `customer_refund` r
JOIN (
    SELECT s.`customer_refund_id`,
           m.mx + ROW_NUMBER() OVER (PARTITION BY s.`facility` ORDER BY s.`customer_refund_id`) AS new_serial
    FROM (
        SELECT `customer_refund_id`, `facility`,
               ROW_NUMBER() OVER (PARTITION BY `facility`, `serial` ORDER BY `customer_refund_id`) AS rn
        FROM `customer_refund`
        WHERE `serial` IS NOT NULL
    ) s
    JOIN (SELECT `facility` AS f, MAX(`serial`) AS mx FROM `customer_refund` GROUP BY `facility`) m
      ON m.f = s.`facility`
    WHERE s.rn > 1
) x ON x.`customer_refund_id` = r.`customer_refund_id`
SET r.`serial` = x.new_serial;

-- Step 3 — the constraint itself. NULL serials (drafts) do not collide.
CREATE UNIQUE INDEX `sales_order_facility_serial_uq` ON `sales_order` (`facility`, `serial`);
CREATE UNIQUE INDEX `sales_quote_facility_serial_uq` ON `sales_quote` (`facility`, `serial`);
CREATE UNIQUE INDEX `customer_refund_facility_serial_uq` ON `customer_refund` (`facility`, `serial`);
