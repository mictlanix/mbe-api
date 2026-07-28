-- 012 Every user is linked to an employee (#127)
--
-- `user.employee` was nullable and nothing enforced it. Measured against the deployment database
-- on 2026-07-28: 34 active users, 2 of them -- `agonzalez` and `augusto` -- with `employee IS
-- NULL`, one of those two an administrator. Both accounts authenticate normally and then fail on
-- every write that authors a document: eight services refuse an unlinked user with 422, each with
-- its own message, at whatever point in a workflow the user happened to reach. The invariant was
-- policy nobody enforced. This makes it true.
--
-- ATTRIBUTION IS NEVER INVENTED, so this migration links nothing and there is no DEFAULT. Unlike
-- `warehouse.in_transit` in 011, no value is safe to pick: 0 fails the `employee_user` foreign
-- key, and the system employee (-1) would file a person's work under a housekeeping account.
-- Someone has to say which employee each account belongs to.
--
-- RESOLVED BEFORE THIS WAS APPLIED ANYWHERE: both accounts were purged. Step 1 is therefore an
-- assertion rather than a call to action, and it stays in the file so the claim is checked at run
-- time rather than only at planning time -- if this is ever applied to a database where an
-- unlinked account still exists, it stops instead of failing obscurely.
--
-- IF ANY DO REMAIN:
--
--   SELECT `user_id`, `email`, `administrator` FROM `user` WHERE `employee` IS NULL;
--
-- and for each row either link the real employee or delete the account. Archiving it
-- (`status = 2`) is not enough -- NOT NULL applies to every row, active or not.
--
-- Step 1 stops while any remain. Without it the ALTER still refuses -- `sql_mode` includes
-- STRICT_TRANS_TABLES, so the NULLs are not silently written as 0 (verified on this server:
-- error 1265) -- but it refuses with "Data truncated for column 'employee' at row 1", which
-- names neither the reason nor the accounts.
--
-- THE OTHER WRITER. The legacy application writes this table too, and a NOT NULL column it
-- violated would fail in production rather than here. Checked before writing this migration
-- (`mbe/Model/User.cs`, `mbe/Web/Controllers/Mvc/UsersController.cs`): `User.EmployeeId` is a
-- non-nullable `int` carrying `[Required]`; the only write path is Edit, which assigns
-- `Employee.Find (item.EmployeeId)`, and ActiveRecord's `Find` throws rather than returning null;
-- and the controller has no create action at all. The legacy application cannot write a NULL
-- here. Neither can this API after the change that accompanies this migration -- `employee_id`
-- is required on user create.
--
-- Idempotent: re-applying the ALTER against an already NOT NULL column changes nothing.
--
-- MariaDB 10.11. Rollback: 012_user_employee_required_rollback.sql

-- ---------------------------------------------------------------------------
-- Step 1 -- precondition: no user is unlinked
-- ---------------------------------------------------------------------------
--
-- A CHECK on a temporary table rather than SIGNAL, for the same reason as 011: SIGNAL is only
-- legal inside a compound statement, and the migration runner splits this file on semicolons, so
-- a BEGIN...END block would be torn apart. The INSERT below fails on `chk_012_no_unlinked_users`
-- while any user has no employee, and the runner prints the failing statement verbatim.

CREATE TEMPORARY TABLE `_012_precondition` (
  `every_user_has_an_employee` TINYINT NOT NULL,
  CONSTRAINT `chk_012_no_unlinked_users` CHECK (`every_user_has_an_employee` = 1)
);

INSERT INTO `_012_precondition` (`every_user_has_an_employee`)
SELECT IF((SELECT COUNT(*) FROM `user` WHERE `employee` IS NULL) = 0, 1, 0);

DROP TEMPORARY TABLE `_012_precondition`;

-- ---------------------------------------------------------------------------
-- Step 2 -- the constraint
-- ---------------------------------------------------------------------------
--
-- MODIFY, not a drop and re-add: the `employee_user` foreign key and the `employee_user_idx`
-- index are on this column and are kept as they are. Only nullability changes.

ALTER TABLE `user`
  MODIFY COLUMN `employee` INT(11) NOT NULL;
