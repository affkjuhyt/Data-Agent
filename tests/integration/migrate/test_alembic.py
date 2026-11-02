"""The Alembic chain the migrate Job runs: dataagent, then the audit database apart from it."""

import os
import subprocess
import sys
import uuid
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit

import psycopg2
import psycopg2.errors
import pytest

from alembic.script import ScriptDirectory

pytestmark = pytest.mark.integration

ALEMBIC = Path(__file__).resolve().parents[3] / "backend" / "alembic"


def _db(dsn: str, name: str) -> str:
    return urlunsplit(urlsplit(dsn)._replace(path=f"/{name}"))


def _fresh(admin: str, *names: str) -> None:
    conn = psycopg2.connect(admin)
    conn.autocommit = True
    with conn.cursor() as cur:
        for name in names:
            cur.execute(f'DROP DATABASE IF EXISTS "{name}" WITH (FORCE)')
        cur.execute(f'CREATE DATABASE "{names[0]}"')
    conn.close()


def _alembic(dsn: str, *args: str) -> None:
    subprocess.run(
        [sys.executable, "-m", "alembic", "-c", str(ALEMBIC / "alembic.ini"), *args],
        env={**os.environ, "DATABASE_URL": dsn},
        check=True,
        cwd=ALEMBIC,
    )


def _one(dsn: str, query: str):
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            return cur.fetchone()[0]
    finally:
        conn.close()


@pytest.fixture
def admin(ddl_server) -> str:
    return _db(ddl_server["dataagent"], "postgres")


def test_a_fresh_install_runs_the_whole_chain_and_puts_audit_apart(admin):
    # Every revision must apply on a database that never saw the legacy schema or its roles.
    _fresh(admin, "chain_fresh", "chain_fresh_audit")
    _alembic(_db(admin, "chain_fresh"), "upgrade", "head")
    head = ScriptDirectory(str(ALEMBIC)).get_current_head()
    assert _one(_db(admin, "chain_fresh"), "SELECT version_num FROM alembic_version") == head
    assert _one(_db(admin, "chain_fresh"), "SELECT to_regnamespace('audit') IS NULL") is True
    assert (
        _one(_db(admin, "chain_fresh_audit"), "SELECT to_regclass('audit.audit_log') IS NOT NULL")
        is True
    )


def test_a_cluster_with_audit_inside_dataagent_is_moved(admin):
    # The first baseline also applied the audit DDL to dataagent; v3_0002 moves it out.
    _fresh(admin, "chain_old", "chain_old_audit")
    old = _db(admin, "chain_old")
    _alembic(old, "upgrade", "v3_0001")
    conn = psycopg2.connect(old)
    with conn, conn.cursor() as cur:
        cur.execute((ALEMBIC / "sql" / "dataagent_audit.sql").read_text())
    conn.close()
    _alembic(old, "upgrade", "v3_0002")
    assert _one(old, "SELECT to_regnamespace('audit') IS NULL") is True
    assert (
        _one(_db(admin, "chain_old_audit"), "SELECT to_regclass('audit.audit_log') IS NOT NULL")
        is True
    )


def test_an_existing_audit_database_gets_the_export_job(admin):
    # A cluster upgraded before 12/10/2026 has the audit database without audit.export_job.
    _fresh(admin, "chain_export", "chain_export_audit")
    _alembic(_db(admin, "chain_export"), "upgrade", "v3_0005")
    audit = _db(admin, "chain_export_audit")
    conn = psycopg2.connect(audit)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP TABLE audit.export_job")
    conn.close()
    _alembic(_db(admin, "chain_export"), "upgrade", "head")
    assert _one(audit, "SELECT to_regclass('audit.export_job') IS NOT NULL") is True
    assert (
        _one(audit, "SELECT relrowsecurity FROM pg_class WHERE oid = 'audit.export_job'::regclass")
        is True
    )
    assert (
        _one(audit, "SELECT has_table_privilege('svc_audit', 'audit.export_job', 'INSERT')") is True
    )


def test_the_decisions_of_12_10_apply_to_a_migrated_cluster(admin):
    _fresh(admin, "chain_1210", "chain_1210_audit")
    db, audit = _db(admin, "chain_1210"), _db(admin, "chain_1210_audit")
    _alembic(db, "upgrade", "v3_0008")
    # Put back what a cluster migrated before 12/10/2026 has (the baseline is generated today).
    for dsn, sql in (
        (audit, "DROP TABLE audit.outbox"),
        (db, "ALTER TABLE catalog.source DROP COLUMN writeback_tables"),
        (db, "ALTER TABLE pipeline.data_product DROP COLUMN owner_id"),
        (db, "ALTER TABLE ingest.quality_result DROP COLUMN acknowledged_note"),
        (db, "DROP INDEX conflict.source_authority_data_space_id_data_kind_key_2"),
    ):
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.close()
    _alembic(db, "upgrade", "head")
    assert _one(audit, "SELECT to_regclass('audit.outbox') IS NOT NULL") is True
    assert _one(audit, "SELECT has_table_privilege('svc_outbox_relay', 'audit.outbox', 'SELECT')")
    for schema, table, name in (
        ("catalog", "source", "writeback_tables"),
        ("pipeline", "data_product", "owner_id"),
        ("ingest", "quality_result", "acknowledged_note"),
    ):
        assert (
            _one(
                db,
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = "
                f"'{schema}' AND table_name = '{table}' AND column_name = '{name}'",
            )
            == 1
        )
    assert _one(
        db,
        "SELECT to_regclass('conflict.source_authority_data_space_id_data_kind_key_2') IS NOT NULL",
    )


def test_the_endpoint_batches_of_12_10_apply_to_a_migrated_cluster(admin):
    # v3_0011: what the A2 endpoints added on 12/10/2026 need, on a cluster at v3_0010.
    _fresh(admin, "chain_ep1210", "chain_ep1210_audit")
    db, audit = _db(admin, "chain_ep1210"), _db(admin, "chain_ep1210_audit")
    _alembic(db, "upgrade", "v3_0010")
    # Put back what a cluster migrated before v3_0011 has (the baseline is generated today).
    # Dropping a column also drops the CHECKs and the FK that name it.
    conn = psycopg2.connect(db)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            """
            DROP TABLE agent.tool_def;
            ALTER TABLE agent.agent_def DROP COLUMN status_before_lock;
            ALTER TABLE agent.agent_def DROP CONSTRAINT agent_def_status_check,
              ADD CONSTRAINT agent_def_status_check
                CHECK (status IN ('draft','testing','running','paused','retired'));
            ALTER TABLE agent.agent_run DROP COLUMN retry_of;
            ALTER TABLE skill.skill_run DROP COLUMN mode;
            ALTER TABLE query.query_run DROP CONSTRAINT query_run_caller_kind_check,
              ADD CONSTRAINT query_run_caller_kind_check
                CHECK (caller_kind IN ('question','tile','skill_run','agent_run','pipeline',
                                       'period','eval','receipt_replay'));
            ALTER TABLE pipeline.data_mart DROP COLUMN suggested_dimensions;
            """
        )
    conn.close()
    _alembic(db, "upgrade", "head")
    for schema, table, name in (
        ("agent", "agent_def", "status_before_lock"),
        ("agent", "agent_run", "retry_of"),
        ("skill", "skill_run", "mode"),
        ("pipeline", "data_mart", "suggested_dimensions"),
    ):
        assert (
            _one(
                db,
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = "
                f"'{schema}' AND table_name = '{table}' AND column_name = '{name}'",
            )
            == 1
        ), (schema, table, name)
    assert _one(db, "SELECT count(*) FROM agent.tool_def") > 0
    assert _one(db, "SELECT has_table_privilege('svc_skill', 'agent.tool_def', 'SELECT')") is True
    assert _one(db, "SELECT has_table_privilege('svc_agent', 'agent.tool_def', 'INSERT')") is False

    def checks(table: str) -> str:
        return _one(
            db,
            "SELECT string_agg(pg_get_constraintdef(oid), ' | ') FROM pg_constraint"
            f" WHERE conrelid = '{table}'::regclass AND contype = 'c'",
        )

    assert "'locked'" in checks("agent.agent_def")
    assert "status_before_lock IS NOT NULL" in checks("agent.agent_def")
    assert "'ad_hoc'" in checks("query.query_run")
    assert "'try'" in checks("skill.skill_run")
    # The CHECKs are replaced, not added next to the old ones.
    assert _one(db, "SELECT count(*) FROM pg_constraint WHERE conname = 'agent_def_check'") == 1
    # The audit worker releases an approved export and reads nothing else of the audit database.
    assert (
        _one(audit, "SELECT has_table_privilege('svc_audit_worker', 'audit.export_job', 'SELECT')")
        is True
    )
    assert (
        _one(
            audit,
            "SELECT has_column_privilege('svc_audit_worker', 'audit.export_job', 'status',"
            " 'UPDATE')",
        )
        is True
    )
    assert (
        _one(audit, "SELECT has_table_privilege('svc_audit_worker', 'audit.audit_log', 'SELECT')")
        is False
    )
    # RESTRICTIVE worker_decision_only: the worker only moves a waiting export to queued (approved)
    # or failed; nothing else of a job is its to change.
    org, requester, approver = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    waiting, waiting_too, queued = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
    conn = psycopg2.connect(audit)
    try:
        with conn.cursor() as cur:
            for job, status in (
                (waiting, "waiting_approval"),
                (waiting_too, "waiting_approval"),
                (queued, "queued"),
            ):
                cur.execute(
                    "INSERT INTO audit.export_job (id, org_id, status, requested_by, row_count)"
                    " VALUES (%s, %s, %s, %s, 200000)",
                    (str(job), str(org), status, str(requester)),
                )
            cur.execute("SET ROLE svc_audit_worker")
            cur.execute("SELECT set_config('app.org_id', %s, false)", (str(org),))
            cur.execute(
                "UPDATE audit.export_job SET status = 'queued', approved_by = %s,"
                " approved_at = now() WHERE id = %s",
                (str(approver), str(waiting)),
            )
            assert cur.rowcount == 1
            cur.execute(
                "UPDATE audit.export_job SET status = 'ready' WHERE id = %s", (str(queued),)
            )
            assert cur.rowcount == 0
            with pytest.raises(psycopg2.errors.InsufficientPrivilege):
                cur.execute(
                    "UPDATE audit.export_job SET status = 'queued' WHERE id = %s",
                    (str(waiting_too),),
                )
    finally:
        conn.rollback()
        conn.close()


def test_the_industry_packs_are_removed_from_a_migrated_cluster(admin):
    # v3_0017 (01/11/2026): a cluster migrated before it has schema pack, its roles, the columns
    # pointing into it and the pack values of a few CHECKs; the baseline is generated without them.
    _fresh(admin, "chain_packs", "chain_packs_audit")
    db = _db(admin, "chain_packs")
    _alembic(db, "upgrade", "v3_0016")
    conn = psycopg2.connect(db)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE ROLE svc_pack NOLOGIN;
            CREATE ROLE pack_read NOLOGIN;
            GRANT pack_read TO svc_context;
            CREATE SCHEMA pack AUTHORIZATION da_owner;
            CREATE TABLE pack.industry_pack (id uuid PRIMARY KEY, org_id uuid NOT NULL);
            CREATE TABLE pack.pack_install (
              id uuid PRIMARY KEY, pack_id uuid REFERENCES pack.industry_pack(id));
            ALTER TABLE pack.industry_pack OWNER TO da_owner;
            ALTER TABLE pack.pack_install OWNER TO da_owner;
            GRANT USAGE ON SCHEMA pack TO svc_pack, pack_read;
            GRANT SELECT, INSERT ON pack.industry_pack TO svc_pack;
            GRANT SELECT ON pack.industry_pack TO pack_read;
            GRANT SELECT ON org.data_space TO svc_pack;
            ALTER TABLE org.organization ADD COLUMN industry_code text;
            ALTER TABLE org.data_space ADD COLUMN pack_install_id uuid;
            ALTER TABLE context.metric ADD COLUMN pack_metric_id uuid;
            ALTER TABLE eval.error_book_entry
              ADD COLUMN pack_contribution text NOT NULL DEFAULT 'none',
              ADD COLUMN pack_proposal_ref text,
              ADD CHECK (pack_contribution IN ('none','proposed','accepted','rejected'));
            ALTER TABLE skill.skill DROP CONSTRAINT skill_origin_check,
              ADD CHECK (origin IN ('manual','from_session','agent_suggested','pack'));
            ALTER TABLE inbox.proposal DROP CONSTRAINT proposal_kind_check,
              ADD CHECK (kind IN ('metric_change','synonym','relationship','example','glossary',
                'schema_change','skill_publish','pack_activate','pack_upgrade','metric_override',
                'source_authority','writeback','document_validity','feedback_fix',
                'knowledge_page','data_product'));
            INSERT INTO agent.tool_def (id, code, permission_level, owner_service, endpoints,
              model_description, execution_kinds, default_checkpoint, use_cases)
            VALUES (gen_random_uuid(), 'pack.suggest_mappings', 'write_derived', 'Pack service',
              '{}', 'd', '{plan}', 'always', '{UC-09-02}');
            INSERT INTO org.role (id, org_id, code, name, capabilities)
            VALUES ('a0000000-0000-0000-0000-0000000000a1', gen_random_uuid(), 'author', 'Author',
              '{ask,pack_author}');
            """
        )
    conn.close()
    _alembic(db, "upgrade", "head")
    assert _one(db, "SELECT to_regnamespace('pack') IS NULL") is True
    assert _one(db, "SELECT count(*) FROM pg_roles WHERE rolname IN ('svc_pack', 'pack_read')") == 0
    for schema, table, name in (
        ("org", "organization", "industry_code"),
        ("org", "data_space", "pack_install_id"),
        ("context", "metric", "pack_metric_id"),
        ("eval", "error_book_entry", "pack_contribution"),
        ("eval", "error_book_entry", "pack_proposal_ref"),
    ):
        assert (
            _one(
                db,
                "SELECT count(*) FROM information_schema.columns WHERE table_schema = "
                f"'{schema}' AND table_name = '{table}' AND column_name = '{name}'",
            )
            == 0
        ), (schema, table, name)

    def checks(table: str) -> str:
        return _one(
            db,
            "SELECT string_agg(pg_get_constraintdef(oid), ' | ') FROM pg_constraint"
            f" WHERE conrelid = '{table}'::regclass AND contype = 'c'",
        )

    # The narrower CHECK replaces the old one, it is not added next to it.
    assert "'pack'" not in checks("skill.skill") and "'from_session'" in checks("skill.skill")
    assert "'pack_activate'" not in checks("inbox.proposal")
    assert "'data_product'" in checks("inbox.proposal")
    assert _one(db, "SELECT count(*) FROM agent.tool_def WHERE code LIKE 'pack.%'") == 0
    assert _one(db, "SELECT count(*) FROM agent.tool_def") > 0
    assert (
        _one(
            db,
            "SELECT capabilities::text FROM org.role"
            " WHERE id = 'a0000000-0000-0000-0000-0000000000a1'",
        )
        == "{ask}"
    )


def test_the_ingest_profile_reaches_a_migrated_cluster(admin):
    # v3_0018 (01/11/2026, GEN-11): a cluster migrated before it has no ingest_profile; the
    # baseline is generated with it, so the test takes it away before the upgrade.
    _fresh(admin, "chain_profile", "chain_profile_audit")
    db = _db(admin, "chain_profile")
    _alembic(db, "upgrade", "v3_0017")
    tables = ("org.data_space", "document.collection", "acquire.crawl_source")
    conn = psycopg2.connect(db)
    conn.autocommit = True
    with conn.cursor() as cur:
        for table in tables:
            cur.execute(f"ALTER TABLE {table} DROP COLUMN ingest_profile")
    conn.close()
    _alembic(db, "upgrade", "head")
    for table in tables:
        schema, name = table.split(".")
        assert (
            _one(
                db,
                "SELECT column_default FROM information_schema.columns WHERE table_schema = "
                f"'{schema}' AND table_name = '{name}' AND column_name = 'ingest_profile'"
                " AND is_nullable = 'NO'",
            )
            == "'{}'::jsonb"
        ), table
        assert "jsonb_typeof(ingest_profile)" in _one(
            db,
            "SELECT string_agg(pg_get_constraintdef(oid), ' | ') FROM pg_constraint"
            f" WHERE conrelid = '{table}'::regclass AND contype = 'c'",
        ), table


def test_the_calendar_the_time_dimension_and_one_normal_form_reach_a_migrated_cluster(admin):
    # v3_0019 to v3_0021 (01/11/2026, GEN-21/22/31): a cluster migrated before them has no
    # calendar columns, no time_dimension_id, the narrow time_grain CHECK, and phrases stored
    # with their punctuation. The baseline is generated with the new shape, so the test takes it
    # back before the upgrade, as the cluster would have it.
    _fresh(admin, "chain_gen", "chain_gen_audit")
    db = _db(admin, "chain_gen")
    _alembic(db, "upgrade", "v3_0018")
    org, space, target = (str(uuid.uuid4()) for _ in range(3))
    conn = psycopg2.connect(db)
    conn.autocommit = True
    with conn.cursor() as cur:
        for column in ("week_start", "fiscal_year_start_month", "fiscal_year_named_by"):
            cur.execute(f"ALTER TABLE org.work_calendar DROP COLUMN {column}")
        cur.execute("ALTER TABLE context.metric_version DROP COLUMN time_dimension_id")
        cur.execute(
            "ALTER TABLE context.metric_version DROP CONSTRAINT metric_version_time_grain_check"
        )
        cur.execute(
            "ALTER TABLE context.metric_version ADD CHECK (time_grain IN ('day','month','quarter','year'))"
        )
        cur.execute(
            "INSERT INTO context.synonym (id, org_id, data_space_id, phrase, phrase_norm,"
            " target_kind, target_id) VALUES (%s, %s, %s, 'Doanh-thu (thuần)', 'doanh-thu (thuan)',"
            " 'metric', %s)",
            (str(uuid.uuid4()), org, space, target),
        )
        cur.execute(
            "INSERT INTO context.glossary_term (id, org_id, data_space_id, term, term_norm,"
            " definition) VALUES (%s, %s, %s, 'Tỷ lệ (%%)', 'ty le (%%)', 'x')",
            (str(uuid.uuid4()), org, space),
        )
    conn.close()
    _alembic(db, "upgrade", "head")
    assert (
        _one(
            db,
            "SELECT count(*) FROM information_schema.columns WHERE table_schema = 'org'"
            " AND table_name = 'work_calendar' AND column_name IN"
            " ('week_start', 'fiscal_year_start_month', 'fiscal_year_named_by')",
        )
        == 3
    )
    checks = _one(
        db,
        "SELECT string_agg(pg_get_constraintdef(oid), ' | ') FROM pg_constraint"
        " WHERE conrelid = 'context.metric_version'::regclass AND contype = 'c'"
        " AND pg_get_constraintdef(oid) LIKE '%time_grain%'",
    )
    # One CHECK, the wide one: the narrow one is not left beside it to refuse a week.
    assert checks.count("time_grain") == 1 and "'fiscal_year'" in checks, checks
    assert _one(db, "SELECT phrase_norm FROM context.synonym") == "doanh thu thuan"
    assert _one(db, "SELECT term_norm FROM context.glossary_term") == "ty le %"


# The ten approval tables of v3_0024 and audit.export_job, before it: the person-inequality
# CHECKs (unnamed in the baseline, so found by definition), the old certifier CHECK, no switch,
# no trigger, no self_approved, no sql_changed_by (v3_0026).
_BEFORE_SELF_APPROVAL = """
DROP FUNCTION org.two_people() CASCADE;
DROP FUNCTION org.self_approval_allowed(uuid);
ALTER TABLE org.organization DROP COLUMN allow_self_approval;
ALTER TABLE context.example_query DROP COLUMN sql_changed_by;
ALTER TABLE catalog.dataset_version DROP CONSTRAINT dataset_version_certifier_created_check;
ALTER TABLE catalog.dataset_version
  ADD CHECK (certified_by IS NULL OR (created_by IS NOT NULL AND certified_by <> created_by));
ALTER TABLE context.knowledge_page_version ADD CHECK (reviewed_by IS NULL OR reviewed_by <> created_by);
ALTER TABLE context.knowledge_page_version DROP CONSTRAINT knowledge_page_version_certifier_check;
ALTER TABLE context.knowledge_page_version ADD CONSTRAINT knowledge_page_version_certifier_check CHECK (
  (certified_by IS NULL) = (certified_at IS NULL)
  AND (status <> 'certified' OR certified_by IS NOT NULL)
  AND (certified_by IS NULL OR (certified_by IS DISTINCT FROM created_by
                                AND certified_by IS DISTINCT FROM reviewed_by)));
ALTER TABLE context.data_product ADD CHECK (owner_id <> steward_id);
ALTER TABLE context.data_product ADD CHECK (activated_by IS NULL OR activated_by <> created_by);
ALTER TABLE pipeline.writeback_request ADD CHECK (approved_by IS NULL OR approved_by <> requested_by);
ALTER TABLE acquire.crawl_source ADD CHECK (legal_reviewed_by IS NULL OR legal_reviewed_by <> owner_id);
ALTER TABLE forecast.model_version ADD CHECK (approved_by IS NULL OR approved_by <> created_by);
ALTER TABLE forecast.formula_component ADD CHECK (certified_by IS NULL OR certified_by <> created_by);
DO $$
DECLARE c record;
BEGIN
  FOR c IN SELECT conname FROM pg_constraint
            WHERE conrelid = 'governance.model_provider'::regclass AND contype = 'c'
              AND pg_get_constraintdef(oid) ~ 'zero_retention AND'
  LOOP
    EXECUTE format('ALTER TABLE governance.model_provider DROP CONSTRAINT %I', c.conname);
  END LOOP;
END $$;
ALTER TABLE governance.model_provider ADD CHECK (NOT (kind = 'frontier' AND enabled)
  OR (zero_retention AND enabled_by IS NOT NULL AND approved_by IS NOT NULL
      AND approved_at IS NOT NULL AND approved_by <> enabled_by));
"""

_AUDIT_BEFORE_SELF_APPROVAL = """
ALTER TABLE audit.export_job DROP CONSTRAINT export_job_self_approved_check;
ALTER TABLE audit.export_job DROP COLUMN self_approved;
ALTER TABLE audit.export_job ADD CHECK (approved_by IS NULL OR approved_by <> requested_by);
"""

_APPROVAL_TABLES = (
    "catalog.dataset_version",
    "context.knowledge_page_version",
    "context.data_product",
    "pipeline.writeback_request",
    "acquire.crawl_source",
    "forecast.model_version",
    "forecast.formula_component",
    "governance.model_provider",
)


def _rows(dsn: str, query: str) -> list[tuple]:
    conn = psycopg2.connect(dsn)
    try:
        with conn.cursor() as cur:
            cur.execute(query)
            return sorted(cur.fetchall())
    finally:
        conn.close()


def _self_approval_shape(dataagent: str, audit: str) -> dict[str, list[tuple]]:
    """What v3_0024 to v3_0026 change, by definition (an unnamed CHECK's name is the server's)."""
    tables = ", ".join(f"'{t}'::regclass" for t in _APPROVAL_TABLES)
    return {
        "checks": _rows(
            dataagent,
            "SELECT conrelid::regclass::text, pg_get_constraintdef(oid) FROM pg_constraint"
            f" WHERE conrelid IN ({tables}) AND contype = 'c'",
        ),
        "named": _rows(
            dataagent,
            "SELECT conrelid::regclass::text, conname FROM pg_constraint"
            " WHERE conname IN ('dataset_version_certifier_created_check',"
            " 'knowledge_page_version_certifier_check')",
        ),
        "triggers": _rows(
            dataagent,
            "SELECT pg_get_triggerdef(t.oid) FROM pg_trigger t JOIN pg_proc p ON p.oid = t.tgfoid"
            " WHERE p.pronamespace = 'org'::regnamespace AND p.proname = 'two_people'"
            " AND NOT t.tgisinternal",
        ),
        "functions": _rows(
            dataagent,
            "SELECT proname, prosrc, prosecdef, proconfig::text, proacl::text FROM pg_proc"
            " WHERE pronamespace = 'org'::regnamespace"
            " AND proname IN ('two_people', 'self_approval_allowed')",
        ),
        "columns": _rows(
            dataagent,
            "SELECT table_schema, table_name, column_name, data_type, column_default, is_nullable"
            " FROM information_schema.columns WHERE (table_schema, table_name, column_name) IN"
            " (('org', 'organization', 'allow_self_approval'),"
            " ('context', 'example_query', 'sql_changed_by'))",
        ),
        "audit_checks": _rows(
            audit,
            "SELECT conname, pg_get_constraintdef(oid) FROM pg_constraint"
            " WHERE conrelid = 'audit.export_job'::regclass AND contype = 'c'"
            " AND pg_get_constraintdef(oid) ~ 'requested_by'",
        ),
        "audit_columns": _rows(
            audit,
            "SELECT column_name, data_type, column_default, is_nullable"
            " FROM information_schema.columns WHERE table_schema = 'audit'"
            " AND table_name = 'export_job' AND column_name = 'self_approved'",
        ),
        "audit_grants": _rows(
            audit,
            "SELECT column_name, privilege_type FROM information_schema.column_privileges"
            " WHERE table_schema = 'audit' AND table_name = 'export_job'"
            " AND grantee = 'svc_audit_worker' AND privilege_type = 'UPDATE'",
        ),
    }


def _same_shape(got: dict[str, list[tuple]], want: dict[str, list[tuple]]) -> None:
    for key in want:
        extra = sorted(set(got[key]) - set(want[key]))
        missing = sorted(set(want[key]) - set(got[key]))
        assert (key, extra, missing) == (key, [], []) and got[key] == want[key]


def test_self_approval_reaches_a_migrated_cluster_as_a_fresh_install_has_it(admin):
    # v3_0024 to v3_0026 (02/11/2026): a cluster migrated before them has the person-inequality
    # CHECKs, no switch, no trigger, the old export CHECK and grant. The baseline is generated
    # with the new shape, so the test puts the old one back before the upgrade.
    _fresh(admin, "chain_sa_fresh", "chain_sa_fresh_audit")
    _alembic(_db(admin, "chain_sa_fresh"), "upgrade", "head")
    fresh = _self_approval_shape(_db(admin, "chain_sa_fresh"), _db(admin, "chain_sa_fresh_audit"))

    _fresh(admin, "chain_sa", "chain_sa_audit")
    db, audit = _db(admin, "chain_sa"), _db(admin, "chain_sa_audit")
    _alembic(db, "upgrade", "v3_0023")
    for dsn, sql in ((db, _BEFORE_SELF_APPROVAL), (audit, _AUDIT_BEFORE_SELF_APPROVAL)):
        conn = psycopg2.connect(dsn)
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(sql)
        conn.close()
    before = _self_approval_shape(db, audit)
    assert before["triggers"] == [] and before["columns"] == [] and before["audit_columns"] == []
    assert ("context.data_product", "CHECK ((owner_id <> steward_id))") in before["checks"]

    _alembic(db, "upgrade", "head")
    migrated = _self_approval_shape(db, audit)
    _same_shape(migrated, fresh)
    pairs = (
        "certified_by <> created_by",
        "reviewed_by <> created_by",
        "owner_id <> steward_id",
        "activated_by <> created_by",
        "approved_by <> requested_by",
        "legal_reviewed_by <> owner_id",
        "approved_by <> created_by",
        "approved_by <> enabled_by",
        "DISTINCT FROM",
    )
    assert sum(p in d for p in pairs for _, d in before["checks"]) >= len(pairs)
    assert [d for _, d in migrated["checks"] if any(p in d for p in pairs)] == []
    assert len(migrated["triggers"]) == len(_APPROVAL_TABLES)

    # Each revision is idempotent: run again over what it already did, nothing changes.
    _alembic(db, "stamp", "v3_0023")
    _alembic(db, "upgrade", "head")
    _same_shape(_self_approval_shape(db, audit), fresh)
