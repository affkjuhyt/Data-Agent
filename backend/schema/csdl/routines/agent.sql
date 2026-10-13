-- routines/agent.sql: what D4.8 (tab 3b) writes in words for schema agent: the depth guard of
-- multi-agent runs (BR-77) and the indexes Agent runtime uses to detect loops (BR-170).

-- covers: agent.agent_run#0
-- BR-170: the "Lượt chạy" screen lists runs an agent stopped for looping or making no progress.
CREATE INDEX agent_run_agent_id_stop_reason_idx ON agent.agent_run (agent_id, stop_reason)
  WHERE stop_reason IN ('loop_detected', 'no_progress');

-- covers: agent.agent_run#1
-- BR-77: the depth is derived from the parent, never trusted from the service; a parent of another
-- organisation (invisible under RLS, or visible to an admin) or a fourth level is refused.
CREATE FUNCTION agent.agent_run_depth_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  parent_org uuid;
  parent_depth smallint;
BEGIN
  IF NEW.parent_run_id IS NULL THEN
    NEW.depth := 0;
    RETURN NEW;
  END IF;
  SELECT org_id, depth INTO parent_org, parent_depth FROM agent.agent_run WHERE id = NEW.parent_run_id;
  IF NOT FOUND OR parent_org <> NEW.org_id THEN
    RAISE EXCEPTION 'Lượt cha không thuộc tổ chức của lượt con (BR-77)' USING ERRCODE = 'check_violation';
  END IF;
  IF parent_depth >= 3 THEN
    RAISE EXCEPTION 'Lượt chạy con sâu quá 3 tầng (BR-77)' USING ERRCODE = 'check_violation';
  END IF;
  NEW.depth := parent_depth + 1;
  RETURN NEW;
END
$$;
CREATE TRIGGER agent_run_depth_guard BEFORE INSERT OR UPDATE OF parent_run_id, depth ON agent.agent_run
  FOR EACH ROW EXECUTE FUNCTION agent.agent_run_depth_guard();

-- covers: agent.agent_step#0
-- BR-170: Agent runtime counts repeated calls per run; the database only provides the index.
CREATE INDEX agent_step_run_id_call_fingerprint_idx ON agent.agent_step (run_id, call_fingerprint)
  WHERE call_fingerprint IS NOT NULL;

-- covers: agent.tool_def#0
-- ADR-54: the tool register is shared by every organisation and written only by migrations
-- (its rows ship with a release); every service reads it to describe, check and expose tools.
REVOKE INSERT, UPDATE, DELETE ON agent.tool_def FROM svc_agent;
DO $$
DECLARE r record;
BEGIN
  FOR r IN SELECT rolname FROM pg_roles WHERE rolname LIKE 'svc\_%' LOOP
    EXECUTE format('GRANT USAGE ON SCHEMA agent TO %I', r.rolname);
    EXECUTE format('GRANT SELECT ON agent.tool_def TO %I', r.rolname);
  END LOOP;
END $$;
