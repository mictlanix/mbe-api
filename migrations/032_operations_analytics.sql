-- 032 Operations analytics: the ledger date index, receivable month-ends, saved views and
-- scheduled reports -- spec 038
--
-- 1. `lot_serial_tracking_date_idx` (research R2): movements in a period, and the velocity and
--    dead-stock windows, become range reads. Today every one of them scans the ledger.
-- 2. `receivable_snapshot`, new (R8): one row per open credit order per month-end, with spec 033's
--    balance as of 23:59:59 that day. `receivable_snapshot_month` holds one marker per written
--    month-end, so a month with no open orders is not rewritten.
-- 3. `analytics_view` and `analytics_view_share`, new (R14): saved views and who they are shared
--    with.
-- 4. `analytics_schedule`, `analytics_schedule_recipient` and `analytics_send`, new (R15): the
--    schedules of a view, their recipients, and every send attempt.
--
-- No column of an existing table changes. The current system reads none of these tables.
--
-- Idempotent. MariaDB 10.11.
-- Rollback: 032_operations_analytics_rollback.sql

CREATE INDEX IF NOT EXISTS `lot_serial_tracking_date_idx`
    ON `lot_serial_tracking` (`date`);

CREATE TABLE IF NOT EXISTS `receivable_snapshot` (
  `month_end` date NOT NULL COMMENT 'The last day of the month; the balance is as of 23:59:59',
  `sales_order` int(11) NOT NULL,
  `customer` int(11) NOT NULL,
  `facility` int(11) NOT NULL COMMENT 'The order''s facility',
  `salesperson` int(11) DEFAULT NULL COMMENT 'The order''s',
  `sales_agent` int(11) DEFAULT NULL COMMENT 'The customer''s agent at write time',
  `due_date` datetime DEFAULT NULL,
  `currency` int(11) NOT NULL,
  `exchange_rate` decimal(18,4) NOT NULL,
  `total` decimal(18,2) NOT NULL COMMENT 'Order total (spec 033)',
  `balance` decimal(18,2) NOT NULL COMMENT 'Spec 033 balance as of the month-end, order currency',
  `credit_limit` decimal(18,2) DEFAULT NULL COMMENT 'The customer''s limit when written',
  `limit_historical` tinyint(1) NOT NULL COMMENT '1 written at that month-end; 0 backfilled',
  `written_at` datetime NOT NULL,
  PRIMARY KEY (`month_end`, `sales_order`),
  KEY `receivable_snapshot_month_end_customer_idx` (`month_end`, `customer`),
  KEY `receivable_snapshot_month_end_facility_idx` (`month_end`, `facility`),
  KEY `receivable_snapshot_sales_order_idx` (`sales_order`),
  KEY `receivable_snapshot_customer_idx` (`customer`),
  CONSTRAINT `receivable_snapshot_sales_order_fk` FOREIGN KEY (`sales_order`) REFERENCES `sales_order` (`sales_order_id`),
  CONSTRAINT `receivable_snapshot_customer_fk` FOREIGN KEY (`customer`) REFERENCES `customer` (`customer_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `receivable_snapshot_month` (
  `month_end` date NOT NULL,
  `written_at` datetime NOT NULL,
  `orders` int(11) NOT NULL COMMENT 'Open credit orders written for the month-end; may be 0',
  PRIMARY KEY (`month_end`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `analytics_view` (
  `analytics_view_id` int(11) NOT NULL AUTO_INCREMENT,
  `user` varchar(20) NOT NULL COMMENT 'The owner',
  `name` varchar(100) NOT NULL COMMENT 'Unique per owner',
  `kind` tinyint(4) NOT NULL COMMENT '0 query, 1 dashboard, 2 detail, 3 customer 360',
  `definition` text NOT NULL COMMENT 'JSON of the request without start and end',
  `period` text NOT NULL COMMENT 'JSON {key, days?, start?, end?}',
  `creation_time` datetime NOT NULL,
  `modification_time` datetime NOT NULL,
  PRIMARY KEY (`analytics_view_id`),
  UNIQUE KEY `analytics_view_user_name_uq` (`user`, `name`),
  CONSTRAINT `analytics_view_user_fk` FOREIGN KEY (`user`) REFERENCES `user` (`user_id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `analytics_view_share` (
  `analytics_view` int(11) NOT NULL,
  `user` varchar(20) NOT NULL,
  PRIMARY KEY (`analytics_view`, `user`),
  KEY `analytics_view_share_user_idx` (`user`),
  CONSTRAINT `analytics_view_share_view_fk` FOREIGN KEY (`analytics_view`) REFERENCES `analytics_view` (`analytics_view_id`) ON DELETE CASCADE,
  CONSTRAINT `analytics_view_share_user_fk` FOREIGN KEY (`user`) REFERENCES `user` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `analytics_schedule` (
  `analytics_schedule_id` int(11) NOT NULL AUTO_INCREMENT,
  `analytics_view` int(11) NOT NULL COMMENT 'The owner is the view''s',
  `cadence` tinyint(4) NOT NULL COMMENT '0 daily, 1 weekly, 2 monthly',
  `weekdays` tinyint(4) NOT NULL DEFAULT 0 COMMENT 'Bitmask, Monday = 1 ... Sunday = 64; weekly only',
  `month_day` tinyint(4) DEFAULT NULL COMMENT '1-31, monthly only; past the month end, its last day',
  `time_of_day` time NOT NULL COMMENT 'Business time',
  `format` tinyint(4) NOT NULL COMMENT '0 xlsx, 1 pdf, 2 csv',
  `subject` varchar(150) NOT NULL,
  `enabled` tinyint(1) NOT NULL DEFAULT 1,
  `next_run_time` datetime NOT NULL COMMENT 'The next occurrence, business time; the claim compares it',
  `creation_time` datetime NOT NULL,
  `modification_time` datetime NOT NULL,
  PRIMARY KEY (`analytics_schedule_id`),
  KEY `analytics_schedule_enabled_next_run_time_idx` (`enabled`, `next_run_time`),
  KEY `analytics_schedule_view_idx` (`analytics_view`),
  CONSTRAINT `analytics_schedule_view_fk` FOREIGN KEY (`analytics_view`) REFERENCES `analytics_view` (`analytics_view_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `analytics_schedule_recipient` (
  `analytics_schedule` int(11) NOT NULL,
  `user` varchar(20) NOT NULL,
  PRIMARY KEY (`analytics_schedule`, `user`),
  KEY `analytics_schedule_recipient_user_idx` (`user`),
  CONSTRAINT `analytics_schedule_recipient_schedule_fk` FOREIGN KEY (`analytics_schedule`) REFERENCES `analytics_schedule` (`analytics_schedule_id`) ON DELETE CASCADE,
  CONSTRAINT `analytics_schedule_recipient_user_fk` FOREIGN KEY (`user`) REFERENCES `user` (`user_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;

CREATE TABLE IF NOT EXISTS `analytics_send` (
  `analytics_send_id` int(11) NOT NULL AUTO_INCREMENT,
  `analytics_schedule` int(11) NOT NULL,
  `occurrence` datetime NOT NULL COMMENT 'The scheduled moment this send is for',
  `started_at` datetime NOT NULL,
  `finished_at` datetime DEFAULT NULL COMMENT 'NULL while running; running over 10 minutes reads as failed',
  `outcome` tinyint(4) NOT NULL COMMENT '0 running, 1 sent, 2 failed, 3 skipped',
  `code` varchar(64) DEFAULT NULL COMMENT 'Spec 036 code of a failure or skip',
  `recipients` text NOT NULL COMMENT 'JSON: user ids sent to, and those dropped and why',
  `format` tinyint(4) NOT NULL COMMENT '0 xlsx, 1 pdf, 2 csv',
  `size` int(11) DEFAULT NULL COMMENT 'Attachment bytes',
  `row_count` int(11) DEFAULT NULL,
  PRIMARY KEY (`analytics_send_id`),
  KEY `analytics_send_schedule_occurrence_idx` (`analytics_schedule`, `occurrence`),
  CONSTRAINT `analytics_send_schedule_fk` FOREIGN KEY (`analytics_schedule`) REFERENCES `analytics_schedule` (`analytics_schedule_id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb3 COLLATE=utf8mb3_unicode_ci;
