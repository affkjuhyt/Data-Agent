"""Temporal is the clock of every schedule (ADR-33); this keeps it in step with the data.

Every RECONCILE_SECONDS the worker compares the schedules the data asks for (targets.wanted) with
the Temporal Schedules it finds, and creates, replaces or deletes the difference. A Schedule's memo
holds the fingerprint of the cron and zone it was made from, since Temporal turns a cron into
calendar specs and never gives the text back. Only the configuration is polled; when a Schedule
fires is Temporal's, with a short catch-up window so a worker that was down does not replay a
day of missed runs.

The same process runs the Temporal worker that executes the firings. Several replicas may
reconcile at once: a create that finds the Schedule already there, or a delete of one already
gone, is not an error. The list Temporal answers from lags a create by about a second, so a pass
that lands in that second only remakes a Schedule once more.
"""

from __future__ import annotations

import asyncio
import contextlib
import datetime
import logging
import os
import uuid
from collections.abc import Iterable

from google.protobuf.duration_pb2 import Duration
from temporalio import activity
from temporalio.api.workflowservice.v1 import RegisterNamespaceRequest
from temporalio.client import (
    Client,
    Schedule,
    ScheduleActionStartWorkflow,
    ScheduleAlreadyRunningError,
    ScheduleOverlapPolicy,
    SchedulePolicy,
    ScheduleSpec,
)
from temporalio.service import RPCError, RPCStatusCode
from temporalio.worker import Worker

from services.worker_agent.schedules import targets
from services.worker_agent.schedules.workflow import FIRE_ACTIVITY, FireSchedule, Firing

logger = logging.getLogger(__name__)

TASK_QUEUE = "schedules"
RECONCILE_SECONDS = 30.0
# A firing missed while the cluster was down is still made within this window, never later.
CATCHUP = datetime.timedelta(minutes=15)
RETENTION = datetime.timedelta(days=7)
FINGERPRINT = "fingerprint"


def address() -> str | None:
    return os.environ.get("TEMPORAL_ADDRESS") or None


def namespace() -> str:
    return os.environ.get("TEMPORAL_NAMESPACE", "dataagent")


@activity.defn(name=FIRE_ACTIVITY)
async def fire_activity(firing: Firing, at: str) -> str:
    return await targets.fire(
        firing.kind,
        uuid.UUID(firing.org_id) if firing.org_id else None,
        uuid.UUID(firing.entity_id) if firing.entity_id else None,
        datetime.datetime.fromisoformat(at),
    )


async def ensure_namespace(client: Client) -> None:
    """The namespace is the application's, not the server's: created on first use."""
    seconds = int(RETENTION.total_seconds())
    try:
        await client.workflow_service.register_namespace(
            RegisterNamespaceRequest(
                namespace=namespace(),
                workflow_execution_retention_period=Duration(seconds=seconds),
            )
        )
        logger.info("worker-agent | Temporal namespace %s registered", namespace())
    except RPCError as exc:
        if exc.status != RPCStatusCode.ALREADY_EXISTS:
            raise


def temporal_crons(expr: str) -> list[str]:
    """The cron expressions that make Temporal fire when libs/cron says it fires.

    With both day fields restricted, cron fires on either one (Vixie, croniter, the previews);
    Temporal fires only where both hold ("30 6 1 * 1" waited for a Monday the 1st). The spec fires
    on the union of its expressions, so such a cron becomes one per day field.
    """
    fields = expr.split()
    if len(fields) != 5 or "*" in (fields[2], fields[4]):
        return [expr]
    minute, hour, day, month, weekday = fields
    return [f"{minute} {hour} {day} {month} *", f"{minute} {hour} * {month} {weekday}"]


def schedule_of(target: targets.Target) -> Schedule:
    return Schedule(
        action=ScheduleActionStartWorkflow(
            FireSchedule.run,
            Firing(
                target.kind,
                str(target.org_id) if target.org_id else None,
                str(target.entity_id) if target.entity_id else None,
            ),
            # Temporal appends the scheduled time, so each firing has its own workflow id.
            id=f"fire-{target.schedule_id}",
            task_queue=TASK_QUEUE,
            execution_timeout=datetime.timedelta(minutes=30),
        ),
        spec=ScheduleSpec(cron_expressions=temporal_crons(target.cron), time_zone_name=target.zone),
        policy=SchedulePolicy(overlap=ScheduleOverlapPolicy.SKIP, catchup_window=CATCHUP),
    )


async def wanted() -> dict[str, targets.Target]:
    out = {t.schedule_id: t for t in targets.global_targets()}
    for org_id in await targets.organisations():
        try:
            out.update((t.schedule_id, t) for t in await targets.wanted(org_id))
        except Exception:
            # The whole pass fails: reconciling without this organisation would delete its Schedules.
            logger.exception("worker-agent | schedules of organisation %s not read", org_id)
            raise
    return out


async def existing(client: Client) -> dict[str, str | None]:
    """Our Schedules (by the kind prefix of their id) and the fingerprint each was made from."""
    found: dict[str, str | None] = {}
    converter = client.data_converter.payload_converter
    async for entry in await client.list_schedules():
        if entry.id.split(":", 1)[0] not in targets.KINDS:
            continue
        payload = entry.raw_entry.memo.fields.get(FINGERPRINT)
        found[entry.id] = converter.from_payload(payload, str) if payload is not None else None
    return found


async def reconcile(client: Client) -> dict[str, int]:
    want = await wanted()
    have = await existing(client)
    counts = {"created": 0, "replaced": 0, "deleted": 0, "failed": 0}
    for schedule_id, target in want.items():
        if have.get(schedule_id, "") == target.fingerprint:
            continue
        replacing = schedule_id in have
        try:
            if replacing:
                # The memo cannot be updated in place, and it is what says the Schedule is current.
                await _delete(client, schedule_id)
            await client.create_schedule(
                schedule_id, schedule_of(target), memo={FINGERPRINT: target.fingerprint}
            )
            counts["replaced" if replacing else "created"] += 1
        except ScheduleAlreadyRunningError:
            pass  # another replica made it in the meantime
        except Exception:
            # A cron Temporal reads differently from libs/cron lands here: that schedule only.
            logger.exception("worker-agent | Temporal schedule %s not made", schedule_id)
            counts["failed"] += 1
    for schedule_id in _gone(have, want):
        await _delete(client, schedule_id)
        counts["deleted"] += 1
    if any(counts.values()):
        logger.info("worker-agent | schedules reconciled: %s", counts)
    return counts


def _gone(have: Iterable[str], want: dict[str, targets.Target]) -> list[str]:
    return [schedule_id for schedule_id in have if schedule_id not in want]


async def _delete(client: Client, schedule_id: str) -> None:
    try:
        await client.get_schedule_handle(schedule_id).delete()
    except RPCError as exc:
        if exc.status != RPCStatusCode.NOT_FOUND:
            raise


async def serve(stop: asyncio.Event) -> None:
    """Keep the Schedules in step and run their firings until `stop`."""
    target = address()
    if target is None:
        logger.warning("worker-agent | TEMPORAL_ADDRESS is not set: no schedule fires")
        return
    client = await _connect(target, stop)
    if client is None:
        return
    await ensure_namespace(client)
    worker = Worker(
        client, task_queue=TASK_QUEUE, workflows=[FireSchedule], activities=[fire_activity]
    )
    async with worker:
        while not stop.is_set():
            try:
                await reconcile(client)
            except Exception:
                logger.exception("worker-agent | schedule reconcile failed")
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=RECONCILE_SECONDS)


async def _connect(target: str, stop: asyncio.Event) -> Client | None:
    """Temporal may start after the worker: keep trying until it answers or the worker stops."""
    while not stop.is_set():
        try:
            return await Client.connect(target, namespace=namespace())
        except RuntimeError as exc:  # the SDK's "Failed client connect"
            logger.warning("worker-agent | Temporal at %s not reachable yet: %s", target, exc)
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(stop.wait(), timeout=RECONCILE_SECONDS)
    return None
