-- Rollback for 010_system_employee.sql
--
-- Refuses to run while anything still references the row, rather than leaving dangling audit
-- trails: a cancellation recorded against an employee that no longer exists is worse than one
-- recorded against a housekeeping account. Clear the references first if you really mean it.

DELETE FROM `employee` WHERE `employee_id` = -1;
