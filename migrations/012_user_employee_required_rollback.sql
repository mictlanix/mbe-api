-- Rollback for 012_user_employee_required.sql
--
-- Restores the nullable column. It does not unlink the accounts that were linked in order to
-- apply the migration: whoever linked them named a real employee, and undoing that is a data
-- decision rather than a schema one.
--
-- The API refuses to create a user without an employee whether or not this has been run -- that
-- guard lives in the request schema, not in the database.

ALTER TABLE `user`
  MODIFY COLUMN `employee` INT(11) DEFAULT NULL;
