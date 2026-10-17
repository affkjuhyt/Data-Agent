"""A dataset's audit read from its aggregates (worker-agent audit/findings.py): what is missing and
why, how values are spread, where one group concentrates, whether the key holds."""

from __future__ import annotations

import pytest

from services.worker_agent.audit.findings import Aggregates, Column, audit

pytestmark = [pytest.mark.unit]

COLUMNS = [
    Column("loan_id", "Mã khoản vay", "string", is_key=True),
    Column("customer_segment", "Nhóm khách hàng", "string"),
    Column("gender", "Giới tính", "string"),
    Column("age_band", "Độ tuổi", "string"),
    Column("national_id", "Số CCCD", "string", pii=True),
    Column("balance", "Dư nợ", "decimal"),
]


def aggregates(**over: object) -> Aggregates:
    base: dict[str, object] = {
        "rows": 1000,
        "non_null": {
            "loan_id": 1000,
            "customer_segment": 1000,
            "gender": 580,
            "age_band": 600,
            "national_id": 1000,
            "balance": 1000,
        },
        "distinct": {
            "loan_id": 1000,
            "customer_segment": 3,
            "gender": 2,
            "age_band": 3,
            "national_id": 990,
            "balance": 870,
        },
        "minimum": {"balance": "50000000", "loan_id": "L00001"},
        "maximum": {"balance": "98000000000", "loan_id": "L01000"},
        "values": {
            "customer_segment": [("Cá nhân", 600), ("SME", 300), ("Doanh nghiệp lớn", 100)],
            "gender": [("Nam", 330), (None, 420), ("Nữ", 250)],
            "age_band": [("26-35", 300), (None, 400), ("36-45", 200), ("18-25", 100)],
        },
        # Rows and filled rows of a column with gaps, by each group of another column.
        "filled_by": {
            ("gender", "customer_segment"): [
                ("Cá nhân", 600, 580),
                ("SME", 300, 0),
                ("Doanh nghiệp lớn", 100, 0),
            ],
            ("age_band", "customer_segment"): [
                ("Cá nhân", 600, 600),
                ("SME", 300, 0),
                ("Doanh nghiệp lớn", 100, 0),
            ],
        },
        # (a, b) → rows of each pair of values.
        "crossed": {
            ("gender", "age_band"): [
                ("Nam", "26-35", 220),
                ("Nam", "36-45", 60),
                ("Nam", "18-25", 50),
                ("Nữ", "26-35", 70),
                ("Nữ", "36-45", 130),
                ("Nữ", "18-25", 50),
            ],
        },
        "duplicate_keys": 0,
        "histograms": {"balance": [(50e6, 1e10, 900), (1e10, 98e9, 100)]},
    }
    base.update(over)
    return Aggregates(**base)  # type: ignore[arg-type]


def texts(kind: str, a: Aggregates | None = None) -> list[str]:
    out = audit("Dư nợ khoản vay", COLUMNS, a or aggregates())
    return [f.text for f in out.findings if f.kind == kind]


def test_a_gap_that_only_means_not_applicable_is_told_apart_from_a_real_one() -> None:
    [gender] = [t for t in texts("missing") if t.startswith("Giới tính")]
    # 400 empty because the column does not apply to companies; 20 individuals really lack it.
    assert "420/1.000" in gender
    assert "400 dòng không áp dụng (SME, Doanh nghiệp lớn)" in gender
    assert "thiếu thật 20/600 dòng (3,3%) ở Cá nhân" in gender
    out = audit("Dư nợ khoản vay", COLUMNS, aggregates())
    severity = {f.column: f.severity for f in out.findings if f.kind == "missing"}
    assert severity == {"gender": "warning", "age_band": "info"}


def test_a_column_empty_only_where_it_does_not_apply_is_not_a_problem() -> None:
    [age] = [t for t in texts("missing") if t.startswith("Độ tuổi")]
    assert "không thiếu ở nhóm áp dụng" in age


def test_a_gap_no_group_explains_is_a_plain_gap() -> None:
    a = aggregates(filled_by={})
    [gender] = [t for t in texts("missing", a) if t.startswith("Giới tính")]
    assert gender == "Giới tính: thiếu 420/1.000 dòng (42,0%)."


def test_where_each_group_concentrates_is_said_against_the_whole() -> None:
    [crossed] = texts("concentration")
    # Men: 220 of 330 are 26-35 (66,7%) against 290/580 overall (50%); women lean to 36-45.
    assert "Nam tập trung ở 26-35 (66,7%, chung 50,0%)" in crossed
    assert "Nữ tập trung ở 36-45 (52,0%, chung 32,8%)" in crossed


def test_the_key_is_checked_for_duplicates() -> None:
    assert texts("key") == ["Khoá Mã khoản vay không trùng."]
    [dup] = texts("key", aggregates(duplicate_keys=7))
    assert dup == "Khoá Mã khoản vay có 7 giá trị trùng."
    out = audit("x", COLUMNS, aggregates(duplicate_keys=7))
    assert [f.severity for f in out.findings if f.kind == "key"] == ["high"]


def test_personal_data_is_profiled_without_its_values() -> None:
    out = audit("Dư nợ khoản vay", COLUMNS, aggregates())
    cccd = next(p for p in out.profiles if p["column"] == "national_id")
    assert cccd["top_values"] == "masked" and cccd["min"] is None and cccd["max"] is None
    assert cccd["null_ratio"] == 0.0 and cccd["distinct"] == 990


def test_each_spread_is_a_part_with_its_chart() -> None:
    out = audit("Dư nợ khoản vay", COLUMNS, aggregates())
    titles = [p["question"] for p in out.parts]
    assert titles[:3] == ["Phân bố Nhóm khách hàng", "Phân bố Giới tính", "Phân bố Độ tuổi"]
    gender = out.parts[1]
    assert gender["columns"] == ["gender", "value"]
    assert gender["rows"] == [["Nam", 330], ["Nữ", 250], ["(trống)", 420]]
    assert gender["chart_spec"]["type"] == "bar"
    assert "Nam 56,9%; Nữ 43,1%" in gender["summary"]  # of the filled rows
    # A wide numeric column is drawn as a histogram; the cross of two columns as series.
    assert "Phân bố Dư nợ" in titles and "Giới tính theo Độ tuổi" in titles


def test_the_summary_counts_what_needs_attention() -> None:
    out = audit("Dư nợ khoản vay", COLUMNS, aggregates(duplicate_keys=3))
    assert out.summary.startswith(
        "Kiểm toán Dư nợ khoản vay (1.000 dòng): 1 vấn đề cần xử lý, 1 cảnh báo."
    )


def test_an_amount_or_a_date_is_never_a_category_even_with_few_values() -> None:
    from services.worker_agent.audit.findings import is_category

    a = aggregates(distinct={**aggregates().distinct, "balance": 3})
    assert not is_category(Column("balance", "Dư nợ", "decimal"), a)
    assert not is_category(Column("as_of", "Ngày", "date"), aggregates(distinct={"as_of": 2}))
    assert is_category(
        Column("debt_group", "Nhóm nợ", "integer"), aggregates(distinct={"debt_group": 5})
    )


def test_a_lone_row_does_not_explain_a_gap_away() -> None:
    # The only row of a group is empty: that is the gap itself, not a group it does not apply to.
    a = aggregates(
        rows=10,
        non_null={**aggregates().non_null, "gender": 8},
        filled_by={("gender", "customer_segment"): [("Cá nhân", 9, 8), ("Khác", 1, 0)]},
    )
    [gender] = [t for t in texts("missing", a) if t.startswith("Giới tính")]
    assert gender == "Giới tính: thiếu 2/10 dòng (20,0%)."


def test_a_two_valued_column_that_concentrates_nowhere_is_said_to_spread_evenly() -> None:
    # "Where do men and women concentrate?" has an answer even when it is "nowhere".
    even = [
        ("Nam", "26-35", 150),
        ("Nam", "36-45", 100),
        ("Nữ", "26-35", 120),
        ("Nữ", "36-45", 80),
    ]
    out = audit(
        "Dư nợ khoản vay",
        COLUMNS,
        aggregates(crossed={("gender", "age_band"): even, ("gender", "customer_segment"): []}),
    )
    [said] = [f.text for f in out.findings if f.kind == "concentration"]
    assert said == (
        "Giới tính: Nam, Nữ phân bố đều theo Độ tuổi (không nhóm nào có tỷ lệ từ 1,2 lần mức chung)."
    )


@pytest.mark.req("AC-06-07-02")
def test_the_audit_suggests_rules_that_hold_its_findings() -> None:
    # Suggestions, not rules: none exists until a person takes one (AC-06-07-02).
    out = audit("Dư nợ khoản vay", COLUMNS, aggregates())
    rules = {(s["kind"], s["column"]): s for s in out.suggestions}
    assert rules[("unique", "loan_id")]["severity"] == "high"
    gender = rules[("not_null", "gender")]
    assert gender["params"] == {"where": {"customer_segment": ["Cá nhân"]}}
    assert gender["severity"] == "medium" and "Cá nhân" in gender["text"]
    assert rules[("range", "balance")]["params"] == {"min": 0}
    segment = rules[("in_list", "customer_segment")]
    assert segment["params"] == {"values": ["Cá nhân", "SME", "Doanh nghiệp lớn"]}
    assert segment["severity"] == "low"
    # Personal data is never listed; a rule a finding contradicts is not suggested.
    assert not any(s["column"] == "national_id" for s in out.suggestions)
    dup = audit("x", COLUMNS, aggregates(duplicate_keys=4))
    assert ("unique", "loan_id") not in {(s["kind"], s["column"]) for s in dup.suggestions}
