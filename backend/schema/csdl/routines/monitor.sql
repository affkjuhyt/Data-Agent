-- routines/monitor.sql: what D4.13 (tab 3b) writes in words for schema monitor: the status machine
-- of an external calendar event and the bulletin time derived from its calendar (BR-187).

-- covers: monitor.external_event#0
-- BR-187: expected → confirmed, shifted, cancelled, occurred; confirmed → shifted, cancelled,
-- occurred; shifted → confirmed, cancelled, occurred (occurred and cancelled are final). When the
-- period moves, the old time is kept in shifted_from and the pre-bulletin time is recomputed from the
-- calendar: pre_bulletin_days_before days earlier at pre_bulletin_local_time, in the calendar's
-- timezone. The time is also derived on insert when the service does not give one. Every change is
-- announced on monitor.outbox for the bulletin workflow.
CREATE FUNCTION monitor.external_event_guard() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE
  cal monitor.external_calendar%ROWTYPE;
BEGIN
  IF TG_OP = 'UPDATE' AND NEW.status IS DISTINCT FROM OLD.status
     AND (OLD.status, NEW.status) NOT IN (
       ('expected', 'confirmed'), ('expected', 'shifted'), ('expected', 'cancelled'), ('expected', 'occurred'),
       ('confirmed', 'shifted'), ('confirmed', 'cancelled'), ('confirmed', 'occurred'),
       ('shifted', 'confirmed'), ('shifted', 'cancelled'), ('shifted', 'occurred')) THEN
    RAISE EXCEPTION 'Kỳ sự kiện không chuyển được từ % sang % (BR-187)', OLD.status, NEW.status
      USING ERRCODE = 'check_violation';
  END IF;
  IF TG_OP = 'INSERT' AND NEW.pre_bulletin_at IS NOT NULL THEN
    RETURN NEW;
  END IF;
  IF TG_OP = 'INSERT' OR NEW.expected_at IS DISTINCT FROM OLD.expected_at THEN
    IF TG_OP = 'UPDATE' THEN
      NEW.shifted_from := OLD.expected_at;
    END IF;
    SELECT * INTO cal FROM monitor.external_calendar WHERE id = NEW.calendar_id;
    IF FOUND THEN
      NEW.pre_bulletin_at :=
        ((NEW.expected_at AT TIME ZONE cal.timezone)::date - cal.pre_bulletin_days_before
         + cal.pre_bulletin_local_time) AT TIME ZONE cal.timezone;
    END IF;
  END IF;
  IF TG_OP = 'UPDATE' THEN
    NEW.updated_at := now();
  END IF;
  RETURN NEW;
END
$$;
CREATE TRIGGER external_event_guard BEFORE INSERT OR UPDATE ON monitor.external_event
  FOR EACH ROW EXECUTE FUNCTION monitor.external_event_guard();

CREATE FUNCTION monitor.external_event_changed() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
  IF to_jsonb(NEW) - 'updated_at' IS DISTINCT FROM to_jsonb(OLD) - 'updated_at' THEN
    INSERT INTO monitor.outbox (id, org_id, aggregate_type, aggregate_id, event_type, payload)
    VALUES (gen_random_uuid(), NEW.org_id, 'external_event', NEW.id, 'monitor.external_event.changed',
            jsonb_build_object('event_id', NEW.id, 'calendar_id', NEW.calendar_id,
                               'period_label', NEW.period_label, 'status', NEW.status,
                               'previous_status', OLD.status, 'expected_at', NEW.expected_at,
                               'shifted_from', NEW.shifted_from, 'pre_bulletin_at', NEW.pre_bulletin_at,
                               'occurred_at', NEW.occurred_at));
  END IF;
  RETURN NULL;
END
$$;
CREATE TRIGGER external_event_changed AFTER UPDATE ON monitor.external_event
  FOR EACH ROW EXECUTE FUNCTION monitor.external_event_changed();
