-- routines/forecast.sql: what D4.23 (tab 3b) writes in words for the forecast schema (P15 model
-- versions and their release gate, backtests, predictions, formula models, the forecast ledger):
-- the guard triggers, the ledger events, and the D5.2 append-only exceptions on prediction and
-- ledger_entry. Runs as da_owner after the generated tables, FKs, RLS and grants.


-- ═══ forecast_model ═══

-- covers: forecast.forecast_model#0
-- BR-160, checked at commit because the model and its version change together (publish a
-- version and repoint the model; degrade both): the current version is a published or degraded
-- version of this model, and the model is degraded exactly when that version is. Both tables
-- get a deferred trigger, so degrading only the version while the model stays active is caught.
CREATE FUNCTION forecast.forecast_model_current_version_ok(p_model_id uuid) RETURNS void
LANGUAGE plpgsql AS $$
DECLARE
  m record;
  v record;
BEGIN
  SELECT status, current_version_id INTO m FROM forecast.forecast_model WHERE id = p_model_id;
  IF NOT FOUND THEN
    RETURN;
  END IF;
  IF m.current_version_id IS NULL THEN
    IF m.status = 'degraded' THEN
      RAISE EXCEPTION 'Mô hình degraded phải trỏ phiên bản degraded (BR-160)'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN;
  END IF;
  SELECT model_id, status INTO v FROM forecast.model_version WHERE id = m.current_version_id;
  IF NOT FOUND OR v.model_id <> p_model_id OR v.status NOT IN ('published','degraded') THEN
    RAISE EXCEPTION 'current_version_id phải là phiên bản published hoặc degraded của chính mô hình (BR-160)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF (m.status = 'degraded') <> (v.status = 'degraded') THEN
    RAISE EXCEPTION 'Mô hình degraded khi và chỉ khi phiên bản hiện hành degraded (BR-160)'
      USING ERRCODE = 'check_violation';
  END IF;
END
$$;

CREATE FUNCTION forecast.forecast_model_current_version_check() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  PERFORM forecast.forecast_model_current_version_ok(NEW.id);
  RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER forecast_model_current_version_check
  AFTER INSERT OR UPDATE ON forecast.forecast_model
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION forecast.forecast_model_current_version_check();

CREATE FUNCTION forecast.model_version_current_check() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  mid uuid;
BEGIN
  FOR mid IN SELECT id FROM forecast.forecast_model WHERE current_version_id = NEW.id LOOP
    PERFORM forecast.forecast_model_current_version_ok(mid);
  END LOOP;
  RETURN NULL;
END
$$;
CREATE CONSTRAINT TRIGGER model_version_current_check
  AFTER UPDATE OF status, model_id ON forecast.model_version
  DEFERRABLE INITIALLY DEFERRED
  FOR EACH ROW EXECUTE FUNCTION forecast.model_version_current_check();


-- ═══ model_version ═══

-- covers: forecast.model_version#0
-- covers: forecast.model_version#1
-- covers: forecast.model_version#3
-- model_version_guard. BR-160: the version life cycle. BR-158: publish only on a finished
-- backtest of this very version with MASE < 1. BR-172: publish needs n_points and the in-sample
-- MASE, and refuses a backtest MASE more than twice the in-sample one (overfitting). BR-183 /
-- ADR-44: a formula_mc version goes to approval only with its formula_model row, and is published
-- on a weekly rolling backtest of at least 20 cutoffs that reports reconstruction_mae.
CREATE FUNCTION forecast.model_version_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  b record;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status NOT IN ('draft','pending_approval') THEN
      RAISE EXCEPTION 'Phiên bản mới phải ở draft hoặc pending_approval (BR-160)'
        USING ERRCODE = 'check_violation';
    END IF;
  ELSIF NEW.status IS DISTINCT FROM OLD.status AND NOT (
       (OLD.status = 'draft' AND NEW.status IN ('pending_approval','rejected'))
    OR (OLD.status = 'pending_approval' AND NEW.status IN ('published','rejected','draft'))
    OR (OLD.status = 'published' AND NEW.status IN ('degraded','retired'))
    OR (OLD.status = 'degraded' AND NEW.status = 'retired')
  ) THEN
    RAISE EXCEPTION 'Không chuyển phiên bản từ % sang % (BR-160)', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'pending_approval' AND NEW.algorithm = 'formula_mc'
     AND (TG_OP = 'INSERT' OR OLD.status IS DISTINCT FROM 'pending_approval')
     AND (SELECT count(*) FROM forecast.formula_model f WHERE f.model_version_id = NEW.id) <> 1 THEN
    RAISE EXCEPTION 'Phiên bản formula_mc gửi duyệt cần đúng một formula_model (BR-183)'
      USING ERRCODE = 'check_violation';
  END IF;

  IF TG_OP = 'UPDATE' AND NEW.status = 'published' AND OLD.status IS DISTINCT FROM 'published' THEN
    SELECT model_version_id, finished_at, mase, in_sample_mase, frequency, cutoffs, metrics INTO b
    FROM forecast.backtest_run WHERE id = NEW.backtest_id;
    IF NOT FOUND OR b.model_version_id <> NEW.id OR b.finished_at IS NULL OR b.mase >= 1 THEN
      RAISE EXCEPTION 'Phát hành cần backtest đã xong của chính phiên bản với MASE < 1 (BR-158)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.n_points IS NULL OR b.in_sample_mase IS NULL THEN
      RAISE EXCEPTION 'Phát hành cần n_points và in_sample_mase (BR-172)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF b.mase / b.in_sample_mase > 2 THEN
      RAISE EXCEPTION 'MASE backtest quá 2 lần MASE trong mẫu, mô hình quá khớp (BR-172)'
        USING ERRCODE = 'check_violation';
    END IF;
    IF NEW.algorithm = 'formula_mc'
       AND (b.frequency <> 'week' OR cardinality(b.cutoffs) < 20 OR NOT b.metrics ? 'reconstruction_mae') THEN
      RAISE EXCEPTION 'Backtest của phiên bản formula_mc phải theo tuần, từ 20 điểm cắt, có reconstruction_mae (BR-183)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER model_version_guard
  BEFORE INSERT OR UPDATE ON forecast.model_version
  FOR EACH ROW EXECUTE FUNCTION forecast.model_version_guard();

-- covers: forecast.model_version#2 (already in the generated CHECK on algorithm)


-- ═══ backtest_run ═══

-- covers: forecast.backtest_run#0
-- ADR-15: the minimum number of cutoffs depends on the frequency, so the frequency is the
-- model's, filled in when omitted and refused when it differs.
CREATE FUNCTION forecast.backtest_run_frequency_sync() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  f text;
BEGIN
  SELECT m.frequency INTO f
  FROM forecast.model_version v JOIN forecast.forecast_model m ON m.id = v.model_id
  WHERE v.id = NEW.model_version_id;
  IF NEW.frequency IS NULL THEN
    NEW.frequency := f;
  ELSIF f IS NOT NULL AND NEW.frequency <> f THEN
    RAISE EXCEPTION 'Tần suất backtest (%) khác tần suất mô hình (%) (ADR-15)', NEW.frequency, f
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER backtest_run_frequency_sync
  BEFORE INSERT OR UPDATE OF frequency, model_version_id ON forecast.backtest_run
  FOR EACH ROW EXECUTE FUNCTION forecast.backtest_run_frequency_sync();


-- ═══ prediction ═══

-- covers: forecast.prediction#0
-- covers: forecast.prediction#2
-- BR-158: output of a version that is not released is labelled experimental. BR-184 / ADR-44:
-- formula_mc output is a Monte Carlo interval with simulated inputs (for the base run; a
-- scenario fixes its inputs, so it stays scenario_fixed) and decomposes like the ledger.
-- monte_carlo itself is already in the generated interval_method CHECK.
CREATE FUNCTION forecast.prediction_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v record;
BEGIN
  SELECT status, algorithm INTO v FROM forecast.model_version WHERE id = NEW.model_version_id;
  IF v.status NOT IN ('published','degraded') AND NOT NEW.is_experimental THEN
    RAISE EXCEPTION 'Dự báo từ phiên bản chưa phát hành phải có is_experimental = true (BR-158)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF v.algorithm = 'formula_mc' AND (
       NEW.interval_method <> 'monte_carlo'
       OR (NEW.scenario_id IS NULL AND NEW.exog_uncertainty <> 'simulated')
       OR NOT NEW.contributions ?& ARRAY['world','fx','tax','fund','proxy_error']
  ) THEN
    RAISE EXCEPTION 'Dự báo formula_mc ghi monte_carlo, simulated và phân rã world, fx, tax, fund, proxy_error (BR-184)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER prediction_guard
  BEFORE INSERT OR UPDATE ON forecast.prediction
  FOR EACH ROW EXECUTE FUNCTION forecast.prediction_guard();

-- covers: forecast.prediction#1
-- prediction_exog_guard (BR-173): a base forecast that depends on exogenous variables must
-- carry their uncertainty; treating future inputs as known would make the interval too narrow.
CREATE FUNCTION forecast.prediction_exog_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF NEW.scenario_id IS NULL AND NEW.exog_uncertainty <> 'simulated'
     AND (SELECT n_exog FROM forecast.model_version WHERE id = NEW.model_version_id) > 0 THEN
    RAISE EXCEPTION 'Dự báo gốc của phiên bản có biến ngoại sinh phải ghi exog_uncertainty = simulated (BR-173)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER prediction_exog_guard
  BEFORE INSERT OR UPDATE ON forecast.prediction
  FOR EACH ROW EXECUTE FUNCTION forecast.prediction_exog_guard();

-- D5.2: predictions are append-only. The generated grants already give svc_forecast only
-- SELECT, INSERT on this "Bản ghi bất biến" table; the revoke states the exception explicitly.
REVOKE UPDATE, DELETE ON forecast.prediction FROM svc_forecast;


-- ═══ formula_model ═══

-- covers: forecast.formula_model#0
-- formula_model_guard (BR-183): a formula model belongs to a formula_mc version, and once that
-- version is released its formula is frozen; a new formula is a new version. Checking that the
-- metric version is still certified and the components have certified values is the Forecast
-- service's job before each run (it reads context and its own components at forecast time).
CREATE FUNCTION forecast.formula_model_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  v record;
BEGIN
  SELECT algorithm, status INTO v FROM forecast.model_version WHERE id = NEW.model_version_id;
  IF v.algorithm IS DISTINCT FROM 'formula_mc' THEN
    RAISE EXCEPTION 'formula_model chỉ gắn phiên bản có algorithm = formula_mc (BR-183)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'UPDATE' THEN
    SELECT status INTO v FROM forecast.model_version WHERE id = OLD.model_version_id;
    IF v.status IN ('published','degraded')
       AND (NEW.formula, NEW.window_rule, NEW.metric_version_id, NEW.model_version_id)
           IS DISTINCT FROM (OLD.formula, OLD.window_rule, OLD.metric_version_id, OLD.model_version_id) THEN
      RAISE EXCEPTION 'Công thức của phiên bản đã phát hành không sửa được; đổi công thức là phiên bản mới (BR-183)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER formula_model_guard
  BEFORE INSERT OR UPDATE ON forecast.formula_model
  FOR EACH ROW EXECUTE FUNCTION forecast.formula_model_guard();


-- ═══ formula_component ═══

-- covers: forecast.formula_component#0
-- formula_component_guard (BR-182): a parameter of the legal price formula is entered as a
-- draft, certified by a second person, and then frozen except for its end of validity. A newly
-- certified value closes the open one of the same (code, product_code) at its own valid_from,
-- and certified intervals never overlap. The advisory lock serialises certifications of one
-- parameter so two concurrent ones cannot both see "no overlap".
CREATE FUNCTION forecast.formula_component_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  prev record;
BEGIN
  IF TG_OP = 'INSERT' THEN
    IF NEW.status NOT IN ('draft','pending_review') THEN
      RAISE EXCEPTION 'Tham số mới phải ở draft hoặc pending_review, chứng nhận là bước riêng (BR-182)'
        USING ERRCODE = 'check_violation';
    END IF;
    RETURN NEW;
  END IF;

  IF NEW.status IS DISTINCT FROM OLD.status AND NOT (
       (OLD.status = 'draft' AND NEW.status IN ('pending_review','rejected'))
    OR (OLD.status = 'pending_review' AND NEW.status IN ('certified','rejected','draft'))
    OR (OLD.status = 'certified' AND NEW.status = 'superseded')
  ) THEN
    RAISE EXCEPTION 'Không chuyển tham số từ % sang % (BR-182)', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;

  IF OLD.status IN ('certified','superseded')
     AND (to_jsonb(NEW) - ARRAY['valid_to','status','updated_at','updated_by'])
         IS DISTINCT FROM (to_jsonb(OLD) - ARRAY['valid_to','status','updated_at','updated_by']) THEN
    RAISE EXCEPTION 'Tham số đã chứng nhận chỉ sửa được valid_to và status (BR-182)'
      USING ERRCODE = 'check_violation';
  END IF;

  IF NEW.status = 'certified' AND (OLD.status <> 'certified' OR NEW.valid_to IS DISTINCT FROM OLD.valid_to) THEN
    PERFORM pg_advisory_xact_lock(hashtextextended(NEW.org_id::text || '/' || NEW.code || '/' || NEW.product_code, 0));
    IF OLD.status <> 'certified' THEN
      FOR prev IN
        SELECT id, valid_from FROM forecast.formula_component
        WHERE org_id = NEW.org_id AND code = NEW.code AND product_code = NEW.product_code
          AND status = 'certified' AND valid_to IS NULL AND id <> NEW.id
      LOOP
        IF NEW.valid_from <= prev.valid_from THEN
          RAISE EXCEPTION 'Bản mới phải có valid_from sau valid_from của bản đang hiệu lực (BR-182)'
            USING ERRCODE = 'check_violation';
        END IF;
        UPDATE forecast.formula_component
        SET valid_to = NEW.valid_from,
            status = CASE WHEN NEW.valid_from <= now() THEN 'superseded' ELSE status END
        WHERE id = prev.id;
      END LOOP;
    END IF;
    IF EXISTS (
      SELECT 1 FROM forecast.formula_component c
      WHERE c.org_id = NEW.org_id AND c.code = NEW.code AND c.product_code = NEW.product_code
        AND c.status = 'certified' AND c.id <> NEW.id
        AND tstzrange(c.valid_from, c.valid_to) && tstzrange(NEW.valid_from, NEW.valid_to)
    ) THEN
      RAISE EXCEPTION 'Hai bản certified cùng (code, product_code) có khoảng hiệu lực chồng nhau (BR-182)'
        USING ERRCODE = 'check_violation';
    END IF;
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER formula_component_guard
  BEFORE INSERT OR UPDATE ON forecast.formula_component
  FOR EACH ROW EXECUTE FUNCTION forecast.formula_component_guard();

-- "chuyển superseded khi tới valid_from": a certified value whose successor starts in the future
-- keeps status certified until then; the Forecast service's scheduler calls this to supersede
-- the ones whose valid_to has been reached. Returns the number of rows superseded.
CREATE FUNCTION forecast.formula_component_supersede_due() RETURNS integer
LANGUAGE plpgsql AS $$
DECLARE
  n integer;
BEGIN
  UPDATE forecast.formula_component SET status = 'superseded'
  WHERE status = 'certified' AND valid_to IS NOT NULL AND valid_to <= now();
  GET DIAGNOSTICS n = ROW_COUNT;
  RETURN n;
END
$$;
REVOKE EXECUTE ON FUNCTION forecast.formula_component_supersede_due() FROM PUBLIC;
GRANT EXECUTE ON FUNCTION forecast.formula_component_supersede_due() TO svc_forecast;


-- ═══ ledger_entry ═══

-- covers: forecast.ledger_entry#0
-- ledger_entry_guard (BR-185): the forecast half of a ledger line is written before the period
-- and never changes; only scoring fills in. Rescoring (scored → scored) follows a corrected
-- published price. error_book_entry_id is written once (tab 3 D5.4). No DELETE for anyone: the
-- ledger shows missed periods too (BR-186), so the trigger refuses it even for the owner, on top
-- of D5.2's revoke for svc_forecast.
CREATE FUNCTION forecast.ledger_entry_guard() RETURNS trigger
LANGUAGE plpgsql AS $$
BEGIN
  IF TG_OP = 'DELETE' THEN
    RAISE EXCEPTION 'Dòng sổ dự báo không xoá được (BR-185)' USING ERRCODE = '42501';
  END IF;
  IF (NEW.forecast_model_id, NEW.model_version_id, NEW.prediction_id, NEW.job_id, NEW.period,
      NEW.scope, NEW.issued_at, NEW.issued_via, NEW.event_id, NEW.event_at, NEW.baseline_value,
      NEW.point, NEW.quantiles, NEW.prob_up, NEW.fund_probs, NEW.contributions)
     IS DISTINCT FROM
     (OLD.forecast_model_id, OLD.model_version_id, OLD.prediction_id, OLD.job_id, OLD.period,
      OLD.scope, OLD.issued_at, OLD.issued_via, OLD.event_id, OLD.event_at, OLD.baseline_value,
      OLD.point, OLD.quantiles, OLD.prob_up, OLD.fund_probs, OLD.contributions) THEN
    RAISE EXCEPTION 'Phần dự báo của dòng sổ không sửa được sau khi ghi (BR-185)'
      USING ERRCODE = 'check_violation';
  END IF;
  IF NEW.status IS DISTINCT FROM OLD.status
     AND NOT (OLD.status = 'open' AND NEW.status IN ('scored','void')) THEN
    RAISE EXCEPTION 'Không chuyển dòng sổ từ % sang % (BR-185)', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;
  IF OLD.error_book_entry_id IS NOT NULL AND NEW.error_book_entry_id IS DISTINCT FROM OLD.error_book_entry_id THEN
    RAISE EXCEPTION 'error_book_entry_id đã ghi không đổi được (BR-186)'
      USING ERRCODE = 'check_violation';
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER ledger_entry_guard
  BEFORE UPDATE OR DELETE ON forecast.ledger_entry
  FOR EACH ROW EXECUTE FUNCTION forecast.ledger_entry_guard();

REVOKE DELETE ON forecast.ledger_entry FROM svc_forecast;

-- Scoring events (tab 3 D5.4 Lượt 4: "chấm kỳ ngoài 95% phát forecast.ledger.scored và
-- forecast.ledger.missed"). Eval service opens the Error Book entry from .missed; its payload is
-- what eval.error_book_entry.forecast_detail needs (BR-186). A rescore emits .scored again when
-- the actual changed, and .missed only if the period was not already a miss.
CREATE FUNCTION forecast.ledger_entry_scored_event() RETURNS trigger
LANGUAGE plpgsql AS $$
DECLARE
  detail jsonb;
BEGIN
  IF NEW.status <> 'scored' OR (OLD.status = 'scored' AND NEW.actual IS NOT DISTINCT FROM OLD.actual) THEN
    RETURN NULL;
  END IF;
  detail := jsonb_build_object(
    'ledger_entry_id', NEW.id, 'forecast_model_id', NEW.forecast_model_id,
    'model_version_id', NEW.model_version_id, 'period', NEW.period, 'scope', NEW.scope,
    'actual', NEW.actual, 'point', NEW.point,
    'lo95', NEW.quantiles->'p02_5', 'hi95', NEW.quantiles->'p97_5',
    'abs_error', NEW.abs_error, 'in80', NEW.in80, 'in95', NEW.in95, 'miss', NEW.miss,
    'miss_attribution', NEW.miss_attribution, 'accuracy_log_id', NEW.accuracy_log_id,
    'rescored', OLD.status = 'scored');
  INSERT INTO forecast.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
  VALUES (gen_random_uuid(), NEW.org_id, 'ledger_entry', NEW.id, 'forecast.ledger.scored', detail);
  IF NEW.miss AND (OLD.status <> 'scored' OR OLD.miss IS NOT TRUE) THEN
    INSERT INTO forecast.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'ledger_entry', NEW.id, 'forecast.ledger.missed', detail);
  END IF;
  RETURN NULL;
END
$$;
CREATE TRIGGER ledger_entry_scored_event
  AFTER UPDATE OF status, actual ON forecast.ledger_entry
  FOR EACH ROW EXECUTE FUNCTION forecast.ledger_entry_scored_event();

-- covers: forecast.ledger_entry#1 (no DDL here: formula_mc and monte_carlo are already in the generated CHECKs of model_version and prediction; hitl_request kind formula_component belongs to the inbox schema)
