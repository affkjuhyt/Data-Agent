-- Minimal Agents catalog for the planner stack. Columns match what the postgres
-- connector reads (name, description, deleted_at, inserted_at, id pk). A full
-- deployment uses the real AgentsORM schema; this seeds a runnable catalog.
CREATE TABLE IF NOT EXISTS "Agents" (
  id uuid PRIMARY KEY DEFAULT gen_random_uuid(),
  tenant_id uuid,
  name varchar(255) NOT NULL,
  description varchar(512),
  deleted_at timestamptz,
  inserted_at timestamptz NOT NULL DEFAULT now()
);

INSERT INTO "Agents" (name, description) VALUES
  ('sql_agent',       'Runs SQL queries against relational databases (Postgres, MySQL) to extract data.'),
  ('transform_agent', 'Cleans, joins, aggregates and reshapes tabular data.'),
  ('report_agent',    'Renders reports, dashboards and visualizations from processed data.'),
  ('fetch_agent',     'Fetches data from external APIs and web sources.'),
  ('validate_agent',  'Validates data quality, schema and constraints.')
ON CONFLICT DO NOTHING;
