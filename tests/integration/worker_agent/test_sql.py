"""A question the semantic layer cannot answer is answered by SQL the model writes (01 §3.4,
worker-agent sqlgen/): six candidates checked, repaired once, run read-only in place, voted on;
the answer labelled "Khám phá", or a narrower question when the candidates disagree."""

from __future__ import annotations

import json
import uuid
from typing import Any

import pytest

from services.worker_agent import questions
from services.worker_agent.llm import ModelUnavailable, Reply

from .conftest import Seed
from .test_answers import (
    Bank,
    answer_of,
    build,
    drain,
    warehouse,  # noqa: F401  (the fixture `credit` reads)
)

pytestmark = [pytest.mark.integration]

QUESTION = "Số khoản vay theo mã chi nhánh ngày 30/09/2026"
GOOD = [
    "SELECT branch_code AS ma_chi_nhanh, COUNT(*) AS so_khoan_vay FROM bank.loan "
    "WHERE as_of_date = DATE '2026-09-30' GROUP BY branch_code",
    "SELECT l.branch_code, COUNT(l.branch_code) AS n FROM bank.loan AS l "
    "WHERE l.as_of_date = '2026-09-30' GROUP BY l.branch_code ORDER BY 1",
]


@pytest.fixture
def credit(warehouse: dict[str, Any]) -> dict[str, Any]:  # noqa: F811
    return warehouse


class Script:
    """The model as a script: text_to_sql answers in the order asked; the planner is away."""

    def __init__(self, sqls: list[str]) -> None:
        self.sqls = list(sqls)
        self.asked: list[tuple[str, float]] = []

    async def json_chat(
        self,
        role: str,
        system: str,
        user: str,
        output: type[Any],
        *,
        max_tokens: int = 800,
        temperature: float = 0.0,
    ) -> Reply[Any]:
        if role != "text_to_sql":
            raise ModelUnavailable("only the SQL model is up in this test")
        self.asked.append((user, temperature))
        return Reply(output.model_validate({"sql": self.sqls.pop(0)}), "agent-main", 100, 5)


def catalogue(bank: Bank) -> None:
    """The columns of the credit warehouse, as a schema scan writes them."""
    seed = bank.seed
    branches = seed.one(
        "SELECT id FROM catalog.dataset WHERE org_id = %s AND table_name = 'branch'", bank.org
    )
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE catalog.dataset SET display_name = 'Khoản vay' WHERE id = %s",
            (str(bank.loans),),
        )
    for dataset, cols in (
        (
            bank.loans,
            [
                ("branch_code", "Mã chi nhánh", "string"),
                ("group_code", "Nhóm nợ", "integer"),
                ("balance", "Dư nợ", "decimal"),
                ("product_code", "Sản phẩm", "string"),
                ("as_of_date", "Ngày số liệu", "date"),
            ],
        ),
        (branches, [("code", "Mã chi nhánh", "string"), ("region_name", "Vùng", "string")]),
    ):
        for ordinal, (name, label, kind) in enumerate(cols):
            seed.row(
                "catalog.column_meta",
                org_id=bank.org,
                dataset_id=dataset,
                dataset_version=1,
                name=name,
                display_name=label,
                ordinal=ordinal,
                data_type=kind,
                normalized_type=kind,
            )


def asked(bank: Bank, script: Script, monkeypatch: pytest.MonkeyPatch) -> uuid.UUID:
    monkeypatch.setattr(questions, "_gateway", script)
    monkeypatch.setattr(questions, "_gateway_read", True)
    catalogue(bank)
    return bank.ask(QUESTION)


async def test_six_candidates_that_agree_answer_with_the_exploratory_label(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, credit, monkeypatch)
    script = Script([GOOD[0], GOOD[1]] * 3)
    q = asked(bank, script, monkeypatch)
    await drain()
    status, kind, text, figures, scope, answer = answer_of(seed, q)
    assert (status, kind, scope) == ("answered", "data", "not_applicable"), text
    assert "6/6 cách viết cho cùng kết quả" in text and "Khám phá" in text
    # Three ways twice, the second sample of each warmer than the first.
    assert sorted(t for _, t in script.asked) == [0.2, 0.2, 0.2, 0.7, 0.7, 0.7]
    # Only the linked tables were shown to the model, with their columns and labels.
    assert "TABLE bank.loan -- Khoản vay" in script.asked[0][0]
    assert all(f["trust"] == "Khám phá (truy vấn do mô hình viết)" for f in figures)
    assert {f["dimensions"]["ma_chi_nhanh"] for f in figures} >= {"HN1", "SG1"}
    plan = seed.one("SELECT steps FROM qa.plan WHERE question_id = %s", q)
    assert plan["path"] == "exploratory_sql" and len(plan["candidates"]) == 6
    assert (
        seed.one(
            "SELECT count(*) FROM query.query_run WHERE caller_kind = 'question' AND caller_id = %s",
            q,
        )
        == 6
    )
    assert seed.one("SELECT chart_spec->>'type' FROM qa.answer WHERE id = %s", answer) == "bar"


async def test_a_candidate_the_checker_rejects_is_repaired_once_and_still_counts(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, credit, monkeypatch)
    # The first candidate reads every column; sent back with the instruction, it names them.
    script = Script(["SELECT * FROM bank.loan"] + [GOOD[0]] * 5 + [GOOD[1]])
    q = asked(bank, script, monkeypatch)
    await drain()
    status, _, text, *_ = answer_of(seed, q)
    assert status == "answered" and "6/6" in text
    repair = script.asked[-1][0]
    assert "SELECT *" in repair and "Không dùng SELECT *" in repair


async def test_candidates_that_disagree_give_no_number_and_ask_back(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, credit, monkeypatch)
    differing = [
        GOOD[0],
        GOOD[1],
        "SELECT branch_code, SUM(balance) FROM bank.loan GROUP BY branch_code",
        "SELECT product_code, COUNT(*) FROM bank.loan GROUP BY product_code",
        "SELECT COUNT(*) FROM bank.loan",
        "SELECT MAX(balance) FROM bank.loan",
    ]
    q = asked(bank, Script(differing), monkeypatch)
    await drain()
    status, kind, text, figures, *_ = answer_of(seed, q)
    assert (status, kind, figures) == ("clarifying", "clarification", [])
    assert "2/6" in text and json.dumps(figures) == "[]"


async def test_a_personal_data_column_is_never_shown_to_the_model_nor_read(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    bank = build(seed, credit, monkeypatch)
    seed.row(
        "catalog.column_meta",
        org_id=bank.org,
        dataset_id=bank.loans,
        dataset_version=1,
        name="national_id",
        display_name="Số CCCD",
        ordinal=9,
        data_type="string",
        normalized_type="string",
        pii_label="national_id",
    )
    # Every candidate tries the personal-data column, even after being sent back.
    leaking = "SELECT national_id, COUNT(*) FROM bank.loan GROUP BY national_id"
    script = Script([leaking] * 12)
    q = asked(bank, script, monkeypatch)
    await drain()
    assert all("national_id" not in user.split("Your SQL")[0] for user, _ in script.asked)
    assert all("Số CCCD" not in user for user, _ in script.asked)
    # No candidate passed its checks, so nothing ran and the question went on as before.
    assert seed.one("SELECT count(*) FROM query.query_run WHERE caller_id = %s", q) == 0
    assert seed.one("SELECT status FROM qa.question WHERE id = %s", q) != "answered"


async def test_tables_the_asker_may_not_read_are_never_shown_to_the_model(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # Someone else's personal table (BR-111) and a table under an access policy (BR-45): the
    # executor would refuse them, and their names, columns and values stay out of the prompt too.
    bank = build(seed, credit, monkeypatch)
    source = seed.one("SELECT source_id FROM catalog.dataset WHERE id = %s", bank.loans)
    other = seed.row(
        "org.app_user", org_id=bank.org, external_id="kc-x", username="x", display_name="X"
    )
    hidden = []
    for table, scope, owner in (
        ("salary", "personal", other),
        ("vip_loan", "data_space", bank.user),
    ):
        dataset = seed.row(
            "catalog.dataset",
            org_id=bank.org,
            data_space_id=bank.space,
            source_id=source,
            origin="source",
            schema_name="bank",
            table_name=table,
            display_name=f"Khoản vay chi nhánh {table}",
            scope=scope,
            created_by=owner,
            enabled=True,
        )
        seed.row(
            "catalog.column_meta",
            org_id=bank.org,
            dataset_id=dataset,
            dataset_version=1,
            name="branch_code",
            display_name="Mã chi nhánh",
            ordinal=0,
            data_type="string",
            normalized_type="string",
        )
        hidden.append((dataset, table))
    seed.row(
        "governance.access_policy",
        org_id=bank.org,
        data_space_id=bank.space,
        dataset_id=hidden[1][0],
        name="Chỉ phòng VIP",
        kind="row",
        condition_expr="1 = 0",
        status="active",
        simulated_at="2026-10-01T00:00:00Z",
    )
    script = Script([GOOD[0]] * 6)
    asked(bank, script, monkeypatch)
    await drain()
    prompt = script.asked[0][0]
    assert "bank.loan" in prompt
    assert "bank.salary" not in prompt and "bank.vip_loan" not in prompt


class Recorder(Script):
    """The model as a script that also records every role it is asked as."""

    def __init__(self, sqls: list[str]) -> None:
        super().__init__(sqls)
        self.roles: list[str] = []
        self.prompts: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], **kw: Any
    ) -> Any:
        self.roles.append(role)
        self.prompts.append(system + "\n" + user)
        return await super().json_chat(role, system, user, output, **kw)


@pytest.mark.req("AC-11-01-01")
async def test_a_person_outside_the_data_space_never_reaches_the_model(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # R1-10: permissions before generation. A configured Data Space the asker holds no ask grant
    # in: neither the planner model nor the SQL model is asked, so no table, column, metric or
    # value name of the space reaches a prompt; the answer is ACCESS_DENIED, not NO_DATA.
    bank = build(seed, credit, monkeypatch)
    ask_role = seed.row(
        "org.role", org_id=bank.org, code="ASK-R110", name="Ask", capabilities=["ask"]
    )
    seed.row(
        "org.data_space_member",
        org_id=bank.org,
        data_space_id=bank.space,
        principal_type="user",
        principal_id=bank.user,
        role_id=ask_role,
    )
    bank.user = seed.row(
        "org.app_user", org_id=bank.org, external_id="kc-out", username="out", display_name="Out"
    )
    script = Recorder([GOOD[0], GOOD[1]] * 3)
    for text in (QUESTION, "Tỷ lệ nợ xấu theo vùng tháng 9/2026", "Chỉ tiêu lạ chưa có tên"):
        q = asked(bank, script, monkeypatch) if text == QUESTION else bank.ask(text)
        await drain()
        _, kind, said, figures, _, answer = answer_of(seed, q)
        assert kind == "refusal" and not figures, said
        refusal = seed.one(
            "SELECT number_check->'refusal'->>'code' FROM qa.answer WHERE id = %s", answer
        )
        assert refusal == "ACCESS_DENIED", (text, refusal)
    assert script.roles == [] and script.prompts == []
    assert seed.one("SELECT count(*) FROM query.query_run WHERE org_id = %s", bank.org) == 0


async def test_a_metric_named_only_in_part_tries_the_tables_before_asking_back(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The model reads the question as a metric it names only in part (to_confirm): rather than
    # asking "Bạn muốn hỏi: Tỷ lệ nợ xấu?" straight away, the agent first writes SQL over the
    # tables; six ways agreeing answer it, labelled exploratory.
    from services.worker_agent.understand import llm_reader
    from services.worker_agent.understand.resolver import Understanding

    async def partial(question: str, vocab: Any, gateway: Any, **kwargs: Any) -> Any:
        guess = next(m for m in vocab.metrics if m.name == "Tỷ lệ nợ xấu")
        reply = Reply(content=None, model="planner", tokens=1, latency_ms=1)
        return Understanding(decision="clarify", options=[guess], to_confirm=True), reply

    monkeypatch.setattr(llm_reader, "read", partial)
    bank = build(seed, credit, monkeypatch)
    q = asked(bank, Script([GOOD[0], GOOD[1]] * 3), monkeypatch)
    await drain()
    status, kind, text, figures, *_ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    assert all(f["trust"] == "Khám phá (truy vấn do mô hình viết)" for f in figures)
    steps = [
        name
        for (name,) in seed.all(
            "SELECT s.name FROM agent.agent_step s JOIN agent.agent_run r ON r.id = s.run_id"
            " WHERE r.trigger_ref = %s AND s.name <> 'model_call' ORDER BY s.seq",
            f"question:{q}",
        )
    ]
    assert "write_sql" in steps and "ask_user" not in steps


async def test_a_partial_metric_the_tables_cannot_answer_still_asks_back(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    from services.worker_agent.understand import llm_reader
    from services.worker_agent.understand.resolver import Understanding

    async def partial(question: str, vocab: Any, gateway: Any, **kwargs: Any) -> Any:
        guess = next(m for m in vocab.metrics if m.name == "Tỷ lệ nợ xấu")
        reply = Reply(content=None, model="planner", tokens=1, latency_ms=1)
        return Understanding(decision="clarify", options=[guess], to_confirm=True), reply

    monkeypatch.setattr(llm_reader, "read", partial)
    bank = build(seed, credit, monkeypatch)
    differing = [
        GOOD[0],
        GOOD[1],
        "SELECT branch_code, SUM(balance) FROM bank.loan GROUP BY branch_code",
        "SELECT product_code, COUNT(*) FROM bank.loan GROUP BY product_code",
        "SELECT COUNT(*) FROM bank.loan",
        "SELECT MAX(balance) FROM bank.loan",
    ]
    q = asked(bank, Script(differing), monkeypatch)
    await drain()
    status, kind, text, *_ = answer_of(seed, q)
    # The ways of writing disagree: no number, and the question back is the metric suggested,
    # not the SQL's own doubt.
    assert (status, kind) == ("clarifying", "clarification"), text
    assert "Tỷ lệ nợ xấu" in text and "cách viết" not in text, text


async def test_an_agent_versions_eval_run_never_writes_sql_outside_its_data_scope(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # An eval run of an agent version carries its data_scope; the exploratory SQL is not
    # limited to it, so it is not tried (as the approved-query path is not, sqlgen/answer.py).
    script = Script([GOOD[0], GOOD[1]] * 3)
    bank = build(seed, credit, monkeypatch)
    q = asked(bank, script, monkeypatch)
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE qa.outbox SET payload = payload || %s::jsonb WHERE aggregate_id = %s",
            ('{"data_scope": {"tables": []}}', str(q)),
        )
    await drain()
    assert all("TABLE bank" not in prompt for prompt, _ in script.asked)
    assert (
        seed.one(
            "SELECT count(*) FROM query.query_run WHERE caller_kind = 'question' AND caller_id = %s",
            q,
        )
        == 0
    )


async def test_a_table_without_a_display_name_is_named_by_its_semantic_entity(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # A scanned table often has only its technical name (AdventureWorks: fact_internet_sales):
    # the entity the semantic layer names it by ("Khoản vay") is its label, for linking a
    # question to it and in the prompt.
    bank = build(seed, credit, monkeypatch)
    script = Script([GOOD[0], GOOD[1]] * 3)
    monkeypatch.setattr(questions, "_gateway", script)
    monkeypatch.setattr(questions, "_gateway_read", True)
    catalogue(bank)
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE catalog.dataset SET display_name = NULL WHERE id = %s", (str(bank.loans),)
        )
    bank.ask(QUESTION)
    await drain()
    assert script.asked and "TABLE bank.loan -- Khoản vay" in script.asked[0][0]


class Writer(Script):
    """The SQL model as the script, and the main model writing the sentences with tags."""

    def __init__(self, sqls: list[str], text: str) -> None:
        super().__init__(sqls)
        self.text = text
        self.wrote: list[str] = []

    async def json_chat(
        self, role: str, system: str, user: str, output: type[Any], **kw: Any
    ) -> Reply[Any]:
        if role == "responder":
            self.wrote.append(user)
            return Reply(output.model_validate({"text": self.text}), "agent-main", 50, 5)
        return await super().json_chat(role, system, user, output, **kw)


async def test_an_exploratory_answer_is_written_in_sentences_and_keeps_its_label(
    seed: Seed, credit: dict[str, Any], monkeypatch: pytest.MonkeyPatch
) -> None:
    # The model writes the sentences from the figures, numbers only as tags (answer/narrator.py,
    # as on the governed path); the exploratory label and the agreement stay after them.
    bank = build(seed, credit, monkeypatch)
    writer = Writer([GOOD[0], GOOD[1]] * 3, "Chi nhánh HN1 có {{F1}} khoản vay.")
    q = asked(bank, writer, monkeypatch)
    await drain()
    status, kind, text, figures, *_ = answer_of(seed, q)
    assert (status, kind) == ("answered", "data"), text
    assert writer.wrote, "the main model was not asked to write"
    assert text.startswith("Chi nhánh HN1 có "), text
    assert "{{" not in text
    assert text.endswith("6/6 cách viết cho cùng kết quả."), text
    assert all(f["trust"] == "Khám phá (truy vấn do mô hình viết)" for f in figures)
