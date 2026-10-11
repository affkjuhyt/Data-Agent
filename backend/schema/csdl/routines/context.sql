-- Hand-written part of schema context: the knowledge layer (knowledge_page … knowledge_lint_finding,
-- BR-161 to BR-167, BR-194) and the data products (data_product, data_product_member, BR-175, BR-178).
-- It holds what D4.5 writes in words: CHECKs given as prose, the append-only grants of D5.2 (BR-164),
-- the reader row-level security of BR-163, the triggers and the functions the Context service calls.
-- Runs as da_owner after the generated tables, FKs, org_isolation policies and grants.
--
-- Reader contract (BR-163). The api sets these with set_config(..., true) for every reader
-- transaction; any one unset or empty hides every knowledge row (never an error):
--   app.knowledge_reader  'on' makes svc_context read as a reader (Context service answering on
--                         behalf of a user). Roles holding context_read (svc_qa, svc_agent, …) are
--                         always readers and do not need it.
--   app.data_space_ids    the Data Spaces the reader may read, as a Postgres uuid[] literal:
--                         '{8b0c…-…,1f2e…-…}' (braces, comma-separated, no spaces needed). '{}' is
--                         a reader with no Data Space: only pages without sources would pass, and a
--                         served page always has one.
--   app.clearance         the highest classification the reader may see, as its rank (an integer):
--                         0 public, 1 internal, 2 confidential, 3 restricted
--                         (context.classification_rank).

-- ─── shared helpers ──────────────────────────────────────────────────────────────────────────────

-- Classification order used by BR-163 (page = highest of its sources) and BR-180 (data product =
-- highest of its members). Unknown labels rank above restricted so they never widen access.
CREATE FUNCTION context.classification_rank(label text)
RETURNS integer LANGUAGE sql IMMUTABLE AS $$
  SELECT CASE label WHEN 'public' THEN 0 WHEN 'internal' THEN 1
                    WHEN 'confidential' THEN 2 WHEN 'restricted' THEN 3 ELSE 99 END
$$;

-- The text left once every allowed token is removed: {{claim:a}}, {{metric:…}}, {{agg:…}},
-- {{hyp:…}} (BR-194, filled from exec.publication when rendered) and [[slug]]. Any digit or brace
-- left over is a hand-written number or a malformed placeholder (BR-162).
CREATE FUNCTION context.knowledge_strip_tokens(content text)
RETURNS text LANGUAGE sql IMMUTABLE AS $$
  SELECT regexp_replace(content,
    '\{\{(?:metric|agg):[a-z0-9_]+(?:\|[a-z_]+=[^|{}]+)*\}\}'
    '|\{\{claim:[a-z][a-z0-9_]{0,31}\}\}'
    '|\{\{hyp:[a-z_]+\}\}'
    '|\[\[[a-z0-9][a-z0-9-]{1,79}\]\]',
    '', 'g')
$$;

CREATE FUNCTION context.knowledge_has_handwritten_number(content text)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT context.knowledge_strip_tokens(coalesce(content, '')) ~ '[0-9{}]'
$$;

-- BR-163 reader test on one row's policy columns, against the GUCs documented in the header.
CREATE FUNCTION context.knowledge_reader_allows(required_data_space_ids uuid[], classification text)
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT coalesce(
    required_data_space_ids <@ NULLIF(current_setting('app.data_space_ids', true), '')::uuid[]
    AND context.classification_rank(classification)
        <= NULLIF(current_setting('app.clearance', true), '')::integer,
    false)
$$;

-- svc_context reads as a reader only when the api says so; it also writes the layer.
CREATE FUNCTION context.knowledge_reader_mode()
RETURNS boolean LANGUAGE sql STABLE AS $$
  SELECT coalesce(current_setting('app.knowledge_reader', true), '') = 'on'
$$;

-- ─── constraints written as prose ────────────────────────────────────────────────────────────────

-- covers: context.knowledge_page#2
-- P16 (BR-194) adds kind 'hypothesis'; the generated CHECK predates it, so it is replaced.
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'context.knowledge_page'::regclass
             AND pg_get_constraintdef(oid) LIKE 'CHECK ((kind = ANY%'
  LOOP
    EXECUTE format('ALTER TABLE context.knowledge_page DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE context.knowledge_page ADD CONSTRAINT knowledge_page_kind_check
  CHECK (kind IN ('metric','entity','source','topic','hypothesis'));

-- covers: context.knowledge_page#0
-- Pairs kind with subject_kind; no subject kind names a person (BR-167). A hypothesis page is
-- about one exec.publication, so its subject_ref is that publication's uuid (BR-194).
ALTER TABLE context.knowledge_page ADD CONSTRAINT knowledge_page_kind_subject_check CHECK (
  (kind = 'metric' AND subject_kind = 'metric')
  OR (kind = 'entity' AND subject_kind IN ('organization','product','segment','branch','channel'))
  OR (kind = 'source' AND subject_kind IN ('dataset','document_collection','crawl_source'))
  OR (kind = 'topic' AND subject_kind = 'topic')
  OR (kind = 'hypothesis' AND subject_kind = 'exec_publication'
      AND subject_ref ~ '^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$')
);

-- covers: context.knowledge_page_version#0
-- Certifier recorded with both columns and is a third person (BR-164); a certified version has one.
ALTER TABLE context.knowledge_page_version ADD CONSTRAINT knowledge_page_version_certifier_check CHECK (
  (certified_by IS NULL) = (certified_at IS NULL)
  AND (status <> 'certified' OR certified_by IS NOT NULL)
  AND (certified_by IS NULL OR (certified_by IS DISTINCT FROM created_by
                                AND certified_by IS DISTINCT FROM reviewed_by))
);

-- covers: context.knowledge_page_version#5
-- 'exec_publish' joins origin (BR-194). The {{hyp:…}} exemption lives in
-- context.knowledge_strip_tokens, used by every BR-162 check below.
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'context.knowledge_page_version'::regclass
             AND pg_get_constraintdef(oid) LIKE 'CHECK ((origin = ANY%'
  LOOP
    EXECUTE format('ALTER TABLE context.knowledge_page_version DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE context.knowledge_page_version ADD CONSTRAINT knowledge_page_version_origin_check
  CHECK (origin IN ('ingest','lint_fix','saveback','manual','exec_publish'));

-- covers: context.knowledge_source_link#0
ALTER TABLE context.knowledge_source_link ADD CONSTRAINT knowledge_source_link_classification_check
  CHECK (classification IN ('public','internal','confidential','restricted'));

-- covers: context.knowledge_source_link#3
-- 'exec_publication' joins source_kind (BR-194); the trigger knowledge_source_link_guard keeps it
-- to versions of hypothesis pages.
DO $$
DECLARE c text;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
           WHERE conrelid = 'context.knowledge_source_link'::regclass
             AND pg_get_constraintdef(oid) LIKE 'CHECK ((source_kind = ANY%'
  LOOP
    EXECUTE format('ALTER TABLE context.knowledge_source_link DROP CONSTRAINT %I', c);
  END LOOP;
END $$;
ALTER TABLE context.knowledge_source_link ADD CONSTRAINT knowledge_source_link_source_kind_check
  CHECK (source_kind IN ('conflict_claim','dataset_version','document_version','document_chunk',
                         'metric_version','answer','glossary_term','knowledge_page','exec_publication'));

-- covers: context.knowledge_figure#0
ALTER TABLE context.knowledge_figure ADD CONSTRAINT knowledge_figure_binding_check CHECK (
  (binding_kind = 'metric' AND metric_code IS NOT NULL AND token LIKE '{{metric:%')
  OR (binding_kind = 'aggregate' AND aggregate_code IS NOT NULL AND dataset_id IS NOT NULL
      AND token LIKE '{{agg:%')
);

-- covers: context.knowledge_lint_finding#0
ALTER TABLE context.knowledge_lint_finding ADD CONSTRAINT knowledge_lint_finding_kind_check
  CHECK (kind IN ('contradiction','expired_claim','orphan_page','metric_without_page',
                  'undefined_term','source_decertified','stale_condition','policy_drift'));

-- covers: context.knowledge_lint_finding#1
-- The agent picks from a fixed action set, never free text (BR-166).
ALTER TABLE context.knowledge_lint_finding ADD CONSTRAINT knowledge_lint_finding_action_check
  CHECK (action IS NULL OR action IN ('propose_edit','mark_stale','retire_page','create_page',
                                      'dismiss','reassign'));

-- covers: context.knowledge_lint_finding#2
-- Dismissing needs a reason and hides the fingerprint until suppressed_until (BR-166).
ALTER TABLE context.knowledge_lint_finding ADD CONSTRAINT knowledge_lint_finding_dismiss_check CHECK (
  (status = 'dismissed') = coalesce(action = 'dismiss', false)
  AND (status <> 'dismissed'
       OR (coalesce(btrim(action_reason), '') <> '' AND suppressed_until IS NOT NULL))
);

-- covers: context.knowledge_lint_finding#3
ALTER TABLE context.knowledge_lint_finding ADD CONSTRAINT knowledge_lint_finding_resolver_check
  CHECK (status NOT IN ('resolved','dismissed') OR (resolved_by IS NOT NULL AND resolved_at IS NOT NULL));

-- covers: context.knowledge_lint_finding#4
ALTER TABLE context.knowledge_lint_finding ADD CONSTRAINT knowledge_lint_finding_propose_edit_check
  CHECK (action IS DISTINCT FROM 'propose_edit' OR page_version_id IS NOT NULL);

-- ─── append-only grants (D5.2, BR-164) ───────────────────────────────────────────────────────────
-- These are the grant halves of knowledge_page_version#4, knowledge_claim#0, knowledge_figure#1,
-- knowledge_link#0, knowledge_source_link#2 and knowledge_lint_finding#6, whose markers sit with
-- their triggers and policies below.

-- No DELETE on the eight knowledge_ tables: pages retire, versions are superseded, history stays.
REVOKE DELETE ON context.knowledge_page, context.knowledge_page_version,
  context.knowledge_source_link, context.knowledge_claim, context.knowledge_figure,
  context.knowledge_link, context.knowledge_run, context.knowledge_lint_finding FROM svc_context;

-- Claims, figures and links are append-only.
REVOKE UPDATE ON context.knowledge_claim, context.knowledge_figure, context.knowledge_link FROM svc_context;

-- A source keeps its content; only the four permission columns follow the source when its Data
-- Space or classification changes (BR-163).
REVOKE UPDATE ON context.knowledge_source_link FROM svc_context;
GRANT UPDATE (data_space_id, classification, pii_column_refs, policy_updated_at)
  ON context.knowledge_source_link TO svc_context;

-- covers: context.knowledge_run#0
-- Runs and lint findings are the agent's working state, not reader content: only svc_context.
REVOKE SELECT ON context.knowledge_run, context.knowledge_lint_finding FROM context_read;

-- ─── page policy (BR-163) ────────────────────────────────────────────────────────────────────────

-- covers: context.knowledge_page#1
-- Readers see a page only when it is served (active, stale), its policy is computed and they hold
-- every Data Space its sources come from at a clearance not below its classification.
CREATE POLICY knowledge_reader ON context.knowledge_page AS RESTRICTIVE FOR SELECT TO context_read
  USING (status IN ('active','stale') AND policy_state = 'current'
         AND context.knowledge_reader_allows(required_data_space_ids, classification));
CREATE POLICY knowledge_reader_service ON context.knowledge_page AS RESTRICTIVE FOR SELECT TO svc_context
  USING (NOT context.knowledge_reader_mode()
         OR (status IN ('active','stale') AND policy_state = 'current'
             AND context.knowledge_reader_allows(required_data_space_ids, classification)));

-- Recomputes the page policy from the sources of its served version. Returns whether it changed.
CREATE FUNCTION context.knowledge_page_policy_refresh(p_page_id uuid)
RETURNS boolean LANGUAGE plpgsql AS $$
DECLARE
  pg context.knowledge_page%ROWTYPE;
  v_spaces uuid[];
  v_class text;
  v_pii text[];
  v_external boolean;
  v_changed boolean;
BEGIN
  SELECT * INTO pg FROM context.knowledge_page WHERE id = p_page_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN false;
  END IF;
  SELECT coalesce(array_agg(DISTINCT l.data_space_id ORDER BY l.data_space_id), '{}'),
         coalesce((array_agg(l.classification
                   ORDER BY context.classification_rank(l.classification) DESC))[1], 'internal'),
         coalesce(bool_or(l.external), false)
    INTO v_spaces, v_class, v_external
    FROM context.knowledge_source_link l
   WHERE l.page_version_id = pg.current_version_id;
  SELECT coalesce(array_agg(DISTINCT c ORDER BY c), '{}') INTO v_pii
    FROM context.knowledge_source_link l, unnest(l.pii_column_refs) AS c
   WHERE l.page_version_id = pg.current_version_id;
  v_changed := (v_spaces, v_class, v_pii, v_external)
    IS DISTINCT FROM (pg.required_data_space_ids, pg.classification, pg.pii_column_refs, pg.has_external_source);
  UPDATE context.knowledge_page
     SET required_data_space_ids = v_spaces, classification = v_class, pii_column_refs = v_pii,
         has_external_source = v_external, policy_state = 'current', policy_computed_at = now(),
         updated_at = now()
   WHERE id = p_page_id;
  IF v_changed THEN
    INSERT INTO context.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), pg.org_id, 'knowledge_page', pg.id, 'context.knowledge_page.policy_changed',
            jsonb_build_object(
              'page_id', pg.id, 'data_space_id', pg.data_space_id,
              'before', jsonb_build_object('required_data_space_ids', pg.required_data_space_ids,
                                           'classification', pg.classification,
                                           'pii_column_refs', pg.pii_column_refs,
                                           'has_external_source', pg.has_external_source),
              'after', jsonb_build_object('required_data_space_ids', v_spaces,
                                          'classification', v_class,
                                          'pii_column_refs', v_pii,
                                          'has_external_source', v_external)));
  END IF;
  RETURN v_changed;
END $$;

-- Hides (policy_state = pending) every page whose served version uses a source whose permissions
-- are about to change, until the Context service recomputes it. Returns the number of pages.
CREATE FUNCTION context.knowledge_policy_invalidate(p_source_kind text, p_source_ref uuid)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
  UPDATE context.knowledge_page p
     SET policy_state = 'pending', updated_at = now()
   WHERE p.policy_state = 'current'
     AND EXISTS (SELECT 1 FROM context.knowledge_source_link l
                  WHERE l.page_version_id = p.current_version_id
                    AND l.source_kind = p_source_kind AND l.source_ref = p_source_ref);
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

-- ─── version reader policy ───────────────────────────────────────────────────────────────────────

-- SECURITY DEFINER: the check must see every source of the version, including ones the reader's own
-- policies would hide; it returns only a boolean for one version id.
CREATE FUNCTION context.knowledge_version_sources_ok(p_version_id uuid)
RETURNS boolean LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT NOT EXISTS (
    SELECT 1 FROM context.knowledge_source_link l
     WHERE l.page_version_id = p_version_id
       AND NOT context.knowledge_reader_allows(ARRAY[l.data_space_id], l.classification))
$$;
REVOKE EXECUTE ON FUNCTION context.knowledge_version_sources_ok(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION context.knowledge_version_sources_ok(uuid) TO svc_context, context_read;

-- covers: context.knowledge_page_version#4
-- A reader sees approved versions (older superseded ones included) of a page it can see, provided
-- it holds every source of that version itself: a superseded version may have wider sources.
CREATE POLICY knowledge_reader ON context.knowledge_page_version AS RESTRICTIVE FOR SELECT TO context_read
  USING (status IN ('reviewed','certified','superseded')
         AND EXISTS (SELECT 1 FROM context.knowledge_page p WHERE p.id = page_id)
         AND context.knowledge_version_sources_ok(id));
CREATE POLICY knowledge_reader_service ON context.knowledge_page_version AS RESTRICTIVE FOR SELECT TO svc_context
  USING (NOT context.knowledge_reader_mode()
         OR (status IN ('reviewed','certified','superseded')
             AND EXISTS (SELECT 1 FROM context.knowledge_page p WHERE p.id = page_id)
             AND context.knowledge_version_sources_ok(id)));

-- The same reader rule reaches the rows of a version (sources, claims, figures, links), otherwise
-- the claims of a draft or of a version the reader may not see would leak through these tables.
CREATE POLICY knowledge_reader ON context.knowledge_source_link AS RESTRICTIVE FOR SELECT TO context_read
  USING (EXISTS (SELECT 1 FROM context.knowledge_page_version v WHERE v.id = page_version_id));
CREATE POLICY knowledge_reader_service ON context.knowledge_source_link AS RESTRICTIVE FOR SELECT TO svc_context
  USING (NOT context.knowledge_reader_mode()
         OR EXISTS (SELECT 1 FROM context.knowledge_page_version v WHERE v.id = page_version_id));
CREATE POLICY knowledge_reader ON context.knowledge_claim AS RESTRICTIVE FOR SELECT TO context_read
  USING (EXISTS (SELECT 1 FROM context.knowledge_page_version v WHERE v.id = page_version_id));
CREATE POLICY knowledge_reader_service ON context.knowledge_claim AS RESTRICTIVE FOR SELECT TO svc_context
  USING (NOT context.knowledge_reader_mode()
         OR EXISTS (SELECT 1 FROM context.knowledge_page_version v WHERE v.id = page_version_id));
CREATE POLICY knowledge_reader ON context.knowledge_figure AS RESTRICTIVE FOR SELECT TO context_read
  USING (EXISTS (SELECT 1 FROM context.knowledge_page_version v WHERE v.id = page_version_id));
CREATE POLICY knowledge_reader_service ON context.knowledge_figure AS RESTRICTIVE FOR SELECT TO svc_context
  USING (NOT context.knowledge_reader_mode()
         OR EXISTS (SELECT 1 FROM context.knowledge_page_version v WHERE v.id = page_version_id));
CREATE POLICY knowledge_reader ON context.knowledge_link AS RESTRICTIVE FOR SELECT TO context_read
  USING (EXISTS (SELECT 1 FROM context.knowledge_page_version v WHERE v.id = from_version_id));
CREATE POLICY knowledge_reader_service ON context.knowledge_link AS RESTRICTIVE FOR SELECT TO svc_context
  USING (NOT context.knowledge_reader_mode()
         OR EXISTS (SELECT 1 FROM context.knowledge_page_version v WHERE v.id = from_version_id));
-- Runs and findings: svc_context only reads them as the writer (context_read has no SELECT).
CREATE POLICY knowledge_reader_service ON context.knowledge_run AS RESTRICTIVE FOR SELECT TO svc_context
  USING (NOT context.knowledge_reader_mode());
CREATE POLICY knowledge_reader_service ON context.knowledge_lint_finding AS RESTRICTIVE FOR SELECT TO svc_context
  USING (NOT context.knowledge_reader_mode());

-- ─── knowledge_page_version triggers ─────────────────────────────────────────────────────────────

-- covers: context.knowledge_page_version#1
-- Versions are immutable drafts that move through review (BR-164); submitting checks that every
-- claim, figure and link the body names exists (BR-161, BR-162, BR-167); certifying needs every
-- external source certified (BR-165). catalog.dataset_version is read through catalog_read.
CREATE FUNCTION context.knowledge_page_version_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  pg context.knowledge_page%ROWTYPE;
  v_missing text;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status <> 'draft' THEN
      RAISE EXCEPTION 'Phiên bản trang mới phải ở trạng thái draft (BR-164)'
        USING ERRCODE = 'check_violation';
    END IF;
    -- Locking the page serialises version numbers of concurrent writers.
    SELECT * INTO pg FROM context.knowledge_page WHERE id = NEW.page_id FOR UPDATE;
    IF FOUND THEN
      IF pg.status = 'retired' THEN
        RAISE EXCEPTION 'Trang đã ngừng (retired) không nhận phiên bản mới (BR-164)'
          USING ERRCODE = 'check_violation';
      END IF;
      NEW.version := pg.latest_version_no + 1;
      NEW.parent_version_id := coalesce(NEW.parent_version_id, pg.current_version_id);
      UPDATE context.knowledge_page SET latest_version_no = NEW.version, updated_at = now()
       WHERE id = NEW.page_id;
    END IF;
    NEW.body_sha256 := encode(sha256(convert_to(NEW.body, 'UTF8')), 'hex');
    IF context.knowledge_has_handwritten_number(NEW.body)
       OR context.knowledge_has_handwritten_number(NEW.summary) THEN
      RAISE EXCEPTION 'Thân trang và tóm tắt không được có số viết tay hoặc chỗ giữ số sai mẫu; số chỉ ở dạng {{metric:…}}, {{agg:…}} (BR-162)'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF (NEW.org_id, NEW.page_id, NEW.version, NEW.parent_version_id, NEW.origin, NEW.body,
      NEW.body_sha256, NEW.summary, NEW.change_note, NEW.change_size, NEW.knowledge_run_id,
      NEW.agent_run_id, NEW.source_answer_id, NEW.created_at, NEW.created_by)
     IS DISTINCT FROM
     (OLD.org_id, OLD.page_id, OLD.version, OLD.parent_version_id, OLD.origin, OLD.body,
      OLD.body_sha256, OLD.summary, OLD.change_note, OLD.change_size, OLD.knowledge_run_id,
      OLD.agent_run_id, OLD.source_answer_id, OLD.created_at, OLD.created_by) THEN
    RAISE EXCEPTION 'Nội dung phiên bản trang không sửa được; sửa là tạo phiên bản mới (BR-164)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IS NOT DISTINCT FROM OLD.status THEN
    RETURN NEW;
  END IF;
  IF NOT ((OLD.status = 'draft' AND NEW.status IN ('pending_review','rejected'))
       OR (OLD.status = 'pending_review' AND NEW.status IN ('reviewed','rejected','draft'))
       OR (OLD.status = 'reviewed' AND NEW.status IN ('certified','superseded'))
       OR (OLD.status = 'certified' AND NEW.status = 'superseded')) THEN
    RAISE EXCEPTION 'Không chuyển được phiên bản trang từ % sang % (BR-164)', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'pending_review' THEN
    IF NOT EXISTS (SELECT 1 FROM context.knowledge_claim WHERE page_version_id = NEW.id) THEN
      RAISE EXCEPTION 'Gửi duyệt cần ít nhất một khẳng định có nguồn (BR-161)'
        USING ERRCODE = 'check_violation';
    END IF;
    SELECT string_agg(DISTINCT r.m[1], ', ') INTO v_missing
      FROM regexp_matches(NEW.body, '\{\{claim:([a-z][a-z0-9_]{0,31})\}\}', 'g') AS r(m)
     WHERE NOT EXISTS (SELECT 1 FROM context.knowledge_claim c
                        WHERE c.page_version_id = NEW.id AND c.anchor = r.m[1]);
    IF v_missing IS NOT NULL THEN
      RAISE EXCEPTION 'Thân trang trích khẳng định không có: % (BR-161)', v_missing
        USING ERRCODE = 'check_violation';
    END IF;
    SELECT string_agg(DISTINCT x.token, ', ') INTO v_missing
      FROM (SELECT r.m[1] AS token
              FROM regexp_matches(NEW.body, '(\{\{(?:metric|agg):[a-z0-9_]+(?:\|[a-z_]+=[^|{}]+)*\}\})', 'g') AS r(m)
            UNION ALL
            SELECT r.m[1]
              FROM context.knowledge_claim c,
                   regexp_matches(c.statement, '(\{\{(?:metric|agg):[a-z0-9_]+(?:\|[a-z_]+=[^|{}]+)*\}\})', 'g') AS r(m)
             WHERE c.page_version_id = NEW.id) AS x
     WHERE NOT EXISTS (SELECT 1 FROM context.knowledge_figure f
                        WHERE f.page_version_id = NEW.id AND f.token = x.token);
    IF v_missing IS NOT NULL THEN
      RAISE EXCEPTION 'Chỗ giữ số chưa ràng buộc knowledge_figure: % (BR-162)', v_missing
        USING ERRCODE = 'check_violation';
    END IF;
    SELECT string_agg(DISTINCT r.m[1], ', ') INTO v_missing
      FROM regexp_matches(NEW.body, '\[\[([a-z0-9][a-z0-9-]{1,79})\]\]', 'g') AS r(m)
     WHERE NOT EXISTS (
       SELECT 1 FROM context.knowledge_page me
         JOIN context.knowledge_page p ON p.data_space_id = me.data_space_id AND p.slug = r.m[1]
         JOIN context.knowledge_link l ON l.from_version_id = NEW.id AND l.to_page_id = p.id
        WHERE me.id = NEW.page_id AND p.status <> 'retired');
    IF v_missing IS NOT NULL THEN
      RAISE EXCEPTION 'Liên kết [[%]] không trỏ trang còn dùng có dòng knowledge_link (BR-167)', v_missing
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.origin = 'saveback' AND NOT EXISTS (
         SELECT 1 FROM context.knowledge_source_link l
          WHERE l.page_version_id = NEW.id AND l.source_kind = 'answer'
            AND l.source_ref = NEW.source_answer_id) THEN
      RAISE EXCEPTION 'Bản lưu ngược phải trích câu trả lời làm nguồn (BR-167)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;

  IF NEW.status = 'certified' THEN
    SELECT string_agg(l.source_label, ', ') INTO v_missing
      FROM context.knowledge_source_link l
      LEFT JOIN catalog.dataset_version dv ON dv.id = l.dataset_version_id
     WHERE l.page_version_id = NEW.id AND l.external AND dv.certified_at IS NULL;
    IF v_missing IS NOT NULL THEN
      RAISE EXCEPTION 'Chưa chứng nhận được: nguồn ngoài chưa có phiên bản dataset được chứng nhận: % (BR-165)', v_missing
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER knowledge_page_version_guard BEFORE INSERT OR UPDATE ON context.knowledge_page_version
  FOR EACH ROW EXECUTE FUNCTION context.knowledge_page_version_guard();

-- covers: context.knowledge_page_version#2
-- A reviewed version is served: the previous one is superseded, the page points at it and is
-- active again, the policy follows the new sources (BR-163). Certified only announces.
CREATE FUNCTION context.knowledge_page_version_publish()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_previous uuid;
BEGIN
  IF NEW.status = 'reviewed' AND OLD.status <> 'reviewed' THEN
    SELECT current_version_id INTO v_previous FROM context.knowledge_page WHERE id = NEW.page_id FOR UPDATE;
    IF v_previous IS NOT NULL AND v_previous <> NEW.id THEN
      UPDATE context.knowledge_page_version SET status = 'superseded'
       WHERE id = v_previous AND status IN ('reviewed','certified');
    END IF;
    UPDATE context.knowledge_page
       SET current_version_id = NEW.id, status = 'active', stale_reason = NULL,
           certification_suspended = false, updated_at = now(), updated_by = NEW.reviewed_by
     WHERE id = NEW.page_id;
    PERFORM context.knowledge_page_policy_refresh(NEW.page_id);
    INSERT INTO context.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'knowledge_page_version', NEW.id, 'context.knowledge_page.published',
            jsonb_build_object('page_id', NEW.page_id, 'version_id', NEW.id, 'version', NEW.version,
                               'previous_version_id', v_previous, 'reviewed_by', NEW.reviewed_by));
  ELSIF NEW.status = 'certified' AND OLD.status <> 'certified' THEN
    INSERT INTO context.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'knowledge_page_version', NEW.id, 'context.knowledge_page.certified',
            jsonb_build_object('page_id', NEW.page_id, 'version_id', NEW.id, 'version', NEW.version,
                               'certified_by', NEW.certified_by));
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER knowledge_page_version_publish AFTER UPDATE OF status ON context.knowledge_page_version
  FOR EACH ROW EXECUTE FUNCTION context.knowledge_page_version_publish();

-- covers: context.knowledge_page_version#3
-- A run edits at most page_limit pages and only while it runs (BR-166); the row lock on the run
-- makes the count exact under concurrent writers.
CREATE FUNCTION context.knowledge_page_version_count_run()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE r context.knowledge_run%ROWTYPE;
BEGIN
  SELECT * INTO r FROM context.knowledge_run WHERE id = NEW.knowledge_run_id FOR UPDATE;
  IF r.status NOT IN ('analyzing','writing','checking') THEN
    RAISE EXCEPTION 'Lượt Agent Tri thức không còn chạy (trạng thái %); không ghi thêm phiên bản (BR-166)', r.status
      USING ERRCODE = 'check_violation';
  END IF;
  IF r.pages_changed >= r.page_limit THEN
    RAISE EXCEPTION 'Lượt đã sửa đủ % trang (page_limit); trang còn lại dời sang lượt sau (BR-166)', r.page_limit
      USING ERRCODE = 'check_violation';
  END IF;
  UPDATE context.knowledge_run SET pages_changed = pages_changed + 1 WHERE id = r.id;
  RETURN NULL;
END $$;
CREATE TRIGGER knowledge_page_version_count_run AFTER INSERT ON context.knowledge_page_version
  FOR EACH ROW WHEN (NEW.knowledge_run_id IS NOT NULL)
  EXECUTE FUNCTION context.knowledge_page_version_count_run();

-- ─── rows of a version: sources, claims, figures, links ──────────────────────────────────────────

-- covers: context.knowledge_source_link#1
-- Sources are fixed once the version leaves draft (BR-164); exec_publication sources belong to
-- hypothesis pages only (BR-194).
CREATE FUNCTION context.knowledge_source_link_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text; v_kind text;
BEGIN
  SELECT v.status, p.kind INTO v_status, v_kind
    FROM context.knowledge_page_version v JOIN context.knowledge_page p ON p.id = v.page_id
   WHERE v.id = NEW.page_version_id;
  IF FOUND AND v_status <> 'draft' THEN
    RAISE EXCEPTION 'Chỉ thêm nguồn vào phiên bản còn draft (BR-164)' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.source_kind = 'exec_publication' AND v_kind IS DISTINCT FROM 'hypothesis' THEN
    RAISE EXCEPTION 'Nguồn exec_publication chỉ dùng cho trang hypothesis (BR-194)' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER knowledge_source_link_guard BEFORE INSERT ON context.knowledge_source_link
  FOR EACH ROW EXECUTE FUNCTION context.knowledge_source_link_guard();

-- covers: context.knowledge_source_link#2
-- A source changing Data Space or classification re-scopes the page that serves this version in
-- the same transaction (BR-163), so a reader never sees the page under the old permissions.
CREATE FUNCTION context.knowledge_source_link_policy()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_page uuid;
BEGIN
  FOR v_page IN SELECT id FROM context.knowledge_page WHERE current_version_id = NEW.page_version_id LOOP
    PERFORM context.knowledge_page_policy_refresh(v_page);
  END LOOP;
  RETURN NULL;
END $$;
CREATE TRIGGER knowledge_source_link_policy
  AFTER UPDATE OF data_space_id, classification, pii_column_refs ON context.knowledge_source_link
  FOR EACH ROW EXECUTE FUNCTION context.knowledge_source_link_policy();

-- covers: context.knowledge_claim#0
-- A claim cites a source of its own version (BR-161) and carries numbers only as placeholders (BR-162).
CREATE FUNCTION context.knowledge_claim_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM context.knowledge_page_version
              WHERE id = NEW.page_version_id AND status <> 'draft') THEN
    RAISE EXCEPTION 'Chỉ thêm khẳng định vào phiên bản còn draft (BR-164)' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM context.knowledge_source_link
                  WHERE id = NEW.source_link_id AND page_version_id = NEW.page_version_id) THEN
    RAISE EXCEPTION 'Nguồn của khẳng định phải thuộc chính phiên bản (BR-161)' USING ERRCODE = 'check_violation';
  END IF;
  IF context.knowledge_has_handwritten_number(NEW.statement) THEN
    RAISE EXCEPTION 'Câu khẳng định không được có số viết tay; số chỉ ở dạng chỗ giữ số (BR-162)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER knowledge_claim_guard BEFORE INSERT ON context.knowledge_claim
  FOR EACH ROW EXECUTE FUNCTION context.knowledge_claim_guard();

-- covers: context.knowledge_figure#1
CREATE FUNCTION context.knowledge_figure_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM context.knowledge_page_version
              WHERE id = NEW.page_version_id AND status <> 'draft') THEN
    RAISE EXCEPTION 'Chỉ thêm chỗ giữ số vào phiên bản còn draft (BR-164)' USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM context.knowledge_source_link
                  WHERE id = NEW.source_link_id AND page_version_id = NEW.page_version_id) THEN
    RAISE EXCEPTION 'Nguồn của chỗ giữ số phải thuộc chính phiên bản (BR-162)' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER knowledge_figure_guard BEFORE INSERT ON context.knowledge_figure
  FOR EACH ROW EXECUTE FUNCTION context.knowledge_figure_guard();

-- covers: context.knowledge_link#0
-- from_page_id is denormalised for the orphan scan; it must be the version's own page.
CREATE FUNCTION context.knowledge_link_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text; v_page uuid;
BEGIN
  SELECT status, page_id INTO v_status, v_page
    FROM context.knowledge_page_version WHERE id = NEW.from_version_id;
  IF FOUND AND v_status <> 'draft' THEN
    RAISE EXCEPTION 'Chỉ thêm liên kết vào phiên bản còn draft (BR-164)' USING ERRCODE = 'check_violation';
  END IF;
  IF FOUND AND v_page <> NEW.from_page_id THEN
    RAISE EXCEPTION 'Liên kết phải thuộc trang của chính phiên bản (from_page_id)' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER knowledge_link_guard BEFORE INSERT ON context.knowledge_link
  FOR EACH ROW EXECUTE FUNCTION context.knowledge_link_guard();

-- ─── lint findings ───────────────────────────────────────────────────────────────────────────────

-- covers: context.knowledge_lint_finding#5
-- A dismissed fingerprint stays quiet until suppressed_until (BR-166); a handled finding keeps
-- the action it was handled with.
CREATE FUNCTION context.knowledge_lint_finding_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF EXISTS (SELECT 1 FROM context.knowledge_lint_finding f
                WHERE f.data_space_id = NEW.data_space_id AND f.fingerprint = NEW.fingerprint
                  AND f.status = 'dismissed' AND f.suppressed_until > now()) THEN
      RAISE EXCEPTION 'Phát hiện cùng vân tay đã bị bỏ qua và còn trong hạn ẩn (BR-166)'
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF OLD.status <> 'open' AND NEW.action IS DISTINCT FROM OLD.action THEN
    RAISE EXCEPTION 'Không đổi hành động của phát hiện đã xử lý (BR-166)' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER knowledge_lint_finding_guard BEFORE INSERT OR UPDATE ON context.knowledge_lint_finding
  FOR EACH ROW EXECUTE FUNCTION context.knowledge_lint_finding_guard();

-- covers: context.knowledge_lint_finding#6
-- The chosen action takes effect on the page; the Inbox service opens and closes the Cần bạn card
-- (kind knowledge_finding) from the outbox events.
CREATE FUNCTION context.knowledge_lint_finding_apply()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.page_id IS NOT NULL AND NEW.action IN ('mark_stale','retire_page')
     AND (TG_OP = 'INSERT' OR OLD.action IS DISTINCT FROM NEW.action) THEN
    IF NEW.action = 'mark_stale' THEN
      UPDATE context.knowledge_page SET status = 'stale', stale_reason = NEW.kind, updated_at = now()
       WHERE id = NEW.page_id AND status IN ('active','stale');
    ELSE
      UPDATE context.knowledge_page SET status = 'retired', updated_at = now()
       WHERE id = NEW.page_id AND status <> 'retired';
    END IF;
  END IF;
  IF TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM NEW.status THEN
    INSERT INTO context.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'knowledge_lint_finding', NEW.id,
            CASE WHEN NEW.status = 'open' THEN 'context.knowledge_finding.opened'
                 ELSE 'context.knowledge_finding.' || NEW.status END,
            jsonb_build_object('finding_id', NEW.id, 'data_space_id', NEW.data_space_id,
                               'page_id', NEW.page_id, 'kind', NEW.kind, 'severity', NEW.severity,
                               'status', NEW.status, 'action', NEW.action,
                               'hitl_request_id', NEW.hitl_request_id));
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER knowledge_lint_finding_apply AFTER INSERT OR UPDATE ON context.knowledge_lint_finding
  FOR EACH ROW EXECUTE FUNCTION context.knowledge_lint_finding_apply();

-- Called on catalog.dataset_version.certification_revoked (BR-165): every page served from that
-- dataset version loses its certification effect and gets a high source_decertified finding,
-- recorded under one completed lint run per Data Space. Returns the number of pages touched.
CREATE FUNCTION context.knowledge_on_dataset_decertified(p_dataset_version_id uuid)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE
  pg record;
  v_runs jsonb := '{}';
  v_run uuid;
  v_fingerprint text;
  n integer := 0;
BEGIN
  FOR pg IN
    SELECT p.id, p.org_id, p.data_space_id FROM context.knowledge_page p
     WHERE EXISTS (SELECT 1 FROM context.knowledge_source_link l
                    WHERE l.page_version_id = p.current_version_id
                      AND l.dataset_version_id = p_dataset_version_id)
     ORDER BY p.id
       FOR UPDATE
  LOOP
    UPDATE context.knowledge_page SET certification_suspended = true, updated_at = now() WHERE id = pg.id;
    n := n + 1;
    v_fingerprint := encode(sha256(convert_to('source_decertified:' || pg.id || ':' || p_dataset_version_id, 'UTF8')), 'hex');
    CONTINUE WHEN EXISTS (
      SELECT 1 FROM context.knowledge_lint_finding f
       WHERE f.data_space_id = pg.data_space_id AND f.fingerprint = v_fingerprint
         AND (f.status IN ('open','actioned') OR (f.status = 'dismissed' AND f.suppressed_until > now())));
    v_run := (v_runs ->> pg.data_space_id::text)::uuid;
    IF v_run IS NULL THEN
      v_run := gen_random_uuid();
      INSERT INTO context.knowledge_run (id, org_id, data_space_id, kind, trigger, trigger_event,
                                         source_kind, source_ref, status, started_at, finished_at)
      VALUES (v_run, pg.org_id, pg.data_space_id, 'lint', 'event',
              'catalog.dataset_version.certification_revoked', 'dataset_version',
              p_dataset_version_id, 'completed', now(), now());
      v_runs := v_runs || jsonb_build_object(pg.data_space_id::text, v_run);
    END IF;
    INSERT INTO context.knowledge_lint_finding (id, org_id, run_id, data_space_id, page_id, kind,
                                                severity, fingerprint, detail)
    VALUES (gen_random_uuid(), pg.org_id, v_run, pg.data_space_id, pg.id, 'source_decertified',
            'high', v_fingerprint, jsonb_build_object('dataset_version_id', p_dataset_version_id));
    UPDATE context.knowledge_run SET findings_count = findings_count + 1 WHERE id = v_run;
  END LOOP;
  RETURN n;
END $$;

-- ─── data products ───────────────────────────────────────────────────────────────────────────────

-- covers: context.data_product#0 (no DDL: org_isolation is generated; Data Space access is checked by the Context service as for the other context tables)
-- covers: context.data_product_member#0 (no DDL: org_isolation is generated)

-- covers: context.data_product#1
-- Activation needs a certified metric, a primary dataset with a freshness commitment and datasets
-- that can be read as of a date (BR-175); svc_context reads catalog through catalog_read. Once out
-- of draft the code is stable, and a change of commitment on an active product is a new version.
CREATE FUNCTION context.data_product_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.code IS DISTINCT FROM OLD.code AND OLD.status <> 'draft' THEN
    RAISE EXCEPTION 'Không đổi code của data product khi đã rời draft (BR-175)' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'active' AND OLD.status <> 'active' THEN
    IF NOT EXISTS (
         SELECT 1 FROM context.data_product_member m
           JOIN context.metric mt ON mt.id = m.member_ref
           JOIN context.metric_version mv ON mv.metric_id = mt.id AND mv.version = mt.current_version
          WHERE m.data_product_id = NEW.id AND m.member_kind = 'metric' AND mv.status = 'certified') THEN
      RAISE EXCEPTION 'Kích hoạt data product cần ít nhất một chỉ số có phiên bản hiện hành đã chứng nhận (BR-175)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NOT EXISTS (
         SELECT 1 FROM context.data_product_member m
          WHERE m.data_product_id = NEW.id AND m.member_kind = 'dataset' AND m.role = 'primary'
            AND m.sla_kind <> 'none') THEN
      RAISE EXCEPTION 'Kích hoạt data product cần một dataset primary có cam kết độ tươi (BR-175)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
         SELECT 1 FROM context.data_product_member m
           LEFT JOIN catalog.dataset d ON d.id = m.member_ref
          WHERE m.data_product_id = NEW.id AND m.member_kind = 'dataset'
            AND NOT coalesce(d.history_kind <> 'none', false)
            AND NOT EXISTS (SELECT 1 FROM catalog.dataset_version dv WHERE dv.dataset_id = m.member_ref)) THEN
      RAISE EXCEPTION 'Mọi dataset của data product phải truy được theo thời điểm (BR-175)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  IF OLD.status = 'active' AND NEW.version = OLD.version
     AND (NEW.period_from, NEW.period_to, NEW.scope_entities, NEW.freshness_statement)
         IS DISTINCT FROM (OLD.period_from, OLD.period_to, OLD.scope_entities, OLD.freshness_statement) THEN
    NEW.version := OLD.version + 1;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER data_product_guard BEFORE UPDATE ON context.data_product
  FOR EACH ROW EXECUTE FUNCTION context.data_product_guard();

-- Agent runtime refreshes its scope catalogue from this event (cache at most 60 s).
CREATE FUNCTION context.data_product_changed()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO context.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
  VALUES (gen_random_uuid(), NEW.org_id, 'data_product', NEW.id, 'context.data_product.changed',
          jsonb_build_object('data_product_id', NEW.id, 'data_space_id', NEW.data_space_id,
                             'code', NEW.code, 'version', NEW.version, 'status', NEW.status,
                             'classification', NEW.classification));
  RETURN NULL;
END $$;
CREATE TRIGGER data_product_changed AFTER INSERT OR UPDATE ON context.data_product
  FOR EACH ROW EXECUTE FUNCTION context.data_product_changed();

-- A product's classification is the highest of its members' (BR-180 routes models on it). When it
-- does not change, the member change is still announced so the scope catalogue follows.
CREATE FUNCTION context.data_product_member_classify()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v_product uuid := CASE WHEN TG_OP = 'DELETE' THEN OLD.data_product_id ELSE NEW.data_product_id END;
  v_class text;
BEGIN
  SELECT coalesce((array_agg(classification ORDER BY context.classification_rank(classification) DESC))[1], 'internal')
    INTO v_class FROM context.data_product_member WHERE data_product_id = v_product;
  UPDATE context.data_product SET classification = v_class, updated_at = now()
   WHERE id = v_product AND classification IS DISTINCT FROM v_class;
  IF NOT FOUND THEN
    INSERT INTO context.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    SELECT gen_random_uuid(), dp.org_id, 'data_product', dp.id, 'context.data_product.changed',
           jsonb_build_object('data_product_id', dp.id, 'data_space_id', dp.data_space_id,
                              'code', dp.code, 'version', dp.version, 'status', dp.status,
                              'classification', dp.classification)
      FROM context.data_product dp WHERE dp.id = v_product;
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER data_product_member_classify
  AFTER INSERT OR DELETE OR UPDATE OF classification, data_product_id, member_kind, member_ref, role
  ON context.data_product_member
  FOR EACH ROW EXECUTE FUNCTION context.data_product_member_classify();

-- Called on context.metric_version.superseded (no certified version left) or
-- catalog.dataset_version.certification_revoked (member_ref = the dataset id): active products
-- built on that primary member are suspended. Returns the number of products.
CREATE FUNCTION context.data_product_on_member_decertified(p_member_kind text, p_member_ref uuid)
RETURNS integer LANGUAGE plpgsql AS $$
DECLARE n integer;
BEGIN
  UPDATE context.data_product dp
     SET status = 'suspended', updated_at = now(),
         suspended_reason = CASE p_member_kind WHEN 'metric' THEN 'metric_decertified'
                                               WHEN 'dataset' THEN 'dataset_decertified'
                                               ELSE p_member_kind || '_decertified' END
   WHERE dp.status = 'active'
     AND EXISTS (SELECT 1 FROM context.data_product_member m
                  WHERE m.data_product_id = dp.id AND m.role = 'primary'
                    AND m.member_kind = p_member_kind AND m.member_ref = p_member_ref);
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;

-- covers: context.data_product_member#1
-- A retired product takes no member changes; an active one only loses reference members: dropping
-- a primary one changes the commitment and goes through a new version (UC-07-13 6a).
CREATE FUNCTION context.data_product_member_guard()
RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE v_status text;
BEGIN
  IF TG_OP = 'DELETE' THEN
    SELECT status INTO v_status FROM context.data_product WHERE id = OLD.data_product_id;
    IF v_status = 'active' AND OLD.role = 'primary' THEN
      RAISE EXCEPTION 'Data product đang active chỉ gỡ được thành phần reference; gỡ thành phần primary phải qua phiên bản mới (UC-07-13)'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN OLD;
  END IF;
  SELECT status INTO v_status FROM context.data_product WHERE id = NEW.data_product_id;
  IF v_status = 'retired' THEN
    RAISE EXCEPTION 'Data product đã ngừng (retired) không nhận thay đổi thành phần (BR-175)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER data_product_member_guard BEFORE INSERT OR UPDATE OR DELETE ON context.data_product_member
  FOR EACH ROW EXECUTE FUNCTION context.data_product_member_guard();

-- Cron (5 fields, local time) matching for the freshness schedule: '*', 'a', 'a-b', lists and '/n'.
CREATE FUNCTION context.cron_field_matches(field text, val integer, field_min integer, field_max integer)
RETURNS boolean LANGUAGE plpgsql IMMUTABLE AS $$
DECLARE
  part text; range_part text; step integer; lo integer; hi integer;
BEGIN
  FOREACH part IN ARRAY string_to_array(field, ',') LOOP
    step := 1;
    range_part := part;
    IF position('/' IN part) > 0 THEN
      step := split_part(part, '/', 2)::integer;
      range_part := split_part(part, '/', 1);
    END IF;
    IF range_part = '*' THEN
      lo := field_min; hi := field_max;
    ELSIF position('-' IN range_part) > 0 THEN
      lo := split_part(range_part, '-', 1)::integer; hi := split_part(range_part, '-', 2)::integer;
    ELSE
      lo := range_part::integer;
      hi := CASE WHEN position('/' IN part) > 0 THEN field_max ELSE lo END;
    END IF;
    IF val BETWEEN lo AND hi AND (val - lo) % step = 0 THEN
      RETURN true;
    END IF;
  END LOOP;
  RETURN false;
END $$;

-- Day part of a cron: day of month and month, and day of week (0 or 7 = Sunday); when both day
-- fields are restricted either one matching is enough, as in cron.
CREATE FUNCTION context.cron_day_matches(fields text[], d date)
RETURNS boolean LANGUAGE sql IMMUTABLE AS $$
  SELECT context.cron_field_matches(fields[4], extract(month FROM d)::integer, 1, 12)
     AND CASE
           WHEN fields[3] <> '*' AND fields[5] <> '*' THEN
             context.cron_field_matches(fields[3], extract(day FROM d)::integer, 1, 31)
             OR context.cron_field_matches(fields[5], extract(dow FROM d)::integer, 0, 7)
             OR (extract(dow FROM d) = 0 AND context.cron_field_matches(fields[5], 7, 0, 7))
           ELSE
             context.cron_field_matches(fields[3], extract(day FROM d)::integer, 1, 31)
             AND (context.cron_field_matches(fields[5], extract(dow FROM d)::integer, 0, 7)
                  OR (extract(dow FROM d) = 0 AND context.cron_field_matches(fields[5], 7, 0, 7)))
         END
$$;

-- A deadline on a non-working day of the work calendar moves to the next working day, same local
-- time. svc_context reads org.work_calendar* through org_read.
CREATE FUNCTION context.data_product_shift_deadline(p_deadline timestamptz, p_calendar_id uuid, p_timezone text)
RETURNS timestamptz LANGUAGE plpgsql STABLE AS $$
DECLARE
  v_local timestamp := p_deadline AT TIME ZONE p_timezone;
  v_day date := (p_deadline AT TIME ZONE p_timezone)::date;
  v_weekend smallint[];
  v_kind text;
BEGIN
  IF p_calendar_id IS NULL THEN
    RETURN p_deadline;
  END IF;
  SELECT weekend_days INTO v_weekend FROM org.work_calendar WHERE id = p_calendar_id;
  FOR i IN 0 .. 60 LOOP
    SELECT kind INTO v_kind FROM org.work_calendar_day WHERE calendar_id = p_calendar_id AND day = v_day;
    EXIT WHEN v_kind = 'working_override'
           OR (v_kind IS NULL AND NOT coalesce(extract(isodow FROM v_day)::smallint = ANY (v_weekend), false));
    v_day := v_day + 1;
  END LOOP;
  RETURN (v_day + v_local::time) AT TIME ZONE p_timezone;
END $$;

-- SECURITY DEFINER: svc_context has no monitor_read (D5.2) but the schedule of a member may follow
-- an external event calendar (P08). It returns only the event times of the caller's organisation.
CREATE FUNCTION context.data_product_calendar_events(p_data_space_id uuid, p_code text)
RETURNS SETOF timestamptz LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT coalesce(e.occurred_at, e.expected_at)
    FROM monitor.external_calendar c
    JOIN monitor.external_event e ON e.calendar_id = c.id AND e.org_id = c.org_id
   WHERE c.org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
     AND c.data_space_id = p_data_space_id AND c.code = p_code AND e.status <> 'cancelled'
$$;
REVOKE EXECUTE ON FUNCTION context.data_product_calendar_events(uuid, text) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION context.data_product_calendar_events(uuid, text) TO svc_context;

-- The scheduled deadline of a member closest to p_bound: the latest one not after it, or with
-- p_next the earliest one after it (both already moved off non-working days). With an event
-- calendar the days come from its events and the cron only gives the time of day.
CREATE FUNCTION context.data_product_sla_deadline(p_member_id uuid, p_bound timestamptz, p_next boolean)
RETURNS timestamptz LANGUAGE plpgsql STABLE AS $$
DECLARE
  m record;
  f text[];
  v_tz text;
  v_day date;
  v_raw timestamptz;
  v_shifted timestamptz;
  v_best timestamptz;
  v_hours integer[];
  v_minutes integer[];
  h integer;
  mi integer;
BEGIN
  SELECT mb.*, dp.data_space_id INTO m
    FROM context.data_product_member mb JOIN context.data_product dp ON dp.id = mb.data_product_id
   WHERE mb.id = p_member_id;
  IF NOT FOUND OR m.sla_kind <> 'schedule' THEN
    RETURN NULL;
  END IF;
  v_tz := m.sla_timezone;
  IF m.sla_cron IS NOT NULL THEN
    f := regexp_split_to_array(btrim(m.sla_cron), '\s+');
    SELECT array_agg(x ORDER BY x) INTO v_hours FROM generate_series(0, 23) x
     WHERE context.cron_field_matches(f[2], x, 0, 23);
    SELECT array_agg(x ORDER BY x) INTO v_minutes FROM generate_series(0, 59) x
     WHERE context.cron_field_matches(f[1], x, 0, 59);
  END IF;

  IF m.sla_event_calendar_code IS NOT NULL THEN
    FOR v_raw IN SELECT * FROM context.data_product_calendar_events(m.data_space_id, m.sla_event_calendar_code) LOOP
      IF f IS NOT NULL THEN
        v_raw := (((v_raw AT TIME ZONE v_tz)::date) + make_time(v_hours[1], v_minutes[1], 0)) AT TIME ZONE v_tz;
      END IF;
      v_shifted := context.data_product_shift_deadline(v_raw, m.sla_work_calendar_id, v_tz);
      IF NOT p_next AND v_shifted <= p_bound AND (v_best IS NULL OR v_shifted > v_best) THEN
        v_best := v_shifted;
      ELSIF p_next AND v_shifted > p_bound AND (v_best IS NULL OR v_shifted < v_best) THEN
        v_best := v_shifted;
      END IF;
    END LOOP;
    RETURN v_best;
  END IF;

  IF f IS NULL OR v_hours IS NULL OR v_minutes IS NULL THEN
    RETURN NULL;
  END IF;
  -- Shifting is monotone, so scanning raw deadlines in order gives the closest shifted one first.
  -- A year of days covers any five-field cron; the forward scan starts a little early because a
  -- deadline before the bound can be shifted past it.
  IF NOT p_next THEN
    v_day := (p_bound AT TIME ZONE v_tz)::date;
    FOR i IN 0 .. 400 LOOP
      IF context.cron_day_matches(f, v_day) THEN
        FOR hi IN REVERSE array_length(v_hours, 1) .. 1 LOOP
          FOR mj IN REVERSE array_length(v_minutes, 1) .. 1 LOOP
            v_raw := (v_day + make_time(v_hours[hi], v_minutes[mj], 0)) AT TIME ZONE v_tz;
            CONTINUE WHEN v_raw > p_bound;
            v_shifted := context.data_product_shift_deadline(v_raw, m.sla_work_calendar_id, v_tz);
            IF v_shifted <= p_bound THEN
              RETURN v_shifted;
            END IF;
          END LOOP;
        END LOOP;
      END IF;
      v_day := v_day - 1;
    END LOOP;
  ELSE
    v_day := (p_bound AT TIME ZONE v_tz)::date - 31;
    FOR i IN 0 .. 431 LOOP
      IF context.cron_day_matches(f, v_day) THEN
        FOREACH h IN ARRAY v_hours LOOP
          FOREACH mi IN ARRAY v_minutes LOOP
            v_raw := (v_day + make_time(h, mi, 0)) AT TIME ZONE v_tz;
            v_shifted := context.data_product_shift_deadline(v_raw, m.sla_work_calendar_id, v_tz);
            IF v_shifted > p_bound THEN
              RETURN v_shifted;
            END IF;
          END LOOP;
        END LOOP;
      END IF;
      v_day := v_day + 1;
    END LOOP;
  END IF;
  RETURN NULL;
END $$;

-- Freshness of one member at p_at (BR-178), run after each load, every 5 minutes for members whose
-- next_due_at has passed and by the freshness.check tool. Returns the new freshness_state.
CREATE FUNCTION context.data_product_freshness_refresh(p_member_id uuid, p_at timestamptz DEFAULT now())
RETURNS text LANGUAGE plpgsql AS $$
DECLARE
  m context.data_product_member%ROWTYPE;
  v_due timestamptz;
  v_expected timestamptz;
  v_next timestamptz;
  v_state text;
BEGIN
  SELECT * INTO m FROM context.data_product_member WHERE id = p_member_id FOR UPDATE;
  IF NOT FOUND THEN
    RETURN NULL;
  END IF;
  IF m.sla_kind = 'schedule' THEN
    v_due := context.data_product_sla_deadline(m.id, p_at - m.sla_grace, false);
    v_next := context.data_product_sla_deadline(m.id, p_at - m.sla_grace, true);
    v_expected := ((v_due AT TIME ZONE m.sla_timezone)::date)::timestamp AT TIME ZONE m.sla_timezone;
    v_state := CASE WHEN v_due IS NULL THEN 'unknown'
                    WHEN m.last_as_of IS NULL THEN 'missing'
                    WHEN m.last_as_of >= v_expected THEN 'fresh'
                    ELSE 'overdue' END;
  ELSIF m.sla_kind = 'max_age' THEN
    v_expected := p_at - m.sla_max_age - m.sla_grace;
    v_next := m.last_as_of + m.sla_max_age + m.sla_grace;
    v_state := CASE WHEN m.last_as_of IS NULL THEN 'missing'
                    WHEN p_at - m.last_as_of <= m.sla_max_age + m.sla_grace THEN 'fresh'
                    ELSE 'overdue' END;
  ELSE
    v_state := 'unknown';
  END IF;
  UPDATE context.data_product_member
     SET expected_as_of = v_expected, next_due_at = v_next, freshness_state = v_state,
         last_checked_at = p_at
   WHERE id = m.id;
  IF v_state IS DISTINCT FROM m.freshness_state THEN
    INSERT INTO context.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), m.org_id, 'data_product_member', m.id, 'context.data_product.freshness_changed',
            jsonb_build_object('data_product_id', m.data_product_id, 'member_id', m.id,
                               'from', m.freshness_state, 'to', v_state,
                               'expected_as_of', v_expected, 'last_as_of', m.last_as_of,
                               'checked_at', p_at));
  END IF;
  RETURN v_state;
END $$;
