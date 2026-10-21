"""The holes the security review found in the policy engine (query/scope.py): a hidden or masked
column reaching the result under another name, and a read of a policy table its name test
missed."""

from __future__ import annotations

import uuid

import pytest

from libs.dataagent_db.models import catalog, governance
from libs.dataagent_db.models import org as org_models
from services.worker_agent.query.scope import (
    OutOfScope,
    check_column_policies,
    rewrite_access_policies,
)

pytestmark = [pytest.mark.unit]

POSTGRES = catalog.Source(id=uuid.uuid4(), kind="postgresql", conn_options={"schema": "bank"})
MYSQL = catalog.Source(
    id=uuid.uuid4(), kind="mysql", conn_options={"schema": "bank"}, database_name="bank"
)
CUSTOMERS = catalog.Dataset(id=uuid.uuid4(), schema_name="bank", table_name="customer")
OTHER = catalog.Dataset(id=uuid.uuid4(), schema_name="bank", table_name="branch")


def mask(*columns: str, kind: str = "mask") -> governance.AccessPolicy:
    return governance.AccessPolicy(
        id=uuid.uuid4(),
        dataset_id=CUSTOMERS.id,
        name="Che CCCD",
        kind=kind,
        columns=list(columns),
        mask_method="null" if kind == "mask" else None,
        status="active",
    )


def check(sql: str, *, kind: str = "mask", source: catalog.Source = POSTGRES) -> None:
    check_column_policies(
        sql,
        dialect="postgres" if source.kind == "postgresql" else "mysql",
        policies=[mask("id_number", kind=kind)],
        datasets=[CUSTOMERS, OTHER],
        source=source,
    )


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT name, id_number FROM bank.customer",
        "SELECT id_number AS id_number FROM bank.customer",
        "SELECT * FROM bank.customer",
        "SELECT c.* FROM bank.customer AS c",
        "SELECT * FROM (SELECT * FROM bank.customer) AS s",
        "SELECT b.code FROM bank.branch AS b WHERE b.id_number = '1'",  # another table's column
    ],
)
def test_a_covered_column_selected_as_itself_reaches_the_masking(sql: str) -> None:
    check(sql)


@pytest.mark.parametrize(
    "sql",
    [
        "SELECT id_number AS x FROM bank.customer",
        "SELECT upper(id_number) FROM bank.customer",
        "SELECT count(DISTINCT id_number) FROM bank.customer",
        "SELECT name FROM bank.customer WHERE id_number = '0123'",
        "SELECT name FROM bank.customer ORDER BY id_number",
        "SELECT s.id_number AS x FROM (SELECT * FROM bank.customer) AS s",
        "WITH c AS (SELECT id_number AS n FROM bank.customer) SELECT n FROM c",
        "SELECT name FROM bank.branch UNION SELECT id_number FROM bank.customer",
        "SELECT id_number AS x FROM bank.CUSTOMER",  # PostgreSQL folds the name: the same table
    ],
)
def test_a_covered_column_under_another_name_or_in_an_expression_is_refused(sql: str) -> None:
    with pytest.raises(OutOfScope):
        check(sql)
    with pytest.raises(OutOfScope):
        check(sql, kind="column")


def test_a_quoted_name_of_another_case_is_another_table_on_postgres() -> None:
    check('SELECT id_number AS x FROM bank."CUSTOMER"')


def test_a_table_read_twice_in_another_case_is_filtered_both_times() -> None:
    policy = governance.AccessPolicy(
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
        policies=[policy],
        user=user,
        datasets=[CUSTOMERS],
        source=MYSQL,
    )
    assert sql.count("'HN'") == 2, sql
