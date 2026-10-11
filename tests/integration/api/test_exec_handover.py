"""Lớp riêng lãnh đạo, UC-16-06 against PostgreSQL: seal, selective handover and shred when a
leader leaves (BR-195). The owner acts on their own vault; AC05 (admin) asks for a seal or a shred
by the contract's exit policy and never reads the vault; AC06 (compliance), another person, approves
a shred the owner takes no part in. The world is test_exec.py's.
"""

from __future__ import annotations

import base64
import os
import uuid
from typing import Any

import httpx
import pytest

from .conftest import Seed
from .test_exec import (
    World,
    build_world,
    client,
    hardware_wrap,
    make_thesis,
    make_vault,
    memory_body,
    thesis_body,
)

pytestmark = [pytest.mark.integration, pytest.mark.contract]

URL = "/v1/exec/handovers"


@pytest.fixture
def w(seed: Seed) -> World:
    return build_world(seed)


def handover(kind: str, reason: str = "leave_position", **extra: Any) -> dict[str, Any]:
    return {"kind": kind, "reason": reason, **extra}


def key_log(w: World, vault_id: object) -> list[tuple[str, str, str]]:
    with w.seed.conn.cursor() as cur:
        cur.execute(
            "SELECT action, actor_kind, actor_ref FROM exec.key_access_log WHERE vault_id = %s"
            " ORDER BY at, id",
            (str(vault_id),),
        )
        return list(cur.fetchall())


async def admin_asks(
    api: httpx.AsyncClient, w: World, who: str, owner: str, body: dict[str, Any]
) -> httpx.Response:
    return await api.post(
        URL, params={"owner_id": str(w.users[owner])}, json=body, headers=w.headers(who)
    )


async def test_owner_seals_their_vault_and_it_takes_no_new_content(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        thesis = await make_thesis(api, w, "leader")
        h = w.headers("leader")
        sealed = await api.post(URL, json=handover("seal", "owner_request"), headers=h)
        assert sealed.status_code == 201, sealed.text
        row = sealed.json()
        assert row["status"] == "completed" and row["completed_at"]
        assert row["owner_participates"] is True and row["requested_by"] == str(w.users["leader"])
        assert row["vault_id"] == vault["id"]
        assert [e[0] for e in w.events(row["id"])] == ["exec.handover.completed"]
        assert w.one("SELECT status FROM exec.vault WHERE id = %s", vault["id"]) == "sealed"

        # A sealed vault is read-only (BR-195): the owner still reads it, writes nothing new.
        read = await api.get("/v1/exec/vault", headers=h)
        assert read.status_code == 200 and read.json()["status"] == "sealed"
        for path, body in (
            ("/v1/exec/theses", thesis_body()),
            ("/v1/exec/memory-items", memory_body()),
            (f"/v1/exec/theses/{thesis['id']}/evidence:refresh", None),
        ):
            refused = await api.post(path, json=body, headers=h)
            assert refused.status_code == 409, (path, refused.text)
            assert refused.json()["code"] == "invalid_state"
        wrap = await api.post("/v1/exec/vault/key-wraps", json=hardware_wrap(), headers=h)
        assert wrap.status_code == 409
        twice = await api.post(URL, json=handover("seal", "owner_request"), headers=h)
        assert twice.status_code == 409 and twice.json()["code"] == "invalid_state"
        # The thesis already due keeps being scored: it is untouched.
        kept = await api.get(f"/v1/exec/theses/{thesis['id']}", headers=h)
        assert kept.json()["status"] == "open"


@pytest.mark.req("AC-16-06-01", "AC-16-06-08")
async def test_admin_seals_a_leaving_owners_vault_without_reading_it(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        thesis = await make_thesis(api, w, "leader")
        await api.post("/v1/exec/memory-items", json=memory_body(), headers=w.headers("leader"))
        sealed = await admin_asks(api, w, "admin", "leader", handover("seal"))
        assert sealed.status_code == 201, sealed.text
        row = sealed.json()
        assert row["status"] == "completed" and row["owner_participates"] is False
        assert row["owner_id"] == str(w.users["leader"]) and row["requested_by"] == str(
            w.users["admin"]
        )
        assert row["approved_by"] is None
        assert w.one("SELECT status FROM exec.vault WHERE id = %s", vault["id"]) == "sealed"
        events = w.events(row["id"])
        assert [e[0] for e in events] == ["exec.handover.completed"]
        assert events[0][1]["owner_participates"] is False

        # AC05 holds no exec_vault: no route of the vault opens for them.
        for path in (
            "/v1/exec/vault",
            "/v1/exec/theses",
            "/v1/exec/memory-items",
            f"/v1/exec/theses/{thesis['id']}",
        ):
            denied = await api.get(path, headers=w.headers("admin"))
            assert denied.status_code == 403, path
        # Holding both admin and exec_vault opens one's own vault, never another leader's.
        boss = await api.get(f"/v1/exec/theses/{thesis['id']}", headers=w.headers("boss"))
        assert boss.status_code == 404
        boss_list = await api.get("/v1/exec/memory-items", headers=w.headers("boss"))
        assert boss_list.json()["items"] == []
        # The handover answer has no key or content column.
        assert not {"box_private_key_ct", "wrapped_vmk", "content_ct"} & set(row)

        again = await admin_asks(api, w, "admin", "leader", handover("seal"))
        assert again.status_code == 409 and again.json()["code"] == "invalid_state"


async def test_admin_may_only_do_what_the_contract_says(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")  # exit_policy seal
        await make_vault(api, w, "leader2", exit_policy="selective_handover")
        shred = await admin_asks(api, w, "admin", "leader", handover("shred", "contract_end"))
        assert shred.status_code == 422 and shred.json()["code"] == "business_rule_violated"
        # No handover without the owner (BR-195): only a seal is left.
        selective = await admin_asks(
            api,
            w,
            "admin",
            "leader2",
            handover("selective_handover", successor_id=str(w.users["leader"])),
        )
        assert selective.status_code == 422 and selective.json()["code"] == "business_rule_violated"
        not_expired = await admin_asks(
            api, w, "admin", "leader", handover("shred", "sealed_expired")
        )
        assert not_expired.status_code == 422
        nobody = await api.post(
            URL,
            params={"owner_id": str(uuid.uuid4())},
            json=handover("seal"),
            headers=w.headers("admin"),
        )
        assert nobody.status_code == 404
        # The other organisation's administrator does not find this leader's vault.
        stranger = await admin_asks(api, w, "stranger", "leader", handover("seal"))
        assert stranger.status_code == 404
        # exec_vault alone never acts on another leader's vault; compliance cannot ask at all.
        peer = await admin_asks(api, w, "leader2", "leader", handover("seal"))
        assert peer.status_code == 403
        officer = await admin_asks(api, w, "compliance", "leader", handover("seal"))
        assert officer.status_code == 403
        analyst = await api.post(URL, json=handover("seal"), headers=w.headers("analyst"))
        assert analyst.status_code == 403
        own = await api.post(URL, json=handover("seal"), headers=w.headers("admin"))
        assert own.status_code == 422
        assert w.one("SELECT count(*) FROM exec.handover WHERE org_id = %s", w.org) == 0


@pytest.mark.req("AC-16-06-04")
async def test_shred_without_the_owner_needs_a_second_person(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader2", exit_policy="shred")
        asked = await admin_asks(api, w, "admin", "leader2", handover("shred", "contract_end"))
        assert asked.status_code == 201, asked.text
        row = asked.json()
        assert row["status"] == "requested" and row["owner_participates"] is False
        assert [e[0] for e in w.events(row["id"])] == ["exec.handover.requested"]
        # Nothing happens to the keys before AC06 approves.
        assert w.one("SELECT status FROM exec.vault WHERE id = %s", vault["id"]) == "active"
        assert key_log(w, vault["id"]) == []
        dup = await admin_asks(api, w, "admin", "leader2", handover("shred", "contract_end"))
        assert dup.status_code == 409 and dup.json()["code"] == "conflict"

        approve = f"{URL}/{row['id']}:approve"
        by_admin = await api.post(approve, headers=w.headers("admin"))
        assert by_admin.status_code == 403
        by_owner = await api.post(approve, headers=w.headers("leader2"))
        assert by_owner.status_code == 403
        by_stranger = await api.post(approve, headers=w.headers("stranger"))
        assert by_stranger.status_code == 404

        done = await api.post(
            approve, json={"note": "Hợp đồng chấm dứt"}, headers=w.headers("compliance")
        )
        assert done.status_code == 200, done.text
        out = done.json()
        assert out["status"] == "approved" and out["approved_by"] == str(w.users["compliance"])
        assert out["approved_at"]
        assert [e[0] for e in w.events(row["id"])][-1] == "exec.handover.approved"
        assert key_log(w, vault["id"]) == [("shred", "admin_request", f"handover:{row['id']}")]
        # The owner sees the administrator's entry in their own log (BR-192).
        log = await api.get("/v1/exec/key-access-log", headers=w.headers("leader2"))
        assert [(r["action"], r["actor_kind"]) for r in log.json()["items"]] == [
            ("shred", "admin_request")
        ]
        twice = await api.post(approve, headers=w.headers("compliance2"))
        assert twice.status_code == 409 and twice.json()["code"] == "invalid_state"


@pytest.mark.req("AC-16-06-04")
async def test_the_requester_cannot_approve_their_own_shred(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader", exit_policy="shred")
        asked = await admin_asks(api, w, "boss", "leader", handover("shred", "contract_end"))
        assert asked.status_code == 201, asked.text
        own = await api.post(f"{URL}/{asked.json()['id']}:approve", headers=w.headers("boss"))
        assert own.status_code == 403 and own.json()["code"] == "policy_denied"
        assert (
            w.one("SELECT approved_by FROM exec.handover WHERE id = %s", asked.json()["id"]) is None
        )
        other = await api.post(
            f"{URL}/{asked.json()['id']}:approve", headers=w.headers("compliance")
        )
        assert other.status_code == 200, other.text


@pytest.mark.req("AC-16-06-03")
async def test_owner_shred_needs_no_approval(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        asked = await api.post(
            URL, json=handover("shred", "owner_request"), headers=w.headers("leader")
        )
        assert asked.status_code == 201, asked.text
        row = asked.json()
        assert row["status"] == "approved" and row["owner_participates"] is True
        assert row["approved_by"] is None
        assert [e[0] for e in w.events(row["id"])] == ["exec.handover.approved"]
        assert key_log(w, vault["id"]) == [("shred", "owner_device", f"handover:{row['id']}")]
        approve = await api.post(f"{URL}/{row['id']}:approve", headers=w.headers("compliance"))
        assert approve.status_code == 409 and approve.json()["code"] == "invalid_state"


async def test_a_seal_past_its_term_is_shredded_without_approval(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")  # exit_policy seal
        sealed = await admin_asks(api, w, "admin", "leader", handover("seal"))
        assert sealed.status_code == 201, sealed.text
        early = await admin_asks(api, w, "admin", "leader", handover("shred", "sealed_expired"))
        assert early.status_code == 422
        w.execute(
            "UPDATE exec.vault SET sealed_read_until = current_date - 1 WHERE id = %s", vault["id"]
        )
        expired = await admin_asks(api, w, "admin", "leader", handover("shred", "sealed_expired"))
        assert expired.status_code == 201, expired.text
        row = expired.json()
        assert row["status"] == "approved" and row["approved_by"] is None
        assert key_log(w, vault["id"]) == [("shred", "admin_request", f"handover:{row['id']}")]
        assert [e[0] for e in w.events(row["id"])] == ["exec.handover.approved"]


@pytest.mark.req("AC-16-06-02", "AC-16-06-05")
async def test_selective_handover_from_owner_to_successor(w: World) -> None:
    async with client(w) as api:
        vault = await make_vault(api, w, "leader", exit_policy="selective_handover")
        await make_vault(api, w, "leader2")
        h = w.headers("leader")
        items = [
            (await api.post("/v1/exec/memory-items", json=memory_body(), headers=h)).json()
            for _ in range(2)
        ]
        no_successor = await api.post(URL, json=handover("selective_handover"), headers=h)
        assert no_successor.status_code == 422
        to_self = await api.post(
            URL,
            json=handover("seal", successor_id=str(w.users["leader2"])),
            headers=h,
        )
        assert to_self.status_code == 422
        made = await api.post(
            URL,
            json=handover("selective_handover", successor_id=str(w.users["leader2"])),
            headers=h,
        )
        assert made.status_code == 201, made.text
        row = made.json()
        assert row["status"] == "in_progress" and row["successor_id"] == str(w.users["leader2"])
        assert [e[0] for e in w.events(row["id"])] == ["exec.handover.in_progress"]

        body = {
            "items": [
                {
                    "source_kind": "memory_item",
                    "source_id": i["id"],
                    "payload_sealed": base64.b64encode(os.urandom(64)).decode(),
                }
                for i in items
            ]
        }
        # AC05 has no path to hand over content (BR-195); neither does another leader.
        admin = await api.post(f"{URL}/{row['id']}/items", json=body, headers=w.headers("admin"))
        assert admin.status_code == 403
        boss = await api.post(f"{URL}/{row['id']}/items", json=body, headers=w.headers("boss"))
        assert boss.status_code == 404
        sent = await api.post(f"{URL}/{row['id']}/items", json=body, headers=h)
        assert sent.status_code == 201, sent.text
        assert len(sent.json()) == 2
        assert {i["successor_id"] for i in sent.json()} == {str(w.users["leader2"])}
        assert w.one("SELECT items_total FROM exec.handover WHERE id = %s", row["id"]) == 2
        assert ("rewrap_handover", "owner_device", f"handover:{row['id']}") in key_log(
            w, vault["id"]
        )
        added = [e for e in w.events(row["id"]) if e[0] == "exec.handover.items_added"]
        assert added[0][1]["items_added"] == 2 and added[0][1]["items_total"] == 2
        dup = await api.post(f"{URL}/{row['id']}/items", json=body, headers=h)
        assert dup.status_code == 409

        # The transit rows are the successor's to read, not the owner's.
        owner_read = await api.get(f"{URL}/{row['id']}/items", headers=h)
        assert owner_read.status_code == 404
        got = await api.get(f"{URL}/{row['id']}/items", headers=w.headers("leader2"))
        assert got.status_code == 200, got.text
        assert {i["source_id"] for i in got.json()["items"]} == {i["id"] for i in items}
        assert all(i["imported_at"] is None for i in got.json()["items"])

        owner_accept = await api.post(f"{URL}/{row['id']}:accept", headers=h)
        assert owner_accept.status_code == 404
        accepted = await api.post(f"{URL}/{row['id']}:accept", headers=w.headers("leader2"))
        assert accepted.status_code == 200, accepted.text
        assert accepted.json()["items_imported"] == 2 and accepted.json()["items_pending"] == 0
        # A3: completed once every item is imported; the old vault ends sealed (UC-16-06).
        assert accepted.json()["status"] == "completed"
        assert w.one("SELECT status FROM exec.handover WHERE id = %s", row["id"]) == "completed"
        assert w.one("SELECT status FROM exec.vault WHERE id = %s", vault["id"]) == "sealed"
        assert (
            w.one(
                "SELECT count(*) FROM exec.handover_item WHERE handover_id = %s AND imported_at IS NOT NULL",
                row["id"],
            )
            == 2
        )
        assert "exec.handover.accepted" in [e[0] for e in w.events(row["id"])]
        twice = await api.post(f"{URL}/{row['id']}:accept", headers=w.headers("leader2"))
        assert twice.status_code == 409 and twice.json()["code"] == "invalid_state"


async def test_a_successor_needs_an_active_vault(w: World) -> None:
    # UC-16-06 precondition: items are sealed to the successor's own vault key.
    async with client(w) as api:
        await make_vault(api, w, "leader", exit_policy="selective_handover")
        nowhere = await api.post(
            URL,
            json=handover("selective_handover", successor_id=str(w.users["leader2"])),
            headers=w.headers("leader"),
        )
        assert nowhere.status_code == 422
        assert nowhere.json()["code"] == "business_rule_violated"


async def test_a_seal_keeps_the_owner_reading_for_the_contract_term(w: World) -> None:
    # UC-16-06 step 3: read-only until sealed_read_until, 24 months unless the contract says.
    async with client(w) as api:
        vault = await make_vault(api, w, "leader")
        sealed = await admin_asks(api, w, "admin", "leader", handover("seal"))
        assert sealed.status_code == 201, sealed.text
        assert w.one(
            "SELECT sealed_read_until = (now() + interval '24 months')::date"
            " FROM exec.vault WHERE id = %s",
            vault["id"],
        )


async def test_selective_handover_follows_the_contract(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")  # exit_policy seal
        refused = await api.post(
            URL,
            json=handover("selective_handover", successor_id=str(w.users["leader2"])),
            headers=w.headers("leader"),
        )
        assert refused.status_code == 422 and refused.json()["code"] == "business_rule_violated"
        sealed = await api.post(URL, json=handover("seal"), headers=w.headers("leader"))
        items = await api.post(
            f"{URL}/{sealed.json()['id']}/items",
            json={
                "items": [
                    {
                        "source_kind": "memory_item",
                        "source_id": str(uuid.uuid4()),
                        "payload_sealed": base64.b64encode(os.urandom(64)).decode(),
                    }
                ]
            },
            headers=w.headers("leader"),
        )
        assert items.status_code == 409 and items.json()["code"] == "invalid_state"


async def test_handover_is_seen_by_its_owner_and_the_two_admin_roles(w: World) -> None:
    async with client(w) as api:
        await make_vault(api, w, "leader")
        await make_vault(api, w, "leader2")
        row = (await admin_asks(api, w, "admin", "leader", handover("seal"))).json()
        path = f"{URL}/{row['id']}"
        for name in ("leader", "admin", "compliance"):
            seen = await api.get(path, headers=w.headers(name))
            assert seen.status_code == 200, (name, seen.text)
            assert seen.json() == row
        other = await api.get(path, headers=w.headers("leader2"))
        assert other.status_code == 404
        stranger = await api.get(path, headers=w.headers("stranger"))
        assert stranger.status_code == 404
        analyst = await api.get(path, headers=w.headers("analyst"))
        assert analyst.status_code == 403
        missing = await api.get(f"{URL}/{uuid.uuid4()}", headers=w.headers("admin"))
        assert missing.status_code == 404
