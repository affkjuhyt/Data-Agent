-- routines/skill.sql: what the skill schema enforces beyond its CHECKs (tab 3b D4, tab 8).

-- AC-03-06-04, BR-97: a version that has run is never deleted, so an old skill_run still shows
-- how it computed; retiring hides it from the library instead.
CREATE FUNCTION skill.skill_version_keep_if_run() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF EXISTS (SELECT 1 FROM skill.skill_run r WHERE r.skill_version_id = OLD.id) THEN
    RAISE EXCEPTION 'Phiên bản kỹ năng đã chạy không xoá được; hãy ngừng dùng (BR-97)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN OLD;
END $$;
CREATE TRIGGER skill_version_keep_if_run BEFORE DELETE ON skill.skill_version
  FOR EACH ROW EXECUTE FUNCTION skill.skill_version_keep_if_run();
