-- routines/conflict.sql: what the conflict schema enforces beyond its CHECKs (tab 3b D4.16).

-- covers: conflict.source_authority#0
-- UC-07-05: editing an active ranking makes a new draft; the active one keeps applying until the
-- draft is approved. Approving the draft supersedes the active one in the same statement, before
-- the partial unique index (one active per data_space_id, data_kind) checks the new row.
CREATE FUNCTION conflict.source_authority_supersede() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  UPDATE conflict.source_authority SET status = 'superseded', updated_at = now()
   WHERE data_space_id = NEW.data_space_id AND data_kind = NEW.data_kind
     AND status = 'active' AND id <> NEW.id;
  RETURN NEW;
END $$;
CREATE TRIGGER source_authority_supersede BEFORE UPDATE OF status ON conflict.source_authority
  FOR EACH ROW WHEN (NEW.status = 'active' AND OLD.status IS DISTINCT FROM 'active')
  EXECUTE FUNCTION conflict.source_authority_supersede();
