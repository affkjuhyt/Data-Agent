-- Grant all necessary permissions to runtime_writer service account.
-- Run once after migrations: psql -U admin -d dataagent -f infra/sql/grant-runtime-writer.sql

DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'runtime_writer') THEN
    CREATE ROLE runtime_writer WITH LOGIN PASSWORD 'runtime_dev_pw';
  END IF;
END
$$;

-- Database access
GRANT CONNECT ON DATABASE dataagent TO runtime_writer;

-- Schema access
GRANT USAGE ON SCHEMA public TO runtime_writer;

-- All existing tables: full DML
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO runtime_writer;

-- All existing sequences
GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO runtime_writer;

-- Future tables/sequences created by migrations
ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO runtime_writer;

ALTER DEFAULT PRIVILEGES IN SCHEMA public
  GRANT USAGE, SELECT ON SEQUENCES TO runtime_writer;

-- Bypass RLS: runtime_writer là service account, application code tự kiểm soát tenant.
-- RLS chỉ cần cho user-facing roles, không cần cho internal service.
ALTER ROLE runtime_writer BYPASSRLS;
