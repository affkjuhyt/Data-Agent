"""The SQL `libs/shared/tenant_rls.py` hands to V2 table migrations (NOP-148).

Behaviour on a real database is covered by
tests/integration/workflow_runtime/test_v2_tenant_rls.py; this pins the parts a
migration author relies on without running one: one statement per item, nothing
a caller passes can reshape the statement, and DELETE / cross-tenant policies
appear only when asked for.
"""

from __future__ import annotations

import pytest

from libs.shared.sql_identifiers import UnsafeIdentifier
from libs.shared.tenant_rls import TENANT_EXPR, disable_tenant_rls, enable_tenant_rls

pytestmark = pytest.mark.unit


def test_a_table_is_enabled_forced_revoked_and_gets_three_tenant_policies() -> None:
    sql = enable_tenant_rls("ingest", "release")

    assert sql[:3] == [
        'ALTER TABLE "ingest"."release" ENABLE ROW LEVEL SECURITY',
        'ALTER TABLE "ingest"."release" FORCE ROW LEVEL SECURITY',
        'REVOKE ALL ON TABLE "ingest"."release" FROM PUBLIC',
    ]
    creates = [s for s in sql if s.startswith("CREATE POLICY")]
    assert [s.split()[2] for s in creates] == ["tenant_select", "tenant_insert", "tenant_update"]
    same_tenant = f'"tenant_id" = {TENANT_EXPR}'
    assert creates[0].endswith(f"FOR SELECT USING ({same_tenant})")
    assert creates[1].endswith(f"FOR INSERT WITH CHECK ({same_tenant})")
    # USING and WITH CHECK both: no editing B's rows, no moving a row to B.
    assert creates[2].endswith(f"FOR UPDATE USING ({same_tenant}) WITH CHECK ({same_tenant})")


def test_every_item_is_one_statement() -> None:
    """asyncpg (the migrate Job) rejects several statements in one call."""
    for stmt in enable_tenant_rls(
        "ingest", "release", allow_delete=True, cross_tenant_roles=("outbox_relay",)
    ):
        assert ";" not in stmt


def test_every_policy_is_dropped_before_it_is_created() -> None:
    sql = enable_tenant_rls("governance", "approval", cross_tenant_roles=("maintenance",))
    for i, stmt in enumerate(sql):
        if stmt.startswith("CREATE POLICY"):
            name = stmt.split()[2]
            assert sql[i - 1].startswith(f"DROP POLICY IF EXISTS {name} ON")


def test_delete_needs_an_explicit_opt_in() -> None:
    assert not any("FOR DELETE" in s for s in enable_tenant_rls("ingest", "release"))
    assert any(
        s.endswith(f'FOR DELETE USING ("tenant_id" = {TENANT_EXPR})')
        for s in enable_tenant_rls("ingest", "queue", allow_delete=True)
    )


def test_a_cross_tenant_role_reads_and_updates_but_never_inserts() -> None:
    sql = enable_tenant_rls("ingest", "outbox", cross_tenant_roles=("outbox_relay",))
    cross = [s for s in sql if s.startswith("CREATE POLICY") and 'TO "outbox_relay"' in s]
    assert len(cross) == 2
    assert any('FOR SELECT TO "outbox_relay" USING (true)' in s for s in cross)
    assert any('FOR UPDATE TO "outbox_relay" USING (true) WITH CHECK (true)' in s for s in cross)
    assert not any("FOR INSERT" in s for s in cross)


def test_a_custom_tenant_column_is_used_everywhere() -> None:
    sql = enable_tenant_rls("knowledge", "claim", tenant_column="owner_tenant")
    policies = [s for s in sql if s.startswith("CREATE POLICY")]
    assert policies and all('"owner_tenant" = ' in s for s in policies)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"schema": "ingest; DROP TABLE x", "table": "release"},
        {"schema": "ingest", "table": 'release" CASCADE'},
        {"schema": "ingest", "table": "release", "tenant_column": "tenant_id) OR (true"},
        {"schema": "ingest", "table": "release", "cross_tenant_roles": ("public; --",)},
        {"schema": "", "table": "release"},
    ],
)
def test_names_that_are_not_plain_identifiers_are_refused(kwargs: dict[str, object]) -> None:
    with pytest.raises(UnsafeIdentifier):
        enable_tenant_rls(**kwargs)  # type: ignore[arg-type]


def test_disable_drops_every_policy_enable_can_create_then_turns_rls_off() -> None:
    created = {
        s.split()[2].strip('"')
        for s in enable_tenant_rls(
            "ingest", "outbox", allow_delete=True, cross_tenant_roles=("outbox_relay",)
        )
        if s.startswith("CREATE POLICY")
    }
    sql = disable_tenant_rls("ingest", "outbox", cross_tenant_roles=("outbox_relay",))
    dropped = {s.split()[4].strip('"') for s in sql if s.startswith("DROP POLICY")}
    assert created <= dropped
    assert sql[-2:] == [
        'ALTER TABLE "ingest"."outbox" NO FORCE ROW LEVEL SECURITY',
        'ALTER TABLE "ingest"."outbox" DISABLE ROW LEVEL SECURITY',
    ]
