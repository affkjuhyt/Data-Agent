-- app_user: the role the tenant_isolation policies are actually written for.
-- Run after migrations: psql -U admin -d dataagent -f deploy/docker/sql/grant-app-user.sql
--
-- Why this file did not exist: migration a1b2c3d4e5f6 creates `app_user` with
-- NOBYPASSRLS and enables + FORCEs RLS on every tenant-scoped table, but it
-- deliberately sets no password and grants no privileges ("password set
-- out-of-band"). Nothing ever set them, no APP_DATABASE_URL was ever
-- configured, and every service kept connecting as ${POSTGRES_USER:-admin}.
--
-- The consequence is easy to miss and total: a Postgres SUPERUSER bypasses
-- row-level security unconditionally, and FORCE does not change that — FORCE
-- only subjects the table OWNER to its own policies. So the policy is correct,
-- `get_client(tenant_id=...)` sets app.tenant_id correctly, and the database
-- still returns every tenant's rows. The one exception is workflow-runtime,
-- which connects as runtime_writer (NOBYPASSRLS, see grant-runtime-writer.sql)
-- and is therefore the only service where RLS does anything today.
--
-- Adopting this role is a behaviour change, not just a credential change: a
-- query that does not set app.tenant_id returns ZERO rows instead of all of
-- them. Move one service at a time and check its unscoped call sites first
-- (tests/unit/test_tenant_isolation.py prints the current inventory).

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user WITH LOGIN PASSWORD 'app_user_dev_pw';
  END IF;
END
$$;

-- Dev password only. Override out-of-band in any real deployment:
--   ALTER ROLE app_user WITH PASSWORD '<from vault>';
ALTER ROLE app_user WITH LOGIN PASSWORD 'app_user_dev_pw';

-- The whole point of the role. Stated explicitly rather than relied on as a
-- default, because a single ALTER ROLE ... BYPASSRLS anywhere (the migration's
-- own documented "emergency rollback") silently disables every policy in the
-- database for this role.
ALTER ROLE app_user NOBYPASSRLS;
ALTER ROLE app_user NOSUPERUSER;

GRANT CONNECT ON DATABASE dataagent TO app_user;
GRANT USAGE ON SCHEMA public TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;

-- Tables created by later migrations inherit the same grants, so adopting a new
-- table does not silently 403 the application.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_user;

-- Verification. Both rows must read `f`; anything else means RLS is inert for
-- this role and the isolation it appears to provide is not there.
--   SELECT rolname, rolsuper, rolbypassrls FROM pg_roles
--   WHERE rolname IN ('app_user', 'runtime_writer');
