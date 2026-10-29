"""The workflow every Schedule starts: one firing, run as one activity.

Kept apart from targets.py and clock.py because Temporal replays workflow code in a sandbox that
re-imports its module: this one imports nothing but the SDK, and names the activity by string.
"""

from __future__ import annotations

import datetime
from dataclasses import dataclass

from temporalio import workflow
from temporalio.common import RetryPolicy, SearchAttributeKey

FIRE_ACTIVITY = "fire_schedule"
SCHEDULED_AT = SearchAttributeKey.for_datetime("TemporalScheduledStartTime")


@dataclass(frozen=True)
class Firing:
    kind: str
    org_id: str | None = None
    entity_id: str | None = None


@workflow.defn(name="FireSchedule")
class FireSchedule:
    @workflow.run
    async def run(self, firing: Firing) -> str:
        # The minute the Schedule meant, not when the workflow got to run: a firing delayed by a
        # restart still serves (and dedupes on) its own minute.
        at = workflow.info().typed_search_attributes.get(SCHEDULED_AT) or workflow.now()
        result: str = await workflow.execute_activity(
            FIRE_ACTIVITY,
            args=[firing, at.astimezone(datetime.UTC).isoformat()],
            start_to_close_timeout=datetime.timedelta(minutes=5),
            retry_policy=RetryPolicy(maximum_attempts=5),
        )
        return result
