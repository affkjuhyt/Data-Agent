-- routines/catalog.sql: what D4.4 (tab 3b) writes in words for schema catalog: certification of a
-- consolidated dataset version (BR-156) through the append-only history
-- catalog.dataset_version_certification, and the D5.2 grant exception of svc_catalog on it.

-- covers: catalog.dataset_version#0
-- BR-156: a version starts uncertified; a consolidated one records its creator (the last spec
-- editor), which never changes, so a second person certifies it. certified_at/certified_by are the
-- current state of the history table and only its trigger writes them (it runs one trigger level
-- deeper, pg_trigger_depth() > 1). Blocking uncertified consolidated versions from regulator figures
-- is the semantic compiler's job (tab 5 G5.2).
CREATE FUNCTION catalog.dataset_version_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  origin text;
BEGIN
  SELECT d.origin INTO origin FROM catalog.dataset d WHERE d.id = NEW.dataset_id;
  IF TG_OP = 'INSERT' THEN
    IF NEW.certified_at IS NOT NULL OR NEW.certified_by IS NOT NULL THEN
      RAISE EXCEPTION 'Phiên bản dataset mới ở trạng thái chưa chứng nhận (BR-156)' USING ERRCODE = 'check_violation';
    END IF;
    IF origin = 'consolidated' AND NEW.created_by IS NULL THEN
      RAISE EXCEPTION 'Phiên bản dataset hợp nhất bắt buộc có created_by (BR-156)' USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;
  IF NEW.created_by IS DISTINCT FROM OLD.created_by THEN
    RAISE EXCEPTION 'created_by của phiên bản dataset không sửa được (BR-156)' USING ERRCODE = 'check_violation';
  END IF;
  IF (NEW.certified_at, NEW.certified_by) IS DISTINCT FROM (OLD.certified_at, OLD.certified_by) THEN
    IF pg_trigger_depth() < 2 THEN
      RAISE EXCEPTION 'Chứng nhận phiên bản dataset chỉ ghi qua catalog.dataset_version_certification (BR-156)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF origin IS DISTINCT FROM 'consolidated' THEN
      RAISE EXCEPTION 'Chỉ chứng nhận phiên bản của dataset hợp nhất (origin = consolidated, BR-156)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER dataset_version_guard BEFORE INSERT OR UPDATE ON catalog.dataset_version
  FOR EACH ROW EXECUTE FUNCTION catalog.dataset_version_guard();

-- covers: catalog.dataset_version_certification#0
-- BR-156: each certify/revoke is a new history row applied to the version in the same transaction
-- and announced through catalog.outbox. A history table rather than revoked_* columns, because a
-- second revocation would overwrite them (D1.6, append only).
CREATE FUNCTION catalog.dataset_version_certification_apply() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  v catalog.dataset_version%ROWTYPE;
BEGIN
  SELECT * INTO v FROM catalog.dataset_version WHERE id = NEW.dataset_version_id FOR UPDATE;
  IF NOT FOUND OR v.org_id <> NEW.org_id THEN
    RAISE EXCEPTION 'Không tìm thấy phiên bản dataset trong tổ chức (BR-156)' USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.action = 'certify' THEN
    IF v.certified_at IS NOT NULL THEN
      RAISE EXCEPTION 'Phiên bản dataset đã được chứng nhận (BR-156)' USING ERRCODE = 'check_violation';
    END IF;
    UPDATE catalog.dataset_version SET certified_at = NEW.created_at, certified_by = NEW.actor_id
     WHERE id = v.id;
    INSERT INTO catalog.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'dataset_version', v.id, 'catalog.dataset_version.certified',
            jsonb_build_object('dataset_version_id', v.id, 'dataset_id', v.dataset_id,
                               'certified_by', NEW.actor_id, 'certified_at', NEW.created_at, 'note', NEW.note));
  ELSE
    IF v.certified_at IS NULL THEN
      RAISE EXCEPTION 'Phiên bản dataset chưa được chứng nhận, không thu hồi được (BR-156)'
        USING ERRCODE = 'check_violation';
    END IF;
    UPDATE catalog.dataset_version SET certified_at = NULL, certified_by = NULL WHERE id = v.id;
    INSERT INTO catalog.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'dataset_version', v.id, 'catalog.dataset_version.certification_revoked',
            jsonb_build_object('dataset_version_id', v.id, 'dataset_id', v.dataset_id,
                               'revoked_by', NEW.actor_id, 'reason', NEW.reason,
                               'previous_certified_by', v.certified_by, 'previous_certified_at', v.certified_at));
  END IF;
  RETURN NULL;
END
$$;
CREATE TRIGGER dataset_version_certification_apply AFTER INSERT ON catalog.dataset_version_certification
  FOR EACH ROW EXECUTE FUNCTION catalog.dataset_version_certification_apply();

-- D5.2 (BR-156): the history is append only for svc_catalog.
REVOKE UPDATE, DELETE ON catalog.dataset_version_certification FROM svc_catalog;
