"""A dimension reached through a chain of many-to-one joins (compiler.join_path), run on sqlite so
the figures are checked: no fan-out, a table shared by two cuts joined once, and two ways to the
same table refused rather than one of them picked."""

from __future__ import annotations

import itertools
import sqlite3
import uuid
from collections.abc import Iterator

import pytest

from services.worker_agent.semantic.compiler import (
    MAX_HOPS,
    CompileError,
    Dimension,
    Join,
    Layer,
    Measure,
    MetricVersion,
    Request,
    Table,
    compile_metric,
    join_path,
)

pytestmark = [pytest.mark.unit]

LOANS, BRANCHES, REGIONS, CUSTOMERS = (uuid.uuid4() for _ in range(4))
LOAN, BRANCH, REGION, CUSTOMER = (uuid.uuid4() for _ in range(4))

AREA = Dimension(uuid.uuid4(), "mien", "Miền", "name", REGION)
CITY = Dimension(uuid.uuid4(), "thanh_pho", "Thành phố", "city", BRANCH)
SEGMENT = Dimension(uuid.uuid4(), "phan_khuc", "Phân khúc", "segment", CUSTOMER)

TABLES = {
    LOAN: Table(LOANS, "dw", "loan"),
    BRANCH: Table(BRANCHES, "dw", "branch"),
    REGION: Table(REGIONS, "dw", "region"),
    CUSTOMER: Table(CUSTOMERS, "dw", "customer"),
}
LOAN_TO_BRANCH = Join(LOANS, ("branch_code",), BRANCHES, ("code",))
BRANCH_TO_REGION = Join(BRANCHES, ("region_code",), REGIONS, ("code",))
LOAN_TO_CUSTOMER = Join(LOANS, ("customer_id",), CUSTOMERS, ("id",))
CUSTOMER_TO_REGION = Join(CUSTOMERS, ("region_code",), REGIONS, ("code",))


def layer(*joins: Join) -> Layer:
    return Layer(
        measures={"du_no": Measure("du_no", "SUM(balance)", LOAN)},
        dimensions={d.code: d for d in (AREA, CITY, SEGMENT)},
        entity_tables=TABLES,
        joins=joins,
    )


def metric() -> MetricVersion:
    return MetricVersion(
        id=uuid.uuid4(),
        metric_code="DU_NO",
        display_name="Dư nợ",
        expression="du_no",
        filters=(),
        unit="tỷ đồng",
        decimals=0,
        period_semantics="sum_over_period",
        allowed_dimension_ids=frozenset({AREA.id, CITY.id, SEGMENT.id}),
        certified=True,
    )


@pytest.fixture
def db() -> Iterator[sqlite3.Connection]:
    conn = sqlite3.connect(":memory:")
    conn.execute("ATTACH DATABASE ':memory:' AS dw")
    conn.executescript(
        """
        CREATE TABLE dw.loan (branch_code TEXT, customer_id TEXT, balance INTEGER);
        CREATE TABLE dw.branch (code TEXT, city TEXT, region_code TEXT);
        CREATE TABLE dw.region (code TEXT, name TEXT);
        CREATE TABLE dw.customer (id TEXT, segment TEXT, region_code TEXT);
        INSERT INTO dw.loan VALUES ('HN1','K1',100), ('HN2','K2',50), ('SG1','K1',30);
        INSERT INTO dw.branch VALUES ('HN1','Hà Nội','B'), ('HN2','Hải Phòng','B'),
                                     ('SG1','TP.HCM','N');
        INSERT INTO dw.region VALUES ('B','Miền Bắc'), ('N','Miền Nam');
        INSERT INTO dw.customer VALUES ('K1','Bán lẻ','N'), ('K2','Doanh nghiệp','B');
        """
    )
    yield conn
    conn.close()


def rows(db: sqlite3.Connection, sql: str) -> list[tuple[object, ...]]:
    return [tuple(r) for r in db.execute(sql).fetchall()]


def test_a_dimension_two_joins_away_is_reached_without_repeating_a_loan(
    db: sqlite3.Connection,
) -> None:
    out = compile_metric(
        Request(metric(), group_by=["mien"]),
        layer(LOAN_TO_BRANCH, BRANCH_TO_REGION),
        dialect="sqlite",
    )
    assert 'LEFT JOIN "dw"."branch" AS t1' in out.sql and 'LEFT JOIN "dw"."region" AS t2' in out.sql
    # The region is joined from the branch, not from the loan.
    assert '"t1"."region_code" = "t2"."code"' in out.sql
    assert rows(db, out.sql) == [("Miền Bắc", 150), ("Miền Nam", 30)]
    assert set(out.dataset_ids) == {LOANS, BRANCHES, REGIONS}


def test_a_table_on_the_way_to_two_cuts_is_joined_once(db: sqlite3.Connection) -> None:
    out = compile_metric(
        Request(metric(), group_by=["mien", "thanh_pho"]),
        layer(LOAN_TO_BRANCH, BRANCH_TO_REGION),
        dialect="sqlite",
    )
    assert out.sql.count('"dw"."branch"') == 1
    assert rows(db, out.sql) == [
        ("Miền Bắc", "Hà Nội", 100),
        ("Miền Bắc", "Hải Phòng", 50),
        ("Miền Nam", "TP.HCM", 30),
    ]


def test_two_ways_to_the_same_table_are_refused_and_named() -> None:
    both = layer(LOAN_TO_BRANCH, BRANCH_TO_REGION, LOAN_TO_CUSTOMER, CUSTOMER_TO_REGION)
    with pytest.raises(CompileError) as caught:
        compile_metric(Request(metric(), group_by=["mien"]), both, dialect="sqlite")
    assert caught.value.code == "NO_JOIN_PATH"
    assert "loan → branch → region" in str(caught.value)
    assert "loan → customer → region" in str(caught.value)


def test_the_shortest_way_wins_over_a_longer_one() -> None:
    direct = Join(LOANS, ("region_code",), REGIONS, ("code",))
    path = join_path(layer(LOAN_TO_BRANCH, BRANCH_TO_REGION, direct), LOANS, REGIONS)
    assert path == (direct,)


def test_a_table_further_than_the_hop_limit_is_no_join_path() -> None:
    ids = [uuid.uuid4() for _ in range(MAX_HOPS + 2)]
    chain = tuple(Join(a, ("k",), b, ("k",)) for a, b in itertools.pairwise(ids))
    far = Layer({}, {}, {uuid.uuid4(): Table(d, None, f"t{i}") for i, d in enumerate(ids)}, chain)
    assert len(join_path(far, ids[0], ids[MAX_HOPS])) == MAX_HOPS
    with pytest.raises(CompileError) as caught:
        join_path(far, ids[0], ids[MAX_HOPS + 1])
    assert caught.value.code == "NO_JOIN_PATH"


def test_a_cycle_does_not_loop() -> None:
    back = Join(REGIONS, ("code",), LOANS, ("region_code",))
    with pytest.raises(CompileError):
        join_path(layer(LOAN_TO_BRANCH, BRANCH_TO_REGION, back), LOANS, CUSTOMERS)
