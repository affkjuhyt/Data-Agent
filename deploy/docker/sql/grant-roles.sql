-- Privileges for the three non-owner roles. Idempotent; runs from the db-init
-- Job after migrations, before any service starts.
--
-- Replaces grant-app-user.sql (never mounted, so app_user had no CONNECT and no
-- password) and grant-runtime-writer.sql (created the role a second time with a
-- hardcoded password that could not match RUNTIME_DB_URL).
--
-- No passwords here. The db-init Job sets them from the same secrets that build
-- APP_DATABASE_URL and RUNTIME_DB_URL, so the role and the DSN cannot drift.
--
-- Verify afterwards; every row must read `f` for rolsuper and rolbypassrls:
--   SELECT rolname, rolsuper, rolbypassrls FROM pg_roles
--   WHERE rolname IN ('app_user', 'runtime_writer', 'sandbox_reader');

-- init.sql creates these, but postgres only runs initdb on an empty data
-- directory. A restored volume or a failed first init leaves Keycloak and Kong
-- without their schemas and their migrations fail. Stated here because db-init
-- runs on every deploy.
CREATE SCHEMA IF NOT EXISTS keycloak;
CREATE SCHEMA IF NOT EXISTS kong;

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'app_user') THEN
    CREATE ROLE app_user WITH LOGIN;
  END IF;
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'runtime_writer') THEN
    CREATE ROLE runtime_writer WITH LOGIN;
  END IF;
END
$$;

-- Stated explicitly rather than relied on as a default: one ALTER ROLE ...
-- BYPASSRLS anywhere silently disables every policy for that role, and a
-- superuser bypasses FORCE row-level security unconditionally.
ALTER ROLE app_user NOBYPASSRLS;
ALTER ROLE app_user NOSUPERUSER;
ALTER ROLE runtime_writer NOBYPASSRLS;
ALTER ROLE runtime_writer NOSUPERUSER;

GRANT CONNECT ON DATABASE dataagent TO app_user;
GRANT CONNECT ON DATABASE dataagent TO runtime_writer;

GRANT USAGE ON SCHEMA public TO app_user;
GRANT USAGE ON SCHEMA public TO runtime_writer;

GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO app_user;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO runtime_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO app_user;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO runtime_writer;

-- Tables created by later migrations inherit the same grants, so adopting a new
-- table does not silently 403 the application.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO app_user;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO runtime_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO runtime_writer;

GRANT SELECT ON "Agents" TO runtime_writer;

-- LangGraph checkpointer (workflow_runtime/orchestrator/graph.py): its setup()
-- runs CREATE TABLE IF NOT EXISTS on every start, and Postgres checks CREATE
-- on the schema before it checks whether the table exists — so pre-creating
-- the tables is not enough. Postgres 15+ grants no CREATE on public, and
-- runtime_writer must not get it there. The checkpointer uses this schema via
-- search_path instead, and runtime_writer owns only what it creates in it.
CREATE SCHEMA IF NOT EXISTS langgraph;
GRANT USAGE, CREATE ON SCHEMA langgraph TO runtime_writer;


-- ── sandbox_reader ───────────────────────────────────────────────────────────
-- Its privileges live in create-extra-dbs.sql against the sandbox_dev database.
-- Only the role is created here: roles are cluster-wide, create-extra-dbs.sql
-- runs from docker-entrypoint-initdb.d (fresh volumes only), and db-init sets
-- this role's password on every deploy.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sandbox_reader') THEN
    CREATE ROLE sandbox_reader WITH LOGIN NOBYPASSRLS;
  END IF;
END
$$;

-- ── kb_backend ───────────────────────────────────────────────────────────────
-- Table-scoped role for the kb-backend service. Unlike app_user it is NOT
-- granted ALL TABLES and gets no ALTER DEFAULT PRIVILEGES in public: a new
-- table reaches this service only by being added to a list below. That is the
-- point — kb-backend cannot read SecretsVault, APIKeys, Members or the release
-- tables at all.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kb_backend') THEN
    CREATE ROLE kb_backend WITH LOGIN;
  END IF;
END
$$;

ALTER ROLE kb_backend NOSUPERUSER;
ALTER ROLE kb_backend NOBYPASSRLS;

GRANT CONNECT ON DATABASE dataagent TO kb_backend;
GRANT USAGE ON SCHEMA public TO kb_backend;

-- Revoke first so removing a table from the lists below actually takes effect.
REVOKE ALL ON ALL TABLES IN SCHEMA public FROM kb_backend;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  "KBData",
  "KBTextBlock",
  "KBTextBlockVersion",
  "KBTextTable",
  "KBTable",
  "KBKnowledgeBase",
  "KBLifecycleHistory",
  "KBConflict",
  "KBConflictBatch",
  "KBFilterPolicy",
  "KBExtractionPolicy",
  "KBWarehouse",
  "KBWarehouseConfig",
  "KBStructuredImportJob",
  "KBQdrantCollection"
TO kb_backend;

GRANT SELECT ON
  "KBQdrantConnection",
  "KBNeo4jNode",
  "KBNeo4jRelationship",
  "KBEntityLookup",
  "RolePermissions"
TO kb_backend;

-- Structured imports: kb-backend creates the physical tables, so it owns them.
CREATE SCHEMA IF NOT EXISTS kb_structured;
GRANT USAGE, CREATE ON SCHEMA kb_structured TO kb_backend;

-- The reader role's default privileges were attached to app_user, which no
-- longer creates these tables. Without this the text-to-SQL tool stops seeing
-- imports the moment kb-backend switches to its own role.
DO $$
BEGIN
  IF EXISTS (SELECT FROM pg_roles WHERE rolname = 'kb_structured_reader') THEN
    EXECUTE 'ALTER DEFAULT PRIVILEGES FOR ROLE kb_backend IN SCHEMA kb_structured '
            'GRANT SELECT ON TABLES TO kb_structured_reader';
  END IF;
END
$$;

-- ── pgbouncer_auth ───────────────────────────────────────────────────────────
-- Lets PgBouncer verify client passwords without a superuser DSN, so the
-- client's own role (kb_backend) is what reaches Postgres and RLS still applies.
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

-- ── kb_ingestion_worker ──────────────────────────────────────────────────────
-- Table-scoped role for the ingestion worker. Same rule as kb_backend: no
-- ALL TABLES grant and no default privileges, so a new table reaches this
-- service only by being listed here.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'kb_ingestion_worker') THEN
    CREATE ROLE kb_ingestion_worker WITH LOGIN;
  END IF;
END
$$;

ALTER ROLE kb_ingestion_worker NOSUPERUSER;
ALTER ROLE kb_ingestion_worker NOBYPASSRLS;

GRANT CONNECT ON DATABASE dataagent TO kb_ingestion_worker;
GRANT USAGE ON SCHEMA public TO kb_ingestion_worker;

REVOKE ALL ON ALL TABLES IN SCHEMA public FROM kb_ingestion_worker;

GRANT SELECT, INSERT, UPDATE, DELETE ON
  "KBData",
  "KBTextBlock",
  "KBTextBlockVersion",
  "KBTextTable",
  "KBLifecycleHistory",
  "KBConflict",
  "KBConflictBatch",
  "KBStructuredImportJob"
TO kb_ingestion_worker;

GRANT SELECT ON
  "KBFilterPolicy",
  "KBEntityLookup",
  "KBQdrantCollection"
TO kb_ingestion_worker;

-- ── ankt schema ──────────────────────────────────────────────────────────────
-- The indicator platform's own schema (migrations d3f4a5b6c7d8 onward). Listed
-- here so a freshly initialised database matches an existing one that got the
-- same grants through migration b7c8d9e0f1a2.
--
-- kb_backend needs writes, not just reads: the Nguồn dữ liệu screen runs
-- connectors, and a connector appends observations and releases. Both roles
-- below are NOBYPASSRLS, so the tenant_isolation policies on these tables are
-- live for them — unlike `admin`, which bypasses and is why the CLI warns.
DO $$
DECLARE
  target text;
  tbl    text;
BEGIN
  FOREACH target IN ARRAY ARRAY['kb_backend', 'runtime_writer', 'app_user'] LOOP
    IF EXISTS (SELECT FROM pg_roles WHERE rolname = target) THEN
      EXECUTE format('GRANT USAGE ON SCHEMA ankt TO %I', target);
      FOREACH tbl IN ARRAY ARRAY['variable', 'source_document', 'release',
                                 'observation', 'indicator', 'threshold',
                                 'source', 'source_binding', 'connector_run'] LOOP
        IF EXISTS (SELECT FROM information_schema.tables
                    WHERE table_schema = 'ankt' AND table_name = tbl) THEN
          EXECUTE format('GRANT SELECT, INSERT, UPDATE, DELETE ON ankt.%I TO %I',
                         tbl, target);
        END IF;
      END LOOP;
    END IF;
  END LOOP;
END
$$;
