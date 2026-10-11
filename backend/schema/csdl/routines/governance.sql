-- routines/governance.sql: what D4.2 (tab 3b) writes in words for the AI model tables of schema
-- governance (BR-180, BR-181, ADR-41): the frontier guard of model_config, the re-approval and the
-- change events of model_provider, the routing-decision guard (append-only except the four result
-- columns, audit of every frontier call, monthly budget), and who may read providers and decisions.

-- covers: governance.model_config#0
-- BR-180: tasks that see raw internal data or only run on site never get a frontier candidate.
ALTER TABLE governance.model_config ADD CONSTRAINT model_config_frontier_task_check
  CHECK (route_role <> 'frontier_candidate'
         OR task NOT IN ('embedding', 'reranking', 'ocr', 'pii_classification', 'sql_generation'));

-- covers: governance.model_config#1
-- The route role follows the provider kind, and an active frontier candidate needs a passed eval run
-- just like an on-site model (the CHECK on eval_run_id applies the same bar).
CREATE FUNCTION governance.model_config_route_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  provider_kind text;
BEGIN
  SELECT kind INTO provider_kind FROM governance.model_provider WHERE id = NEW.provider_id;
  IF provider_kind IS NULL THEN
    RAISE EXCEPTION 'Không tìm thấy provider của cấu hình mô hình' USING ERRCODE = 'check_violation';
  END IF;
  IF provider_kind = 'frontier' AND NEW.route_role <> 'frontier_candidate' THEN
    RAISE EXCEPTION 'Provider frontier chỉ dùng cho route_role frontier_candidate (BR-180)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF provider_kind = 'on_prem' AND NEW.route_role <> 'primary' THEN
    RAISE EXCEPTION 'Provider tại chỗ chỉ dùng cho route_role primary (BR-180)' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.route_role = 'frontier_candidate' AND NEW.status = 'active' AND NEW.eval_run_id IS NULL THEN
    RAISE EXCEPTION 'Ứng viên frontier active phải có eval_run_id đạt như mô hình tại chỗ (BR-180)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER model_config_route_guard BEFORE INSERT OR UPDATE ON governance.model_config
  FOR EACH ROW EXECUTE FUNCTION governance.model_config_route_guard();


-- covers: governance.model_provider#0
-- Only svc_governance reads providers (admin and compliance read through the Admin service), so the
-- schema-wide read role used by other services does not include this table.
REVOKE SELECT ON governance.model_provider FROM governance_read;

-- covers: governance.model_provider#1
-- BR-181: changing where or under which contract a frontier provider receives data needs a new
-- approval, so the change switches it off. Every change is audited (audit is a separate database,
-- D1.1, hence the outbox request; the database holds no API key, only the OpenBao path) and announced
-- so the Model gateway reloads and the egress proxy allow-list is updated.
CREATE FUNCTION governance.model_provider_reapproval() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.kind = 'frontier'
     AND (NEW.endpoint, NEW.secret_ref, NEW.data_region, NEW.zero_retention)
         IS DISTINCT FROM (OLD.endpoint, OLD.secret_ref, OLD.data_region, OLD.zero_retention) THEN
    NEW.enabled := false;
    NEW.approved_by := NULL;
    NEW.approved_at := NULL;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER model_provider_reapproval BEFORE UPDATE ON governance.model_provider
  FOR EACH ROW EXECUTE FUNCTION governance.model_provider_reapproval();

CREATE FUNCTION governance.model_provider_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO governance.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
  VALUES (gen_random_uuid(), NEW.org_id, 'model_provider', NEW.id, 'audit.requested',
          jsonb_build_object('action', 'model_provider.changed', 'object_kind', 'model_provider',
                             'object_id', NEW.id,
                             'before', CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END,
                             'after', to_jsonb(NEW)));
  INSERT INTO governance.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
  VALUES (gen_random_uuid(), NEW.org_id, 'model_provider', NEW.id, 'governance.model_provider.changed',
          jsonb_build_object('provider_id', NEW.id, 'code', NEW.code, 'kind', NEW.kind,
                             'endpoint', NEW.endpoint, 'enabled', NEW.enabled, 'status', NEW.status));
  RETURN NULL;
END
$$;
CREATE TRIGGER model_provider_changed AFTER INSERT OR UPDATE ON governance.model_provider
  FOR EACH ROW EXECUTE FUNCTION governance.model_provider_changed();


-- covers: governance.model_route_decision#0
-- Decisions are read by svc_governance and by Agent runtime (svc_qa, svc_agent) for the receipt, and
-- only for the call it is recording: the app sets app.caller_id. The compliance role reads through
-- the Admin service. audit_read lives in the audit database's role set and is not granted here.
REVOKE SELECT ON governance.model_route_decision FROM governance_read;
GRANT SELECT ON governance.model_route_decision TO svc_qa, svc_agent;
CREATE POLICY model_route_decision_caller ON governance.model_route_decision AS RESTRICTIVE FOR SELECT
  TO svc_qa, svc_agent
  USING (caller_id = NULLIF(current_setting('app.caller_id', true), '')::uuid);

-- covers: governance.model_route_decision#1
-- BR-180/BR-181: a frontier call needs an enabled, active, public-only frontier provider and the
-- organisation's frontier_routing switched on for the task; any other decision runs on site. The row
-- is written before the call and only its four result columns are filled, once (D5.2: svc_governance
-- updates only those, never deletes; old rows leave by dropping partitions, which fires no trigger).
CREATE FUNCTION governance.model_route_decision_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  p governance.model_provider%ROWTYPE;
  routing jsonb;
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Quyết định định tuyến không được xoá; chỉ dọn theo phân vùng khi quá hạn lưu (BR-181)'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    IF OLD.outcome <> 'pending'
       OR to_jsonb(NEW) - ARRAY['input_tokens', 'output_tokens', 'outcome', 'model_usage_id']
          IS DISTINCT FROM to_jsonb(OLD) - ARRAY['input_tokens', 'output_tokens', 'outcome', 'model_usage_id'] THEN
      RAISE EXCEPTION 'Quyết định định tuyến chỉ được điền kết quả một lần khi outcome = pending (BR-181)'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  SELECT * INTO p FROM governance.model_provider WHERE id = NEW.chosen_provider_id;
  IF NEW.decision = 'frontier_allowed' THEN
    SELECT value INTO routing FROM governance.ai_setting WHERE org_id = NEW.org_id AND key = 'frontier_routing';
    IF p.kind IS DISTINCT FROM 'frontier' OR NOT p.enabled OR p.status <> 'active'
       OR p.max_classification <> 'public' THEN
      RAISE EXCEPTION 'frontier_allowed phải gọi provider frontier đang bật, active, chỉ nhận mức public (BR-180)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NOT coalesce((routing->>'enabled')::boolean, false)
       OR NOT coalesce(routing->'tasks' ? NEW.task, false) THEN
      RAISE EXCEPTION 'Định tuyến frontier chưa bật cho tác vụ % (BR-180)', NEW.task
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF p.kind IS DISTINCT FROM 'on_prem' THEN
    RAISE EXCEPTION 'Quyết định % phải gọi provider tại chỗ (BR-180)', NEW.decision
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER model_route_decision_guard BEFORE INSERT OR UPDATE OR DELETE ON governance.model_route_decision
  FOR EACH ROW EXECUTE FUNCTION governance.model_route_decision_guard();

-- After the call: audit every frontier call (outbox request, D1.1), and add the filled tokens to the
-- provider's spend of the month. Spend is priced from model_provider.price, read as
-- {"<model_name>": {"input": <VND per 1M tokens>, "output": <VND per 1M tokens>}}; the event fires
-- once, on the call that crosses monthly_budget_vnd, so the Model gateway stops the frontier route.
CREATE FUNCTION governance.model_route_decision_events() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  p governance.model_provider%ROWTYPE;
  spent numeric;
  this_call numeric;
BEGIN
  SELECT * INTO p FROM governance.model_provider WHERE id = NEW.chosen_provider_id;
  IF TG_OP = 'INSERT' THEN
    IF NEW.decision = 'frontier_allowed' THEN
      INSERT INTO governance.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
      VALUES (gen_random_uuid(), NEW.org_id, 'model_route_decision', NEW.id, 'audit.requested',
              jsonb_build_object('action', 'model.frontier_call', 'object_kind', 'model_route_decision',
                                 'object_id', NEW.id, 'before', NULL,
                                 'after', jsonb_build_object('provider', p.code, 'model_name', NEW.model_name,
                                                             'context_labels', to_jsonb(NEW.context_labels),
                                                             'product_scope', NEW.product_scope,
                                                             'prompt_sha256', NEW.prompt_sha256)));
    END IF;
    RETURN NULL;
  END IF;
  IF p.monthly_budget_vnd IS NULL OR (NEW.input_tokens IS NULL AND NEW.output_tokens IS NULL) THEN
    RETURN NULL;
  END IF;
  SELECT coalesce(sum((coalesce(d.input_tokens, 0) * coalesce((p.price->d.model_name->>'input')::numeric, 0)
                     + coalesce(d.output_tokens, 0) * coalesce((p.price->d.model_name->>'output')::numeric, 0))
                     / 1000000), 0)
    INTO spent
    FROM governance.model_route_decision d
   WHERE d.chosen_provider_id = NEW.chosen_provider_id
     AND d.decided_at >= date_trunc('month', NEW.decided_at)
     AND d.decided_at < date_trunc('month', NEW.decided_at) + interval '1 month';
  this_call := (coalesce(NEW.input_tokens, 0) * coalesce((p.price->NEW.model_name->>'input')::numeric, 0)
              + coalesce(NEW.output_tokens, 0) * coalesce((p.price->NEW.model_name->>'output')::numeric, 0))
              / 1000000;
  IF spent > p.monthly_budget_vnd AND spent - this_call <= p.monthly_budget_vnd THEN
    INSERT INTO governance.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'model_provider', p.id, 'governance.model_provider.budget_exceeded',
            jsonb_build_object('provider_id', p.id, 'code', p.code, 'month', to_char(NEW.decided_at, 'YYYY-MM'),
                               'spent_vnd', round(spent), 'monthly_budget_vnd', p.monthly_budget_vnd));
  END IF;
  RETURN NULL;
END
$$;
CREATE TRIGGER model_route_decision_events AFTER INSERT OR UPDATE ON governance.model_route_decision
  FOR EACH ROW EXECUTE FUNCTION governance.model_route_decision_events();

-- D5.2 (BR-181): svc_governance fills only the four result columns and never deletes.
REVOKE UPDATE, DELETE ON governance.model_route_decision FROM svc_governance;
GRANT UPDATE (input_tokens, output_tokens, outcome, model_usage_id) ON governance.model_route_decision
  TO svc_governance;
