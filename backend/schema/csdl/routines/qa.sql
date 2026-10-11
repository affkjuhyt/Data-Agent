-- routines/qa.sql: what D4.6 (tab 3b) writes in words for schema qa: the number-check and scope
-- CHECKs of qa.answer, the answer receipt (BR-175, BR-176: numbered, sealed with a SHA-256, append
-- only, kept until retain_until), its figures, the receipt replay (BR-179), the extra RLS that
-- ties figures and replays to a visible receipt, and the D5.2 grant exception of svc_qa.

-- covers: qa.answer#0
-- BR-171: an answer is stored only once every number in it traces to a source.
ALTER TABLE qa.answer ADD CONSTRAINT answer_untraceable_final_check
  CHECK (jsonb_typeof(number_check) = 'object' AND coalesce((number_check->>'untraceable_final')::int, 0) = 0);

-- covers: qa.answer#1
-- BR-175: only answers that carry numbers can be in the scope of a data product.
ALTER TABLE qa.answer ADD CONSTRAINT answer_in_scope_kind_check
  CHECK (product_scope <> 'in_scope' OR kind IN ('data', 'mixed', 'forecast'));


-- covers: qa.answer_receipt#0
-- Whoever reads a receipt must be able to read its answer (qa.answer's own RLS decides). Masking the
-- numbers by the viewer's rights is done by the Policy engine in the application, as for qa.answer
-- (D1.3: rights inside a Data Space are checked in the application).
CREATE POLICY answer_receipt_answer_visible ON qa.answer_receipt AS RESTRICTIVE FOR SELECT
  USING (EXISTS (SELECT 1 FROM qa.answer a WHERE a.id = answer_id));

-- covers: qa.answer_receipt#1
-- BR-175/BR-176: a receipt belongs to an in-scope answer of the same data product, is numbered per
-- organisation and day of answered_at (UTC, D1.9), and starts unsealed: figures are added, then the
-- receipt is sealed once. After that nothing changes; only the retention job removes it once
-- retain_until has passed. Writing the receipt in the answer's transaction and hiding numbers of an
-- in-scope answer without a sealed receipt are Agent runtime rules.
CREATE FUNCTION qa.answer_receipt_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  a_scope text;
  a_product uuid;
  prefix text;
  last_no integer;
  body jsonb;
BEGIN
  IF TG_OP = 'INSERT' THEN
    SELECT product_scope, data_product_id INTO a_scope, a_product FROM qa.answer WHERE id = NEW.answer_id;
    IF a_scope IS DISTINCT FROM 'in_scope' OR a_product IS DISTINCT FROM NEW.data_product_id THEN
      RAISE EXCEPTION 'Biên nhận chỉ cấp cho câu trả lời trong phạm vi cùng data product (BR-175)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.sealed_at IS NOT NULL OR NEW.receipt_sha256 IS NOT NULL THEN
      RAISE EXCEPTION 'Biên nhận mới chưa được khoá; khoá sau khi ghi đủ số (BR-176)'
        USING ERRCODE = 'check_violation';
    END IF;
    -- Serialise numbering per organisation and day so two answers never get the same receipt_no.
    prefix := 'BN-' || to_char(NEW.answered_at AT TIME ZONE 'UTC', 'YYYYMMDD') || '-';
    PERFORM pg_advisory_xact_lock(hashtextextended('qa.answer_receipt:' || NEW.org_id || ':' || prefix, 0));
    SELECT max(substr(receipt_no, 13)::integer) INTO last_no
      FROM qa.answer_receipt WHERE org_id = NEW.org_id AND receipt_no LIKE prefix || '%';
    NEW.receipt_no := prefix || lpad((coalesce(last_no, 0) + 1)::text, 6, '0');
    NEW.figure_count := 0;
    NEW.masked_count := 0;
    RETURN NEW;
  ELSIF TG_OP = 'UPDATE' THEN
    IF OLD.sealed_at IS NOT NULL OR NEW.sealed_at IS NULL
       OR to_jsonb(NEW) - ARRAY['sealed_at', 'figure_count', 'masked_count', 'receipt_sha256']
          IS DISTINCT FROM to_jsonb(OLD) - ARRAY['sealed_at', 'figure_count', 'masked_count', 'receipt_sha256'] THEN
      RAISE EXCEPTION 'Biên nhận chỉ ghi thêm: chỉ được đặt sealed_at một lần (BR-176)'
        USING ERRCODE = 'check_violation';
    END IF;
    SELECT count(*), count(*) FILTER (WHERE display_state <> 'shown')
      INTO NEW.figure_count, NEW.masked_count
      FROM qa.answer_receipt_figure WHERE receipt_id = NEW.id;
    -- jsonb prints keys in a fixed order, so its text is the normalised JSON the hash is taken of.
    body := jsonb_build_object(
      'receipt', to_jsonb(NEW) - ARRAY['receipt_sha256', 'sealed_at', 'retain_until', 'created_at'],
      'figures', coalesce((SELECT jsonb_agg(to_jsonb(f) ORDER BY f.figure_key)
                           FROM qa.answer_receipt_figure f WHERE f.receipt_id = NEW.id), '[]'::jsonb));
    NEW.receipt_sha256 := encode(sha256(convert_to(body::text, 'UTF8')), 'hex');
    RETURN NEW;
  ELSE
    IF OLD.retain_until >= current_date THEN
      RAISE EXCEPTION 'Biên nhận còn trong hạn lưu giữ, không được xoá (BR-176)'
        USING ERRCODE = 'check_violation';
    END IF;
    PERFORM qa.answer_receipt_purge_figures(OLD.id);
    RETURN OLD;
  END IF;
END
$$;

-- Figures go with their receipt. svc_qa has no DELETE on qa.answer_receipt_figure (D5.2), so the
-- receipt's retention delete removes them as the owner; the figure trigger still checks the date.
CREATE FUNCTION qa.answer_receipt_purge_figures(receipt uuid) RETURNS void
LANGUAGE sql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  DELETE FROM qa.answer_receipt_figure WHERE receipt_id = receipt;
$$;
REVOKE ALL ON FUNCTION qa.answer_receipt_purge_figures(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION qa.answer_receipt_purge_figures(uuid) TO svc_qa;

CREATE TRIGGER answer_receipt_guard BEFORE INSERT OR UPDATE OR DELETE ON qa.answer_receipt
  FOR EACH ROW EXECUTE FUNCTION qa.answer_receipt_guard();

-- D5.2 (BR-176): svc_qa may only set sealed_at; DELETE stays for the retention job (trigger above).
REVOKE UPDATE ON qa.answer_receipt FROM svc_qa;
GRANT UPDATE (sealed_at) ON qa.answer_receipt TO svc_qa;


-- covers: qa.answer_receipt_figure#0
CREATE POLICY answer_receipt_figure_receipt_visible ON qa.answer_receipt_figure AS RESTRICTIVE FOR SELECT
  USING (EXISTS (SELECT 1 FROM qa.answer_receipt r WHERE r.id = receipt_id));

-- covers: qa.answer_receipt_figure#1
-- BR-176: figures are added only before the seal and never change; they are deleted only together
-- with a receipt past retain_until.
CREATE FUNCTION qa.answer_receipt_figure_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  sealed timestamptz;
  keep_until date;
BEGIN
  IF TG_OP = 'INSERT' THEN
    -- FOR SHARE waits for a concurrent seal, so no figure slips in after the hash is taken.
    SELECT sealed_at INTO sealed FROM qa.answer_receipt WHERE id = NEW.receipt_id FOR SHARE;
    IF sealed IS NOT NULL THEN
      RAISE EXCEPTION 'Biên nhận đã khoá, không thêm số (BR-176)' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  ELSIF TG_OP = 'UPDATE' THEN
    RAISE EXCEPTION 'Số trong biên nhận chỉ ghi thêm, không sửa (BR-176)' USING ERRCODE = 'check_violation';
  END IF;
  SELECT retain_until INTO keep_until FROM qa.answer_receipt WHERE id = OLD.receipt_id;
  IF keep_until >= current_date THEN
    RAISE EXCEPTION 'Số trong biên nhận còn trong hạn lưu giữ, không được xoá (BR-176)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN OLD;
END
$$;
CREATE TRIGGER answer_receipt_figure_guard BEFORE INSERT OR UPDATE OR DELETE ON qa.answer_receipt_figure
  FOR EACH ROW EXECUTE FUNCTION qa.answer_receipt_figure_guard();

-- D5.2 (BR-176): svc_qa only SELECT, INSERT on figures.
REVOKE UPDATE, DELETE ON qa.answer_receipt_figure FROM svc_qa;


-- covers: qa.receipt_replay#0
CREATE POLICY receipt_replay_receipt_visible ON qa.receipt_replay AS RESTRICTIVE FOR SELECT
  USING (EXISTS (SELECT 1 FROM qa.answer_receipt r WHERE r.id = receipt_id));

-- covers: qa.receipt_replay#1
-- BR-179: an unexplained difference becomes an Error Book entry; the Eval service creates it from
-- the event and Agent runtime writes error_book_entry_id back. Every replay is audited; audit is a
-- separate database (D1.1), so the audit row is requested through the outbox.
CREATE FUNCTION qa.receipt_replay_events() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    INSERT INTO qa.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'receipt_replay', NEW.id, 'audit.requested',
            jsonb_build_object('action', 'receipt.replay', 'object_kind', 'answer_receipt',
                               'object_id', NEW.receipt_id, 'before', NULL,
                               'after', jsonb_build_object('replay_id', NEW.id, 'requested_by', NEW.requested_by,
                                                           'mode', NEW.mode, 'reason', NEW.reason)));
  ELSIF NEW.status = 'completed' AND OLD.status IS DISTINCT FROM 'completed'
        AND NEW.outcome IN ('differs', 'not_reproducible') THEN
    INSERT INTO qa.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'receipt_replay', NEW.id, 'qa.receipt_replay.mismatch',
            jsonb_build_object('replay_id', NEW.id, 'receipt_id', NEW.receipt_id, 'outcome', NEW.outcome,
                               'differs_count', NEW.differs_count, 'figure_results', NEW.figure_results));
  END IF;
  RETURN NULL;
END
$$;
CREATE TRIGGER receipt_replay_events AFTER INSERT OR UPDATE OF status ON qa.receipt_replay
  FOR EACH ROW EXECUTE FUNCTION qa.receipt_replay_events();
