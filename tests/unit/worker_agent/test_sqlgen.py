"""The exploratory SQL path's deterministic parts (01 §3.4, DeepEye-SQL): linking a question to
the Data Space's tables, checking each candidate before it runs, and voting on the results."""

from __future__ import annotations

import uuid

import pytest

from services.worker_agent.sqlgen.check import problems
from services.worker_agent.sqlgen.schema import Col, Fk, Schema, Tbl, link
from services.worker_agent.sqlgen.vote import signature, vote

pytestmark = [pytest.mark.unit]

LOANS = Tbl(
    uuid.uuid4(),
    "workspace",
    "credit_loan",
    "Dư nợ khoản vay",
    (
        Col("loan_id", "Mã khoản vay", "string"),
        Col("branch_code", "Mã chi nhánh", "string"),
        Col("product", "Sản phẩm", "string", samples=("Thẻ tín dụng", "Vay mua nhà")),
        Col("balance", "Dư nợ", "decimal"),
        Col("as_of_date", "Ngày số liệu", "date"),
    ),
)
BRANCHES = Tbl(
    uuid.uuid4(),
    "workspace",
    "branch",
    "Chi nhánh",
    (
        Col("code", "Mã chi nhánh", "string"),
        Col("city", "Thành phố", "string", samples=("Hà Nội",)),
    ),
)
PRICES = Tbl(
    uuid.uuid4(), "workspace", "gold_price", "Giá vàng", (Col("close", "Giá đóng cửa", "decimal"),)
)
SCHEMA = Schema(
    tables=[LOANS, BRANCHES, PRICES],
    fks=[Fk(LOANS.dataset_id, ("branch_code",), BRANCHES.dataset_id, ("code",))],
)


def test_a_question_is_linked_to_the_tables_it_names_and_their_keys() -> None:
    linked = link("Tổng dư nợ thẻ tín dụng ở Hà Nội", SCHEMA)
    # Dư nợ → loans; "Hà Nội" is a value of branch.city; the join comes with them.
    assert [t.name for t in linked.tables] == ["credit_loan", "branch"]
    assert linked.fks == SCHEMA.fks
    assert ("branch", "city", "Hà Nội") in linked.values
    assert ("credit_loan", "product", "Thẻ tín dụng") in linked.values


def test_a_question_about_nothing_in_the_tables_links_nothing() -> None:
    assert link("Fed nói gì về lạm phát", SCHEMA).tables == []


CHECKED = link("dư nợ theo thành phố", SCHEMA)


def test_a_candidate_that_reads_only_the_linked_tables_passes() -> None:
    sql = (
        "SELECT b.city, SUM(l.balance) AS du_no FROM workspace.credit_loan AS l "
        "JOIN workspace.branch AS b ON b.code = l.branch_code GROUP BY b.city"
    )
    assert problems(sql, CHECKED, "postgres") == []


@pytest.mark.parametrize(
    ("sql", "said"),
    [
        ("SELEC balance FROM workspace.credit_loan", "không phân tích được"),
        ("DELETE FROM workspace.credit_loan", "chỉ đọc"),
        ("SELECT close FROM workspace.gold_price", "workspace.gold_price"),
        ("SELECT amount FROM workspace.credit_loan", "amount"),
        ("SELECT * FROM workspace.credit_loan", "SELECT *"),
    ],
)
def test_a_candidate_gets_an_instruction_for_what_is_wrong(sql: str, said: str) -> None:
    found = problems(sql, CHECKED, "postgres")
    assert found and any(said in p for p in found), found


def test_results_that_agree_up_to_order_and_rounding_have_one_signature() -> None:
    a = signature([["Hà Nội", "1200.0000001"], ["HCM", 900]])
    b = signature([["HCM", "900.00"], ["Hà Nội", 1200]])
    assert a == b and a != signature([["Hà Nội", 1201], ["HCM", 900]])


@pytest.mark.parametrize(
    ("agreeing", "decision"),
    [(6, "answer"), (4, "answer"), (3, "review"), (2, "ask"), (0, "failed")],
)
def test_the_vote_follows_the_design_thresholds(agreeing: int, decision: str) -> None:
    # Six candidates: `agreeing` give the same table, the others differ or fail.
    same = [["x", 1]]
    results: list[list[list[object]] | None] = [same] * agreeing
    results += [[["x", i + 10]] for i in range(min(2, 6 - agreeing))] if agreeing else []
    results += [None] * (6 - len(results))
    v = vote(results)
    assert v.decision == decision
    if agreeing:
        assert v.confidence == pytest.approx(agreeing / 6) and v.winner == 0


@pytest.mark.parametrize(
    "sql",
    [
        # Every way of reading a whole row reads the columns the model was never shown.
        "SELECT l.* FROM workspace.credit_loan AS l",
        "SELECT COUNT(DISTINCT l.*) FROM workspace.credit_loan AS l",
        "SELECT to_jsonb(l) FROM workspace.credit_loan AS l",
        "SELECT l FROM workspace.credit_loan AS l",
        "SELECT row_to_json(credit_loan) FROM workspace.credit_loan",
    ],
)
def test_a_whole_row_is_never_read(sql: str) -> None:
    assert problems(sql, CHECKED, "postgres"), sql


def test_count_star_is_still_a_count() -> None:
    assert problems("SELECT COUNT(*) AS n FROM workspace.credit_loan", CHECKED, "postgres") == []


def test_examples_and_joins_name_only_readable_tables() -> None:
    from services.worker_agent.sqlgen.schema import readable

    hidden = Tbl(uuid.uuid4(), "workspace", "salary", "Lương", (Col("amount", "Lương", "decimal"),))
    full = Schema(
        tables=[LOANS, BRANCHES],
        fks=[*SCHEMA.fks, Fk(LOANS.dataset_id, ("x",), hidden.dataset_id, ("y",))],
        examples=[
            (
                "Dư nợ theo chi nhánh",
                "SELECT branch_code, SUM(balance) FROM workspace.credit_loan GROUP BY 1",
            ),
            ("Lương trung bình", "SELECT AVG(amount) FROM workspace.salary"),
            ("Không đọc được", "SELEC"),
        ],
    )
    kept = readable(full, "postgres")
    assert [q for q, _ in kept.examples] == ["Dư nợ theo chi nhánh"]
    assert kept.fks == SCHEMA.fks


@pytest.mark.parametrize(
    ("sql", "dialect"),
    [
        # A name in another database or catalog: `other.workspace.credit_loan` is not ours.
        ("SELECT balance FROM other.workspace.credit_loan", "postgres"),
        ("SELECT balance FROM otherdb.workspace.credit_loan", "tsql"),
        # A table function reads outside the source (ClickHouse url/remote).
        ("SELECT a FROM url('http://x/data.csv', CSV, 'a String')", "clickhouse"),
        ("SELECT a FROM remote('host:9000', db.t)", "clickhouse"),
    ],
)
def test_nothing_outside_the_linked_tables_is_read(sql: str, dialect: str) -> None:
    assert problems(sql, CHECKED, dialect), sql
