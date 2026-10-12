-- routines/acquire.sql: what D4.22 (tab 3b) writes in words for the acquire schema (P14 crawl
-- sources, legal review, schema drift, reference entities, consolidation): prose CHECKs and
-- indexes, the "Bổ sung 10/10/2026" amendments, the guard triggers, and the D5.2 append-only
-- exception on crawl_page. Runs as da_owner after the generated tables, FKs, RLS and grants.


-- ═══ crawl_source ═══

-- covers: acquire.crawl_source#0
-- UC-14-04: the review gate may skip human review only after the four most recent sampled runs
-- of the source all reached 98% on the random sample. Runs without a sample (failed, still
-- fetching) do not count as a lượt mẫu.
CREATE FUNCTION acquire.crawl_source_review_gate_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  good integer;
BEGIN
  IF NEW.review_gate = 'auto' AND (TG_OP = 'INSERT' OR OLD.review_gate IS DISTINCT FROM 'auto') THEN
    SELECT count(*) FILTER (WHERE r.sample_accuracy >= 0.98) INTO good
    FROM (
      SELECT sample_accuracy FROM acquire.crawl_run
      WHERE source_id = NEW.id AND sample_accuracy IS NOT NULL
      ORDER BY coalesce(finished_at, started_at, created_at) DESC
      LIMIT 4
    ) r;
    IF good < 4 THEN
      RAISE EXCEPTION 'Cổng tự động chỉ bật khi 4 lượt gần nhất có độ chính xác mẫu ≥ 98%% (UC-14-04)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER crawl_source_review_gate_guard
  BEFORE INSERT OR UPDATE OF review_gate ON acquire.crawl_source
  FOR EACH ROW EXECUTE FUNCTION acquire.crawl_source_review_gate_guard();

-- covers: acquire.crawl_source#1
-- BR-174: an approval is complete (reviewer, time, terms page and its hash, the four checklist
-- items, at least one piece of evidence) and is due for review within 184 days.
ALTER TABLE acquire.crawl_source ADD CONSTRAINT crawl_source_legal_approval_check CHECK (
  legal_status <> 'approved' OR (
    legal_reviewed_by IS NOT NULL AND legal_reviewed_at IS NOT NULL
    AND terms_url IS NOT NULL AND terms_sha256 IS NOT NULL
    AND legal_review_due_at > legal_reviewed_at
    AND legal_review_due_at <= legal_reviewed_at + interval '184 days'
    AND legal_checklist ?& ARRAY['terms','robots','copyright','personal_data']
    AND jsonb_array_length(legal_evidence) >= 1
  )
);

-- covers: acquire.crawl_source#2
ALTER TABLE acquire.crawl_source ADD CONSTRAINT crawl_source_terms_sha256_check
  CHECK (terms_sha256 IS NULL OR terms_sha256 ~ '^[0-9a-f]{64}$');
ALTER TABLE acquire.crawl_source ADD CONSTRAINT crawl_source_terms_observed_sha256_check
  CHECK (terms_observed_sha256 IS NULL OR terms_observed_sha256 ~ '^[0-9a-f]{64}$');

-- covers: acquire.crawl_source#3
-- BR-174: changed terms void the approval; a source that was active and loses its approval
-- (changed terms, or the review date has passed) pauses itself instead of failing the write of
-- the daily terms check; turning a source on without a valid approval is refused.
CREATE FUNCTION acquire.crawl_source_legal_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.legal_status = 'approved' AND NEW.terms_observed_sha256 IS NOT NULL
     AND NEW.terms_observed_sha256 IS DISTINCT FROM NEW.terms_sha256 THEN
    NEW.legal_status := 'expired';
  END IF;
  IF NEW.status = 'active'
     AND (NEW.legal_status <> 'approved' OR NEW.legal_review_due_at <= now()) THEN
    IF TG_OP = 'UPDATE' AND OLD.status = 'active' THEN
      NEW.status := 'paused';
    ELSE
      RAISE EXCEPTION 'Nguồn chỉ bật khi đã duyệt pháp lý và chưa quá hạn xem lại (BR-174)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER crawl_source_legal_guard
  BEFORE INSERT OR UPDATE ON acquire.crawl_source
  FOR EACH ROW EXECUTE FUNCTION acquire.crawl_source_legal_guard();

-- Same rule seen from the crawler (tab 3 D5.4 "tải khi duyệt hết hiệu lực"): no page is stored
-- for a source whose legal approval is not in force, even if its run started before it lapsed.
CREATE FUNCTION acquire.crawl_page_legal_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (
    SELECT 1 FROM acquire.crawl_source s
    WHERE s.id = NEW.source_id AND s.legal_status = 'approved' AND s.legal_review_due_at > now()
  ) THEN
    RAISE EXCEPTION 'Không tải trang khi duyệt pháp lý của nguồn hết hiệu lực (BR-174)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER crawl_page_legal_guard
  BEFORE INSERT ON acquire.crawl_page
  FOR EACH ROW EXECUTE FUNCTION acquire.crawl_page_legal_guard();

-- D5.2 (BR-150): raw pages are append-only. The generated grants already give svc_acquire only
-- SELECT, INSERT on this "Bản ghi bất biến" table; the revoke states the exception explicitly.
REVOKE UPDATE, DELETE ON acquire.crawl_page FROM svc_acquire;


-- ═══ crawl_run ═══

-- covers: acquire.crawl_run#0
-- covers: acquire.schema_drift#1
-- crawl_run_publish_guard. BR-152: only an approved schema version (active, or superseded after
-- it was active) may publish. BR-153: nothing pending or flagged may remain in the run.
-- BR-168: an open semantic drift on the source blocks publishing every run of that source,
-- including later ones, until a human resolves it.
CREATE FUNCTION acquire.crawl_run_publish_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.status = 'published' AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'published') THEN
    IF NOT EXISTS (
      SELECT 1 FROM acquire.crawl_schema_version v
      WHERE v.id = NEW.schema_version_id AND v.status IN ('active','superseded')
    ) THEN
      RAISE EXCEPTION 'Chỉ phát hành lượt trích bằng phiên bản schema active hoặc superseded (BR-152)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
      SELECT 1 FROM acquire.extracted_record r
      WHERE r.run_id = NEW.id AND r.status IN ('pending','flagged')
    ) THEN
      RAISE EXCEPTION 'Lượt còn bản ghi pending hoặc flagged, chưa phát hành được (BR-153)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF EXISTS (
      SELECT 1 FROM acquire.schema_drift d
      WHERE d.source_id = NEW.source_id AND d.drift_kind = 'semantic' AND d.resolution IS NULL
    ) THEN
      RAISE EXCEPTION 'Nguồn còn lệch ngầm chưa đóng, không phát hành lượt nào của nguồn (BR-168)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER crawl_run_publish_guard
  BEFORE INSERT OR UPDATE OF status ON acquire.crawl_run
  FOR EACH ROW EXECUTE FUNCTION acquire.crawl_run_publish_guard();


-- ═══ schema_drift ═══

-- covers: acquire.schema_drift#0
-- BR-168 amendment: semantic drift (same structure, changed meaning) quarantines the run, is
-- always major, carries its evidence, and only a named person closes it, possibly as a false
-- alarm. The generated action / resolution CHECKs predate the amendment, so they are replaced.
DO $$
DECLARE
  c record;
BEGIN
  FOR c IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'acquire.schema_drift'::regclass AND contype = 'c'
      AND (pg_get_constraintdef(oid) LIKE '%action = ANY%'
           OR pg_get_constraintdef(oid) LIKE '%resolution = ANY%')
  LOOP
    EXECUTE format('ALTER TABLE acquire.schema_drift DROP CONSTRAINT %I', c.conname);
  END LOOP;
END
$$;
ALTER TABLE acquire.schema_drift ADD CONSTRAINT schema_drift_action_check
  CHECK (action IN ('auto_remap','revert','paused','quarantine'));
ALTER TABLE acquire.schema_drift ADD CONSTRAINT schema_drift_resolution_check
  CHECK (resolution IS NULL OR resolution IN ('approved_new','manual_fix','reverted','false_alarm'));
ALTER TABLE acquire.schema_drift ADD CONSTRAINT schema_drift_drift_kind_check
  CHECK (drift_kind IN ('structural','semantic'));
ALTER TABLE acquire.schema_drift ADD CONSTRAINT schema_drift_semantic_action_check
  CHECK ((drift_kind = 'semantic') = (action = 'quarantine'));
ALTER TABLE acquire.schema_drift ADD CONSTRAINT schema_drift_semantic_severity_check
  CHECK (drift_kind <> 'semantic' OR severity = 'major');
ALTER TABLE acquire.schema_drift ADD CONSTRAINT schema_drift_semantic_evidence_check
  CHECK (drift_kind <> 'semantic' OR (signals ? 'affected_fields' AND signals ? 'evidence'));
ALTER TABLE acquire.schema_drift ADD CONSTRAINT schema_drift_false_alarm_check
  CHECK (resolution IS DISTINCT FROM 'false_alarm' OR drift_kind = 'semantic');
ALTER TABLE acquire.schema_drift ADD CONSTRAINT schema_drift_semantic_resolver_check
  CHECK (drift_kind <> 'semantic' OR resolution IS NULL OR resolved_by IS NOT NULL);


-- ═══ entity_link ═══

-- covers: acquire.entity_link#0
-- BR-169 amendment: matching by registry identifier joins the methods; a confirmed link always
-- points at the reference catalogue, and a fuzzy match confirms itself only from 0.95. The FK to
-- reference_entity is already generated (entity_link_reference_entity_id_fkey).
DO $$
DECLARE
  c record;
BEGIN
  FOR c IN
    SELECT conname FROM pg_constraint
    WHERE conrelid = 'acquire.entity_link'::regclass AND contype = 'c'
      AND pg_get_constraintdef(oid) LIKE '%method = ANY%'
  LOOP
    EXECUTE format('ALTER TABLE acquire.entity_link DROP CONSTRAINT %I', c.conname);
  END LOOP;
END
$$;
ALTER TABLE acquire.entity_link ADD CONSTRAINT entity_link_method_check
  CHECK (method IN ('identifier','exact','fuzzy','human'));
ALTER TABLE acquire.entity_link ADD CONSTRAINT entity_link_confirmed_reference_check
  CHECK (status <> 'confirmed' OR reference_entity_id IS NOT NULL);
ALTER TABLE acquire.entity_link ADD CONSTRAINT entity_link_identifier_reference_check
  CHECK (method <> 'identifier' OR reference_entity_id IS NOT NULL);
ALTER TABLE acquire.entity_link ADD CONSTRAINT entity_link_fuzzy_score_check
  CHECK (NOT (method = 'fuzzy' AND status = 'confirmed') OR match_score >= 0.95);

-- covers: acquire.entity_link#1
-- entity_link_reference_sync (BR-169): the canonical key is the catalogue's, never typed by the
-- matcher, and a link cannot tie a source key to an entity of another type.
CREATE FUNCTION acquire.entity_link_reference_sync() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  ref record;
BEGIN
  IF NEW.reference_entity_id IS NOT NULL THEN
    SELECT entity_type, canonical_key INTO ref
    FROM acquire.reference_entity WHERE id = NEW.reference_entity_id;
    IF FOUND THEN
      IF ref.entity_type <> NEW.entity_type THEN
        RAISE EXCEPTION 'Loại thực thể của liên kết khác loại của thực thể chuẩn (BR-169)'
          USING ERRCODE = 'check_violation';
      END IF;
      NEW.canonical_key := ref.canonical_key;
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER entity_link_reference_sync
  BEFORE INSERT OR UPDATE OF reference_entity_id, entity_type, canonical_key ON acquire.entity_link
  FOR EACH ROW EXECUTE FUNCTION acquire.entity_link_reference_sync();


-- ═══ reference_entity ═══

-- covers: acquire.reference_entity#0
-- BR-169: each registry identifier names one entity per organisation. The generator read
-- "duy nhất (org_id, sbv_code), (org_id, swift_bic), (org_id, tax_code) khi khác null" as one
-- unique and two plain indexes; sbv_code's unique index already ignores NULLs, the other two are
-- replaced by partial unique ones. GIN indexes serve the fallback name and domain matching.
DROP INDEX acquire.reference_entity_org_id_swift_bic_idx;
DROP INDEX acquire.reference_entity_org_id_tax_code_idx;
CREATE UNIQUE INDEX reference_entity_org_id_swift_bic_key ON acquire.reference_entity (org_id, swift_bic)
  WHERE swift_bic IS NOT NULL;
CREATE UNIQUE INDEX reference_entity_org_id_tax_code_key ON acquire.reference_entity (org_id, tax_code)
  WHERE tax_code IS NOT NULL;
CREATE INDEX reference_entity_official_domains_idx ON acquire.reference_entity USING gin (official_domains);
CREATE INDEX reference_entity_short_names_idx ON acquire.reference_entity USING gin (short_names);

-- covers: acquire.reference_entity#1 (no DDL: no catalogue management API yet, open point tab 6 O4.4)


-- ═══ consolidation_spec ═══

-- covers: acquire.consolidation_spec#0
-- BR-156: last_edited_by is the person who last changed what the spec computes; it becomes the
-- created_by of the consolidated dataset version, which that person may then not certify.
-- updated_by is not used for this because activation, renames, owner changes and system jobs
-- also move it. Direct writes to the two columns are ignored.
CREATE FUNCTION acquire.consolidation_spec_last_edit() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' THEN
    NEW.last_edited_by := NEW.created_by;
    NEW.last_edited_at := now();
  ELSIF (NEW.entity_type, NEW.grain, NEW.source_ids, NEW.normalization, NEW.source_priority,
         NEW.aggregates, NEW.summary_template, NEW.allow_interpolation, NEW.target_dataset_id)
        IS DISTINCT FROM
        (OLD.entity_type, OLD.grain, OLD.source_ids, OLD.normalization, OLD.source_priority,
         OLD.aggregates, OLD.summary_template, OLD.allow_interpolation, OLD.target_dataset_id) THEN
    NEW.last_edited_by := NEW.updated_by;
    NEW.last_edited_at := now();
  ELSE
    NEW.last_edited_by := OLD.last_edited_by;
    NEW.last_edited_at := OLD.last_edited_at;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER consolidation_spec_last_edit
  BEFORE INSERT OR UPDATE ON acquire.consolidation_spec
  FOR EACH ROW EXECUTE FUNCTION acquire.consolidation_spec_last_edit();


-- ═══ consolidation_run ═══

-- covers: acquire.consolidation_run#0
-- BR-153: only published crawl runs feed consolidation. BR-156: the spec editor is taken from
-- the spec when the run starts, whatever the caller sends, and never changes afterwards.
CREATE FUNCTION acquire.consolidation_run_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'INSERT' OR NEW.input_run_ids IS DISTINCT FROM OLD.input_run_ids THEN
    IF EXISTS (
      SELECT 1 FROM unnest(NEW.input_run_ids) AS i(run_id)
      WHERE NOT EXISTS (
        SELECT 1 FROM acquire.crawl_run r WHERE r.id = i.run_id AND r.status = 'published'
      )
    ) THEN
      RAISE EXCEPTION 'Hợp nhất chỉ nhận lượt thu đã phát hành (BR-153)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  IF TG_OP = 'INSERT' THEN
    NEW.spec_editor_id := (SELECT last_edited_by FROM acquire.consolidation_spec WHERE id = NEW.spec_id);
  ELSIF NEW.spec_editor_id IS DISTINCT FROM OLD.spec_editor_id THEN
    RAISE EXCEPTION 'spec_editor_id không sửa được (BR-156)' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER consolidation_run_guard
  BEFORE INSERT OR UPDATE ON acquire.consolidation_run
  FOR EACH ROW EXECUTE FUNCTION acquire.consolidation_run_guard();
