"""PL-02: an outbox event whose handler raises no longer holds its batch. It is recorded (attempts,
last_error), left until its backoff ends, and after five failures it is dead: out of every claim,
announced as outbox.dead_letter on integration.outbox, and told once to each admin."""

from __future__ import annotations

import json
import uuid

import pytest

from libs.dataagent_db.outbox_relay import BACKOFF_MINUTES, DEAD_AFTER
from services.worker_agent import dead_letter
from services.worker_agent.events import Event, OutboxTableSource
from services.worker_agent.main import run_once

from .conftest import Seed

pytestmark = [pytest.mark.integration]

FAILS, PASSES = "probe.fails", "probe.passes"
handled: list[uuid.UUID] = []


async def _fails(event: Event) -> None:
    raise RuntimeError("the probe broke")


async def _passes(event: Event) -> None:
    handled.append(event.event_id)


HANDLERS = {FAILS: _fails, PASSES: _passes}


def _org(seed: Seed) -> tuple[uuid.UUID, uuid.UUID]:
    org_id = seed.row("org.organization", code=f"o-{uuid.uuid4().hex[:8]}", name="Relay")
    admin = seed.row(
        "org.app_user",
        org_id=org_id,
        external_id=f"kc-{org_id.hex[:6]}",
        username="admin",
        display_name="Admin",
    )
    role = seed.row("org.role", org_id=org_id, code="ADMIN", name="Admin", capabilities=["admin"])
    seed.row("org.user_role", org_id=org_id, user_id=admin, role_id=role, source="manual")
    return org_id, admin


def _event(seed: Seed, org_id: uuid.UUID, event_type: str) -> uuid.UUID:
    return seed.row(
        "monitor.outbox",
        org_id=org_id,
        aggregate_type="probe",
        aggregate_id=uuid.uuid4(),
        event_type=event_type,
        payload=json.dumps({}),
    )


def _state(seed: Seed, event_id: uuid.UUID) -> tuple:
    return seed.all(
        "SELECT published_at IS NOT NULL, attempts, last_error,"
        " round(extract(epoch FROM next_attempt_at - now()) / 60), dead_at IS NOT NULL"
        " FROM monitor.outbox WHERE id = %s",
        event_id,
    )[0]


def _source() -> OutboxTableSource:
    return OutboxTableSource(schema="monitor", event_types=frozenset(HANDLERS))


async def test_a_failing_event_does_not_hold_the_one_behind_it(seed: Seed) -> None:
    org_id, _ = _org(seed)
    broken, fine = _event(seed, org_id, FAILS), _event(seed, org_id, PASSES)
    assert await run_once(_source(), HANDLERS, batch=10) == 2
    assert fine in handled
    assert _state(seed, fine)[0] is True
    published, attempts, error, wait, dead = _state(seed, broken)
    assert (published, attempts, dead) == (False, 1, False)
    assert error == "RuntimeError: the probe broke"
    assert wait == BACKOFF_MINUTES[0]
    # Not due yet: the next claim passes it by.
    assert await run_once(_source(), HANDLERS, batch=10) == 0


async def test_the_fifth_failure_leaves_it_dead_and_tells_the_admins_once(seed: Seed) -> None:
    org_id, admin = _org(seed)
    broken = _event(seed, org_id, FAILS)
    # Four failures already, and due again.
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE monitor.outbox SET attempts = %s, next_attempt_at = now() - interval '1 second'"
            " WHERE id = %s",
            (DEAD_AFTER - 1, str(broken)),
        )
    assert await run_once(_source(), HANDLERS, batch=10) == 1
    published, attempts, _, _, dead = _state(seed, broken)
    assert (published, attempts, dead) == (False, DEAD_AFTER, True)
    with seed.conn.cursor() as cur:
        cur.execute(
            "UPDATE monitor.outbox SET next_attempt_at = now() WHERE id = %s", (str(broken),)
        )
    assert await run_once(_source(), HANDLERS, batch=10) == 0  # dead: never claimed again

    (letter,) = seed.all(
        "SELECT payload FROM integration.outbox WHERE event_type = 'outbox.dead_letter'"
        " AND org_id = %s",
        org_id,
    )
    assert letter[0]["event_id"] == str(broken) and letter[0]["schema"] == "monitor"
    assert letter[0]["attempts"] == DEAD_AFTER

    letters = OutboxTableSource(schema="integration", event_types=frozenset(dead_letter.HANDLERS))
    assert await run_once(letters, dead_letter.HANDLERS, batch=10) == 1
    told = seed.all(
        "SELECT user_id, kind, priority, body FROM notify.notification WHERE org_id = %s", org_id
    )
    assert [(t[0], t[1], t[2]) for t in told] == [(admin, "system", "high")]
    assert "probe.fails" in told[0][3] and "RuntimeError" in told[0][3]
    # The same dead letter delivered again tells nobody twice.
    await dead_letter.handle(
        Event(uuid.uuid4(), org_id, dead_letter.EVENT_TYPE, "outbox_event", broken, letter[0])
    )
    assert len(seed.all("SELECT 1 FROM notify.notification WHERE org_id = %s", org_id)) == 1


async def test_a_source_without_fail_still_leaves_the_batch_on_an_error() -> None:
    class Plain:
        def claim(self, batch: int):  # type: ignore[no-untyped-def]
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def claimed():  # type: ignore[no-untyped-def]
                yield [Event(uuid.uuid4(), uuid.uuid4(), FAILS, "probe", uuid.uuid4(), {})]

            return claimed()

    with pytest.raises(RuntimeError, match="the probe broke"):
        await run_once(Plain(), HANDLERS, batch=1)
