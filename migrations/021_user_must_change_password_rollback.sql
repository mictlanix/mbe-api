-- Rollback for 021_user_must_change_password.sql
--
-- Drops `user`.`must_change_password`.
--
-- WHAT IS LOST. Which users still owe a password change. Each of them keeps working with the
-- administrator-issued password, unprompted, and nothing else in the schema records that it was not
-- their own. Before running it, if that matters:
--
--   SELECT `user_id` FROM `user` WHERE `must_change_password` = 1;
--
-- The model still declares the column afterwards, so revert the application code with it or every
-- read of `user` -- including every authenticated request -- raises "Unknown column".
--
-- MariaDB 10.11.

ALTER TABLE `user`
  DROP COLUMN IF EXISTS `must_change_password`;
