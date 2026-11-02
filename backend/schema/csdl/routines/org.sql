-- routines/org.sql: the "two different people" rule of the approval tables, kept in one place
-- because an organisation may switch it off (org.organization.allow_self_approval). It used to be
-- a CHECK on every table (created_by <> certified_by, ...); a CHECK cannot read another table.

-- SECURITY DEFINER: a service role sees only its own organisation's row, and the answer for an
-- unknown organisation is false, so the rule stays on when in doubt. Only the trigger function
-- below (same owner) calls it: PUBLIC may not, or any role could read any organisation's flag.
CREATE FUNCTION org.self_approval_allowed(p_org uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT COALESCE((SELECT allow_self_approval FROM org.organization WHERE id = p_org), false)
$$;
REVOKE EXECUTE ON FUNCTION org.self_approval_allowed(uuid) FROM PUBLIC;

-- TG_ARGV is column-name pairs a, b, a, b, ... Each pair must hold two different people while the
-- organisation has not allowed self-approval. A NULL side is never refused (no person yet). On an
-- UPDATE only a pair that changed is judged, so turning the switch off leaves existing rows
-- editable: their other columns can still change. A pair naming a column the row does not have is
-- a mistake in the CREATE TRIGGER, and raises instead of silently guarding nothing.
-- SECURITY DEFINER so that it runs as the owner, which may call self_approval_allowed.
CREATE FUNCTION org.two_people() RETURNS trigger LANGUAGE plpgsql
SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  i int := 0;
  a text;
  b text;
  n jsonb := to_jsonb(NEW);
  o jsonb := CASE WHEN TG_OP = 'UPDATE' THEN to_jsonb(OLD) END;
BEGIN
  IF TG_NARGS % 2 <> 0 THEN
    RAISE EXCEPTION 'org.two_people nhận cặp tên cột, nhận % đối số', TG_NARGS;
  END IF;
  WHILE i < TG_NARGS LOOP
    a := TG_ARGV[i];
    b := TG_ARGV[i + 1];
    i := i + 2;
    IF NOT (n ? a AND n ? b) THEN
      RAISE EXCEPTION 'org.two_people: bảng % không có cột % hoặc %', TG_TABLE_NAME, a, b;
    END IF;
    IF o IS NOT NULL AND n ->> a IS NOT DISTINCT FROM o ->> a AND n ->> b IS NOT DISTINCT FROM o ->> b THEN
      CONTINUE;
    END IF;
    IF n ->> a IS NOT NULL AND n ->> a = n ->> b AND NOT org.self_approval_allowed(NEW.org_id) THEN
      RAISE EXCEPTION 'Cần hai người khác nhau (% và %): tổ chức chưa bật tự duyệt', a, b
        USING ERRCODE = 'check_violation';
    END IF;
  END LOOP;
  RETURN NEW;
END
$$;

CREATE TRIGGER dataset_version_two_people BEFORE INSERT OR UPDATE ON catalog.dataset_version
  FOR EACH ROW EXECUTE FUNCTION org.two_people('certified_by', 'created_by');
CREATE TRIGGER knowledge_page_version_two_people BEFORE INSERT OR UPDATE ON context.knowledge_page_version
  FOR EACH ROW EXECUTE FUNCTION org.two_people('reviewed_by', 'created_by', 'certified_by', 'created_by', 'certified_by', 'reviewed_by');
CREATE TRIGGER data_product_two_people BEFORE INSERT OR UPDATE ON context.data_product
  FOR EACH ROW EXECUTE FUNCTION org.two_people('owner_id', 'steward_id', 'activated_by', 'created_by');
CREATE TRIGGER writeback_request_two_people BEFORE INSERT OR UPDATE ON pipeline.writeback_request
  FOR EACH ROW EXECUTE FUNCTION org.two_people('approved_by', 'requested_by');
CREATE TRIGGER crawl_source_two_people BEFORE INSERT OR UPDATE ON acquire.crawl_source
  FOR EACH ROW EXECUTE FUNCTION org.two_people('legal_reviewed_by', 'owner_id');
CREATE TRIGGER model_version_two_people BEFORE INSERT OR UPDATE ON forecast.model_version
  FOR EACH ROW EXECUTE FUNCTION org.two_people('approved_by', 'created_by');
CREATE TRIGGER formula_component_two_people BEFORE INSERT OR UPDATE ON forecast.formula_component
  FOR EACH ROW EXECUTE FUNCTION org.two_people('certified_by', 'created_by');
CREATE TRIGGER model_provider_two_people BEFORE INSERT OR UPDATE ON governance.model_provider
  FOR EACH ROW EXECUTE FUNCTION org.two_people('approved_by', 'enabled_by');
