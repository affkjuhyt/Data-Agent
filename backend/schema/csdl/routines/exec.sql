-- Schema exec (P16, the executive vault, D4.24): row-level security beyond org_isolation, column
-- grants, triggers and the two SECURITY DEFINER entry points of the Exec vault service.
--
-- GUC contract (set by the Gateway / the worker per transaction, read with
-- NULLIF(current_setting('app.<name>', true), '') so an unset setting denies, never errors):
--   app.org_id         uuid  organisation (generated org_isolation, every table)
--   app.user_id        uuid  the vault owner svc_exec acts for; owner_only compares owner_id to it
--   app.exec_workflow  'on'  set by svc_exec_worker (scoring workflow, Evidence agent on Temporal);
--                            without it the worker sees and writes nothing
--   app.exec_admin     'on'  set by svc_exec for AC05/AC06 requests (UC-16-06); opens exec.handover
--                            and exec.vault_admin_overview(), never a column of ciphertext
--
-- Roles: svc_exec (Exec service, acts for the owner), svc_exec_worker (column grants on vault and
-- thesis, INSERT on the three append-only tables), exec_read (granted to svc_context, only
-- exec.publication). No administrative role reads exec (BR-189). Encryption on the device and in
-- OpenBao is the primary protection (ADR-46); these rules are the second layer.
-- Functions that must act beyond the caller's rows (shred, cross-row checks the worker cannot read)
-- are SECURITY DEFINER: they run as da_owner, which FORCE RLS still keeps inside org_isolation.

-- ─── grants (D5.2: svc_exec, svc_exec_worker, exec_read) ───

-- exec_read is granted to svc_context only for the source of hypothesis pages (BR-194); the
-- generator gives every <schema>_read SELECT on all tables of its schema.
REVOKE SELECT ON exec.vault, exec.vault_key_wrap, exec.thesis, exec.thesis_evidence,
  exec.thesis_score, exec.memory_item, exec.key_access_log, exec.handover, exec.handover_item
  FROM exec_read;
-- The worker is the schema's third role (D5.2); it starts with nothing.
GRANT USAGE ON SCHEMA exec TO svc_exec_worker;

-- ─── shared helpers ───

-- BR-189: a vault takes new content only while it can still be opened without the server: at least
-- two live key wraps, one of them offline (hardware key or paper recovery code). Invoker rights:
-- svc_exec sees only the owner's wraps, so another owner's vault reads as not unlockable.
CREATE FUNCTION exec.vault_unlockable(p_vault_id uuid) RETURNS boolean
LANGUAGE sql STABLE AS $$
  SELECT count(*) >= 2 AND bool_or(w.kind IN ('hardware_key', 'recovery_code'))
  FROM exec.vault_key_wrap w
  WHERE w.vault_id = p_vault_id AND w.revoked_at IS NULL
$$;

-- ─── exec.vault ───

-- covers: exec.vault#0
CREATE POLICY owner_only ON exec.vault AS RESTRICTIVE FOR ALL TO svc_exec
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
CREATE POLICY workflow_only ON exec.vault AS RESTRICTIVE FOR SELECT TO svc_exec_worker
  USING (NULLIF(current_setting('app.exec_workflow', true), '') = 'on');
GRANT SELECT (owner_id, status, box_public_key, transit_key_name, transit_key_version)
  ON exec.vault TO svc_exec_worker;
-- AC05/AC06 see five operational columns, no key column. RLS cannot narrow columns for one role,
-- so the table policy has no admin branch and the read goes through this function (UC-16-06).
CREATE FUNCTION exec.vault_admin_overview()
RETURNS TABLE (id uuid, owner_id uuid, status text, exit_policy text, sealed_read_until date)
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT v.id, v.owner_id, v.status, v.exit_policy, v.sealed_read_until
  FROM exec.vault v
  WHERE NULLIF(current_setting('app.exec_admin', true), '') = 'on'
    AND v.org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
$$;
REVOKE EXECUTE ON FUNCTION exec.vault_admin_overview() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION exec.vault_admin_overview() TO svc_exec;

-- covers: exec.vault#1
-- SECURITY DEFINER because shredding deletes the key wraps and svc_exec has no DELETE on them
-- (BR-195); a trigger function cannot be called directly, so this is the only path to that DELETE.
REVOKE DELETE ON exec.vault FROM svc_exec;
CREATE FUNCTION exec.vault_guard_update() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.box_public_key IS DISTINCT FROM OLD.box_public_key
     OR NEW.transit_key_name IS DISTINCT FROM OLD.transit_key_name THEN
    RAISE EXCEPTION 'Không đổi khoá công khai hay tên khoá Transit của kho (BR-195)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.status = 'shredded' THEN
    IF NEW.status <> 'shredded' OR NEW.box_private_key_ct IS DISTINCT FROM OLD.box_private_key_ct THEN
      RAISE EXCEPTION 'Kho đã huỷ khoá là trạng thái cuối, không mở lại (BR-195)'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status
     AND NOT (OLD.status = 'active' AND NEW.status IN ('sealed', 'shredded'))
     AND NOT (OLD.status = 'sealed' AND NEW.status = 'shredded') THEN
    RAISE EXCEPTION 'Kho chỉ chuyển active → sealed, shredded; sealed → shredded (BR-195)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'sealed' AND OLD.status <> 'sealed' THEN
    NEW.sealed_at := coalesce(NEW.sealed_at, now());
  END IF;
  IF NEW.status = 'shredded' THEN
    NEW.box_private_key_ct := '\x'::bytea;
    NEW.shredded_at := coalesce(NEW.shredded_at, now());
    DELETE FROM exec.vault_key_wrap WHERE vault_id = OLD.id;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER vault_guard_update BEFORE UPDATE ON exec.vault
  FOR EACH ROW EXECUTE FUNCTION exec.vault_guard_update();

-- ─── exec.vault_key_wrap ───

-- covers: exec.vault_key_wrap#0
CREATE POLICY owner_only ON exec.vault_key_wrap AS RESTRICTIVE FOR ALL TO svc_exec
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

-- covers: exec.vault_key_wrap#1
-- Replacing a key is a new row; the only DELETE is exec.vault_guard_update on shred.
REVOKE DELETE, UPDATE ON exec.vault_key_wrap FROM svc_exec;
GRANT UPDATE (last_used_at, revoked_at) ON exec.vault_key_wrap TO svc_exec;
CREATE FUNCTION exec.vault_key_wrap_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF (to_jsonb(NEW) - 'last_used_at' - 'revoked_at') IS DISTINCT FROM (to_jsonb(OLD) - 'last_used_at' - 'revoked_at') THEN
    RAISE EXCEPTION 'Khoá bọc chỉ đổi last_used_at, revoked_at; thay khoá là thêm dòng mới (BR-189)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.revoked_at IS NOT NULL AND NEW.revoked_at IS DISTINCT FROM OLD.revoked_at THEN
    RAISE EXCEPTION 'Khoá bọc đã thu hồi không khôi phục được (BR-189)'
      USING ERRCODE = 'check_violation';
  END IF;
  -- A revocation must leave two live wraps, one offline, unless the vault is being shredded
  -- (already shredded, or a shred handover approved or in progress).
  IF OLD.revoked_at IS NULL AND NEW.revoked_at IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM exec.vault v WHERE v.id = NEW.vault_id AND v.status = 'shredded')
     AND NOT EXISTS (SELECT 1 FROM exec.handover h WHERE h.vault_id = NEW.vault_id AND h.kind = 'shred'
                       AND h.status IN ('approved', 'in_progress'))
     AND NOT (SELECT count(*) >= 2 AND coalesce(bool_or(w.kind IN ('hardware_key', 'recovery_code')), false)
              FROM exec.vault_key_wrap w
              WHERE w.vault_id = NEW.vault_id AND w.revoked_at IS NULL AND w.id <> NEW.id) THEN
    RAISE EXCEPTION 'Thu hồi làm kho còn dưới 2 khoá bọc hiệu lực hoặc không còn khoá ngoại tuyến (BR-189)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER vault_key_wrap_guard_update BEFORE UPDATE ON exec.vault_key_wrap
  FOR EACH ROW EXECUTE FUNCTION exec.vault_key_wrap_guard_update();

-- ─── exec.thesis ───

-- covers: exec.thesis#0
-- Content is immutable after commit (BR-188): no updated_* columns, and only the workflow columns
-- are updatable.
REVOKE UPDATE ON exec.thesis FROM svc_exec;
GRANT UPDATE (status, evidence_state, evidence_refreshed_at, withdrawn_at, workflow_id)
  ON exec.thesis TO svc_exec;

-- covers: exec.thesis#1
CREATE POLICY owner_only ON exec.thesis AS RESTRICTIVE FOR ALL TO svc_exec
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
CREATE POLICY workflow_only ON exec.thesis AS RESTRICTIVE FOR ALL TO svc_exec_worker
  USING (NULLIF(current_setting('app.exec_workflow', true), '') = 'on')
  WITH CHECK (NULLIF(current_setting('app.exec_workflow', true), '') = 'on');
-- The worker decrypts the condition through OpenBao; it never gets content_ct or wrapped_dek.
GRANT SELECT (id, owner_id, vault_id, cond_ct, cond_key_version, due_at, status) ON exec.thesis
  TO svc_exec_worker;
GRANT UPDATE (status, evidence_state, evidence_refreshed_at) ON exec.thesis TO svc_exec_worker;

-- covers: exec.thesis#2
CREATE FUNCTION exec.thesis_guard_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- svc_exec sees only the owner's vault, so another owner's vault fails here too.
  IF NOT EXISTS (SELECT 1 FROM exec.vault v WHERE v.id = NEW.vault_id AND v.status = 'active'
                   AND v.owner_id = NEW.owner_id) THEN
    RAISE EXCEPTION 'Luận điểm cần kho active của chính chủ kho (BR-189)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NOT coalesce(exec.vault_unlockable(NEW.vault_id), false) THEN
    RAISE EXCEPTION 'Kho cần từ 2 khoá bọc còn hiệu lực, trong đó một khoá ngoại tuyến (BR-189)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.supersedes_id IS NOT NULL
     AND NOT EXISTS (SELECT 1 FROM exec.thesis t WHERE t.id = NEW.supersedes_id AND t.owner_id = NEW.owner_id) THEN
    RAISE EXCEPTION 'Luận điểm được thay phải cùng chủ kho (BR-188)'
      USING ERRCODE = 'check_violation';
  END IF;
  -- R-35: a whole UTC hour; date_trunc on timestamptz is not immutable, so not a CHECK.
  IF NEW.due_at <> date_trunc('hour', NEW.due_at AT TIME ZONE 'UTC') AT TIME ZONE 'UTC' THEN
    RAISE EXCEPTION 'Thời điểm chấm phải tròn giờ theo UTC (R-35)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER thesis_guard_insert BEFORE INSERT ON exec.thesis
  FOR EACH ROW EXECUTE FUNCTION exec.thesis_guard_insert();

-- covers: exec.thesis#3
REVOKE DELETE ON exec.thesis FROM svc_exec;
CREATE FUNCTION exec.thesis_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- The column grants already stop svc_exec and the worker; this also binds da_owner paths.
  IF NEW.content_ct IS DISTINCT FROM OLD.content_ct OR NEW.wrapped_dek IS DISTINCT FROM OLD.wrapped_dek
     OR NEW.cond_ct IS DISTINCT FROM OLD.cond_ct OR NEW.cond_key_version IS DISTINCT FROM OLD.cond_key_version
     OR NEW.due_at IS DISTINCT FROM OLD.due_at OR NEW.committed_at IS DISTINCT FROM OLD.committed_at
     OR NEW.owner_id IS DISTINCT FROM OLD.owner_id THEN
    RAISE EXCEPTION 'Nội dung, điều kiện và ngày chấm của luận điểm không sửa được (BR-188)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status THEN
    IF NOT (OLD.status = 'open' AND NEW.status IN ('scoring', 'invalid_condition', 'withdrawn'))
       AND NOT (OLD.status = 'scoring' AND NEW.status IN ('scored', 'unresolvable', 'open')) THEN
      RAISE EXCEPTION 'Luận điểm chỉ chuyển open → scoring, invalid_condition, withdrawn; scoring → scored, unresolvable, open (BR-188)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.status = 'withdrawn' THEN
      IF now() >= OLD.due_at THEN
        RAISE EXCEPTION 'Chỉ rút luận điểm trước thời điểm chấm (BR-188)'
          USING ERRCODE = 'check_violation';
      END IF;
      NEW.withdrawn_at := coalesce(NEW.withdrawn_at, now());
    END IF;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER thesis_guard_update BEFORE UPDATE ON exec.thesis
  FOR EACH ROW EXECUTE FUNCTION exec.thesis_guard_update();

-- ─── exec.thesis_evidence ───

-- covers: exec.thesis_evidence#0
REVOKE INSERT ON exec.thesis_evidence FROM svc_exec;
CREATE POLICY owner_only ON exec.thesis_evidence AS RESTRICTIVE FOR SELECT TO svc_exec
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
CREATE POLICY workflow_only ON exec.thesis_evidence AS RESTRICTIVE FOR INSERT TO svc_exec_worker
  WITH CHECK (NULLIF(current_setting('app.exec_workflow', true), '') = 'on');
-- created_at and recorded_at are left to their defaults: the batch check below tells this
-- transaction's rows from earlier batches by created_at = transaction_timestamp().
GRANT INSERT (id, org_id, thesis_id, owner_id, batch_no, side, item_kind, strength, freshness_ok,
  sealed_ct, agent_run_id) ON exec.thesis_evidence TO svc_exec_worker;
-- "No role has UPDATE, DELETE": refused by trigger so it binds the table owner and the DBA too.
CREATE FUNCTION exec.thesis_evidence_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Bằng chứng là bản ghi bất biến, không sửa, không xoá (BR-190)'
    USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER thesis_evidence_append_only BEFORE UPDATE OR DELETE ON exec.thesis_evidence
  FOR EACH ROW EXECUTE FUNCTION exec.thesis_evidence_append_only();

-- covers: exec.thesis_evidence#1
-- SECURITY DEFINER: the worker inserts but cannot read the batch it has to check.
CREATE FUNCTION exec.thesis_evidence_batch_check() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM exec.thesis t WHERE t.id = NEW.thesis_id AND t.status = 'open') THEN
    RAISE EXCEPTION 'Chỉ thêm bằng chứng cho luận điểm đang open (BR-190)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NOT EXISTS (SELECT 1 FROM exec.thesis_evidence e WHERE e.thesis_id = NEW.thesis_id
                   AND e.batch_no = NEW.batch_no AND e.side = 'support')
     OR NOT EXISTS (SELECT 1 FROM exec.thesis_evidence e WHERE e.thesis_id = NEW.thesis_id
                      AND e.batch_no = NEW.batch_no AND e.side = 'against') THEN
    RAISE EXCEPTION 'Lô bằng chứng cần cả phía support và phía against (BR-190)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.batch_no <> 1 + coalesce((SELECT max(e.batch_no) FROM exec.thesis_evidence e
                                    WHERE e.thesis_id = NEW.thesis_id
                                      AND e.created_at <> transaction_timestamp()), 0) THEN
    RAISE EXCEPTION 'Số lô bằng chứng phải bằng lô lớn nhất cộng 1 (BR-190)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NULL;
END $$;
CREATE CONSTRAINT TRIGGER thesis_evidence_batch_check AFTER INSERT ON exec.thesis_evidence
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION exec.thesis_evidence_batch_check();

-- covers: exec.thesis_evidence#2
-- SECURITY DEFINER for the same reason: the state is computed from rows the worker cannot read.
CREATE FUNCTION exec.thesis_evidence_refresh_state() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v_state text;
BEGIN
  SELECT CASE
           WHEN bool_or(e.side = 'support' AND e.item_kind = 'evidence')
                AND bool_or(e.side = 'against' AND e.item_kind = 'evidence') THEN 'two_sided'
           WHEN bool_or(e.item_kind = 'insufficient') THEN 'insufficient'
           ELSE 'one_sided'
         END
    INTO v_state
  FROM exec.thesis_evidence e
  WHERE e.thesis_id = NEW.thesis_id AND e.batch_no = NEW.batch_no;
  UPDATE exec.thesis SET evidence_state = v_state, evidence_refreshed_at = now()
  WHERE id = NEW.thesis_id;
  RETURN NULL;
END $$;
CREATE TRIGGER thesis_evidence_refresh_state AFTER INSERT ON exec.thesis_evidence
  FOR EACH ROW EXECUTE FUNCTION exec.thesis_evidence_refresh_state();

-- ─── exec.thesis_score ───

-- covers: exec.thesis_score#0
REVOKE INSERT ON exec.thesis_score FROM svc_exec;
CREATE POLICY owner_only ON exec.thesis_score AS RESTRICTIVE FOR SELECT TO svc_exec
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
CREATE POLICY workflow_only ON exec.thesis_score AS RESTRICTIVE FOR INSERT TO svc_exec_worker
  WITH CHECK (NULLIF(current_setting('app.exec_workflow', true), '') = 'on');
GRANT INSERT ON exec.thesis_score TO svc_exec_worker;
-- BR-191: append-only for every role, the table owner and the DBA included.
CREATE FUNCTION exec.thesis_score_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Điểm chấm chỉ ghi thêm, không sửa, không xoá (BR-191)'
    USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER thesis_score_append_only BEFORE UPDATE OR DELETE ON exec.thesis_score
  FOR EACH ROW EXECUTE FUNCTION exec.thesis_score_append_only();

-- covers: exec.thesis_score#1
-- SECURITY DEFINER: a restatement must see the final row, which the worker cannot read.
CREATE FUNCTION exec.thesis_score_guard_insert() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NEW.kind = 'final' AND NOT EXISTS (
       SELECT 1 FROM exec.thesis t WHERE t.id = NEW.thesis_id AND t.status = 'scoring'
         AND t.due_at = NEW.as_of) THEN
    RAISE EXCEPTION 'Điểm final cần luận điểm ở scoring và as_of bằng due_at (BR-191)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.kind = 'restatement' AND NOT EXISTS (
       SELECT 1 FROM exec.thesis_score s WHERE s.thesis_id = NEW.thesis_id AND s.kind = 'final') THEN
    RAISE EXCEPTION 'Điểm restatement cần đã có điểm final (BR-191)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER thesis_score_guard_insert BEFORE INSERT ON exec.thesis_score
  FOR EACH ROW EXECUTE FUNCTION exec.thesis_score_guard_insert();

-- covers: exec.thesis_score#2
CREATE FUNCTION exec.thesis_score_close_thesis() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  UPDATE exec.thesis
  SET status = CASE WHEN NEW.outcome = 'unresolvable' THEN 'unresolvable' ELSE 'scored' END
  WHERE id = NEW.thesis_id;
  RETURN NULL;
END $$;
CREATE TRIGGER thesis_score_close_thesis AFTER INSERT ON exec.thesis_score
  FOR EACH ROW WHEN (NEW.kind = 'final') EXECUTE FUNCTION exec.thesis_score_close_thesis();

-- ─── exec.memory_item ───

-- covers: exec.memory_item#0
CREATE POLICY owner_only ON exec.memory_item AS RESTRICTIVE FOR ALL TO svc_exec
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);

-- covers: exec.memory_item#1
CREATE FUNCTION exec.memory_item_guard_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM exec.vault v WHERE v.id = NEW.vault_id AND v.status = 'active'
                   AND v.owner_id = NEW.owner_id) THEN
    RAISE EXCEPTION 'Mục bộ nhớ cần kho active của chính chủ kho (BR-189)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NOT coalesce(exec.vault_unlockable(NEW.vault_id), false) THEN
    RAISE EXCEPTION 'Kho cần từ 2 khoá bọc còn hiệu lực, trong đó một khoá ngoại tuyến (BR-189)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER memory_item_guard_insert BEFORE INSERT ON exec.memory_item
  FOR EACH ROW EXECUTE FUNCTION exec.memory_item_guard_insert();

-- covers: exec.memory_item#2
REVOKE DELETE ON exec.memory_item FROM svc_exec;
CREATE FUNCTION exec.memory_item_guard_update() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  -- Shredding drops the wrapped DEK and the ciphertext; nothing else ever changes (UC-16-02).
  IF OLD.shredded_at IS NOT NULL
     OR NEW.content_ct IS NOT NULL OR NEW.wrapped_dek IS NOT NULL
     OR (to_jsonb(NEW) - 'content_ct' - 'wrapped_dek' - 'shredded_at')
        IS DISTINCT FROM (to_jsonb(OLD) - 'content_ct' - 'wrapped_dek' - 'shredded_at') THEN
    RAISE EXCEPTION 'Mục bộ nhớ không sửa được, chỉ huỷ (BR-189)'
      USING ERRCODE = 'check_violation';
  END IF;
  NEW.shredded_at := now();
  RETURN NEW;
END $$;
CREATE TRIGGER memory_item_guard_update BEFORE UPDATE ON exec.memory_item
  FOR EACH ROW EXECUTE FUNCTION exec.memory_item_guard_update();

-- ─── exec.key_access_log ───

-- covers: exec.key_access_log#0
CREATE POLICY owner_only ON exec.key_access_log AS RESTRICTIVE FOR SELECT TO svc_exec
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
-- An AC05/AC06 request (UC-16-06) logs against the leaving owner's vault, as actor admin_request.
CREATE POLICY owner_only_insert ON exec.key_access_log AS RESTRICTIVE FOR INSERT TO svc_exec
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid
              OR (NULLIF(current_setting('app.exec_admin', true), '') = 'on' AND actor_kind = 'admin_request'));
CREATE POLICY workflow_only ON exec.key_access_log AS RESTRICTIVE FOR ALL TO svc_exec_worker
  USING (NULLIF(current_setting('app.exec_workflow', true), '') = 'on')
  WITH CHECK (NULLIF(current_setting('app.exec_workflow', true), '') = 'on');
GRANT INSERT ON exec.key_access_log TO svc_exec_worker;
-- The workflow calls OpenBao only when INSERT ... RETURNING id returns a row (#1), and RETURNING
-- needs SELECT on the returned column: id only, nothing that says what was accessed.
GRANT SELECT (id) ON exec.key_access_log TO svc_exec_worker;
-- BR-192: append-only for every role, the table owner and the DBA included.
CREATE FUNCTION exec.key_access_log_append_only() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  RAISE EXCEPTION 'Sổ truy cập khoá chỉ ghi thêm, không sửa, không xoá (BR-192)'
    USING ERRCODE = 'insufficient_privilege';
END $$;
CREATE TRIGGER key_access_log_append_only BEFORE UPDATE OR DELETE ON exec.key_access_log
  FOR EACH ROW EXECUTE FUNCTION exec.key_access_log_append_only();

-- Each row says who touched which key: the vault and owner must be one pair (and the thesis theirs),
-- the actor kind must be the caller's own, and `at` is the database's clock. Without this a caller
-- could forge entries for another vault, pose as the workflow, or backdate `at` to slip past the
-- 24-hour decrypt limit below (BR-192).
CREATE FUNCTION exec.key_access_target_ok(p_org uuid, p_vault uuid, p_owner uuid, p_thesis uuid)
RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  -- Definer: the worker cannot read exec.vault or exec.thesis; it only learns yes or no.
  SELECT EXISTS (SELECT 1 FROM exec.vault v
                 WHERE v.id = p_vault AND v.owner_id = p_owner AND v.org_id = p_org)
     AND (p_thesis IS NULL OR EXISTS (SELECT 1 FROM exec.thesis t
                                      WHERE t.id = p_thesis AND t.vault_id = p_vault
                                        AND t.owner_id = p_owner AND t.org_id = p_org))
$$;
REVOKE EXECUTE ON FUNCTION exec.key_access_target_ok(uuid, uuid, uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION exec.key_access_target_ok(uuid, uuid, uuid, uuid) TO svc_exec, svc_exec_worker;

CREATE FUNCTION exec.vault_has_open_handover(p_org uuid, p_vault uuid) RETURNS boolean
LANGUAGE sql STABLE SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
  SELECT EXISTS (SELECT 1 FROM exec.handover h
                 WHERE h.vault_id = p_vault AND h.org_id = p_org AND h.status IN ('approved', 'in_progress'))
$$;
REVOKE EXECUTE ON FUNCTION exec.vault_has_open_handover(uuid, uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION exec.vault_has_open_handover(uuid, uuid) TO svc_exec;

-- Invoker rights on purpose: current_user is the calling service role here.
CREATE FUNCTION exec.key_access_log_check_actor() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  NEW.at := now();
  IF NOT exec.key_access_target_ok(NEW.org_id, NEW.vault_id, NEW.owner_id, NEW.thesis_id) THEN
    RAISE EXCEPTION 'Sổ truy cập khoá: kho, chủ kho và luận điểm không khớp nhau (BR-192)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF current_user = 'svc_exec_worker' AND NEW.actor_kind <> 'workflow' THEN
    RAISE EXCEPTION 'Workflow chỉ ghi dòng actor_kind = workflow (BR-192)' USING ERRCODE = 'insufficient_privilege';
  ELSIF current_user = 'svc_exec' THEN
    IF NEW.owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid THEN
      IF NEW.actor_kind <> 'owner_device' THEN
        RAISE EXCEPTION 'Thiết bị của chủ kho chỉ ghi dòng owner_device (BR-192)' USING ERRCODE = 'insufficient_privilege';
      END IF;
    ELSIF NEW.actor_kind <> 'admin_request' OR NOT exec.vault_has_open_handover(NEW.org_id, NEW.vault_id) THEN
      RAISE EXCEPTION 'Quản trị chỉ ghi dòng admin_request cho kho có yêu cầu rời vị trí đã duyệt (BR-192, BR-195)'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
  END IF;
  RETURN NEW;
END $$;
-- Named to sort before key_access_log_decrypt_limit: BEFORE triggers fire in name order, and the
-- limit must count against a row whose `at` is already the server's.
CREATE TRIGGER key_access_log_check_actor BEFORE INSERT ON exec.key_access_log
  FOR EACH ROW EXECUTE FUNCTION exec.key_access_log_check_actor();

-- covers: exec.key_access_log#1
-- The fourth decrypt of one condition in 24 hours is not written (RETURN NULL), so RETURNING is
-- empty and the workflow never calls OpenBao; raising would roll back the event as well. Counted
-- over decrypt_condition rows. SECURITY DEFINER: the worker can neither read the log nor write the
-- outbox. Audit is a separate database (D1.1), so the Audit service writes audit.security_event
-- from this event.
CREATE FUNCTION exec.key_access_log_decrypt_limit() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  -- Two concurrent decrypts of one condition would both count two and both pass; serialise them.
  PERFORM pg_advisory_xact_lock(hashtextextended('exec.decrypt:' || NEW.thesis_id::text, 0));
  IF (SELECT count(*) FROM exec.key_access_log l
      WHERE l.thesis_id = NEW.thesis_id AND l.action = 'decrypt_condition'
        AND l.at > now() - interval '24 hours') >= 3 THEN
    INSERT INTO exec.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'key_access_log', NEW.thesis_id, 'exec.key.decrypt_limit_exceeded',
            jsonb_build_object('thesis_id', NEW.thesis_id, 'vault_id', NEW.vault_id, 'owner_id', NEW.owner_id,
                               'actor_ref', NEW.actor_ref, 'correlation_id', NEW.correlation_id,
                               'purpose', NEW.purpose));
    RETURN NULL;
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER key_access_log_decrypt_limit BEFORE INSERT ON exec.key_access_log
  FOR EACH ROW WHEN (NEW.action = 'decrypt_condition')
  EXECUTE FUNCTION exec.key_access_log_decrypt_limit();

-- ─── exec.handover ───

-- covers: exec.handover#0
REVOKE DELETE ON exec.handover FROM svc_exec;
CREATE POLICY owner_only ON exec.handover AS RESTRICTIVE FOR ALL TO svc_exec
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid
         OR NULLIF(current_setting('app.exec_admin', true), '') = 'on')
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid
              OR NULLIF(current_setting('app.exec_admin', true), '') = 'on');
-- RLS cannot narrow the columns of the admin branch, so a trigger does: AC05/AC06 change status,
-- approved_by, approved_at, plus completed_at and certificate_sha256 that the CHECKs demand when
-- they complete a shred, and the updated_* stamps.
CREATE FUNCTION exec.handover_admin_columns() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF OLD.owner_id IS DISTINCT FROM NULLIF(current_setting('app.user_id', true), '')::uuid
     AND (to_jsonb(NEW) - 'status' - 'approved_by' - 'approved_at' - 'completed_at'
            - 'certificate_sha256' - 'updated_at' - 'updated_by')
         IS DISTINCT FROM (to_jsonb(OLD) - 'status' - 'approved_by' - 'approved_at' - 'completed_at'
            - 'certificate_sha256' - 'updated_at' - 'updated_by') THEN
    RAISE EXCEPTION 'Quản trị chỉ đổi status, approved_by, approved_at của yêu cầu xử lý kho (BR-195)'
      USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER handover_admin_columns BEFORE UPDATE ON exec.handover
  FOR EACH ROW EXECUTE FUNCTION exec.handover_admin_columns();

-- Two-person control (BR-195): requested_by and approved_by are the people acting, not values a
-- caller may type. The requester is whoever inserts the row; approval is a second, separate act by
-- whoever sets approved_by, once; the CHECK approved_by <> requested_by then means two people.
CREATE FUNCTION exec.handover_actors() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  actor uuid := NULLIF(current_setting('app.user_id', true), '')::uuid;
BEGIN
  IF current_user NOT IN ('svc_exec', 'svc_exec_worker') THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'INSERT' THEN
    IF actor IS NULL OR NEW.requested_by IS DISTINCT FROM actor THEN
      RAISE EXCEPTION 'Người yêu cầu phải là người đang thao tác (BR-195)' USING ERRCODE = 'insufficient_privilege';
    END IF;
    IF NEW.approved_by IS NOT NULL OR NEW.approved_at IS NOT NULL THEN
      RAISE EXCEPTION 'Duyệt là một bước riêng của người thứ hai, không ghi cùng yêu cầu (BR-195)'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.requested_by IS DISTINCT FROM OLD.requested_by THEN
    RAISE EXCEPTION 'Không đổi được người yêu cầu (BR-195)' USING ERRCODE = 'insufficient_privilege';
  END IF;
  IF NEW.approved_by IS DISTINCT FROM OLD.approved_by THEN
    IF OLD.approved_by IS NOT NULL OR actor IS NULL OR NEW.approved_by IS DISTINCT FROM actor THEN
      RAISE EXCEPTION 'Người duyệt phải là người đang thao tác và chỉ duyệt một lần (BR-195)'
        USING ERRCODE = 'insufficient_privilege';
    END IF;
    NEW.approved_at := now();
  ELSIF NEW.approved_at IS DISTINCT FROM OLD.approved_at THEN
    RAISE EXCEPTION 'approved_at do CSDL ghi khi duyệt (BR-195)' USING ERRCODE = 'insufficient_privilege';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER handover_actors BEFORE INSERT OR UPDATE ON exec.handover
  FOR EACH ROW EXECUTE FUNCTION exec.handover_actors();

-- A handover acts on one vault: its own owner's. Bound when the row is written, because the
-- completion below runs as definer for an admin and must never reach another leader's vault.
-- SECURITY DEFINER so the admin path (no app.user_id) can see the vault it checks.
CREATE FUNCTION exec.handover_vault_guard() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM exec.vault v
                 WHERE v.id = NEW.vault_id AND v.owner_id = NEW.owner_id AND v.org_id = NEW.org_id) THEN
    RAISE EXCEPTION 'Yêu cầu rời vị trí phải đúng kho của chủ kho (BR-195)' USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER handover_vault_guard BEFORE INSERT OR UPDATE OF vault_id, owner_id, org_id ON exec.handover
  FOR EACH ROW EXECUTE FUNCTION exec.handover_vault_guard();

-- covers: exec.handover#1
-- SECURITY DEFINER: AC05/AC06 complete a handover for another owner and the vault policy has no
-- admin branch. sealed_read_until is the vault's contract term (set with exit_policy); the seal
-- keeps it. The worker deletes the Transit key on exec.vault.shred_requested (BR-195).
CREATE FUNCTION exec.handover_complete() RETURNS trigger
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  v exec.vault%ROWTYPE;
BEGIN
  IF NEW.kind = 'seal' THEN
    UPDATE exec.vault SET status = 'sealed', sealed_at = now(),
      contract_ref = coalesce(NEW.contract_ref, contract_ref)
    WHERE id = NEW.vault_id AND owner_id = NEW.owner_id AND org_id = NEW.org_id;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Yêu cầu rời vị trí không khớp kho của chủ kho (BR-195)' USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.kind = 'shred' THEN
    UPDATE exec.vault SET status = 'shredded', shredded_at = now()
    WHERE id = NEW.vault_id AND owner_id = NEW.owner_id AND org_id = NEW.org_id
    RETURNING * INTO v;
    IF NOT FOUND THEN
      RAISE EXCEPTION 'Yêu cầu rời vị trí không khớp kho của chủ kho (BR-195)' USING ERRCODE = 'check_violation';
    END IF;
    INSERT INTO exec.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'vault', NEW.vault_id, 'exec.vault.shred_requested',
            jsonb_build_object('vault_id', NEW.vault_id, 'handover_id', NEW.id, 'owner_id', NEW.owner_id,
                               'transit_key_name', v.transit_key_name,
                               'transit_key_version', v.transit_key_version));
  END IF;
  RETURN NULL;
END $$;
CREATE TRIGGER handover_complete AFTER UPDATE OF status ON exec.handover
  FOR EACH ROW WHEN (NEW.status = 'completed' AND OLD.status IS DISTINCT FROM 'completed')
  EXECUTE FUNCTION exec.handover_complete();

-- ─── exec.handover_item ───

-- covers: exec.handover_item#0
CREATE POLICY owner_insert ON exec.handover_item AS RESTRICTIVE FOR INSERT TO svc_exec
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
CREATE POLICY successor_read ON exec.handover_item AS RESTRICTIVE FOR SELECT TO svc_exec
  USING (successor_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
CREATE POLICY successor_update ON exec.handover_item AS RESTRICTIVE FOR UPDATE TO svc_exec
  USING (successor_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (successor_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
REVOKE UPDATE, DELETE ON exec.handover_item FROM svc_exec;
GRANT UPDATE (imported_at) ON exec.handover_item TO svc_exec;

-- covers: exec.handover_item#1
CREATE FUNCTION exec.handover_item_guard_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM exec.handover h WHERE h.id = NEW.handover_id
                   AND h.kind = 'selective_handover' AND h.status IN ('approved', 'in_progress')
                   AND h.owner_id = NEW.owner_id AND h.successor_id = NEW.successor_id) THEN
    RAISE EXCEPTION 'Mục bàn giao cần yêu cầu selective_handover đã duyệt hoặc đang chạy, cùng chủ kho và người nhận (BR-195)'
      USING ERRCODE = 'check_violation';
  END IF;
  UPDATE exec.handover SET items_total = items_total + 1 WHERE id = NEW.handover_id;
  RETURN NEW;
END $$;
CREATE TRIGGER handover_item_guard_insert BEFORE INSERT ON exec.handover_item
  FOR EACH ROW EXECUTE FUNCTION exec.handover_item_guard_insert();
-- The daily cleanup is the one DELETE of schema exec: imported rows, or rows older than 30 days.
-- It runs without a user (app.user_id), so it is a function svc_exec may call, scoped by
-- org_isolation, rather than a DELETE grant the successor-only policies would block.
CREATE FUNCTION exec.handover_item_cleanup() RETURNS integer
LANGUAGE plpgsql SECURITY DEFINER SET search_path = pg_catalog, pg_temp AS $$
DECLARE
  n integer;
BEGIN
  DELETE FROM exec.handover_item
  WHERE org_id = NULLIF(current_setting('app.org_id', true), '')::uuid
    AND (imported_at IS NOT NULL OR created_at < now() - interval '30 days');
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END $$;
REVOKE EXECUTE ON FUNCTION exec.handover_item_cleanup() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION exec.handover_item_cleanup() TO svc_exec;

-- ─── exec.publication ───

-- covers: exec.publication#0
REVOKE DELETE ON exec.publication FROM svc_exec;
CREATE POLICY owner_only ON exec.publication AS RESTRICTIVE FOR ALL TO svc_exec
  USING (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid)
  WITH CHECK (owner_id = NULLIF(current_setting('app.user_id', true), '')::uuid);
-- svc_context (through exec_read) reads only what a hypothesis page may show; page readers are
-- then filtered by the page's own policy (BR-163, BR-194).
CREATE POLICY context_source ON exec.publication AS RESTRICTIVE FOR SELECT TO exec_read
  USING (status IN ('pending_review', 'published'));

-- covers: exec.publication#1
CREATE FUNCTION exec.publication_guard_insert() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NOT EXISTS (SELECT 1 FROM exec.thesis t WHERE t.id = NEW.thesis_id AND t.owner_id = NEW.owner_id
                   AND t.status IN ('open', 'scored') AND t.due_at = NEW.due_at) THEN
    RAISE EXCEPTION 'Bản công bố cần luận điểm cùng chủ kho, ở open hoặc scored, cùng due_at (BR-194)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END $$;
CREATE TRIGGER publication_guard_insert BEFORE INSERT ON exec.publication
  FOR EACH ROW EXECUTE FUNCTION exec.publication_guard_insert();

-- covers: exec.publication#2
CREATE FUNCTION exec.publication_emit() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO exec.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
  VALUES (gen_random_uuid(), NEW.org_id, 'publication', NEW.id, 'exec.publication.' || NEW.status,
          jsonb_build_object('publication_id', NEW.id, 'thesis_id', NEW.thesis_id,
                             'knowledge_page_id', NEW.knowledge_page_id,
                             'target_data_space_id', NEW.target_data_space_id, 'status', NEW.status));
  RETURN NULL;
END $$;
CREATE TRIGGER publication_emit AFTER UPDATE OF status ON exec.publication
  FOR EACH ROW WHEN (NEW.status IN ('published', 'withdrawn') AND NEW.status IS DISTINCT FROM OLD.status)
  EXECUTE FUNCTION exec.publication_emit();

-- covers: exec.publication#3 (no DDL here: the D4.5 amendment is context.knowledge_page#2, knowledge_page_version#5, knowledge_source_link#3 in routines/context.sql)
-- covers: exec.publication#4 (no DDL: page RLS unchanged; hypothesis pages excluded as figure or evidence sources by the answer and Evidence agents, G9.5, BR-190)
