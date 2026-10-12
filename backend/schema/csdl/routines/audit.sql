-- routines/audit.sql: the hash chain of audit.audit_log (tab 3 D5.3, BR-47) in the separate
-- database dataagent_audit: the trigger audit.chain_hash numbers each organisation's rows and links
-- them by SHA-256, audit.verify_chain finds the first broken link (UC-11-03 "Kiểm toàn vẹn"), and
-- svc_audit may only append (D5.2).

-- The hash of one row: SHA-256 over the row as jsonb (fixed key order) without `hash`, so seq and
-- prev_hash are part of it. occurred_at is written in UTC so the text, and the hash, do not depend
-- on the session's TimeZone. It takes jsonb because a row trigger on the partitioned table sees the
-- partition's row type, not audit.audit_log's.
CREATE FUNCTION audit.row_hash(r jsonb) RETURNS text
LANGUAGE sql STABLE AS $$
  SELECT encode(sha256(convert_to(
    (jsonb_set(r - 'hash', '{occurred_at}',
               to_jsonb(to_char((r->>'occurred_at')::timestamptz AT TIME ZONE 'UTC',
                                'YYYY-MM-DD"T"HH24:MI:SS.US"Z"'))))::text,
    'UTF8')), 'hex')
$$;

-- D5.3: seq increases per organisation, prev_hash is the hash of the previous row (64 zeros for the
-- first), and an advisory lock per organisation keeps two writers from taking the same seq or
-- linking to the same predecessor. Values sent by the service for seq, prev_hash, hash are replaced.
CREATE FUNCTION audit.chain_hash() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  last_seq bigint;
  last_hash text;
BEGIN
  PERFORM pg_advisory_xact_lock(hashtextextended('audit.audit_log:' || NEW.org_id, 0));
  SELECT seq, hash INTO last_seq, last_hash
    FROM audit.audit_log WHERE org_id = NEW.org_id ORDER BY seq DESC LIMIT 1;
  NEW.seq := coalesce(last_seq, 0) + 1;
  NEW.prev_hash := coalesce(last_hash, repeat('0', 64));
  NEW.hash := audit.row_hash(to_jsonb(NEW));
  RETURN NEW;
END
$$;
CREATE TRIGGER audit_log_chain_hash BEFORE INSERT ON audit.audit_log
  FOR EACH ROW EXECUTE FUNCTION audit.chain_hash();

-- First seq at or after from_seq whose row was altered, removed or relinked; null when the chain is
-- intact. Runs with the caller's rights, so RLS limits it to the caller's organisation.
CREATE FUNCTION audit.verify_chain(org uuid, from_seq bigint DEFAULT 1) RETURNS bigint
LANGUAGE plpgsql STABLE AS $$
DECLARE
  r audit.audit_log%ROWTYPE;
  expected_seq bigint := greatest(from_seq, 1);
  expected_prev text;
BEGIN
  IF expected_seq = 1 THEN
    expected_prev := repeat('0', 64);
  ELSE
    SELECT hash INTO expected_prev FROM audit.audit_log WHERE org_id = org AND seq = expected_seq - 1;
  END IF;
  FOR r IN SELECT * FROM audit.audit_log WHERE org_id = org AND seq >= expected_seq ORDER BY seq LOOP
    IF r.seq <> expected_seq OR r.prev_hash IS DISTINCT FROM expected_prev OR r.hash <> audit.row_hash(to_jsonb(r)) THEN
      RETURN least(r.seq, expected_seq);
    END IF;
    expected_prev := r.hash;
    expected_seq := expected_seq + 1;
  END LOOP;
  RETURN NULL;
END
$$;

-- D5.2 (BR-47): svc_audit only SELECT, INSERT on audit_log.
REVOKE UPDATE, DELETE ON audit.audit_log FROM svc_audit;

-- covers: audit.export_job#0 (no DDL: org_isolation is generated; the file is written by the export worker from the masked before/after columns, which already hold no personal data, BR-138)

-- D5.2 (12/10/2026): svc_audit_worker is worker-agent's role for releasing an approved export:
-- it reads export_job and moves its state, nothing on audit_log, security_event or outbox.
-- org_isolation applies to every role, so it stays inside the organisation it sets.
GRANT USAGE ON SCHEMA audit TO svc_audit_worker;
GRANT SELECT ON audit.export_job TO svc_audit_worker;
GRANT UPDATE (status, approved_by, approved_at, updated_at) ON audit.export_job TO svc_audit_worker;
-- The only change it makes is the approver's decision on a waiting export: approved (queued, with
-- the approver; the table's CHECK keeps the approver apart from the requester) or rejected (failed).
CREATE POLICY worker_decision_only ON audit.export_job AS RESTRICTIVE FOR UPDATE TO svc_audit_worker
  USING (status = 'waiting_approval')
  WITH CHECK ((status = 'queued' AND approved_by IS NOT NULL AND approved_at IS NOT NULL) OR status = 'failed');
