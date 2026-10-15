-- Kanak Beauty Service: MariaDB database and least-privilege application user.
-- Run as an administrative user, e.g.:
--   mysql -u root -p < scripts/mariadb_setup.sql
-- Replace the password placeholder before running and mirror it in backend/.env (DB_PASSWORD).

CREATE DATABASE IF NOT EXISTS kanak_academy
  CHARACTER SET utf8mb4
  COLLATE utf8mb4_unicode_ci;

CREATE USER IF NOT EXISTS 'kanak'@'localhost' IDENTIFIED BY 'replace-with-db-password';
CREATE USER IF NOT EXISTS 'kanak'@'127.0.0.1' IDENTIFIED BY 'replace-with-db-password';

-- Application privileges: data + schema changes for migrations, no global/admin rights.
GRANT SELECT, INSERT, UPDATE, DELETE, CREATE, ALTER, INDEX, DROP, REFERENCES, LOCK TABLES
  ON kanak_academy.* TO 'kanak'@'localhost', 'kanak'@'127.0.0.1';

-- Test database: Django's test runner creates/drops it, so grant on the name pattern.
GRANT ALL PRIVILEGES ON `test\_kanak\_academy`.* TO 'kanak'@'localhost', 'kanak'@'127.0.0.1';

FLUSH PRIVILEGES;

-- Production hardening notes:
--  * Use a separate read-only user for reporting/BI if needed.
--  * Revoke CREATE/ALTER/DROP from the runtime user after migrations are applied
--    by a deploy-time migration user, if your deployment process allows it.
