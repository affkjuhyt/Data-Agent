from __future__ import annotations

import uuid

import pytest
from sqlglot import exp, parse_one

from libs.dataagent_db.models import catalog, governance
from libs.dataagent_db.models import org as org_models
from services.worker_agent.query.scope import (
    OutOfScope,
    rewrite_access_policies,
    rewrite_row_scopes,
)

pytestmark = [pytest.mark.unit]
_SOURCE = catalog.Source(id=uuid.uuid4(), kind="postgresql", conn_options={})


def _dataset(table: str = "loan") -> catalog.Dataset:
    return catalog.Dataset(id=uuid.uuid4(), table_name=table)


def _policy(dataset: catalog.Dataset, condition: str = "branch = :user.branch"):
    return governance.AccessPolicy(
        id=uuid.uuid4(),
        org_id=uuid.uuid4(),
        dataset_id=dataset.id,
        name="branch policy",
        kind="row",
        condition_expr=condition,
        status="active",
    )


def _user(**attributes: object) -> org_models.AppUser:
    return org_models.AppUser(id=uuid.uuid4(), username="an", attributes=attributes)


def _rewrite(sql: str, *, dialect: str = "postgres", branch: object = "HN") -> str:
    dataset = _dataset()
    return rewrite_access_policies(
        sql,
        dialect=dialect,
        policies=[_policy(dataset)],
        user=_user(branch=branch),
        datasets=[dataset],
        source=_SOURCE,
    )


def _unfiltered(sql: str, table: str, column: str, dialect: str = "postgres") -> list[str]:
    """Aliases of every read of `table` whose own SELECT has no predicate on `column`."""
    missing = []
    for select_node in parse_one(sql, dialect=dialect).find_all(exp.Select):
        where = select_node.args.get("where")
        guarded = {
            c.table for c in (where.find_all(exp.Column) if where else []) if c.name == column
        }
        from_clause = select_node.args.get("from") or select_node.args.get("from_")
        sources = [from_clause.this] if from_clause is not None else []
        sources += [join.this for join in select_node.args.get("joins") or []]
        missing += [
            source.alias_or_name
            for source in sources
            if isinstance(source, exp.Table)
            and source.name == table
            and source.alias_or_name not in guarded
        ]
    return missing


def test_row_policy_filters_a_single_read() -> None:
    sql = _rewrite("SELECT SUM(amount) FROM loan")
    assert _unfiltered(sql, "loan", "branch") == []
    assert "'HN'" in sql


def test_row_policy_filters_every_union_branch() -> None:
    sql = _rewrite("SELECT amount FROM loan UNION ALL SELECT amount FROM loan")
    assert _unfiltered(sql, "loan", "branch") == []


def test_row_policy_filters_both_sides_of_a_self_join() -> None:
    sql = _rewrite("SELECT a.amount, b.amount FROM loan AS a JOIN loan AS b ON a.id = b.parent_id")
    assert _unfiltered(sql, "loan", "branch") == []


def test_row_policy_filters_a_read_inside_a_subquery() -> None:
    sql = _rewrite(
        "SELECT id FROM customer WHERE id IN (SELECT customer_id FROM loan) "
        "UNION SELECT customer_id FROM loan"
    )
    assert _unfiltered(sql, "loan", "branch") == []


@pytest.mark.parametrize("dialect", ["postgres", "mysql", "tsql"])
def test_user_attribute_is_bound_as_one_literal(dialect: str) -> None:
    hostile = "x\\' OR 1=1 -- "
    sql = _rewrite("SELECT amount FROM loan", dialect=dialect, branch=hostile)
    where = parse_one(sql, dialect=dialect).find(exp.Where)
    assert where is not None
    assert isinstance(where.this, exp.EQ)
    assert where.this.expression.this == hostile


def test_row_scope_refuses_a_branch_it_cannot_place() -> None:
    with pytest.raises(OutOfScope):
        rewrite_row_scopes(
            "SELECT amount FROM loan UNION ALL "
            "SELECT l.amount FROM loan AS l JOIN customer AS c ON c.id = l.customer_id",
            dialect="postgres",
            scopes=[{"branch": ["HN"]}],
        )


def test_row_scope_filters_every_union_branch() -> None:
    sql = rewrite_row_scopes(
        "SELECT amount FROM loan UNION ALL SELECT amount FROM loan",
        dialect="postgres",
        scopes=[{"branch": ["HN"]}],
    )
    assert _unfiltered(sql, "loan", "branch") == []
