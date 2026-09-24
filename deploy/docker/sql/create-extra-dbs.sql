CREATE DATABASE litellm;
GRANT ALL PRIVILEGES ON DATABASE litellm TO admin;
CREATE DATABASE langfuse;
GRANT ALL PRIVILEGES ON DATABASE langfuse TO admin;

-- sandbox_executor SANDBOX_SQL_BACKEND=postgres: local/dev/CI stand-in for
-- Snowflake (docs/sandbox_report.md, backend/services/sandbox_executor/postgres_readonly.py)
-- for developers/CI without a real Snowflake account. Not used by any other
-- service.
CREATE DATABASE sandbox_dev;
GRANT ALL PRIVILEGES ON DATABASE sandbox_dev TO admin;

-- runtime_writer: limited role for workflow_runtime service (INSERT/SELECT only).
-- No password here: the db-init Job sets it from RUNTIME_WRITER_PASSWORD so the
-- role and the DSN in RUNTIME_DB_URL cannot drift apart.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'runtime_writer') THEN
    CREATE ROLE runtime_writer WITH LOGIN NOBYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE dataagent TO runtime_writer;

-- sandbox_reader: mirrors the Snowflake service account's privilege shape
-- (USAGE+SELECT only, no INSERT/UPDATE/DELETE/DDL — see
-- snowflake_readonly.py's module docstring) so sql_guard.py's SELECT-only
-- check is exercised against the same access shape on both backends.
-- Password set by the db-init Job from SANDBOX_READER_PASSWORD.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sandbox_reader') THEN
    CREATE ROLE sandbox_reader WITH LOGIN NOBYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE sandbox_dev TO sandbox_reader;

-- crawler_ro: the schema-crawler worker's login for the demo warehouse
-- (KBWarehouseConfig.connection.username). Read-only, same shape as
-- sandbox_reader; separate so the crawler does not share the agents' SQL
-- credential. The warehouse used to connect as "admin" with this password,
-- which never matched and failed authentication every 5 seconds.
-- Password set by the db-init Job from POSTGRES_CRAWLER_PASSWORD.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'crawler_ro') THEN
    CREATE ROLE crawler_ro WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS;
  END IF;
END
$$;
GRANT CONNECT ON DATABASE sandbox_dev TO crawler_ro;

\connect sandbox_dev

CREATE TABLE IF NOT EXISTS sample_orders (
    id SERIAL PRIMARY KEY,
    customer_name TEXT NOT NULL,
    amount NUMERIC(10, 2) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

INSERT INTO sample_orders (customer_name, amount)
SELECT * FROM (VALUES
    ('Acme Corp', 120.50),
    ('Globex', 75.00),
    ('Initech', 300.25)
) AS v(customer_name, amount)
WHERE NOT EXISTS (SELECT 1 FROM sample_orders);

GRANT USAGE ON SCHEMA public TO sandbox_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO sandbox_reader;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO sandbox_reader;

GRANT USAGE ON SCHEMA public TO crawler_ro;
GRANT SELECT ON ALL TABLES IN SCHEMA public TO crawler_ro;
ALTER DEFAULT PRIVILEGES IN SCHEMA public GRANT SELECT ON TABLES TO crawler_ro;
