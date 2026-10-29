"""clock.reconcile keeps one Temporal Schedule per target: made when missing, replaced when its
cron or zone moved, deleted when the data no longer asks for it, and nobody else's touched."""

from __future__ import annotations

import uuid
from types import SimpleNamespace
from typing import Any

import pytest
from temporalio.api.common.v1 import Memo
from temporalio.converter import default as default_converter

from services.worker_agent.schedules import clock, targets

pytestmark = [pytest.mark.unit]

ORG = uuid.UUID("a0000000-0000-0000-0000-000000000001")
JOB = uuid.UUID("b0000000-0000-0000-0000-000000000001")
PIPELINE = uuid.UUID("b0000000-0000-0000-0000-000000000002")


class FakeClient:
    """The slice of temporalio.client.Client that reconcile uses."""

    def __init__(self, schedules: dict[str, str | None]) -> None:
        self.data_converter = default_converter()
        self.schedules = dict(schedules)
        self.created: list[tuple[str, Any, dict[str, str]]] = []
        self.deleted: list[str] = []
        self.refuse: set[str] = set()

    async def list_schedules(self) -> Any:
        converter = self.data_converter.payload_converter
        entries = []
        for schedule_id, fingerprint in self.schedules.items():
            memo = Memo()
            if fingerprint is not None:
                memo.fields[clock.FINGERPRINT].CopyFrom(converter.to_payload(fingerprint))
            entries.append(SimpleNamespace(id=schedule_id, raw_entry=SimpleNamespace(memo=memo)))

        async def iterate() -> Any:
            for entry in entries:
                yield entry

        return iterate()

    async def create_schedule(
        self, schedule_id: str, schedule: Any, *, memo: dict[str, str]
    ) -> None:
        if schedule_id in self.refuse:
            raise RuntimeError("Temporal refused the spec")
        self.created.append((schedule_id, schedule, memo))
        self.schedules[schedule_id] = memo[clock.FINGERPRINT]

    def get_schedule_handle(self, schedule_id: str) -> Any:
        async def delete() -> None:
            self.deleted.append(schedule_id)
            self.schedules.pop(schedule_id, None)

        return SimpleNamespace(delete=delete)


def wants(monkeypatch: pytest.MonkeyPatch, *wanted: targets.Target) -> None:
    async def fake() -> dict[str, targets.Target]:
        return {t.schedule_id: t for t in wanted}

    monkeypatch.setattr(clock, "wanted", fake)


async def test_a_missing_schedule_is_made_from_its_cron_and_zone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    job = targets.Target("job", "*/15 * * * *", "UTC", ORG, JOB)
    wants(monkeypatch, job)
    client = FakeClient({})
    assert (await clock.reconcile(client))["created"] == 1  # type: ignore[arg-type]
    schedule_id, schedule, memo = client.created[0]
    assert schedule_id == f"job:{ORG}:{JOB}"
    assert schedule.spec.cron_expressions == ["*/15 * * * *"]
    assert schedule.spec.time_zone_name == "UTC"
    assert schedule.action.task_queue == clock.TASK_QUEUE
    assert schedule.policy.catchup_window == clock.CATCHUP
    assert memo == {clock.FINGERPRINT: job.fingerprint}
    # In step now: the next pass leaves it alone.
    assert not any((await clock.reconcile(client)).values())  # type: ignore[arg-type]


async def test_a_moved_cron_replaces_the_schedule_and_a_dropped_one_is_deleted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    old = targets.Target("pipeline", "0 7 * * *", "UTC", ORG, PIPELINE)
    moved = targets.Target("pipeline", "0 7 * * *", "Asia/Ho_Chi_Minh", ORG, PIPELINE)
    assert old.fingerprint != moved.fingerprint
    wants(monkeypatch, moved)
    client = FakeClient(
        {
            moved.schedule_id: old.fingerprint,
            f"job:{ORG}:{JOB}": "stale",  # its job was paused
            "someone-elses-schedule": None,  # not ours: no kind prefix
        }
    )
    counts = await clock.reconcile(client)  # type: ignore[arg-type]
    assert counts == {"created": 0, "replaced": 1, "deleted": 1, "failed": 0}
    assert client.deleted == [moved.schedule_id, f"job:{ORG}:{JOB}"]
    assert client.schedules[moved.schedule_id] == moved.fingerprint
    assert "someone-elses-schedule" in client.schedules


async def test_one_schedule_temporal_refuses_does_not_stop_the_others(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    bad = targets.Target("job", "0 0 L * *", "UTC", ORG, JOB)
    good = targets.Target("pipeline", "0 7 * * *", "UTC", ORG, PIPELINE)
    wants(monkeypatch, bad, good)
    client = FakeClient({})
    client.refuse.add(bad.schedule_id)
    counts = await clock.reconcile(client)  # type: ignore[arg-type]
    assert counts["failed"] == 1 and counts["created"] == 1
    assert good.schedule_id in client.schedules


def test_a_schedule_id_names_its_kind_organisation_and_row() -> None:
    assert targets.Target("quality", "0 * * * *", "UTC", ORG).schedule_id == f"quality:{ORG}"
    assert targets.Target("nightly-evals", "0 2 * * *", "Asia/Ho_Chi_Minh").schedule_id == (
        "nightly-evals"
    )
    assert {t.kind for t in targets.global_targets()} <= targets.KINDS


@pytest.mark.parametrize(
    ("expr", "crons"),
    [
        ("0 7 * * 1-5", ["0 7 * * 1-5"]),
        ("0 0 1,15 * *", ["0 0 1,15 * *"]),
        ("@daily", ["@daily"]),
        # Either day field fires, as libs/cron reads it; Temporal wants both, so one per field.
        ("30 6 1 * 1", ["30 6 1 * *", "30 6 * * 1"]),
        ("0 9 1 jul wed", ["0 9 1 jul *", "0 9 * jul wed"]),
    ],
)
def test_a_cron_on_both_day_fields_fires_on_either(expr: str, crons: list[str]) -> None:
    assert clock.temporal_crons(expr) == crons
