CREATE DATABASE litellm;
GRANT ALL PRIVILEGES ON DATABASE litellm TO admin;
CREATE DATABASE langfuse;
GRANT ALL PRIVILEGES ON DATABASE langfuse TO admin;
CREATE DATABASE knowledge_base;
GRANT ALL PRIVILEGES ON DATABASE knowledge_base TO admin;

-- runtime_writer: limited role for workflow-runtime service (INSERT/SELECT only)
DO $$
BEGIN
  IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'runtime_writer') THEN
    CREATE ROLE runtime_writer WITH LOGIN PASSWORD 'runtime_dev_pw';
  END IF;
END
$$;
GRANT CONNECT ON DATABASE dataagent TO runtime_writer;
