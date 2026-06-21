-- Grant permissions to runtime service roles.
-- Run after migrations: psql -U admin -d dataagent -f infra/sql/grant-runtime-writer.sql

-- ── runtime_writer: INSERT/UPDATE/DELETE, BYPASSRLS (service account) ──────
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'runtime_writer') THEN
    CREATE ROLE runtime_writer WITH LOGIN PASSWORD 'runtime_dev_pw';
  END IF;
END
$$;

GRANT CONNECT ON DATABASE dataagent TO runtime_writer;
GRANT USAGE ON SCHEMA public TO runtime_writer;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO runtime_writer;
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO runtime_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO runtime_writer;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT USAGE, SELECT ON SEQUENCES TO runtime_writer;

-- RLS enforced: app code tự set tenant context qua get_client(tenant_id=...).
ALTER ROLE runtime_writer NOBYPASSRLS;

-- Re-grant on tables created after initial setup (e.g. by SQLAlchemy migrations).
GRANT SELECT ON "Agents" TO runtime_writer;
