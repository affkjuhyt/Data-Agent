CREATE DATABASE litellm;
GRANT ALL PRIVILEGES ON DATABASE litellm TO admin;
CREATE DATABASE langfuse;
GRANT ALL PRIVILEGES ON DATABASE langfuse TO admin;
CREATE DATABASE knowledge_base;
GRANT ALL PRIVILEGES ON DATABASE knowledge_base TO admin;

-- sandbox_executor SANDBOX_SQL_BACKEND=postgres: local/dev/CI stand-in for
-- Snowflake (docs/sandbox_report.md, backend/services/sandbox_executor/postgres_readonly.py)
-- for developers/CI without a real Snowflake account. Not used by any other
-- service.
CREATE DATABASE sandbox_dev;
GRANT ALL PRIVILEGES ON DATABASE sandbox_dev TO admin;

-- runtime_writer: limited role for workflow_runtime service (INSERT/SELECT only)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'runtime_writer') THEN
    CREATE ROLE runtime_writer WITH LOGIN PASSWORD 'runtime_dev_pw';
  END IF;
END
$$;
GRANT CONNECT ON DATABASE dataagent TO runtime_writer;

-- sandbox_reader: mirrors the Snowflake service account's privilege shape
-- (USAGE+SELECT only, no INSERT/UPDATE/DELETE/DDL — see
-- snowflake_readonly.py's module docstring) so sql_guard.py's SELECT-only
-- check is exercised against the same access shape on both backends.
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'sandbox_reader') THEN
    CREATE ROLE sandbox_reader WITH LOGIN PASSWORD 'sandbox_reader_dev_pw';
  END IF;
END
$$;
GRANT CONNECT ON DATABASE sandbox_dev TO sandbox_reader;

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
