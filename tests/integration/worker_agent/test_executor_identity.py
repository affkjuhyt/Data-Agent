"""AE-17 (F-29): a query on a PostgreSQL source runs as the role its role_mapping names for the
asker (SET LOCAL ROLE), so the source's own grants apply to them; every run records executed_as,
and a result read as one role is never reused for another."""

from __future__ import annotations

import uuid
from typing import Any

import psycopg2
import pytest

from services.worker_agent.query.executor import run_query

from .conftest import Seed
from .test_executor import _source
from .test_executor import warehouse as warehouse

pytestmark = [pytest.mark.integration, pytest.mark.req("AE-17")]

SQL = "SELECT branch_code, balance FROM loan_balance ORDER BY balance DESC"


@pytest.fixture(scope="module")
def roles(ddl_server: dict[str, str]) -> None:
    """Two roles of the source: one may read the table, one may not; the service login is a
    member of both, so it can SET ROLE to either."""
    conn = psycopg2.connect(ddl_server["dataagent"])
    conn.autocommit = True
    with conn.cursor() as cur:
        for role in ("dw_branch_reader", "dw_no_read"):
            cur.execute(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = %s) THEN "
                f"CREATE ROLE {role} NOLOGIN; END IF; END $$",
                (role,),
            )
            cur.execute(f"GRANT {role} TO dw_reader")
            cur.execute(f"GRANT USAGE ON SCHEMA dw TO {role}")
        cur.execute("GRANT SELECT ON dw.loan_balance TO dw_branch_reader")
    conn.close()


def _user(seed: Seed, org: uuid.UUID, space: uuid.UUID, name: str) -> uuid.UUID:
    """A user who may ask in the Data Space."""
    user = seed.row(
        "org.app_user", org_id=org, external_id=f"kc-{name}", username=name, display_name=name
    )
    ask = seed.row(
        "org.role",
        org_id=org,
        code=f"ASK-{uuid.uuid4().hex[:6]}",
        name="Ask",
        capabilities=["ask"],
    )
    seed.row(
        "org.data_space_member",
        org_id=org,
        data_space_id=space,
        principal_type="user",
        principal_id=user,
        role_id=ask,
    )
    return user


async def _run(source: Any, secrets: Any, org: uuid.UUID, space: uuid.UUID, who: uuid.UUID) -> Any:
    return await run_query(
        org_id=org,
        data_space_id=space,
        principal_id=who,
        caller_kind="question",
        caller_id=who,
        source=source,
        secrets=secrets,
        sql=SQL,
    )


async def test_each_asker_runs_as_their_mapped_role_and_it_is_recorded(
    seed: Seed,
    warehouse: dict[str, object],  # noqa: F811
    roles: None,
) -> None:
    org, space, user, source, secrets = _source(seed, warehouse)
    barred = _user(seed, org, space, "barred")
    grouped = _user(seed, org, space, "grouped")
    group = seed.row("org.user_group", org_id=org, external_id="g-br", name="Chi nhánh")
    seed.row("org.user_group_member", org_id=org, group_id=group, user_id=grouped)
    unmapped = _user(seed, org, space, "plain")
    source.role_mapping = {
        "users": {str(user): "dw_branch_reader", str(barred): "dw_no_read"},
        "groups": {str(group): "dw_branch_reader"},
    }

    allowed = await _run(source, secrets, org, space, user)
    assert allowed.ok, allowed.error
    # The same SQL a moment later, as a role the source does not let read the table: the source
    # refuses it, and the first run's rows are not reused for it.
    refused = await _run(source, secrets, org, space, barred)
    assert not refused.ok and "InsufficientPrivilege" in (refused.error or "")
    by_group = await _run(source, secrets, org, space, grouped)
    assert by_group.ok, by_group.error
    plain = await _run(source, secrets, org, space, unmapped)
    assert plain.ok, plain.error

    def executed_as(run: Any) -> Any:
        return seed.one("SELECT executed_as FROM query.query_run WHERE id = %s", run.query_run_id)

    assert executed_as(allowed) == "dw_branch_reader"
    assert executed_as(refused) == "dw_no_read"
    assert executed_as(by_group) == "dw_branch_reader"
    # Not named by the mapping: the service account, as before AE-17.
    assert executed_as(plain) == "dw_reader"


async def test_groups_that_map_to_two_roles_refuse_the_query(
    seed: Seed,
    warehouse: dict[str, object],  # noqa: F811
    roles: None,
) -> None:
    org, space, user, source, secrets = _source(seed, warehouse)
    groups = [
        seed.row("org.user_group", org_id=org, external_id=f"g-{i}", name=f"Nhóm {i}")
        for i in range(2)
    ]
    for group in groups:
        seed.row("org.user_group_member", org_id=org, group_id=group, user_id=user)
    source.role_mapping = {
        "groups": {str(groups[0]): "dw_branch_reader", str(groups[1]): "dw_no_read"}
    }
    out = await _run(source, secrets, org, space, user)
    assert not out.ok and "different roles" in (out.error or "")
    # Refused before the source was asked.
    assert (
        seed.one("SELECT executed_as FROM query.query_run WHERE id = %s", out.query_run_id) is None
    )
