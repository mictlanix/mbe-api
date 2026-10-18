-- 021 Record that a user still has to replace an administrator-issued password -- issue #240
--
-- mbe-ui makes a user change their password at first login. The client cannot tell when to: the
-- `user` row has no password state, and a password an administrator typed into `POST /users` is
-- indistinguishable from one the user chose. A client-only flag is trivially skipped and forgets
-- itself across devices, so the state lives here.
--
-- Set by `POST /users` and, by hand, through `PUT /users/{id}`. Cleared by
-- `POST /auth/change-password` and `POST /auth/recover`, both of which end with a password the
-- user chose. While it is set, every endpoint except `GET /auth/me` and
-- `POST /auth/change-password` answers 403 `password_change_required`.
--
-- EVERY EXISTING ROW GETS 0. Nobody is forced to change a password they already have, and nobody
-- is locked out by the deploy. The same default keeps the column invisible to the legacy
-- application, which goes on creating users without knowing it exists -- so a user legacy creates
-- is never flagged. That is the same trade migration 011 made for `in_transit`.
--
-- TINYINT(1) NOT NULL DEFAULT 0, matching `cancelled` (019) and `in_transit` (011) and the model's
-- `Boolean` with `server_default='0'`. Adding a NOT NULL column with a constant default cannot fail
-- on existing data. Idempotent: `IF NOT EXISTS`, so re-running it is a no-op rather than error 1060.
--
-- MariaDB 10.11. Rollback: 021_user_must_change_password_rollback.sql

ALTER TABLE `user`
  ADD COLUMN IF NOT EXISTS `must_change_password` TINYINT(1) NOT NULL DEFAULT 0
  COMMENT '1 = an administrator issued the password; cleared when the user sets one (#240)';
