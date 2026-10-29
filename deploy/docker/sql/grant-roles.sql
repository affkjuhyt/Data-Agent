-- Cluster-level setup the V3 baseline does not own. Idempotent; runs from the db-init Job after
-- the migrate Job, before any service starts.
--
-- The application roles (da_owner, svc_<schema>, <schema>_read, svc_exec_worker) and every grant
-- on the 23 schemas come from the migration (backend/alembic/sql/dataagent.sql, design tab 3
-- D5.2). The db-init Job only turns the svc_ roles into logins: it sets their passwords from the
-- DB_ROLE_PASSWORDS secret, so a role and the URL that embeds its password cannot drift.

-- init.sql creates these, but postgres only runs initdb on an empty data directory. A restored
-- volume or a failed first init leaves Keycloak and Kong without their schemas and their
-- migrations fail. Stated here because db-init runs on every deploy.
CREATE SCHEMA IF NOT EXISTS keycloak;
CREATE SCHEMA IF NOT EXISTS kong;

-- PgBouncer authenticates every login (the svc_ roles included) with auth_query through this
-- role, so no service password has to be copied into its userlist.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'pgbouncer_auth') THEN
    CREATE ROLE pgbouncer_auth WITH LOGIN NOSUPERUSER NOBYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE dataagent TO pgbouncer_auth;
CREATE OR REPLACE FUNCTION public.pgbouncer_get_auth(p_usename TEXT)
RETURNS TABLE(usename NAME, passwd TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT usename, passwd FROM pg_shadow WHERE usename = p_usename;
$$;
REVOKE ALL ON FUNCTION public.pgbouncer_get_auth(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pgbouncer_get_auth(TEXT) TO pgbouncer_auth;

-- Every service role may connect; what it may touch is the migration's grants and row-level
-- security. Stated explicitly: one BYPASSRLS would silently disable every policy for that role.
-- SELECT on alembic_version lets a workload's wait-for-migrations init container check the
-- schema is at head over its own login instead of the migrator's owner URL.
DO $$
DECLARE
  r record;
  has_alembic_version boolean := to_regclass('public.alembic_version') IS NOT NULL;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'svc\_%' LOOP
    EXECUTE format('GRANT CONNECT ON DATABASE dataagent TO %I', r.rolname);
    EXECUTE format('ALTER ROLE %I NOSUPERUSER NOBYPASSRLS', r.rolname);
    IF has_alembic_version THEN
      EXECUTE format('GRANT SELECT ON public.alembic_version TO %I', r.rolname);
    END IF;
  END LOOP;
END
$$;

-- The audit log lives in its own database, <db>_audit (v3_0002, design tab 3 D1 item 1). PgBouncer
-- runs its auth_query in the database a client asks for, so that database needs the same lookup,
-- and svc_audit (the api's audit module) plus the outbox relay (worker-agent) must reach it.
SELECT EXISTS (SELECT 1 FROM pg_database WHERE datname = current_database() || '_audit') AS has_audit_db,
       current_database() || '_audit' AS audit_db \gset
\if :has_audit_db
\connect :audit_db
GRANT CONNECT ON DATABASE :"audit_db" TO pgbouncer_auth;
CREATE OR REPLACE FUNCTION public.pgbouncer_get_auth(p_usename TEXT)
RETURNS TABLE(usename NAME, passwd TEXT)
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog AS $$
  SELECT usename, passwd FROM pg_shadow WHERE usename = p_usename;
$$;
REVOKE ALL ON FUNCTION public.pgbouncer_get_auth(TEXT) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION public.pgbouncer_get_auth(TEXT) TO pgbouncer_auth;
GRANT CONNECT ON DATABASE :"audit_db" TO svc_audit, svc_audit_worker, svc_outbox_relay;
\endif

-- Temporal (ADR-33) keeps its state in two databases of its own, owned by its own login; the
-- temporal-schema Job creates and upgrades the tables in them as that login. Cluster-level, so it
-- does not matter which database this file is connected to here. No password here: the db-init
-- Job sets it from TEMPORAL_DB_PASSWORD, as it does for the svc_ roles.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'temporal') THEN
    CREATE ROLE temporal WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END
$$;
-- CREATE DATABASE cannot run inside a DO block, so the missing ones are generated and executed.
SELECT format('CREATE DATABASE %I OWNER temporal', db)
FROM unnest(ARRAY['temporal', 'temporal_visibility']) AS db
WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = db)
\gexec
