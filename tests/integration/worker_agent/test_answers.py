"""worker-agent answers a single-metric question end to end (UC-01-01, tab 5 G1.3 fast path): the
Data Space's semantic layer, the rule Understander, the compiler, a query in place on a PostgreSQL
source (a schema of the test server reached as a read-only login), the answer with traced figures,
and, in a data product's scope, the sealed receipt."""

from __future__ import annotations

import decimal
import json
import uuid
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlsplit

import psycopg2
import pytest

from services.worker_agent import questions
from services.worker_agent.events import OutboxTableSource
from services.worker_agent.main import run_once
from services.worker_agent.query.secrets import StaticReader

from .conftest import Seed

pytestmark = [pytest.mark.integration]

PASSWORD = "loans-reader"


@pytest.fixture(scope="module")
def warehouse(ddl_server: dict[str, str]) -> dict[str, Any]:
    dsn = ddl_server["dataagent"]
    conn = psycopg2.connect(dsn)
    conn.autocommit = True
    with conn.cursor() as cur:
        cur.execute("DROP SCHEMA IF EXISTS bank CASCADE")
        cur.execute("CREATE SCHEMA bank")
        cur.execute("CREATE TABLE bank.branch (code text PRIMARY KEY, region_name text)")
        cur.execute(
            "CREATE TABLE bank.loan (branch_code text, group_code int, balance numeric(18,2), "
            "product_code text, as_of_date date)"
        )
        cur.execute(
            "INSERT INTO bank.branch VALUES ('HN1','Miền Bắc'),('HN2','Miền Bắc'),('SG1','Miền Nam')"
        )
        cur.execute(
            "INSERT INTO bank.loan VALUES "
            # 31/08: an older balance that must not be summed with September's.
            "('HN1',1,500,'TD','2026-08-31'),"
            "('HN1',1,800,'TD','2026-09-30'),('HN1',3,200,'TD','2026-09-30'),"
            "('HN2',1,1000,'TD','2026-09-30'),"
            "('SG1',1,900,'TD','2026-09-30'),('SG1',4,100,'TD','2026-09-30'),"
            # Internal lending is excluded by the metric's own filter.
            "('SG1',5,5000,'NOI_BO','2026-09-30')"
        )
        cur.execute(
            "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'loans_reader') THEN "
            "CREATE ROLE loans_reader LOGIN; END IF; END $$"
        )
        cur.execute("ALTER ROLE loans_reader PASSWORD %s", (PASSWORD,))
        cur.execute("GRANT USAGE ON SCHEMA bank TO loans_reader")
        cur.execute("GRANT SELECT ON ALL TABLES IN SCHEMA bank TO loans_reader")
    conn.close()
    parts = urlsplit(dsn)
    return {"host": parts.hostname, "port": parts.port, "database": parts.path.lstrip("/")}


@dataclass
class Bank:
    org: uuid.UUID
    space: uuid.UUID
    user: uuid.UUID
    npl: uuid.UUID
    loans: uuid.UUID
    seed: Seed

    def ask(self, text: str) -> uuid.UUID:
        conv = self.seed.row(
            "qa.conversation", org_id=self.org, data_space_id=self.space, user_id=self.user
        )
        question = self.seed.row(
            "qa.question", org_id=self.org, conversation_id=conv, seq=1, text=text
        )
        self.seed.row(
            "qa.outbox",
            org_id=self.org,
            aggregate_type="question",
            aggregate_id=question,
            event_type="question.received",
            payload=json.dumps({"question_id": str(question), "user_id": str(self.user)}),
        )
        return question


def build(seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> Bank:
    org = seed.row("org.organization", code=f"org-{uuid.uuid4().hex[:8]}", name="Ngân hàng thử")
    user = seed.row("org.app_user", org_id=org, external_id="kc-a", username="a", display_name="A")
    owner = seed.row("org.app_user", org_id=org, external_id="kc-o", username="o", display_name="O")
    space = seed.row("org.data_space", org_id=org, code="DS-TD", name="Tín dụng", steward_id=user)
    ref = f"secret/tenants/{org}/sources/bank"
    source = seed.row(
        "catalog.source",
        org_id=org,
        data_space_id=space,
        name="Kho tín dụng",
        kind="postgresql",
        host=warehouse["host"],
        port=warehouse["port"],
        database_name=warehouse["database"],
        conn_options=json.dumps({"schema": "bank", "sslmode": "disable"}),
        secret_ref=ref,
        mode="federated",
        status="active",
    )
    monkeypatch.setattr(
        questions, "_secrets", StaticReader({ref: {"user": "loans_reader", "password": PASSWORD}})
    )

    def dataset(table: str, history: str) -> uuid.UUID:
        return seed.row(
            "catalog.dataset",
            org_id=org,
            data_space_id=space,
            source_id=source,
            origin="source",
            schema_name="bank",
            table_name=table,
            history_kind=history,
            enabled=True,
        )

    loans, branches = dataset("loan", "snapshot"), dataset("branch", "none")
    loan_e = seed.row(
        "context.semantic_entity",
        org_id=org,
        data_space_id=space,
        code="khoan_vay",
        name="Khoản vay",
        primary_dataset_id=loans,
        key_columns=["branch_code"],
    )
    branch_e = seed.row(
        "context.semantic_entity",
        org_id=org,
        data_space_id=space,
        code="chi_nhanh",
        name="Chi nhánh",
        primary_dataset_id=branches,
        key_columns=["code"],
    )
    for code, expr in (
        ("no_xau", "SUM(CASE WHEN group_code >= 3 THEN balance ELSE 0 END)"),
        ("du_no", "SUM(balance)"),
    ):
        seed.row(
            "context.semantic_measure",
            org_id=org,
            data_space_id=space,
            entity_id=loan_e,
            code=code,
            name=code,
            expression=expr,
            aggregation="sum",
            additivity="semi",
        )
    region = seed.row(
        "context.semantic_dimension",
        org_id=org,
        data_space_id=space,
        entity_id=branch_e,
        code="vung",
        name="vùng",
        expression="region_name",
    )
    product_dim = seed.row(
        "context.semantic_dimension",
        org_id=org,
        data_space_id=space,
        entity_id=loan_e,
        code="san_pham",
        name="sản phẩm",
        expression="product_code",
    )
    seed.row(
        "context.semantic_dimension",
        org_id=org,
        data_space_id=space,
        entity_id=loan_e,
        code="ngay",
        name="ngày",
        expression="as_of_date",
        is_time=True,
    )
    seed.row(
        "context.relationship",
        org_id=org,
        data_space_id=space,
        from_dataset_id=loans,
        from_columns=["branch_code"],
        to_dataset_id=branches,
        to_columns=["code"],
        cardinality="many_to_one",
        status="active",
    )
    seed.row(
        "context.value_index",
        org_id=org,
        data_space_id=space,
        dimension_id=region,
        value_code="Miền Bắc",
        value_label="Miền Bắc",
        label_norm="mien bac",
    )

    def metric(code: str, name: str, expression: str) -> uuid.UUID:
        m = seed.row(
            "context.metric",
            org_id=org,
            data_space_id=space,
            code=code,
            owner_id=owner,
            current_version=1,
            status="active",
        )
        seed.row(
            "context.metric_version",
            org_id=org,
            metric_id=m,
            version=1,
            display_name=name,
            expression=expression,
            unit="%" if code == "NPL_RATIO" else "tỷ đồng",
            period_semantics="end_of_period",
            status="certified",
            certified_by=owner,
            certified_at="2026-06-01T00:00:00Z",
            valid_from="2026-06-01T00:00:00Z",
            allowed_dimensions=[region, product_dim],
            filters=json.dumps([{"dimension": "san_pham", "op": "!=", "values": ["NOI_BO"]}]),
        )
        return m

    npl = metric("NPL_RATIO", "Tỷ lệ nợ xấu", "no_xau / du_no * 100")
    metric("DU_NO", "Dư nợ", "du_no")
    return Bank(org, space, user, npl, loans, seed)


def activate(bank: Bank, *, freshness: str = "fresh") -> uuid.UUID:
    seed = bank.seed
    owner, steward = (
        seed.row("org.app_user", org_id=bank.org, external_id=f"kc-{n}", username=n, display_name=n)
        for n in ("po", "ps")
    )
    # An active product carries its eval set (UC-07-10); what is in it does not matter here.
    eval_set = seed.row(
        "eval.eval_set",
        org_id=bank.org,
        data_space_id=bank.space,
        subject_type="context",
        name="Tín dụng",
    )
    product = seed.row(
        "context.data_product",
        org_id=bank.org,
        data_space_id=bank.space,
        code="tin_dung",
        name="Tín dụng",
        description="Nợ xấu và dư nợ",
        period_grain="month",
        period_from="2024-01-01",
        owner_id=owner,
        steward_id=steward,
        freshness_statement="Cập nhật ngày làm việc kế tiếp",
        scope_entities=json.dumps([]),
        status="draft",
    )
    seed.row(
        "context.data_product_member",
        org_id=bank.org,
        data_product_id=product,
        member_kind="metric",
        member_ref=bank.npl,
    )
    seed.row(
        "context.data_product_member",
        org_id=bank.org,
        data_product_id=product,
        member_kind="dataset",
        member_ref=bank.loans,
        role="primary",
        sla_kind="max_age",
        sla_max_age="P1D",
        freshness_state=freshness,
    )
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE context.data_product SET status = 'active', activated_by = %s, activated_at = now(),"
            " sample_questions = %s, eval_set_id = %s WHERE id = %s",
            (
                str(owner),
                json.dumps([{"text": f"q{i}"} for i in range(10)]),
                str(eval_set),
                str(product),
            ),
        )
    return product


async def drain() -> None:
    # question.received and plan.edited both come from qa.outbox.
    source = OutboxTableSource(schema="qa", event_types=frozenset(questions.HANDLERS))
    while await run_once(source, questions.HANDLERS, batch=50):
        pass


def answer_of(seed: Seed, question: uuid.UUID) -> tuple[Any, ...]:
    return seed.one(  # type: ignore[return-value]
        "SELECT q.status, a.kind, a.text, a.figures, a.product_scope, a.id FROM qa.question q "
        "JOIN qa.answer a ON a.question_id = q.id WHERE q.id = %s",
        question,
    )


async def test_ac_01_01_01_a_ratio_by_region_in_scope_with_a_sealed_receipt(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank)
    q = bank.ask("Tỷ lệ nợ xấu theo vùng tháng 9/2026")
    await drain()
    status, kind, text, figures, scope, answer = answer_of(seed, q)
    assert (status, kind, scope) == ("answered", "data", "in_scope"), text
    # Miền Bắc: 200 / 2000 = 10%; Miền Nam: 100 / 1000 = 10% (internal loans excluded, August not summed).
    assert [(f["dimensions"]["vung"], float(f["value"])) for f in figures] == [
        ("Miền Bắc", 10.0),
        ("Miền Nam", 10.0),
    ]
    assert "10,00%" in text and all(f["trust"] == "Theo chỉ số chuẩn" for f in figures)
    assert seed.one(
        "SELECT status, caller_kind FROM query.query_run WHERE id = %s", figures[0]["query_run_id"]
    ) == ("succeeded", "question")
    assert seed.one(
        "SELECT r.sealed_at IS NOT NULL, r.figure_count, f.compiled_sql_sha256 IS NOT NULL "
        "FROM qa.answer_receipt r JOIN qa.answer_receipt_figure f ON f.receipt_id = r.id "
        "WHERE r.answer_id = %s LIMIT 1",
        answer,
    ) == (True, 2, True)
    assert (
        seed.one("SELECT number_check->>'untraceable_final' FROM qa.answer WHERE id = %s", answer)
        == "0"
    )


async def test_out_of_scope_is_answered_and_says_so_without_a_receipt(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Tỷ lệ nợ xấu ở Miền Bắc tháng 9/2026")
    await drain()
    status, kind, text, figures, scope, answer = answer_of(seed, q)
    assert (status, kind, scope) == ("answered", "data", "out_of_scope"), text
    assert figures[0]["trust"].startswith("Ngoài phạm vi data product")
    assert seed.one("SELECT count(*) FROM qa.answer_receipt WHERE answer_id = %s", answer) == 0


async def test_ac_01_01_02_two_metrics_ask_which_and_run_nothing(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Dư nợ và tỷ lệ nợ xấu tháng 9/2026")
    await drain()
    status, kind, text, *_ = answer_of(seed, q)
    assert (status, kind) == ("clarifying", "clarification") and "Dư nợ" in text
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", q) == 0


async def test_an_unknown_metric_is_refused_with_a_way_forward(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Doanh thu phí bảo hiểm tháng này")
    await drain()
    status, kind, text, *_ = answer_of(seed, q)
    assert (status, kind) == ("refused", "refusal") and "Chưa nhận ra chỉ số" in text


async def test_br_178_an_overdue_product_masks_its_figures(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank, freshness="overdue")
    q = bank.ask("Tỷ lệ nợ xấu tháng 9/2026")
    await drain()
    status, kind, text, figures, scope, answer = answer_of(seed, q)
    assert (status, scope) == ("answered", "in_scope")
    assert figures[0]["value"] is None and figures[0]["display_state"] == "masked_stale"
    assert "10,00" not in text and "đã ẩn" in text
    assert seed.one(
        "SELECT f.display_state, f.value FROM qa.answer_receipt r "
        "JOIN qa.answer_receipt_figure f ON f.receipt_id = r.id WHERE r.answer_id = %s",
        answer,
    ) == ("masked_stale", None)


def edit_plan(bank: Bank, question: uuid.UUID, chips: list[dict[str, Any]]) -> uuid.UUID:
    """What PATCH /v1/questions/{id}/plan writes (qa/routes.py edit_question_plan)."""
    seed = bank.seed
    version = seed.one("SELECT max(version) + 1 FROM qa.plan WHERE question_id = %s", question)
    plan = seed.row(
        "qa.plan",
        org_id=bank.org,
        question_id=question,
        version=version,
        chips=json.dumps(chips),
        steps=json.dumps([]),
        edited_by_user=True,
    )
    with seed.conn.cursor() as cur:
        cur.execute("UPDATE qa.question SET status = 'planning' WHERE id = %s", (str(question),))
    seed.row(
        "qa.outbox",
        org_id=bank.org,
        aggregate_type="plan",
        aggregate_id=plan,
        event_type="plan.edited",
        payload=json.dumps(
            {"plan_id": str(plan), "question_id": str(question), "version": version}
        ),
    )
    return plan


async def test_uc_01_03_the_picked_metric_is_answered_from_the_edited_chips(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Dư nợ và tỷ lệ nợ xấu tháng 9/2026")
    await drain()
    assert answer_of(seed, q)[0] == "clarifying"
    plan = edit_plan(
        bank,
        q,
        [
            {"kind": "metric", "value": "DU_NO", "label": "Dư nợ", "origin": "user"},
            {"kind": "dimension", "value": "vung", "label": "vùng", "origin": "user"},
            {"kind": "period", "value": "2026-09-01/2026-10-01", "label": "tháng 9/2026"},
        ],
    )
    await drain()
    await drain()  # a redelivery changes nothing: the plan already has its steps
    assert seed.one("SELECT status FROM qa.question WHERE id = %s", q) == "answered"
    kind, figures, plan_of_answer = seed.one(
        "SELECT kind, figures, plan_id FROM qa.answer WHERE question_id = %s "
        "ORDER BY created_at DESC LIMIT 1",
        q,
    )
    assert kind == "data" and str(plan_of_answer) == str(plan)
    # Dư nợ end of September by region, internal loans excluded: Bắc 2000, Nam 1000.
    assert [(f["dimensions"]["vung"], float(f["value"])) for f in figures] == [
        ("Miền Bắc", 2000.0),
        ("Miền Nam", 1000.0),
    ]
    assert seed.one("SELECT steps->>'version' FROM qa.plan WHERE id = %s", plan) == "2"
    assert seed.one("SELECT count(*) FROM qa.answer WHERE question_id = %s", q) == 2


async def test_uc_01_03_a_chip_the_space_does_not_have_is_refused(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    q = bank.ask("Dư nợ và tỷ lệ nợ xấu tháng 9/2026")
    await drain()
    edit_plan(bank, q, [{"kind": "metric", "value": "KHONG_CO", "origin": "user"}])
    await drain()
    status, kind, text = seed.one(
        "SELECT q.status, a.kind, a.text FROM qa.question q JOIN qa.answer a ON a.question_id = q.id "
        "WHERE q.id = %s ORDER BY a.created_at DESC LIMIT 1",
        q,
    )
    assert (status, kind) == ("refused", "refusal") and "KHONG_CO" in text


async def test_uc_01_07_a_receipt_replays_identical_then_differs_after_the_source_changed(
    seed: Seed,
    warehouse: dict[str, Any],
    monkeypatch: pytest.MonkeyPatch,
    ddl_server: dict[str, str],
) -> None:
    from services.worker_agent import replay

    bank = build(seed, warehouse, monkeypatch)
    activate(bank)
    q = bank.ask("Tỷ lệ nợ xấu theo vùng tháng 9/2026")
    await drain()
    answer = answer_of(seed, q)[5]
    receipt = seed.one("SELECT id FROM qa.answer_receipt WHERE answer_id = %s", answer)

    def ask_replay() -> uuid.UUID:
        rid = seed.row(
            "qa.receipt_replay",
            org_id=bank.org,
            receipt_id=receipt,
            requested_by=bank.user,
            mode="figures",
            compiler_version_used="v3-compiler-1",
            status="running",
        )
        seed.row(
            "qa.outbox",
            org_id=bank.org,
            aggregate_type="receipt_replay",
            aggregate_id=rid,
            event_type="receipt_replay.requested",
            payload=json.dumps({"replay_id": str(rid), "requested_by": str(bank.user)}),
        )
        return rid

    source = OutboxTableSource(schema="qa", event_types=frozenset(replay.HANDLERS))
    first = ask_replay()
    while await run_once(source, replay.HANDLERS, batch=50):
        pass
    assert seed.one(
        "SELECT status, outcome, differs_count FROM qa.receipt_replay WHERE id = %s", first
    ) == ("completed", "identical", 0)

    conn = psycopg2.connect(ddl_server["dataagent"])
    conn.autocommit = True
    with conn.cursor() as cur:
        # A late correction lands in the source after the answer.
        cur.execute("INSERT INTO bank.loan VALUES ('HN2',3,1000,'TD','2026-09-30')")
    try:
        second = ask_replay()
        while await run_once(source, replay.HANDLERS, batch=50):
            pass
        outcome, differs, results = seed.one(
            "SELECT outcome, differs_count, figure_results FROM qa.receipt_replay WHERE id = %s",
            second,
        )
        assert (outcome, differs) == ("differs", 1)
        assert [r["figure_key"] for r in results if r["cause"]] == ["F1"]
    finally:
        with conn.cursor() as cur:
            cur.execute("DELETE FROM bank.loan WHERE branch_code = 'HN2' AND group_code = 3")
        conn.close()


class FakeGateway:
    """Stands in for LiteLLM: answers the planner's JSON from a fixed reading."""

    def __init__(self, content: dict[str, Any]) -> None:
        self.content = content
        self.calls: list[str] = []

    async def json_chat(self, role: str, system: str, user: str, *, max_tokens: int = 800) -> Any:
        from services.worker_agent.llm import Reply

        self.calls.append(role)
        return Reply(self.content, model="planner", tokens=120, latency_ms=5)


async def test_g5_1_the_model_reads_what_the_rules_could_not(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    activate(bank)
    fake = FakeGateway({"metric": "NPL_RATIO", "group_by": ["vung"], "filters": {}, "period": None})
    monkeypatch.setattr(questions, "_gateway", fake)
    monkeypatch.setattr(questions, "_gateway_read", True)
    q = bank.ask("Khoản vay khó đòi chiếm bao nhiêu phần trăm ở mỗi miền, tháng 9/2026?")
    await drain()
    status, kind, _text, figures, scope, answer = answer_of(seed, q)
    assert (status, kind, scope) == ("answered", "data", "in_scope") and fake.calls == ["planner"]
    assert [f["dimensions"]["vung"] for f in figures] == ["Miền Bắc", "Miền Nam"]
    # The plan says the reading is the model's, at the proceed threshold, and the receipt names it.
    assert seed.one(
        "SELECT confidence FROM qa.plan WHERE question_id = %s ORDER BY version DESC LIMIT 1", q
    ) == pytest.approx(decimal.Decimal("0.8"))
    assert (
        seed.one("SELECT models->0->>'task' FROM qa.answer_receipt WHERE answer_id = %s", answer)
        == "understand"
    )


async def test_g5_1_a_code_the_model_invents_is_never_compiled(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    monkeypatch.setattr(questions, "_gateway", FakeGateway({"metric": "LOI_NHUAN", "group_by": []}))
    monkeypatch.setattr(questions, "_gateway_read", True)
    q = bank.ask("Lợi nhuận ròng quý trước?")
    await drain()
    assert answer_of(seed, q)[:2] == ("refused", "refusal")
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", q) == 0


async def test_g5_1_an_ambiguous_model_reading_asks_which(
    seed: Seed, warehouse: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, warehouse, monkeypatch)
    monkeypatch.setattr(
        questions, "_gateway", FakeGateway({"metric": None, "alternatives": ["DU_NO", "NPL_RATIO"]})
    )
    monkeypatch.setattr(questions, "_gateway_read", True)
    q = bank.ask("Tình hình tín dụng ra sao?")
    await drain()
    assert answer_of(seed, q)[:2] == ("clarifying", "clarification")
