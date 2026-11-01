"""GEN-07: a fresh install gets its organisation, the default roles and a first admin from the
bootstrap (services/api/modules/org/bootstrap.py) as svc_org, with no demo seed, and the admin it
creates is someone the api resolves and lets administer."""

from __future__ import annotations

import uuid
from typing import Any

import pytest

from services.api.kernel.auth import CAPABILITIES
from services.api.modules.org import bootstrap as b
from services.api.modules.org.identity import OrgIdentityResolver

from .conftest import Seed

pytestmark = [pytest.mark.integration]


def _org(**changes: Any) -> b.Organisation:
    org_id = changes.pop("id", uuid.uuid4())
    values = {
        "id": org_id,
        "code": f"aw-{org_id.hex[:8]}",
        "name": "Adventure Works Cycles",
        "locale": "vi-VN",
        "time_zone": "Asia/Ho_Chi_Minh",
        "currency": "VND",
        "admin_username": "first-admin",
        "admin_email": "admin@adventure-works.example",
        "admin_external_id": str(uuid.uuid4()),
    }
    return b.Organisation(**{**values, **changes})


def _all(seed: Seed, sql: str, *params: object) -> list[tuple[Any, ...]]:
    with seed.conn.cursor() as cur:
        cur.execute(sql, [str(p) if isinstance(p, uuid.UUID) else p for p in params])
        return list(cur.fetchall())


async def test_a_fresh_install_gets_its_organisation_roles_and_an_admin_it_resolves(
    api_database: str, seed: Seed
) -> None:
    o = _org()
    report = await b.bootstrap(o)
    assert f"organisation {o.code}" in report["created"]
    assert _all(
        seed,
        "SELECT name, status, locale, time_zone, settings->>'currency' FROM org.organization"
        " WHERE id = %s",
        o.id,
    ) == [("Adventure Works Cycles", "active", "vi-VN", "Asia/Ho_Chi_Minh", "VND")]
    roles = dict(
        _all(seed, "SELECT code, capabilities FROM org.role WHERE org_id = %s AND is_system", o.id)
    )
    assert sorted(roles) == [r.code for r in b.DEFAULT_ROLES]
    assert roles["AC01"] == ["ask"]
    assert all(set(caps) <= CAPABILITIES for caps in roles.values())

    identity = await OrgIdentityResolver().resolve(
        org_id=o.id, external_id=o.admin_external_id, data_space=None
    )
    assert identity is not None
    # The admin administers and nothing more: compliance is somebody else's.
    assert set(identity.capabilities) == {"admin"}


async def test_running_it_again_keeps_a_role_an_administrator_changed(seed: Seed) -> None:
    o = _org()
    await b.bootstrap(o)
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE org.role SET capabilities = '{ask,view_detail}' WHERE org_id = %s AND code = 'AC01'",
            (str(o.id),),
        )
    again = await b.bootstrap(
        _org(
            id=o.id,
            code=o.code,
            name="Adventure Works",
            currency="USD",
            admin_external_id=o.admin_external_id,
        )
    )
    assert again["created"] == []
    assert _all(
        seed, "SELECT capabilities FROM org.role WHERE org_id = %s AND code = 'AC01'", o.id
    ) == [(["ask", "view_detail"],)]
    assert _all(
        seed, "SELECT name, settings->>'currency' FROM org.organization WHERE id = %s", o.id
    ) == [("Adventure Works", "USD")]
    assert _all(seed, "SELECT count(*) FROM org.user_role WHERE org_id = %s", o.id) == [(1,)]


async def test_an_admin_bound_to_another_keycloak_user_is_not_repointed(seed: Seed) -> None:
    o = _org()
    await b.bootstrap(o)
    with pytest.raises(b.BootstrapRefused, match="another Keycloak user"):
        await b.bootstrap(_org(id=o.id, code=o.code, admin_external_id=str(uuid.uuid4())))
    assert _all(seed, "SELECT external_id FROM org.app_user WHERE org_id = %s", o.id) == [
        (o.admin_external_id,)
    ]


async def test_an_organisation_keeps_its_code(seed: Seed) -> None:
    o = _org()
    await b.bootstrap(o)
    report = await b.bootstrap(
        _org(id=o.id, code="someone-else", admin_external_id=o.admin_external_id)
    )
    assert f"code {o.code} (asked someone-else)" in report["kept"]
    assert _all(seed, "SELECT code FROM org.organization WHERE id = %s", o.id) == [(o.code,)]


@pytest.mark.parametrize(
    ("change", "says"),
    [
        ({"locale": "vietnamese"}, "locale"),
        ({"time_zone": "Asia/Saigon City"}, "time zone"),
        ({"currency": "dong"}, "currency"),
        ({"code": "Adventure Works"}, "code"),
        ({"admin_email": "nobody"}, "admin email"),
    ],
)
def test_nothing_is_guessed_a_bad_value_writes_nothing(
    seed: Seed, change: dict[str, str], says: str
) -> None:
    o = _org(**change)
    args = [
        "--org", str(o.id), "--code", o.code, "--name", o.name, "--locale", o.locale,
        "--time-zone", o.time_zone, "--currency", o.currency, "--admin-username",
        o.admin_username, "--admin-email", o.admin_email, "--admin-external-id",
        o.admin_external_id,
    ]  # fmt: skip
    with pytest.raises(b.BootstrapRefused, match=says):
        o.check()
    assert b.main(args) == 1
    assert _all(seed, "SELECT count(*) FROM org.organization WHERE id = %s", o.id) == [(0,)]
