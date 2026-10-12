"""routines/acquire.sql and routines/forecast.sql: the P14 / P15 rules tab 3b writes in words.

The refused and allowed cases follow tab 3 D5.4 (rows "Quy tắc nghiệp vụ P14", "P15", "lệch ngầm",
"thực thể chuẩn", "pháp lý nguồn", "ít dữ liệu", "Lượt 4 · dự báo theo công thức, sổ dự báo").
Fixtures are inserted as admin with only the columns a row needs; the rule is then exercised
through the service role.
"""

from __future__ import annotations

import datetime as dt

import psycopg2.extras
import pytest

from tests.integration.migrate._ddl import as_admin, as_role, fails, new_id, new_org, one

pytestmark = pytest.mark.integration

Json = psycopg2.extras.Json

SHA_A = "a" * 64
SHA_B = "b" * 64
CHECK = "23514"
DENIED = "42501"


def ins(conn, table: str, **cols):
    """INSERT one row with the given columns; returns its id."""
    cols.setdefault("id", new_id())
    names = ", ".join(cols)
    marks = ", ".join(["%s"] * len(cols))
    with conn.cursor() as cur:
        cur.execute(f"INSERT INTO {table} ({names}) VALUES ({marks})", tuple(cols.values()))
    return cols["id"]


def run(conn, sql: str, params: tuple = ()) -> None:
    with conn.cursor() as cur:
        cur.execute(sql, params)


def immediate(conn) -> None:
    run(conn, "SET CONSTRAINTS ALL IMMEDIATE")


# ─── acquire fixtures ───

LEGAL_OK = {
    "legal_status": "approved",
    "terms_url": "https://bank.example/terms",
    "terms_sha256": SHA_A,
    "legal_checklist": Json(
        {k: {"result": "ok"} for k in ("terms", "robots", "copyright", "personal_data")}
    ),
    "legal_evidence": Json([{"kind": "terms", "object_key": "legal/1", "sha256": SHA_A}]),
}


def source(conn, org, approved: bool = True, **cols):
    owner = cols.pop("owner_id", new_id())
    base = {
        "org_id": org,
        "data_space_id": new_id(),
        "name": f"src-{new_id().hex[:6]}",
        "goal": "lãi suất 12 tháng",
        "kind": "web_page",
        "allowed_domains": ["bank.example"],
        "trust_level": 0.8,
        "owner_id": owner,
    }
    if approved:
        now = dt.datetime.now(dt.UTC)
        base.update(
            LEGAL_OK,
            legal_reviewed_by=new_id(),
            legal_reviewed_at=now - dt.timedelta(days=1),
            legal_review_due_at=now + dt.timedelta(days=150),
        )
    base.update(cols)
    return ins(conn, "acquire.crawl_source", **base)


def schema_version(conn, org, src, status="active", version=1):
    cols = {
        "org_id": org,
        "source_id": src,
        "version": version,
        "record_selector": "table tr",
        "status": "draft",
    }
    sv = ins(conn, "acquire.crawl_schema_version", **cols)
    if status != "draft":
        run(
            conn,
            "UPDATE acquire.crawl_schema_version SET status = %s, approved_by = %s, approved_at = now(), "
            "test_required_accuracy = 0.97 WHERE id = %s",
            (status, new_id(), sv),
        )
    return sv


def crawl_run(conn, org, src, sv=None, **cols):
    return ins(
        conn,
        "acquire.crawl_run",
        org_id=org,
        source_id=src,
        schema_version_id=sv,
        trigger="manual",
        **cols,
    )


def active_source(conn, org):
    src = source(conn, org)
    sv = schema_version(conn, org, src)
    run(
        conn,
        "UPDATE acquire.crawl_source SET status = 'active', active_schema_version_id = %s WHERE id = %s",
        (sv, src),
    )
    return src, sv


# ─── crawl_source: review gate (UC-14-04) ───


def test_auto_review_gate_needs_four_runs_at_98_percent(db):
    org = new_org(db)
    src = source(db, org)
    base = dt.datetime(2026, 10, 1, tzinfo=dt.UTC)
    for i, acc in enumerate([0.97, 0.99, 0.985, 0.99]):
        crawl_run(
            db,
            org,
            src,
            status="failed",
            sample_accuracy=acc,
            started_at=base + dt.timedelta(days=i),
        )
    as_role(db, "svc_acquire", org)
    # Only 3 of the last 4 sampled runs reach 98%.
    fails(
        db, "UPDATE acquire.crawl_source SET review_gate = 'auto' WHERE id = %s", (src,), code=CHECK
    )
    crawl_run(
        db, org, src, status="failed", sample_accuracy=0.99, started_at=base + dt.timedelta(days=9)
    )
    run(db, "UPDATE acquire.crawl_source SET review_gate = 'auto' WHERE id = %s", (src,))
    assert one(db, "SELECT review_gate FROM acquire.crawl_source WHERE id = %s", (src,)) == "auto"


# ─── crawl_source: legal review (BR-174) ───


def test_legal_approval_must_be_complete(db):
    org = new_org(db)
    owner = new_id()
    now = dt.datetime.now(dt.UTC)
    src = source(db, org, approved=False, owner_id=owner)
    as_role(db, "svc_acquire", org)
    approve = (
        "UPDATE acquire.crawl_source SET legal_status = 'approved', legal_reviewed_by = %s, legal_reviewed_at = %s, "
        "legal_review_due_at = %s, terms_url = 'https://bank.example/terms', terms_sha256 = %s, "
        "legal_checklist = %s, legal_evidence = %s WHERE id = %s"
    )
    checklist = LEGAL_OK["legal_checklist"]
    evidence = LEGAL_OK["legal_evidence"]
    due = now + dt.timedelta(days=180)
    # The owner approves their own source.
    fails(db, approve, (owner, now, due, SHA_A, checklist, evidence, src), code=CHECK)
    # No evidence.
    fails(db, approve, (new_id(), now, due, SHA_A, checklist, Json([]), src), code=CHECK)
    # Review due in 200 days.
    fails(
        db,
        approve,
        (new_id(), now, now + dt.timedelta(days=200), SHA_A, checklist, evidence, src),
        code=CHECK,
    )
    # Not a SHA-256.
    fails(db, approve, (new_id(), now, due, "ABC", checklist, evidence, src), code=CHECK)
    run(db, approve, (new_id(), now, due, SHA_A, checklist, evidence, src))


def test_source_turns_on_only_with_a_valid_approval(db):
    org = new_org(db)
    pending = source(db, org, approved=False)
    sv = schema_version(db, org, pending)
    as_role(db, "svc_acquire", org)
    fails(
        db,
        "UPDATE acquire.crawl_source SET status = 'active', active_schema_version_id = %s WHERE id = %s",
        (sv, pending),
        code=CHECK,
    )
    as_admin(db)
    now = dt.datetime.now(dt.UTC)
    overdue = source(
        db,
        org,
        legal_reviewed_at=now - dt.timedelta(days=100),
        legal_review_due_at=now - dt.timedelta(days=1),
    )
    sv2 = schema_version(db, org, overdue)
    as_role(db, "svc_acquire", org)
    fails(
        db,
        "UPDATE acquire.crawl_source SET status = 'active', active_schema_version_id = %s WHERE id = %s",
        (sv2, overdue),
        code=CHECK,
    )
    as_admin(db)
    src, _ = active_source(db, org)
    assert one(db, "SELECT status FROM acquire.crawl_source WHERE id = %s", (src,)) == "active"


def test_changed_terms_expire_the_approval_and_pause_the_source(db):
    org = new_org(db)
    src, _ = active_source(db, org)
    run_id = crawl_run(db, org, src, status="fetching")
    page = {
        "org_id": org,
        "run_id": run_id,
        "source_id": src,
        "url": "https://bank.example/rates",
        "fetched_at": dt.datetime.now(dt.UTC),
        "sha256": SHA_A,
    }
    as_role(db, "svc_acquire", org)
    ins(db, "acquire.crawl_page", **page)  # approval in force: the page is stored
    run(
        db,
        "UPDATE acquire.crawl_source SET terms_observed_sha256 = %s, terms_checked_at = now() WHERE id = %s",
        (SHA_B, src),
    )
    assert (
        one(
            db,
            "SELECT (legal_status, status)::text FROM acquire.crawl_source WHERE id = %s",
            (src,),
        )
        == "(expired,paused)"
    )
    fails(
        db,
        "INSERT INTO acquire.crawl_page (id, org_id, run_id, source_id, url, fetched_at, sha256) "
        "VALUES (gen_random_uuid(), %s, %s, %s, 'https://bank.example/p2', now(), %s)",
        (org, run_id, src, SHA_A),
        code=CHECK,
    )


def test_crawl_page_is_append_only(db):
    org = new_org(db)
    src, _ = active_source(db, org)
    run_id = crawl_run(db, org, src, status="fetching")
    page = ins(
        db,
        "acquire.crawl_page",
        org_id=org,
        run_id=run_id,
        source_id=src,
        url="https://bank.example/r",
        fetched_at=dt.datetime.now(dt.UTC),
        sha256=SHA_A,
    )
    as_role(db, "svc_acquire", org)
    fails(db, "UPDATE acquire.crawl_page SET changed = false WHERE id = %s", (page,), code=DENIED)
    fails(db, "DELETE FROM acquire.crawl_page WHERE id = %s", (page,), code=DENIED)


# ─── crawl_run: publish guard (BR-152, BR-153, BR-168) ───


def _record(conn, org, run_id, src, sv, status):
    page = ins(
        conn,
        "acquire.crawl_page",
        org_id=org,
        run_id=run_id,
        source_id=src,
        url=f"https://bank.example/{new_id().hex}",
        fetched_at=dt.datetime.now(dt.UTC),
        sha256=SHA_A,
    )
    reasons = ["low_confidence"] if status == "flagged" else []
    return ins(
        conn,
        "acquire.extracted_record",
        org_id=org,
        run_id=run_id,
        page_id=page,
        schema_version_id=sv,
        entity_key="Bank A",
        period="2026-10-10",
        values=Json({"rate_12m": 4.8}),
        record_confidence=0.9,
        status=status,
        review_reasons=reasons,
    )


def test_publish_needs_an_approved_schema_and_no_open_records(db):
    org = new_org(db)
    src, sv = active_source(db, org)
    draft = schema_version(db, org, src, status="draft", version=2)
    r1 = crawl_run(db, org, src, draft, status="in_review")
    r2 = crawl_run(db, org, src, sv, status="in_review")
    rec = _record(db, org, r2, src, sv, "flagged")
    as_role(db, "svc_acquire", org)
    fails(db, "UPDATE acquire.crawl_run SET status = 'published' WHERE id = %s", (r1,), code=CHECK)
    fails(db, "UPDATE acquire.crawl_run SET status = 'published' WHERE id = %s", (r2,), code=CHECK)
    run(db, "UPDATE acquire.extracted_record SET status = 'approved' WHERE id = %s", (rec,))
    run(db, "UPDATE acquire.crawl_run SET status = 'published' WHERE id = %s", (r2,))


def test_open_semantic_drift_blocks_every_run_until_resolved(db):
    org = new_org(db)
    src, sv = active_source(db, org)
    r1 = crawl_run(db, org, src, sv, status="quarantined")
    r2 = crawl_run(db, org, src, sv, status="in_review")
    drift = ins(
        db,
        "acquire.schema_drift",
        org_id=org,
        source_id=src,
        run_id=r1,
        from_version_id=sv,
        drift_kind="semantic",
        severity="major",
        action="quarantine",
        signals=Json({"affected_fields": ["rate_12m"], "evidence": ["p1"]}),
    )
    as_role(db, "svc_acquire", org)
    fails(db, "UPDATE acquire.crawl_run SET status = 'published' WHERE id = %s", (r2,), code=CHECK)
    # Closing a semantic drift needs the person who closed it.
    fails(
        db,
        "UPDATE acquire.schema_drift SET resolution = 'false_alarm', resolved_at = now() WHERE id = %s",
        (drift,),
        code=CHECK,
    )
    run(
        db,
        "UPDATE acquire.schema_drift SET resolution = 'false_alarm', resolved_at = now(), resolved_by = %s WHERE id = %s",
        (new_id(), drift),
    )
    run(db, "UPDATE acquire.crawl_run SET status = 'published' WHERE id = %s", (r2,))


def test_semantic_drift_shape(db):
    org = new_org(db)
    src, sv = active_source(db, org)
    r = crawl_run(db, org, src, sv, status="quarantined")
    as_role(db, "svc_acquire", org)
    sql = (
        "INSERT INTO acquire.schema_drift (id, org_id, source_id, run_id, from_version_id, drift_kind, severity, action, signals, "
        "resolution, resolved_at, resolved_by) VALUES (gen_random_uuid(), %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)"
    )
    ev = Json({"affected_fields": ["rate_12m"], "evidence": ["p1"]})
    now = dt.datetime.now(dt.UTC)
    fails(
        db, sql, (org, src, r, sv, "semantic", "major", "paused", ev, None, None, None), code=CHECK
    )
    fails(
        db,
        sql,
        (
            org,
            src,
            r,
            sv,
            "semantic",
            "major",
            "quarantine",
            Json({"affected_fields": []}),
            None,
            None,
            None,
        ),
        code=CHECK,
    )
    fails(
        db,
        sql,
        (org, src, r, sv, "semantic", "minor", "quarantine", ev, None, None, None),
        code=CHECK,
    )
    fails(
        db,
        sql,
        (org, src, r, sv, "structural", "major", "paused", Json({}), "false_alarm", now, new_id()),
        code=CHECK,
    )
    run(db, sql, (org, src, r, sv, "semantic", "major", "quarantine", ev, None, None, None))
    run(
        db,
        sql,
        (org, src, r, sv, "structural", "minor", "auto_remap", Json({}), "approved_new", now, None),
    )


# ─── entity_link, reference_entity (BR-169) ───


def _bank(conn, org, **cols):
    base = {
        "org_id": org,
        "entity_type": "bank",
        "canonical_key": "SBV:01203001",
        "legal_name": "Ngân hàng TMCP A",
        "sbv_code": "01203001",
        "registry_source": "sbv_registry",
    }
    base.update(cols)
    return ins(conn, "acquire.reference_entity", **base)


def test_reference_entity_identifiers(db):
    org = new_org(db)
    _bank(db, org, swift_bic="BFTVVNVX")
    as_role(db, "svc_acquire", org)
    sql = (
        "INSERT INTO acquire.reference_entity (id, org_id, entity_type, canonical_key, legal_name, sbv_code, swift_bic, "
        "registry_source, verified_by) VALUES (gen_random_uuid(), %s, 'bank', %s, 'B', %s, %s, %s, %s)"
    )
    fails(db, sql, (org, "SBV:X", None, None, "sbv_registry", None), code=CHECK)  # no identifier
    fails(db, sql, (org, "BIC:BAD", None, "bad-bic", "swift_directory", None), code=CHECK)
    fails(
        db, sql, (org, "SBV:01999001", "01999001", None, "manual", None), code=CHECK
    )  # manual, not verified
    fails(
        db, sql, (org, "BIC:BFTVVNVX", None, "BFTVVNVX", "swift_directory", None), code="23505"
    )  # same BIC twice
    run(db, sql, (org, "SBV:01999001", "01999001", None, "manual", new_id()))
    run(
        db, sql, (org, "SBV:01999002", "01999002", None, "sbv_registry", None)
    )  # two NULL BICs coexist


def test_entity_link_rules_and_reference_sync(db):
    org = new_org(db)
    src = source(db, org)
    bank = _bank(db, org)
    as_role(db, "svc_acquire", org)
    sql = (
        "INSERT INTO acquire.entity_link (id, org_id, data_space_id, entity_type, canonical_key, reference_entity_id, "
        "source_id, source_key, match_score, method, status, decided_by) "
        "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s) "
    )
    ds = new_id()
    fails(
        db,
        sql,
        (new_id(), org, ds, "bank", "x", None, src, "A1", None, "identifier", "pending", None),
        code=CHECK,
    )
    fails(
        db,
        sql,
        (new_id(), org, ds, "bank", "x", bank, src, "A2", 0.91, "fuzzy", "confirmed", None),
        code=CHECK,
    )
    fails(
        db,
        sql,
        (new_id(), org, ds, "bank", "x", None, src, "A3", 1, "exact", "confirmed", None),
        code=CHECK,
    )
    fails(
        db,
        sql,
        (new_id(), org, ds, "enterprise", "x", bank, src, "A4", 1, "identifier", "confirmed", None),
        code=CHECK,
    )
    link = new_id()
    run(
        db,
        sql,
        (
            link,
            org,
            ds,
            "bank",
            "typed-by-matcher",
            bank,
            src,
            "NH A",
            1,
            "identifier",
            "confirmed",
            None,
        ),
    )
    assert (
        one(db, "SELECT canonical_key FROM acquire.entity_link WHERE id = %s", (link,))
        == "SBV:01203001"
    )
    run(
        db,
        sql,
        (
            new_id(),
            org,
            ds,
            "bank",
            "x",
            bank,
            src,
            "Ngan hang A",
            0.82,
            "human",
            "confirmed",
            new_id(),
        ),
    )


# ─── consolidation (BR-153, BR-156) ───


def test_consolidation_spec_tracks_the_last_content_editor(db):
    org = new_org(db)
    creator, renamer, editor = new_id(), new_id(), new_id()
    as_role(db, "svc_acquire", org)
    spec = new_id()
    run(
        db,
        "INSERT INTO acquire.consolidation_spec (id, org_id, data_space_id, name, entity_type, grain, source_ids, owner_id, "
        "last_edited_by, created_by) VALUES (%s, %s, %s, 'lãi suất', 'bank', 'bank x day', %s, %s, %s, %s)",
        (spec, org, new_id(), [new_id()], new_id(), new_id(), creator),
    )
    q = "SELECT last_edited_by FROM acquire.consolidation_spec WHERE id = %s"
    assert one(db, q, (spec,)) == creator
    run(
        db,
        "UPDATE acquire.consolidation_spec SET name = 'lãi suất 12T', updated_by = %s, last_edited_by = %s WHERE id = %s",
        (renamer, renamer, spec),
    )
    assert one(db, q, (spec,)) == creator
    run(
        db,
        "UPDATE acquire.consolidation_spec SET grain = 'bank x week', updated_by = %s WHERE id = %s",
        (editor, spec),
    )
    assert one(db, q, (spec,)) == editor


def test_consolidation_run_takes_published_runs_and_the_spec_editor(db):
    org = new_org(db)
    src, sv = active_source(db, org)
    published = crawl_run(db, org, src, sv, status="published")
    unpublished = crawl_run(db, org, src, sv, status="failed")
    editor = new_id()
    spec = ins(
        db,
        "acquire.consolidation_spec",
        org_id=org,
        data_space_id=new_id(),
        name="ls",
        entity_type="bank",
        grain="bank x day",
        source_ids=[src],
        owner_id=new_id(),
        last_edited_by=editor,
        created_by=editor,
    )
    as_role(db, "svc_acquire", org)
    sql = (
        "INSERT INTO acquire.consolidation_run (id, org_id, spec_id, input_run_ids, started_at, spec_editor_id) "
        "VALUES (%s, %s, %s, %s, now(), %s)"
    )
    fails(db, sql, (new_id(), org, spec, [published, unpublished], editor), code=CHECK)
    cr = new_id()
    run(db, sql, (cr, org, spec, [published], new_id()))  # the sent editor is overwritten
    assert (
        one(db, "SELECT spec_editor_id FROM acquire.consolidation_run WHERE id = %s", (cr,))
        == editor
    )
    fails(
        db,
        "UPDATE acquire.consolidation_run SET spec_editor_id = %s WHERE id = %s",
        (new_id(), cr),
        code=CHECK,
    )


def test_cross_schema_reads_of_the_two_services(db):
    org, other = new_org(db), new_org(db)
    source(db, org)
    as_role(db, "svc_forecast", org)
    assert one(db, "SELECT count(*) FROM acquire.crawl_source") == 1
    fails(db, "UPDATE acquire.crawl_source SET name = 'x'", code=DENIED)
    fails(db, "SELECT count(*) FROM acquire.outbox", code=DENIED)
    as_role(db, "svc_acquire", org)
    fails(db, "SELECT count(*) FROM forecast.prediction", code=DENIED)
    as_role(db, "svc_acquire", other)
    assert one(db, "SELECT count(*) FROM acquire.crawl_source") == 0


# ─── forecast fixtures ───


def model(conn, org, frequency="month"):
    return ins(
        conn,
        "forecast.forecast_model",
        org_id=org,
        data_space_id=new_id(),
        metric_code="huy_dong",
        frequency=frequency,
        owner_id=new_id(),
    )


def version(conn, org, m, algorithm="ets", version_no=1, **cols):
    base = {
        "org_id": org,
        "model_id": m,
        "version": version_no,
        "algorithm": algorithm,
        "training_from": dt.date(2020, 1, 1),
        "training_to": dt.date(2026, 6, 1),
        "n_points": 60,
        "created_by": new_id(),
    }
    base.update(cols)
    return ins(conn, "forecast.model_version", **base)


def backtest(conn, org, v, mase=0.82, in_sample=0.7, n_cutoffs=6, frequency=None, metrics=None):
    return ins(
        conn,
        "forecast.backtest_run",
        org_id=org,
        model_version_id=v,
        frequency=frequency,
        cutoffs=[dt.date(2025, 1, 1) + dt.timedelta(days=7 * i) for i in range(n_cutoffs)],
        horizon=3,
        metrics=Json(metrics or {}),
        baseline_metrics=Json({}),
        mase=mase,
        in_sample_mase=in_sample,
        started_at=dt.datetime(2026, 7, 1, tzinfo=dt.UTC),
        finished_at=dt.datetime(2026, 7, 1, 1, tzinfo=dt.UTC),
    )


PUBLISH = (
    "UPDATE forecast.model_version SET status = 'published', approved_by = %s, approved_at = now(), "
    "backtest_id = %s WHERE id = %s"
)


def published(conn, org, m=None, **bt):
    """A published version that is the model's current one."""
    m = m or model(conn, org)
    v = version(conn, org, m)
    b = backtest(conn, org, v, **bt)
    run(conn, "UPDATE forecast.model_version SET status = 'pending_approval' WHERE id = %s", (v,))
    run(conn, PUBLISH, (new_id(), b, v))
    run(conn, "UPDATE forecast.forecast_model SET current_version_id = %s WHERE id = %s", (v, m))
    return m, v


# ─── model_version, forecast_model (BR-158, BR-160, BR-172) ───


def test_version_life_cycle(db):
    org = new_org(db)
    m = model(db, org)
    as_role(db, "svc_forecast", org)
    fails(
        db,
        "INSERT INTO forecast.model_version (id, org_id, model_id, version, algorithm, training_from, training_to, status) "
        "VALUES (gen_random_uuid(), %s, %s, 9, 'ets', '2020-01-01', '2026-01-01', 'published')",
        (org, m),
        code=CHECK,
    )
    as_admin(db)
    v = version(db, org, m)
    as_role(db, "svc_forecast", org)
    run(db, "UPDATE forecast.model_version SET status = 'pending_approval' WHERE id = %s", (v,))
    fails(
        db, "UPDATE forecast.model_version SET status = 'degraded' WHERE id = %s", (v,), code=CHECK
    )


def test_publish_gate(db):
    org = new_org(db)
    m = model(db, org)
    v = version(db, org, m)
    bad = backtest(db, org, v, mase=1.05)
    overfit = backtest(db, org, v, mase=0.9, in_sample=0.3)
    good = backtest(db, org, v, mase=0.82, in_sample=0.7)
    other = version(db, org, m, version_no=2)
    others = backtest(db, org, other)
    run(
        db,
        "UPDATE forecast.model_version SET status = 'pending_approval' WHERE id IN (%s, %s)",
        (v, other),
    )
    creator = one(db, "SELECT created_by FROM forecast.model_version WHERE id = %s", (v,))
    as_role(db, "svc_forecast", org)
    fails(db, PUBLISH, (new_id(), bad, v), code=CHECK)  # MASE 1.05
    fails(db, PUBLISH, (new_id(), overfit, v), code=CHECK)  # 3x the in-sample MASE
    fails(db, PUBLISH, (new_id(), others, v), code=CHECK)  # backtest of another version
    fails(db, PUBLISH, (creator, good, v), code=CHECK)  # creator approves
    fails(
        db,
        "UPDATE forecast.model_version SET status = 'published', backtest_id = %s WHERE id = %s",
        (good, v),
        code=CHECK,
    )
    run(db, "UPDATE forecast.model_version SET n_points = NULL WHERE id = %s", (v,))
    fails(db, PUBLISH, (new_id(), good, v), code=CHECK)  # n_points missing (BR-172)
    run(db, "UPDATE forecast.model_version SET n_points = 60 WHERE id = %s", (v,))
    run(db, PUBLISH, (new_id(), good, v))
    run(db, "UPDATE forecast.forecast_model SET current_version_id = %s WHERE id = %s", (v, m))
    immediate(db)


def test_small_data_rules(db):
    org = new_org(db)
    m = model(db, org)
    as_role(db, "svc_forecast", org)
    sql = (
        "INSERT INTO forecast.model_version (id, org_id, model_id, version, algorithm, training_from, training_to, "
        "n_points, n_exog, pooled_series) VALUES (gen_random_uuid(), %s, %s, %s, %s, '2020-01-01', '2026-01-01', %s, %s, %s)"
    )
    fails(db, sql, (org, m, 1, "lightgbm", 40, 0, 1), code=CHECK)
    fails(db, sql, (org, m, 2, "sarimax", 30, 3, 1), code=CHECK)
    fails(db, sql, (org, m, 3, "ets", 20, 0, 1), code=CHECK)
    run(db, sql, (org, m, 4, "lightgbm", 40, 0, 5))


def test_current_version_must_be_released(db):
    org = new_org(db)
    m = model(db, org)
    v = version(db, org, m)
    as_role(db, "svc_forecast", org)
    fails(
        db,
        "UPDATE forecast.forecast_model SET current_version_id = %s WHERE id = %s; SET CONSTRAINTS ALL IMMEDIATE",
        (v, m),
        code=CHECK,
    )


def test_model_and_version_degrade_together(db):
    org = new_org(db)
    m, v = published(db, org)
    immediate(db)
    run(db, "SET CONSTRAINTS ALL DEFERRED")
    as_role(db, "svc_forecast", org)
    fails(
        db,
        "UPDATE forecast.model_version SET status = 'degraded' WHERE id = %s; SET CONSTRAINTS ALL IMMEDIATE",
        (v,),
        code=CHECK,
    )
    run(db, "SET CONSTRAINTS ALL DEFERRED")
    run(db, "UPDATE forecast.model_version SET status = 'degraded' WHERE id = %s", (v,))
    run(db, "UPDATE forecast.forecast_model SET status = 'degraded' WHERE id = %s", (m,))
    immediate(db)


# ─── backtest_run (ADR-15) ───


def test_backtest_frequency_comes_from_the_model(db):
    org = new_org(db)
    weekly = model(db, org, frequency="week")
    v = version(db, org, weekly)
    as_role(db, "svc_forecast", org)
    b = backtest(db, org, v, n_cutoffs=20)
    assert one(db, "SELECT frequency FROM forecast.backtest_run WHERE id = %s", (b,)) == "week"
    fails(
        db,
        "INSERT INTO forecast.backtest_run (id, org_id, model_version_id, frequency, cutoffs, horizon, metrics, "
        "baseline_metrics, mase, started_at) SELECT gen_random_uuid(), org_id, model_version_id, 'month', cutoffs, "
        "horizon, metrics, baseline_metrics, mase, started_at FROM forecast.backtest_run WHERE id = %s",
        (b,),
        code=CHECK,
    )
    fails(
        db,
        "INSERT INTO forecast.backtest_run (id, org_id, model_version_id, cutoffs, horizon, metrics, baseline_metrics, "
        "mase, started_at) SELECT gen_random_uuid(), org_id, model_version_id, cutoffs[1:6], horizon, metrics, "
        "baseline_metrics, mase, started_at FROM forecast.backtest_run WHERE id = %s",
        (b,),
        code=CHECK,
    )


# ─── prediction (BR-158, BR-173, BR-184) ───

PRED = (
    "INSERT INTO forecast.prediction (id, org_id, model_version_id, scenario_id, cutoff_at, period, point, lo80, hi80, "
    "lo95, hi95, interval_method, exog_uncertainty, contributions, is_experimental) "
    "VALUES (%s, %s, %s, %s, now(), %s, 5, 4, 6, 3, 7, %s, %s, %s, %s)"
)


def test_prediction_labels(db):
    org = new_org(db)
    m = model(db, org)
    draft = version(db, org, m, n_exog=2)
    scen = ins(db, "forecast.scenario", org_id=org, model_id=m, name="+0,5 điểm")
    as_role(db, "svc_forecast", org)
    p = dt.date(2026, 11, 1)
    fails(
        db,
        PRED,
        (new_id(), org, draft, None, p, "conformal", "simulated", Json({}), False),
        code=CHECK,
    )
    fails(
        db, PRED, (new_id(), org, draft, None, p, "conformal", "none", Json({}), True), code=CHECK
    )
    fails(
        db,
        PRED,
        (new_id(), org, draft, scen, p, "conformal", "simulated", Json({}), True),
        code=CHECK,
    )
    fails(
        db,
        PRED,
        (new_id(), org, draft, None, p, "bootstrap", "simulated", Json({}), True),
        code=CHECK,
    )
    run(db, PRED, (new_id(), org, draft, None, p, "conformal_pooled", "simulated", Json({}), True))
    pid = new_id()
    run(db, PRED, (pid, org, draft, scen, p, "parametric", "scenario_fixed", Json({}), True))
    fails(db, "UPDATE forecast.prediction SET point = 6 WHERE id = %s", (pid,), code=DENIED)
    fails(db, "DELETE FROM forecast.prediction WHERE id = %s", (pid,), code=DENIED)


def test_formula_mc_prediction_shape(db):
    org = new_org(db)
    m = model(db, org, frequency="week")
    v = version(db, org, m, algorithm="formula_mc", n_points=30)
    as_role(db, "svc_forecast", org)
    p = dt.date(2026, 7, 9)
    parts = Json({k: {"vnd_per_litre": 0} for k in ("world", "fx", "tax", "fund", "proxy_error")})
    fails(db, PRED, (new_id(), org, v, None, p, "conformal", "simulated", parts, True), code=CHECK)
    fails(db, PRED, (new_id(), org, v, None, p, "monte_carlo", "none", parts, True), code=CHECK)
    fails(
        db,
        PRED,
        (new_id(), org, v, None, p, "monte_carlo", "simulated", Json({"world": {}}), True),
        code=CHECK,
    )
    run(db, PRED, (new_id(), org, v, None, p, "monte_carlo", "simulated", parts, True))


# ─── formula_model, formula_component (BR-182, BR-183) ───


def _formula_model(conn, org, v):
    return ins(
        conn,
        "forecast.formula_model",
        org_id=org,
        model_version_id=v,
        metric_version_id=new_id(),
        product_code="e5ron92",
        formula=Json({"terms": []}),
        owner_id=new_id(),
    )


def test_formula_model_belongs_to_a_formula_version_and_freezes(db):
    org = new_org(db)
    m = model(db, org, frequency="week")
    ets = version(db, org, m)
    fv = version(db, org, m, algorithm="formula_mc", version_no=2, n_points=30)
    as_role(db, "svc_forecast", org)
    fails(
        db,
        "INSERT INTO forecast.formula_model (id, org_id, model_version_id, metric_version_id, product_code, formula, owner_id) "
        "VALUES (gen_random_uuid(), %s, %s, gen_random_uuid(), 'e5ron92', '{}', gen_random_uuid())",
        (org, ets),
        code=CHECK,
    )
    fails(
        db,
        "UPDATE forecast.model_version SET status = 'pending_approval' WHERE id = %s",
        (fv,),
        code=CHECK,
    )
    fm = _formula_model(db, org, fv)
    run(db, "UPDATE forecast.model_version SET status = 'pending_approval' WHERE id = %s", (fv,))
    no_recon = backtest(db, org, fv, n_cutoffs=20)
    fails(db, PUBLISH, (new_id(), no_recon, fv), code=CHECK)
    b = backtest(db, org, fv, n_cutoffs=20, metrics={"reconstruction_mae": 41})
    run(db, PUBLISH, (new_id(), b, fv))
    fails(
        db,
        "UPDATE forecast.formula_model SET formula = '{\"terms\": [1]}' WHERE id = %s",
        (fm,),
        code=CHECK,
    )
    run(db, "UPDATE forecast.formula_model SET n_draws = 20000 WHERE id = %s", (fm,))


COMPONENT = (
    "INSERT INTO forecast.formula_component (id, org_id, code, product_code, value, unit, valid_from, "
    "legal_doc_number, legal_doc_url, status, created_by) "
    "VALUES (%s, %s, 'import_tax_rate', 'e5ron92', %s, 'percent', %s, '9017/BTC-QLG', 'https://mof.gov.vn/9017', %s, %s)"
)
CERTIFY = "UPDATE forecast.formula_component SET status = 'certified', certified_by = %s, certified_at = now() WHERE id = %s"


def test_formula_component_life_cycle(db):
    org = new_org(db)
    entrant, checker = new_id(), new_id()
    as_role(db, "svc_forecast", org)
    july2 = dt.datetime(2026, 7, 2, tzinfo=dt.UTC)
    july16 = dt.datetime(2026, 7, 16, tzinfo=dt.UTC)
    fails(
        db, COMPONENT, (new_id(), org, 2.15, july2, "certified", entrant), code=CHECK
    )  # certified at once
    fails(db, COMPONENT, (new_id(), org, 120, july2, "draft", entrant), code=CHECK)  # 120 %
    old = new_id()
    run(db, COMPONENT, (old, org, 2.15, july2, "pending_review", entrant))
    fails(db, CERTIFY, (entrant, old), code=CHECK)  # self-certification
    run(db, CERTIFY, (checker, old))
    fails(db, "UPDATE forecast.formula_component SET value = 3 WHERE id = %s", (old,), code=CHECK)
    earlier = new_id()
    run(db, COMPONENT, (earlier, org, 2.5, july2 - dt.timedelta(days=5), "pending_review", entrant))
    fails(db, CERTIFY, (checker, earlier), code=CHECK)  # valid_from before the value in force
    new = new_id()
    run(db, COMPONENT, (new, org, 2.0, july16, "pending_review", entrant))
    run(db, CERTIFY, (checker, new))
    assert (
        one(
            db,
            "SELECT valid_to = %s AND status = 'superseded' FROM forecast.formula_component WHERE id = %s",
            (july16, old),
        )
        is True
    )


# ─── ledger_entry (BR-185, BR-186) ───


def _ledger(conn, org):
    m, v = published(conn, org)
    pred = new_id()
    run(conn, PRED, (pred, org, v, None, dt.date(2026, 7, 9), "conformal", "none", Json({}), False))
    issued = dt.datetime(2026, 7, 8, 13, tzinfo=dt.UTC)
    entry = ins(
        conn,
        "forecast.ledger_entry",
        org_id=org,
        forecast_model_id=m,
        model_version_id=v,
        prediction_id=pred,
        period=dt.date(2026, 7, 9),
        issued_at=issued,
        issued_via="bulletin",
        event_at=issued + dt.timedelta(hours=2),
        baseline_value=19730,
        point=19900,
        quantiles=Json({"p02_5": 19500, "p50": 19900, "p97_5": 20300}),
    )
    return entry, m, v, pred


SCORE = (
    "UPDATE forecast.ledger_entry SET status = 'scored', actual = %s, abs_error = %s, in80 = %s, in95 = %s, miss = %s, "
    "scored_at = now() WHERE id = %s"
)


def test_ledger_entry_is_frozen_and_scored_once(db):
    org = new_org(db)
    entry, m, v, pred = _ledger(db, org)
    as_role(db, "svc_forecast", org)
    fails(
        db,
        "INSERT INTO forecast.ledger_entry (id, org_id, forecast_model_id, model_version_id, prediction_id, period, issued_at, "
        "issued_via, event_at, baseline_value, point, quantiles) VALUES (gen_random_uuid(), %s, %s, %s, %s, '2026-07-09', "
        "'2026-07-09 16:00+07', 'answer', '2026-07-09 15:00+07', 19730, 19900, '{}')",
        (org, m, v, pred),
        code=CHECK,
    )
    fails(db, "UPDATE forecast.ledger_entry SET point = 20000 WHERE id = %s", (entry,), code=CHECK)
    fails(db, "DELETE FROM forecast.ledger_entry WHERE id = %s", (entry,), code=DENIED)
    fails(
        db, SCORE, (20600, 700, False, False, False, entry), code=CHECK
    )  # outside 95 % but not a miss
    run(db, SCORE, (20600, 700, False, False, True, entry))
    events = one(
        db,
        "SELECT string_agg(event_type, ',' ORDER BY event_type) FROM forecast.outbox WHERE aggregate_id = %s",
        (entry,),
    )
    assert events == "forecast.ledger.missed,forecast.ledger.scored"
    fails(
        db, "UPDATE forecast.ledger_entry SET status = 'open' WHERE id = %s", (entry,), code=CHECK
    )
    run(
        db,
        "UPDATE forecast.ledger_entry SET error_book_entry_id = %s WHERE id = %s",
        (new_id(), entry),
    )
    fails(
        db,
        "UPDATE forecast.ledger_entry SET error_book_entry_id = %s WHERE id = %s",
        (new_id(), entry),
        code=CHECK,
    )
    run(db, SCORE, (20590, 690, False, False, True, entry))  # rescoring after a corrected price
    as_admin(db)
    fails(db, "DELETE FROM forecast.ledger_entry WHERE id = %s", (entry,), code=DENIED)
