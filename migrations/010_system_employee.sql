-- 010 A system employee for automated actions (specs/012-delivery-logistics-endpoints, #118)
--
-- The expiry sweep cancels abandoned sales orders, and `sales_order.updater` is a NOT NULL
-- foreign key to `employee`. Something has to be recorded as having done it, and attributing an
-- automated cancellation to the salesperson would read in the audit trail as their decision.
--
-- WHY -1 AND NOT 0. `sales_order_updater_fk` is enforced (InnoDB, foreign_key_checks = 1) and no
-- employee 0 exists, so a 0 sentinel fails at write time with error 1452 -- mid-sweep, after some
-- orders have already been cancelled. Nor can employee 0 be created: `NO_AUTO_VALUE_ON_ZERO` is
-- not in `sql_mode`, so an explicit 0 is replaced by the next auto-increment value.
--
-- WHY NOT A HIGH ID. Inserting, say, 999999 would push AUTO_INCREMENT past it and every real
-- employee created afterwards would be numbered from 1000000 -- normal numbering polluted by a
-- housekeeping row. A negative id is genuinely out of band: InnoDB only advances the counter for
-- values above it, so -1 leaves it untouched. Verified on 2026-07-28: AUTO_INCREMENT stayed at
-- 111 across the insert, and `updater = -1` satisfied the foreign key.
--
-- Status 2 is ARCHIVED, so the row stays out of the employee pickers.
--
-- MariaDB 10.11. Rollback: 010_system_employee_rollback.sql

INSERT INTO `employee` (
  `employee_id`, `first_name`, `last_name`, `nickname`, `gender`, `birthday`,
  `sales_person`, `start_job_date`, `comment`, `status`
) VALUES (
  -1, 'System', 'Process', 'system', 0, '1970-01-01',
  0, '1970-01-01', 'Recorded as the actor for automated actions such as the expiry sweep', 2
);
