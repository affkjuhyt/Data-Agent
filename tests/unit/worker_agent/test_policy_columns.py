"""Column and mask policies applied in the SQL before the source reads the table
(query/scope.rewrite_column_policies), and policy tables matched as datasets_read matches them.

Every read of a covered table becomes a subquery listing its columns, a hidden one left out and a
masked one replaced, so no rename, expression or whole-row reference reaches the raw column. The
figures are checked on PostgreSQL in tests/integration/worker_agent/test_column_masking.py."""

from __future__ import annotations

import uuid

import pytest
from sqlglot import exp, parse_one

from libs.dataagent_db.models import catalog, governance
from libs.dataagent_db.models import org as org_models
from services.worker_agent.query.scope import (
    OutOfScope,
    rewrite_access_policies,
    rewrite_column_policies,
)

pytestmark = [pytest.mark.unit]

POSTGRES = catalog.Source(id=uuid.uuid4(), kind="postgresql", conn_options={"schema": "bank"})
MYSQL = catalog.Source(
    id=uuid.uuid4(), kind="mysql", conn_options={"schema": "bank"}, database_name="bank"
)
CUSTOMERS = catalog.Dataset(id=uuid.uuid4(), schema_name="bank", table_name="customer")
COLUMNS = {CUSTOMERS.id: ["id", "name", "id_number", "salary"]}


def policy(kind: str, *columns: str, method: str | None = None) -> governance.AccessPolicy:
    return governance.AccessPolicy(
        id=uuid.uuid4(),
        dataset_id=CUSTOMERS.id,
        name=f"{kind} policy",
        kind=kind,
        columns=list(columns),
        mask_method=method,
        status="active",
    )


POLICIES = [policy("mask", "id_number", method="hash"), policy("column", "salary")]


def rewrite(sql: str, *, columns: dict[uuid.UUID, list[str]] = COLUMNS) -> str:
    return rewrite_column_policies(
        sql,
        dialect="postgres",
        policies=POLICIES,
        datasets=[CUSTOMERS],
        source=POSTGRES,
        columns=columns,
    )


def reads_of_customer(sql: str) -> list[str]:
    """The SELECTs that read bank.customer itself (not through a subquery)."""
    out = []
    for select in parse_one(sql, dialect="postgres").find_all(exp.Select):
        from_clause = select.args.get("from") or select.args.get("from_")
        source = from_clause.this if from_clause is not None else None
        if isinstance(source, exp.Table) and source.name.lower() == "customer":
            out.append(select.sql(dialect="postgres"))
    return out


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id_number AS x FROM bank.customer",
        "SELECT row_to_json(c) FROM bank.customer AS c",
        "SELECT row_to_json(c.*) FROM bank.customer AS c",
        "SELECT c.text FROM bank.customer AS c",
        "SELECT * FROM bank.customer AS c(a, b, x)",
        "SELECT name FROM bank.branch UNION SELECT id_number FROM bank.customer",
        "WITH x AS (SELECT * FROM bank.customer) SELECT x.id_number AS y FROM x",
        "SELECT * FROM bank.CUSTOMER",
    ],
)
def test_every_read_of_the_table_is_its_masked_columns(sql: str) -> None:
    out = rewrite(sql)
    reads = reads_of_customer(out)
    assert reads, out
    for read in reads:
        # The hidden column is not read; the masked one is read only through its mask.
        assert "salary" not in read
        assert 'MD5(CAST("id_number" AS TEXT)) AS "id_number"' in read
    # No other read of the raw table is left anywhere in the query.
    raw = [
        t
        for t in parse_one(out, dialect="postgres").find_all(exp.Table)
        if t.name.lower() == "customer"
    ]
    assert len(raw) == len(reads)


def test_the_alias_of_the_table_is_kept_for_the_rest_of_the_query() -> None:
    out = rewrite("SELECT c.name FROM bank.customer AS c WHERE c.id = 1")
    assert ") AS c WHERE c.id = 1" in out


def test_a_covered_table_without_its_columns_in_the_catalog_is_refused() -> None:
    with pytest.raises(OutOfScope, match="not in the catalog"):
        rewrite("SELECT name FROM bank.customer", columns={})


def test_a_quoted_name_of_another_case_is_another_table_on_postgres() -> None:
    sql = 'SELECT id_number FROM bank."CUSTOMER"'
    assert rewrite(sql) == parse_one(sql, dialect="postgres").sql(dialect="postgres")


def test_a_row_policy_filters_the_inner_read_where_a_hidden_column_is_still_there() -> None:
    row = governance.AccessPolicy(
        id=uuid.uuid4(),
        dataset_id=CUSTOMERS.id,
        name="Lương thấp",
        kind="row",
        condition_expr="salary < 100",
        status="active",
    )
    user = org_models.AppUser(id=uuid.uuid4(), username="an", attributes={})
    out = rewrite_access_policies(
        rewrite("SELECT name FROM bank.customer AS c"),
        dialect="postgres",
        policies=[row],
        user=user,
        datasets=[CUSTOMERS],
        source=POSTGRES,
    )
    (read,) = reads_of_customer(out)
    assert "WHERE customer.salary < 100" in read


def test_a_table_read_twice_in_another_case_is_filtered_both_times() -> None:
    row = governance.AccessPolicy(
        id=uuid.uuid4(),
        dataset_id=CUSTOMERS.id,
        name="Chi nhánh",
        kind="row",
        condition_expr="branch = :user.branch",
        status="active",
    )
    user = org_models.AppUser(id=uuid.uuid4(), username="an", attributes={"branch": "HN"})
    sql = rewrite_access_policies(
        "SELECT a.name FROM customer AS a JOIN CUSTOMER AS b ON a.id = b.id",
        dialect="mysql",
        policies=[row],
        user=user,
        datasets=[CUSTOMERS],
        source=MYSQL,
    )
    assert sql.count("'HN'") == 2, sql
