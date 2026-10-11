-- routines/eval.sql: what D4.9 (tab 3b) writes in words for schema eval: the Error Book (BR-179):
-- entry numbering, the status machine with its regression proof, the opened/reopened events and the
-- function that reopens an entry when its regression case fails again.

-- covers: eval.error_book_entry#0 (no DDL beyond org_isolation: rights inside a Data Space and masking of detail are checked by the Policy engine in the Eval service, D1.3)

-- covers: eval.error_book_entry#1
-- BR-179: EB-0001… per organisation; open → fixed → regression_added, open → dismissed, and
-- regression_added → open on reopening (a fixed entry does not go back to open). regression_added
-- needs the regression case to pass in a deploy or nightly run started after fixed_at. Filling
-- assignee_id with the product steward and checking that fix_proposal_id is accepted are done by the
-- Eval service (the proposal lives in schema inbox).
CREATE FUNCTION eval.error_book_entry_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  last_no integer;
BEGIN
  IF TG_OP = 'INSERT' THEN
    PERFORM pg_advisory_xact_lock(hashtextextended('eval.error_book_entry:' || NEW.org_id, 0));
    SELECT max(substr(entry_no, 4)::integer) INTO last_no
      FROM eval.error_book_entry WHERE org_id = NEW.org_id AND entry_no ~ '^EB-[0-9]+$';
    last_no := coalesce(last_no, 0) + 1;
    NEW.entry_no := 'EB-' || lpad(last_no::text, greatest(4, length(last_no::text)), '0');
    RETURN NEW;
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status
     AND (OLD.status, NEW.status) NOT IN (('open', 'fixed'), ('fixed', 'regression_added'),
                                          ('open', 'dismissed'), ('regression_added', 'open')) THEN
    RAISE EXCEPTION 'Mục Error Book không chuyển được từ % sang % (BR-179)', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status = 'regression_added' AND OLD.status = 'fixed' AND NOT EXISTS (
       SELECT 1
         FROM eval.eval_result r
         JOIN eval.eval_run e ON e.id = r.eval_run_id
        WHERE r.eval_item_id = NEW.eval_item_id AND r.passed
          AND e.id = NEW.regression_run_id
          AND e.trigger IN ('deploy', 'nightly')
          AND e.started_at > NEW.fixed_at) THEN
    RAISE EXCEPTION 'Ca hồi quy chưa đạt trong lần chạy deploy hoặc nightly sau fixed_at (BR-179)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER error_book_entry_guard BEFORE INSERT OR UPDATE ON eval.error_book_entry
  FOR EACH ROW EXECUTE FUNCTION eval.error_book_entry_guard();

-- The Notification service tells the assignee.
CREATE FUNCTION eval.error_book_entry_opened() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  INSERT INTO eval.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
  VALUES (gen_random_uuid(), NEW.org_id, 'error_book_entry', NEW.id, 'eval.error_book.opened',
          jsonb_build_object('entry_id', NEW.id, 'entry_no', NEW.entry_no, 'source_kind', NEW.source_kind,
                             'data_space_id', NEW.data_space_id, 'data_product_id', NEW.data_product_id,
                             'assignee_id', NEW.assignee_id, 'severity', NEW.severity, 'title', NEW.title));
  RETURN NULL;
END
$$;
CREATE TRIGGER error_book_entry_opened AFTER INSERT ON eval.error_book_entry
  FOR EACH ROW EXECUTE FUNCTION eval.error_book_entry_opened();

-- Called by the Eval service when an eval_result of the item has passed = false: the closed entries
-- of that regression case reopen. Returns how many reopened.
CREATE FUNCTION eval.error_book_on_regression_failed(item_id uuid) RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
  e eval.error_book_entry%ROWTYPE;
  n integer := 0;
BEGIN
  FOR e IN
    UPDATE eval.error_book_entry
       SET status = 'open', reopened_count = reopened_count + 1, updated_at = now()
     WHERE eval_item_id = item_id AND status = 'regression_added'
    RETURNING *
  LOOP
    INSERT INTO eval.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), e.org_id, 'error_book_entry', e.id, 'eval.error_book.reopened',
            jsonb_build_object('entry_id', e.id, 'entry_no', e.entry_no, 'eval_item_id', item_id,
                               'reopened_count', e.reopened_count, 'assignee_id', e.assignee_id));
    n := n + 1;
  END LOOP;
  RETURN n;
END
$$;
REVOKE ALL ON FUNCTION eval.error_book_on_regression_failed(uuid) FROM PUBLIC;
GRANT EXECUTE ON FUNCTION eval.error_book_on_regression_failed(uuid) TO svc_eval;
