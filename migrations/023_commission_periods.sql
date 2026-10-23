-- 023 Commission periods with stable history, and the paid stamp they read -- spec 025
--
-- The current system pays commissions from a report it recomputes from live sales every time it
-- runs, crediting whoever is the customer's salesperson at that moment. Reassigning a customer, or
-- changing a rate, rewrites every past period. Nothing is ever closed or marked paid, and
-- `commissions_history` is a stale snapshot nothing reads or writes; it is left untouched here.
--
-- Three changes:
--
-- 1. `commission_agent.scheme`: each agent is paid either on fully paid orders (1, the current
--    system's rule) or proportionally on partial payments (2). The current system maps only
--    `employee` and creates agents directly in the database, so a defaulted column is invisible to
--    it. Every existing agent starts on 1, the rule they are paid by today.
--
-- 2. `commission_period` and `commission_period_line`: one row per closed month, and the lines it
--    froze. Names and rates are copied, never joined, so editing a customer, a product, a label or a
--    rate never changes a closed month. Order, product and customer ids are kept for tracing but are
--    deliberately not foreign keys, for the same reason.
--
-- 3. `sales_order.balance_zeroed_time` backfill: the date that places a paid order in a month. The
--    current system stamps it in every payment path, and has since 2025 (51% of that year's paid
--    orders, 99.9% of 2026's). This API never did; it does from now on. Paid orders dated from
--    2025-10-01 that have no stamp (NULL or the `0001-01-01` sentinel) get the date of their latest
--    standing application, or their `modification_time` if none is dated: 38 rows in mbe_dev,
--    6 of them paid through this API. Older orders are left alone and earn no commission here.
--
-- Idempotent: `IF NOT EXISTS` on the column and the tables, and the backfill only touches rows that
-- still lack a stamp. The rollback drops the tables and the column; it does not undo the backfill,
-- which matches what the current system would have written.
--
-- MariaDB 10.11. Rollback: 023_commission_periods_rollback.sql

ALTER TABLE `commission_agent`
  ADD COLUMN IF NOT EXISTS `scheme` SMALLINT NOT NULL DEFAULT 1
  COMMENT '1 fully paid orders, 2 partial payments (spec 025)';

CREATE TABLE IF NOT EXISTS `commission_period` (
  `commission_period_id` int(11) NOT NULL AUTO_INCREMENT,
  `year` smallint(6) NOT NULL,
  `month` tinyint(4) NOT NULL,
  `status` smallint(6) NOT NULL DEFAULT 0 COMMENT '0 open (after a reopen), 1 closed, 2 paid',
  `closed_by` int(11) DEFAULT NULL,
  `closed_at` datetime DEFAULT NULL,
  `reopened_by` int(11) DEFAULT NULL,
  `reopened_at` datetime DEFAULT NULL,
  `paid_by` int(11) DEFAULT NULL,
  `paid_at` datetime DEFAULT NULL,
  PRIMARY KEY (`commission_period_id`),
  UNIQUE KEY `commission_period_year_month_uq` (`year`, `month`),
  KEY `commission_period_closed_by` (`closed_by`),
  KEY `commission_period_reopened_by` (`reopened_by`),
  KEY `commission_period_paid_by` (`paid_by`),
  CONSTRAINT `commission_period_closed_by_fk` FOREIGN KEY (`closed_by`) REFERENCES `employee` (`employee_id`),
  CONSTRAINT `commission_period_reopened_by_fk` FOREIGN KEY (`reopened_by`) REFERENCES `employee` (`employee_id`),
  CONSTRAINT `commission_period_paid_by_fk` FOREIGN KEY (`paid_by`) REFERENCES `employee` (`employee_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `commission_period_line` (
  `commission_period_line_id` int(11) NOT NULL AUTO_INCREMENT,
  `commission_period` int(11) NOT NULL,
  `scheme` smallint(6) NOT NULL,
  `movement` smallint(6) NOT NULL COMMENT '0 sale, 1 refund',
  `sales_order` int(11) NOT NULL,
  `sales_order_detail` int(11) NOT NULL,
  `customer_refund_detail` int(11) DEFAULT NULL,
  `order_date` datetime NOT NULL,
  `paid_date` datetime NOT NULL,
  `customer` int(11) NOT NULL,
  `customer_name` varchar(250) NOT NULL,
  `product` int(11) NOT NULL,
  `product_name` varchar(250) NOT NULL,
  `label` int(11) DEFAULT NULL,
  `label_name` varchar(50) DEFAULT NULL,
  `label_rate` decimal(20,6) NOT NULL,
  `salesperson` int(11) NOT NULL,
  `order_salesperson` int(11) NOT NULL,
  `customer_salesperson` int(11) NOT NULL,
  `participation` int(11) NOT NULL,
  `participation_name` varchar(50) NOT NULL,
  `participation_rate` decimal(20,6) NOT NULL,
  `rate` decimal(20,8) NOT NULL,
  `price` decimal(22,2) NOT NULL,
  `quantity` decimal(18,4) NOT NULL,
  `total` decimal(22,2) NOT NULL,
  `full_commission` decimal(22,2) NOT NULL,
  `share` decimal(9,6) DEFAULT NULL,
  `commission` decimal(22,2) NOT NULL,
  PRIMARY KEY (`commission_period_line_id`),
  KEY `commission_period_line_period_salesperson` (`commission_period`, `salesperson`),
  KEY `commission_period_line_key` (`sales_order_detail`, `salesperson`, `participation`),
  KEY `commission_period_line_salesperson` (`salesperson`),
  CONSTRAINT `commission_period_line_period_fk` FOREIGN KEY (`commission_period`) REFERENCES `commission_period` (`commission_period_id`),
  CONSTRAINT `commission_period_line_salesperson_fk` FOREIGN KEY (`salesperson`) REFERENCES `employee` (`employee_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

UPDATE `sales_order` so
SET so.`balance_zeroed_time` = COALESCE(
  (
    SELECT MAX(COALESCE(sop.`date`, cp.`date`))
    FROM `sales_order_payment` sop
    JOIN `customer_payment` cp ON cp.`customer_payment_id` = sop.`customer_payment`
    WHERE sop.`sales_order` = so.`sales_order_id` AND sop.`cancelled` = 0
  ),
  so.`modification_time`
)
WHERE so.`paid` = 1 AND so.`completed` = 1 AND so.`cancelled` = 0
  AND so.`date` >= '2025-10-01'
  AND (so.`balance_zeroed_time` IS NULL OR so.`balance_zeroed_time` < '1900-01-01');
