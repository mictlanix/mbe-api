-- Rollback for 016_user_password_width.sql
--
-- Narrows `user`.`password` back to varchar(40).
--
-- READ THIS BEFORE RUNNING IT. Unlike the forward migration, this one CAN DESTROY CREDENTIALS.
-- Widening a varchar is lossless; narrowing it is not. Any hash longer than 40 characters -- which is
-- to say any bcrypt hash, at 60 -- cannot survive. STRICT_TRANS_TABLES makes MariaDB refuse the ALTER
-- rather than truncate, so the failure is loud; but on a server without strict mode the value would
-- be cut to 40 characters, and a truncated hash never verifies. That account is then locked out with
-- nothing logged.
--
-- Step 1 therefore refuses to proceed while any password would not fit, instead of relying on the
-- server's sql_mode to catch it. A rollback that depends on a setting outside this repository to
-- avoid destroying credentials is not a rollback.
--
-- IF IT STOPS: some account holds a hash this column cannot store, meaning the bcrypt work has begun.
-- Rolling the column back is then not the operation you want -- roll back the hashing change first,
-- or re-hash those accounts to SHA1, which requires their plaintext and therefore a password reset.
-- Identify them with:
--
--   SELECT `user_id`, LENGTH(`password`) FROM `user` WHERE LENGTH(`password`) > 40;
--
-- MariaDB 10.11.

-- ---------------------------------------------------------------------------
-- Step 1 -- precondition: every stored hash still fits in 40 characters
-- ---------------------------------------------------------------------------
--
-- A CHECK on a temporary table rather than SIGNAL, as in 011, 012 and 015: the runner splits on
-- semicolons, so a compound statement would be torn apart.

CREATE TEMPORARY TABLE `_016_precondition` (
  `every_hash_fits_in_40` TINYINT NOT NULL,
  CONSTRAINT `chk_016_no_long_passwords` CHECK (`every_hash_fits_in_40` = 1)
);

INSERT INTO `_016_precondition` (`every_hash_fits_in_40`)
SELECT IF((SELECT COUNT(*) FROM `user` WHERE LENGTH(`password`) > 40) = 0, 1, 0);

DROP TEMPORARY TABLE `_016_precondition`;

-- ---------------------------------------------------------------------------
-- Step 2 -- the narrowing
-- ---------------------------------------------------------------------------
--
-- The model still declares String(255); after this the two disagree again, which is the state
-- issue #161 describes. Revert the model with the code if that is the intent.

ALTER TABLE `user`
  MODIFY COLUMN `password` VARCHAR(40) NOT NULL;
